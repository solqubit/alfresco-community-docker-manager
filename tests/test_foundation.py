import copy
import gzip
import io
import json
from pathlib import Path
import re
import tarfile
import tempfile
import unittest

from acdm.archive import extract_checked, members
from acdm.catalog import Catalog, bundle_key, check_bundle, parse_compose
from acdm.config import (access_url, applications, caddyfile, compose, csrf_patterns, image_tag, initial,
                         java_options, start_order, validate, validate_images)
from acdm.core import Error, ROOT, State, atomic, sha
from acdm.docker import curl_quote
from acdm.operations import bundle_changes

DIGEST = '@sha256:' + 'a' * 64


def pinned(bundle):
    return {role: ref.split('@')[0] + DIGEST for role, ref in bundle['images'].items()}


def packaged():
    return Catalog(tempfile.mkdtemp()).packaged()


def configuration(index=0):
    """Pinned configuration of a packaged bundle (0 = newest)."""
    return initial(pinned(packaged()[index]), storage='named')


class ConfigurationTests(unittest.TestCase):
    def test_elasticsearch_configuration_is_valid(self):
        cfg = configuration()
        validate(cfg)
        services = compose(cfg)['services']
        self.assertIn('elasticsearch', services)
        self.assertIn('batch-indexing', services)
        self.assertEqual(set(services), set(start_order(cfg)))
        self.assertEqual(start_order(cfg)[-1], 'proxy')
        self.assertIn('-Dindex.subsystem.name=elasticsearch', services['alfresco']['environment']['JAVA_OPTS'])

    def test_solr_bundles_are_not_supported(self):
        images = {role: ref for role, ref in pinned(packaged()[0]).items() if role not in ('elasticsearch', 'batch-indexing')}
        with self.assertRaises(Error):
            validate_images(images)
        self.assertEqual(parse_compose('image: docker.io/alfresco/alfresco-search-services:2.0.20\n'), {})

    def test_only_the_proxy_publishes_ports(self):
        services = compose(configuration())['services']
        self.assertEqual([name for name, service in services.items() if 'ports' in service], ['proxy'])
        ports = services['proxy']['ports']
        self.assertEqual([(row['target'], row['published']) for row in ports], [(80, '80'), (443, '443')])

    def test_generated_secrets_reach_services_and_differ(self):
        cfg = configuration()
        services = compose(cfg)['services']
        self.assertIn('-Ddb.password=' + cfg['db_password'], services['alfresco']['environment']['JAVA_OPTS'])
        self.assertEqual(services['postgres']['environment']['POSTGRES_PASSWORD'], cfg['db_password'])
        self.assertEqual(services['batch-indexing']['environment']['ALFRESCO_CONTENT_TRANSFORM_SHAREDSECRET'], cfg['search_secret'])
        self.assertEqual(services['activemq']['environment']['ACTIVEMQ_ADMIN_PASSWORD'], cfg['mq_password'])
        self.assertEqual(len({cfg['db_password'], cfg['search_secret'], cfg['mq_password'], cfg['admin_password']}), 4)
        self.assertNotIn('=alfresco ', services['alfresco']['environment']['JAVA_OPTS'].replace('db.username=alfresco', ''))

    def test_compose_interpolation_is_escaped(self):
        cfg = configuration()
        for service in compose(cfg)['services'].values():
            for value in service['environment'].values():
                self.assertNotRegex(value, r'(?<!\$)\$(?!\$)')

    def test_private_networks_do_not_overlap(self):
        import ipaddress
        networks = compose(configuration())['networks']
        proxy = ipaddress.ip_network(networks['proxy']['ipam']['config'][0]['subnet'])
        backend = ipaddress.ip_network(networks['backend']['ipam']['config'][0]['subnet'])
        self.assertFalse(proxy.overlaps(backend))
        self.assertTrue(networks['backend']['internal'])

    def test_proxy_uses_dynamic_address_across_restarts(self):
        services = compose(configuration())['services']
        self.assertEqual(services['proxy']['networks'], ['proxy'])
        self.assertNotIn('ipv4_address', json.dumps(services))

    def test_fresh_search_index_rebuilds_historical_nodes(self):
        environment = compose(configuration())['services']['batch-indexing']['environment']
        self.assertEqual(environment['ALFRESCO_REINDEX_CONTINUOUS_SEEDFROMEARLIEST'], 'true')
        self.assertEqual(environment['ALFRESCO_REINDEX_CONTINUOUS_MAXGAPAGE'], '0')

    def test_invalid_images_are_rejected(self):
        cfg = configuration()
        broken = copy.deepcopy(cfg)
        broken['images']['alfresco'] = 'evil/alfresco:26.2.0' + DIGEST
        with self.assertRaises(Error):
            validate(broken)
        broken = copy.deepcopy(cfg)
        broken['images']['share'] = broken['images']['share'].split('@')[0]
        with self.assertRaises(Error):
            validate(broken)


class HttpsTests(unittest.TestCase):
    """Intranet only: local addresses with HTTPS from Caddy's own CA."""

    def https_configuration(self):
        cfg = configuration()
        cfg['maintenance_gate'] = False
        cfg['domains'].append(dict(host='alfresco.lan', kind='local', mode='local', tls='internal', email=''))
        cfg['domains'].append(dict(host='192.168.1.10', kind='local', mode='local', tls='off', email=''))
        cfg['primary'] = 'alfresco.lan'
        validate(cfg)
        return cfg

    def test_new_installations_use_local_https_by_default(self):
        cfg = configuration()
        self.assertEqual(cfg['domains'][0]['tls'], 'internal')
        self.assertEqual(access_url(cfg), 'https://127.0.0.1')
        self.assertIn('default_sni 127.0.0.1', caddyfile(cfg))

    def test_local_https_uses_internal_ca_and_redirects_http(self):
        text = caddyfile(self.https_configuration())
        block = text[text.index('https://alfresco.lan {'):]
        self.assertIn('tls internal', block)
        self.assertIn('redir https://alfresco.lan{uri} 308', text)
        self.assertNotIn('acme', text)
        self.assertNotIn('letsencrypt', text)

    def test_the_proxy_does_not_restrict_client_networks(self):
        # Exposure is the administrator's network decision (firewall, router, VPN).
        text = caddyfile(self.https_configuration())
        self.assertNotIn('remote_ip', text)
        self.assertNotIn('403', text.replace('respond @blocked "Forbidden" 403', '')
                         .replace('respond @shareblocked "Forbidden" 403', ''))

    def test_primary_https_url_drives_alfresco_share_and_csrf(self):
        cfg = self.https_configuration()
        self.assertEqual(access_url(cfg), 'https://alfresco.lan')
        options = java_options(cfg)
        self.assertIn('-Dalfresco.protocol=https', options)
        self.assertIn('-Dshare.host=alfresco.lan', options)
        origin, referer = csrf_patterns(cfg)
        for url in ('https://alfresco.lan', 'http://192.168.1.10', 'https://127.0.0.1'):
            self.assertRegex(url, '^' + origin + '$')
            self.assertRegex(url + '/share/page', '^' + referer + '$')
        self.assertNotRegex('https://evil.example.org', '^' + origin + '$')
        self.assertEqual(applications(cfg)['share'], 'https://alfresco.lan/share/')

    def test_share_csrf_values_survive_the_image_sed_substitution(self):
        cfg = self.https_configuration()
        environment = compose(cfg)['services']['share']['environment']
        origin, _ = csrf_patterns(cfg)
        # GNU sed replacement: «\x» yields x for the delimiter, «\\» a backslash, «\&» an ampersand.
        unescaped = re.sub(r'\\(.)', r'\1', environment['CSRF_FILTER_ORIGIN'].replace('$$', '$'))
        self.assertEqual(unescaped, origin)
        self.assertNotRegex(environment['CSRF_FILTER_ORIGIN'], r'(?<!\\)\|')

    def test_ip_without_server_name_gets_its_certificate(self):
        cfg = self.https_configuration()
        self.assertNotIn('default_sni', caddyfile(cfg).split('servers')[0].replace('default_sni 127.0.0.1', ''))
        cfg['primary'] = '127.0.0.1'
        self.assertIn('default_sni 127.0.0.1', caddyfile(cfg))

    def test_maintenance_gate_answers_503_and_internal_apis_are_blocked(self):
        cfg = configuration()
        self.assertIn('respond "Alfresco maintenance" 503', caddyfile(cfg))
        cfg['maintenance_gate'] = False
        text = caddyfile(cfg)
        self.assertIn('respond @blocked "Forbidden" 403', text)
        blocked = re.compile(r'^/alfresco(;[^/]*)?/(service|s|wcservice|wcs)(;[^/]*)?/(api(;[^/]*)?/solr|remoteadm|prometheus)(;[^/]*)?(/|$)')
        self.assertTrue(blocked.match('/alfresco/s/api/solr/aclchangesets'))
        self.assertTrue(blocked.match('/alfresco/service/prometheus'))
        self.assertFalse(blocked.match('/alfresco/api/-default-/public/alfresco/versions/1/people'))
        self.assertLess(text.index('respond @blocked'), text.index('reverse_proxy alfresco:8080'))

    def test_public_domains_tunnels_and_acme_are_rejected(self):
        for entry in (dict(host='docs.example.com', kind='public', mode='direct', tls='acme', email=''),
                      dict(host='docs.example.net', kind='public', mode='tunnel', tls='acme', email=''),
                      dict(host='alfresco.lan', kind='local', mode='local', tls='acme', email='')):
            cfg = configuration()
            cfg['domains'].append(entry)
            with self.assertRaises(Error, msg=entry['mode']):
                validate(cfg)
        cfg = configuration()
        cfg['environment'] = 'vps'
        with self.assertRaises(Error):
            validate(cfg)


class CatalogTests(unittest.TestCase):
    SAMPLE = '''services:
  alfresco:
    image: docker.io/alfresco/alfresco-content-repository-community:26.2.0
  kibana:
    image: kibana:8.17.10
  postgres:
    image: postgres:17.9
  proxy:
    image: traefik:3.6
  control-center:
    image: quay.io/alfresco/alfresco-control-center:11.0.0
'''

    def test_parse_keeps_managed_services_only(self):
        images = parse_compose(self.SAMPLE)
        self.assertEqual(images, {'alfresco': 'alfresco/alfresco-content-repository-community:26.2.0',
                                  'postgres': 'postgres:17.9',
                                  'control-center': 'quay.io/alfresco/alfresco-control-center:11.0.0'})

    def test_packaged_bundles_are_valid_and_sorted(self):
        bundles = packaged()
        self.assertGreaterEqual(len(bundles), 2)
        for bundle in bundles:
            check_bundle(bundle)
        self.assertEqual(bundles, sorted(bundles, key=bundle_key, reverse=True))

    def test_newer_bundle_updates_only_changed_components(self):
        newest, older = packaged()[0], packaged()[1]
        cfg = initial(pinned(older), storage='named')
        changes = bundle_changes(cfg, newest)
        self.assertEqual(changes['share'], [image_tag(older['images']['share']), image_tag(newest['images']['share'])])
        self.assertNotIn('alfresco', changes)
        self.assertNotIn('proxy', changes)
        self.assertEqual(bundle_changes(cfg, older), {})

    def test_newer_postgres_patch_is_never_downgraded(self):
        bundle = packaged()[0]
        cfg = initial(pinned(bundle), storage='named')
        cfg['images']['postgres'] = 'postgres:17.12' + DIGEST
        self.assertNotIn('postgres', bundle_changes(cfg, bundle))


class SecurityHelpersTests(unittest.TestCase):
    def test_curl_configuration_quoting(self):
        self.assertEqual(curl_quote('a"b\\c\nd'), '"a\\"b\\\\c\\nd"')

    def test_archive_rejects_traversal(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w') as archive:
            info = tarfile.TarInfo('../escape')
            info.size = 1
            archive.addfile(info, io.BytesIO(b'x'))
        buffer.seek(0)
        with tarfile.open(fileobj=buffer) as archive, self.assertRaises(Error):
            members(archive)


class BackupFormatTests(unittest.TestCase):
    def test_extract_checked_validates_checksums(self):
        cfg = configuration()
        with tempfile.TemporaryDirectory() as work:
            work = Path(work)
            with gzip.open(work / 'db.sql.gz', 'wb') as sql:
                sql.write(b'-- PostgreSQL database dump\n' * 10)
            with tarfile.open(work / 'proxy.tar', 'w'):
                pass
            files = {name: {'bytes': (work / name).stat().st_size, 'sha256': sha(work / name)} for name in ('db.sql.gz', 'proxy.tar')}
            atomic(work / 'manifest.json', {'format': 1, 'configuration': cfg, 'files': files,
                                            'sql_estimated_bytes': 1})
            package = work / 'backup.acdm.tar'
            with tarfile.open(package, 'w') as archive:
                for name in ('manifest.json', 'db.sql.gz', 'proxy.tar'):
                    archive.add(work / name, arcname=name)
            target = work / 'out'
            target.mkdir()
            self.assertEqual(extract_checked(package, target)['files'], files)
            files['db.sql.gz']['sha256'] = '0' * 64
            atomic(work / 'manifest.json', {'format': 1, 'configuration': cfg, 'files': files, 'sql_estimated_bytes': 1})
            with tarfile.open(package, 'w') as archive:
                for name in ('manifest.json', 'db.sql.gz', 'proxy.tar'):
                    archive.add(work / name, arcname=name)
            other = work / 'other'
            other.mkdir()
            with self.assertRaises(Error):
                extract_checked(package, other)


class StateTests(unittest.TestCase):
    def test_revision_activation_and_journal(self):
        with tempfile.TemporaryDirectory() as folder:
            state = State(Path(folder) / 'state').open()
            try:
                cfg = configuration()
                revision = state.revision(cfg)
                state.activate(revision)
                self.assertTrue(state.configured)
                self.assertEqual(state.load()['id'], cfg['id'])
                self.assertTrue((revision / 'compose.json').exists())
                state.begin('backup', source=str(revision))
                with self.assertRaises(Error):
                    state.begin('update')
                state.done()
                self.assertIsNone(state.pending)
            finally:
                state.lock_file.close()


class LocaleTests(unittest.TestCase):
    def test_maintenance_certificate_and_update_labels_are_translated(self):
        from acdm.i18n import messages
        from acdm.terminal_ui import Terminal
        from acdm.views import result_rows

        result = {'certificates': [{'names': ['alfresco.lan'], 'seconds_remaining': 36000,
                                    'days_remaining': 0.42}],
                  'components': {'proxy': {'current': '2.11.9', 'available': '2.11.9', 'image_changed': False}}}
        for language in ('es', 'en'):
            ui = Terminal(messages(language))
            rows = result_rows(ui, 'maintenance', result, None)
            text = '\n'.join(''.join(part for part, _ in label) if isinstance(label, list) else label
                             for label, _ in rows)
            for key in ('names', 'seconds_remaining', 'days_remaining', 'image_changed'):
                self.assertIn(key, ui.msg, language)
                self.assertNotIn('§ ' + key, text, language)
                if '_' in key:
                    self.assertNotIn(key, text, language)
            self.assertNotIn('§ proxy', text, language)

    def test_languages_share_keys_and_cover_code(self):
        es = json.loads((ROOT / 'locales' / 'es.json').read_text(encoding='utf-8'))
        en = json.loads((ROOT / 'locales' / 'en.json').read_text(encoding='utf-8'))
        self.assertEqual(set(es), set(en))
        used = set()
        for path in (ROOT / 'acdm').glob('*.py'):
            text = path.read_text(encoding='utf-8')
            used |= set(re.findall(r"\.t\('([a-z_]+)'[,)]", text))
            used |= set(re.findall(r"(?:pick|run|view|edit)\('([a-z_]+)'[,)]", text))
        from acdm.dashboard import GROUPS
        for title, actions in GROUPS:
            used.add(title)
            used |= set(actions)
            used |= {'desc_' + action for action in actions}
        used |= {'search_elasticsearch', 'app_share', 'app_content_app', 'app_control_center', 'app_repository'}
        missing = sorted(key for key in used if key not in es)
        self.assertEqual(missing, [])

    def test_start_script_is_valid_bash(self):
        import shutil
        import subprocess
        if not shutil.which('bash'):
            self.skipTest('bash unavailable')
        subprocess.run(['bash', '-n', str(ROOT / 'start.sh')], check=True)


if __name__ == '__main__':
    unittest.main()
