"""Validated configuration and reproducible Compose/Caddy generation for Alfresco Community."""
from __future__ import annotations

import ipaddress
import re
import secrets
import shlex
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .core import Error, LABEL, version_tuple


DEFAULT_HTTP_PORT = 80
DEFAULT_HTTPS_PORT = 443

# Official repositories accepted for every service role. The role is also the Compose
# service name, matching Alfresco's own community-compose.yaml.
REPOSITORIES = {
    'alfresco': ('alfresco/alfresco-content-repository-community',),
    'share': ('alfresco/alfresco-share',),
    'postgres': ('postgres',),
    'transform-core-aio': ('alfresco/alfresco-transform-core-aio',),
    'activemq': ('alfresco/alfresco-activemq',),
    'elasticsearch': ('elasticsearch',),
    'batch-indexing': ('alfresco/alfresco-elasticsearch-batch-indexing',),
    'content-app': ('alfresco/alfresco-content-app',),
    'control-center': ('quay.io/alfresco/alfresco-control-center', 'alfresco/alfresco-control-center'),
    'proxy': ('caddy',),
}
COMMON = ('alfresco', 'share', 'postgres', 'transform-core-aio', 'activemq', 'content-app', 'control-center', 'proxy')
# Alfresco 26.2 retired Solr: the manager only runs Elasticsearch with batch-indexing.
ENGINES = {'elasticsearch': ('elasticsearch', 'batch-indexing')}
VOLUMES = ('content', 'db', 'search', 'proxy_data', 'proxy_config')
# Mount point of each persistent volume inside its service.
MOUNTS = {'content': ('alfresco', '/usr/local/tomcat/alf_data'),
          'db': ('postgres', '/var/lib/postgresql/data'),
          'search': (None, None),
          'proxy_data': ('proxy', '/data'), 'proxy_config': ('proxy', '/config')}
SEARCH_MOUNTS = {'elasticsearch': '/usr/share/elasticsearch/data'}
# The repository image ships this keystore with these documented default passwords
# (Alfresco's own community-compose.yaml); they protect metadata, not the login.
KEYSTORE_OPTIONS = ('-Dencryption.keystore.type=JCEKS -Dencryption.cipherAlgorithm=DESede/CBC/PKCS5Padding '
                    '-Dencryption.keyAlgorithm=DESede '
                    '-Dencryption.keystore.location=/usr/local/tomcat/shared/classes/alfresco/extension/keystore/keystore '
                    '-Dmetadata-keystore.password=mp6yc0UD9e -Dmetadata-keystore.aliases=metadata '
                    '-Dmetadata-keystore.metadata.password=oKIWzVdEdA -Dmetadata-keystore.metadata.algorithm=DESede')


# Container memory limits by server RAM. Java uses up to 80 % of its container (MaxRAMPercentage);
# Elasticsearch gets a fixed heap of half its container. «base» is the measured 8 GB footprint.
MEMORY_TIERS = {
    'base': {'alfresco': '2g', 'elasticsearch': '2g', 'es_heap': '1g', 'transform-core-aio': '1536m',
             'share': '1g', 'postgres': '1g', 'shared_buffers': '256MB'},
    'medium': {'alfresco': '4g', 'elasticsearch': '3g', 'es_heap': '1536m', 'transform-core-aio': '2g',
               'share': '1536m', 'postgres': '2g', 'shared_buffers': '512MB'},
    'large': {'alfresco': '8g', 'elasticsearch': '4g', 'es_heap': '2g', 'transform-core-aio': '3g',
              'share': '2g', 'postgres': '4g', 'shared_buffers': '1GB'},
}
EMAIL = r'[^\s@{}"\\<>]+@[^\s@{}"\\<>]+\.[^\s@{}"\\<>]+'
MAIL_SECURITY = ('starttls', 'ssl', 'none')
KEYSTORE_PATH = '/tmp/acdm-keystore/keystore'


def memory_tier(memory):
    """A server sold with 16 GB reports about 15.6 GiB to Linux and one with 32 GB about 31 GiB."""
    gib = 1024 ** 3
    return 'large' if memory >= 30 * gib else 'medium' if memory >= 15 * gib else 'base'


def validate_mail(mail):
    """Optional outbound SMTP settings; None means Alfresco sends no email."""
    if mail is None:
        return
    if not isinstance(mail, dict) or set(mail) != {'host', 'port', 'security', 'username', 'password', 'sender'}:
        raise Error('Invalid email settings')
    host(mail['host'])
    port(mail['port'])
    if mail['security'] not in MAIL_SECURITY:
        raise Error('Invalid email settings')
    if not re.fullmatch(EMAIL, mail['sender']):
        raise Error('Remitente inválido / Invalid sender address')
    for key in ('username', 'password'):
        value = mail[key]
        if not isinstance(value, str) or len(value) > 256 or any(ord(c) < 32 for c in value):
            raise Error('Invalid email credentials')


def validate_keystore(keystore):
    if keystore is None:
        return
    if (not isinstance(keystore, dict) or set(keystore) != {'data', 'store_password', 'key_password'}
            or not re.fullmatch(r'[A-Za-z0-9+/=]{100,8000}', keystore['data'])
            or not all(re.fullmatch(r'[A-Za-z0-9_-]{16,128}', keystore[key]) for key in ('store_password', 'key_password'))):
        raise Error('Invalid metadata keystore')


def host(value):
    value = value.strip().lower().rstrip('.')
    if not value or any(c in value for c in '/\\:@*\n\r\t '):
        raise Error('Use an IPv4 address or hostname without protocol/port / IP o dominio sin protocolo ni puerto')
    try:
        return str(ipaddress.IPv4Address(value))
    except ipaddress.AddressValueError:
        pass
    try:
        value = value.encode('idna').decode('ascii')
    except UnicodeError as exc:
        raise Error('Dominio inválido / Invalid hostname') from exc
    if len(value) > 253 or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', p)
                                   for p in value.split('.')):
        raise Error('Dominio inválido / Invalid hostname')
    return value


def port(value):
    value = int(value)
    if not 1 <= value <= 65535:
        raise Error('Puerto inválido / Invalid port')
    return value


def normalize_image(ref):
    """docker.io/library/postgres:17.9 -> postgres:17.9 (the form Compose and Docker print)."""
    ref = ref.strip()
    for prefix in ('docker.io/library/', 'docker.io/', 'index.docker.io/'):
        if ref.startswith(prefix):
            return ref[len(prefix):]
    return ref


def image_parts(ref):
    """(repository, tag, digest) of repo:tag[@sha256:...]."""
    name, _, digest = normalize_image(ref).partition('@')
    repository, _, tag = name.rpartition(':')
    if not repository or '/' in tag:
        raise Error('Invalid image reference: ' + ref)
    return repository, tag, digest


def image_tag(ref):
    return image_parts(ref)[1]


def check_image(role, ref, *, pinned=True):
    if role not in REPOSITORIES:
        raise Error('Unknown service role: ' + role)
    repository, tag, digest = image_parts(ref)
    if repository not in REPOSITORIES[role] or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', tag):
        raise Error(f'Imagen no oficial para {role} / Unofficial image for {role}: {ref}')
    if pinned and not re.fullmatch(r'sha256:[a-f0-9]{64}', digest):
        raise Error('Images must use exact official tags and digests')
    if not pinned and digest:
        raise Error('Unexpected digest in an upstream bundle')


def engine_of(images):
    found = [name for name, roles in ENGINES.items() if all(role in images for role in roles)]
    if len(found) != 1:
        raise Error('El paquete debe usar Elasticsearch (Alfresco 26.2 o posterior) / '
                    'The bundle must use Elasticsearch (Alfresco 26.2 or later)')
    return found[0]


def roles(cfg_or_images):
    images = cfg_or_images.get('images', cfg_or_images)
    return list(COMMON) + list(ENGINES[engine_of(images)])


def validate_images(images, *, pinned=True):
    expected = set(COMMON) | set(ENGINES[engine_of(images)])
    if set(images) != expected:
        raise Error('Componentes incompletos o desconocidos / Incomplete or unknown components')
    for role, ref in images.items():
        check_image(role, ref, pinned=pinned)


def validate(cfg):
    if cfg.get('format') != 1 or not re.fullmatch('[a-f0-9]{16}', cfg.get('id', '')):
        raise Error('Invalid configuration format')
    if cfg.get('project') != 'acdm_' + cfg['id']:
        raise Error('Invalid project identity')
    version_tuple(cfg['version'])
    validate_images(cfg['images'])
    if image_tag(cfg['images']['alfresco']) != cfg['version']:
        raise Error('Image/application version mismatch')
    if cfg['search'] != engine_of(cfg['images']):
        raise Error('Search engine mismatch')
    # Intranet only: no public domains, Let's Encrypt or tunnels.
    if cfg['environment'] != 'lan' or cfg['storage'] not in ('named', 'directory'):
        raise Error('Invalid installation environment/storage')
    ipaddress.IPv4Address(cfg['bind'])
    if port(cfg['http_port']) == port(cfg['https_port']):
        raise Error('HTTP and HTTPS ports must differ')
    try:
        ZoneInfo(cfg['timezone'])
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise Error('Unknown timezone or missing tzdata / Zona horaria inválida o falta tzdata') from exc
    network = ipaddress.ip_network(cfg['subnet'])
    if network.version != 4 or network.prefixlen != 28 or not network.is_private:
        raise Error('Invalid private network')
    if cfg['language'] not in ('es', 'en') or not isinstance(cfg['maintenance_gate'], bool):
        raise Error('Invalid language/state')
    if cfg['status'] not in ('starting', 'ready'):
        raise Error('Invalid installation state')
    if set(cfg['volumes']) != set(VOLUMES):
        raise Error('Invalid volume set')
    for name in VOLUMES:
        if not re.fullmatch(r'acdm_[a-f0-9]{16}_[a-z0-9_]+', cfg['volumes'][name]):
            raise Error('Invalid volume identity')
    if cfg['storage'] == 'directory':
        root = Path(cfg['storage_root'])
        if not root.is_absolute() or str(root) == '/' or '..' in root.parts:
            raise Error('Invalid storage root')
    entries = cfg['domains']
    if not entries or len(entries) > 32 or len({e['host'] for e in entries}) != len(entries):
        raise Error('Se necesita al menos una dirección única / Unique access addresses required')
    for entry in entries:
        if entry['host'] != host(entry['host']) or entry['kind'] != 'local' or entry['mode'] != 'local':
            raise Error('Solo se admiten direcciones locales / Only local addresses are supported')
        if entry['tls'] not in ('off', 'internal'):
            raise Error('Invalid local HTTPS mode')
    if cfg['primary'] not in [e['host'] for e in entries]:
        raise Error('Primary address must be allowed')
    for secret in ('db_password', 'search_secret', 'mq_password'):
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', cfg[secret]):
            raise Error('Invalid generated secret')
    if cfg.get('memory_tier', 'base') not in MEMORY_TIERS:
        raise Error('Invalid memory profile')
    validate_mail(cfg.get('mail'))
    validate_keystore(cfg.get('keystore'))
    # The administrator may later choose any password; it is only sent to Alfresco's API.
    admin = cfg['admin_password']
    if not isinstance(admin, str) or not 1 <= len(admin) <= 256 or any(ord(c) < 32 for c in admin):
        raise Error('Invalid stored administrator password')


def volume_names(identity):
    return {role: f'acdm_{identity}_{role}' for role in VOLUMES}


def initial(images, *, bind='127.0.0.1', address=None, local_tls=True,
            http_port=DEFAULT_HTTP_PORT, https_port=DEFAULT_HTTPS_PORT, timezone='UTC', language='es',
            storage='directory', storage_root=None, subnet=None):
    identity = secrets.token_hex(8)
    cfg = dict(format=1, id=identity, project='acdm_' + identity, version=image_tag(images['alfresco']),
               images=images, search=engine_of(images), environment='lan', bind=bind,
               http_port=port(http_port), https_port=port(https_port), timezone=timezone,
               language=language, storage=storage,
               storage_root=storage_root or '/opt/alfresco-community-docker-manager/data',
               volumes=volume_names(identity),
               subnet=subnet or f'172.29.{secrets.randbelow(220) + 10}.0/28',
               db_password=secrets.token_urlsafe(30), search_secret=secrets.token_urlsafe(30),
               mq_password=secrets.token_urlsafe(30), admin_password=secrets.token_urlsafe(15),
               maintenance_gate=True, primary=host(address or bind),
               domains=[dict(host=host(address or bind), kind='local', tls='internal' if local_tls else 'off',
                             mode='local', email='')],
               status='starting', retention=3, data_device=None)
    validate(cfg)
    return cfg


def endpoint(cfg, entry=None):
    """(scheme, host, port) a browser uses for an access entry."""
    entry = entry or next(e for e in cfg['domains'] if e['host'] == cfg['primary'])
    secure = entry['tls'] != 'off'
    return ('https' if secure else 'http'), entry['host'], cfg['https_port'] if secure else cfg['http_port']


def access_url(cfg, entry=None):
    scheme, name, value = endpoint(cfg, entry)
    return f'{scheme}://{name}' + (f':{value}' if value != (443 if scheme == 'https' else 80) else '')


def applications(cfg):
    """Web applications behind the proxy, relative to the primary address."""
    base = access_url(cfg)
    return {'share': base + '/share/', 'content_app': base + '/content-app/',
            'control_center': base + '/control-center/', 'repository': base + '/alfresco/'}


def sed_replacement(value):
    """The Share image inserts CSRF_FILTER_* with «sed s|...|VALUE|»: keep the regex intact."""
    return value.replace('\\', '\\\\').replace('|', '\\|').replace('&', '\\&')


def csrf_patterns(cfg):
    """Share compares Origin/Referer with regular expressions: allow every configured address."""
    origins = sorted({re.escape(access_url(cfg, entry)) for entry in cfg['domains']})
    joined = '|'.join(origins)
    return f'({joined})', f'({joined})/share/.*'


BLOCKED = (r'^/alfresco(;[^/]*)?/(service|s|wcservice|wcs)(;[^/]*)?/(api(;[^/]*)?/solr|remoteadm|prometheus)(;[^/]*)?(/|$)',
           r'^/share(;[^/]*)?/(service(;[^/]*)?/)?proxy(;[^/]*)?/alfresco(-(noauth|feed|api))?(;[^/]*)?'
           r'/(api(;[^/]*)?/solr|remoteadm|-default-(;[^/]*)?/proxy(;[^/]*)?/[^/]+/api)(;[^/]*)?(/|$)')


def _proxy_route(gate):
    if gate:
        return ['respond "Alfresco maintenance" 503']
    # Same exclusions as Alfresco's reference proxy: Solr, remote admin and Prometheus APIs
    # are internal and must never be reachable from browsers. «route» keeps this literal
    # order (Caddy would otherwise sort handle blocks before respond).
    routes = [
        '@blocked path_regexp blocked ' + BLOCKED[0],
        'respond @blocked "Forbidden" 403',
        '@shareblocked path_regexp shareblocked ' + BLOCKED[1],
        'respond @shareblocked "Forbidden" 403',
        'redir / /share/ 302',
        'redir /content-app /content-app/ 308',
        'redir /control-center /control-center/ 308',
        'redir /admin /control-center/ 308',
        'redir /share /share/ 308',
        'handle_path /content-app/* {', '    reverse_proxy content-app:8080', '}',
        'handle_path /control-center/* {', '    reverse_proxy control-center:8080', '}',
        'handle_path /admin/* {', '    reverse_proxy control-center:8080', '}',
        'handle /share/* {', '    reverse_proxy share:8080', '}',
        'reverse_proxy alfresco:8080']
    return ['route {'] + ['    ' + line for line in routes] + ['}']


def default_sni(cfg):
    """Browsers send no server name when they open an IP address, and inside Docker the proxy
    only sees its container address: tell Caddy which certificate answers those connections."""
    addresses = [entry['host'] for entry in cfg['domains'] if entry['tls'] != 'off']
    ips = [name for name in addresses if re.fullmatch(r'[0-9.]+', name)]
    return cfg['primary'] if cfg['primary'] in ips else (ips[0] if ips else None)


def caddyfile(cfg):
    """Intranet proxy: plain HTTP or HTTPS signed by Caddy's own local CA. Who can reach it is a
    network decision (firewall, router, VPN) of the administrator, not of the manager."""
    sni = default_sni(cfg)
    lines = ['{', '    admin off', '    auto_https disable_redirects'] + ([f'    default_sni {sni}'] if sni else []) + [
             '    servers {', '        trusted_proxies static private_ranges', '    }', '}', '', 'http://:80 {',
             '    handle /acdm-health {', '        respond "ok" 200', '    }']
    for i, entry in enumerate(cfg['domains']):
        lines += [f"    @entry{i} host {entry['host']}", f'    handle @entry{i} {{']
        if entry['tls'] != 'off':
            suffix = '' if cfg['https_port'] == 443 else ':' + str(cfg['https_port'])
            lines += [f'        redir https://{entry["host"]}{suffix}{{uri}} 308']
        else:
            lines += ['        ' + line for line in _proxy_route(cfg['maintenance_gate'])]
        lines += ['    }']
    lines += ['    respond "Unknown host" 404', '}']
    for entry in cfg['domains']:
        if entry['tls'] == 'off':
            continue
        lines += ['', f"https://{entry['host']} {{", '    tls internal', '    handle {']
        lines += ['        ' + line for line in _proxy_route(cfg['maintenance_gate'])]
        lines += ['    }', '}']
    return '\n'.join(lines) + '\n'


def keystore_options(cfg):
    """Alfresco's metadata keystore: the image default (documented passwords) or this installation's own."""
    keystore = cfg.get('keystore')
    if not keystore:
        return KEYSTORE_OPTIONS
    return ('-Dencryption.keystore.type=JCEKS -Dencryption.cipherAlgorithm=DESede/CBC/PKCS5Padding '
            '-Dencryption.keyAlgorithm=DESede -Dencryption.keystore.location=' + KEYSTORE_PATH + ' '
            '-Dmetadata-keystore.password=' + keystore['store_password'] + ' -Dmetadata-keystore.aliases=metadata '
            '-Dmetadata-keystore.metadata.password=' + keystore['key_password'] +
            ' -Dmetadata-keystore.metadata.algorithm=DESede')


def mail_options(mail):
    """Outbound email for Alfresco. catalina.sh evaluates JAVA_OPTS with the shell, so every value
    supplied by the administrator is shell-quoted."""
    if not mail:
        return []
    quote = shlex.quote
    auth = 'true' if mail['username'] else 'false'
    options = ['-Dmail.host=' + quote(mail['host']), f"-Dmail.port={int(mail['port'])}",
               '-Dmail.protocol=' + ('smtps' if mail['security'] == 'ssl' else 'smtp'),
               '-Dmail.smtp.auth=' + auth, '-Dmail.smtps.auth=' + auth,
               '-Dmail.smtp.starttls.enable=' + ('true' if mail['security'] == 'starttls' else 'false'),
               '-Dmail.from.default=' + quote(mail['sender']), '-Dmail.from.enabled=false', '-Dmail.encoding=UTF-8',
               '-Dmail.smtp.timeout=30000', '-Dmail.smtp.connectiontimeout=30000']
    if mail['username']:
        options += ['-Dmail.username=' + quote(mail['username']), '-Dmail.password=' + quote(mail['password'])]
    return options


def java_options(cfg):
    scheme, name, value = endpoint(cfg)
    options = ['-Ddb.driver=org.postgresql.Driver', '-Ddb.username=alfresco', '-Ddb.password=' + cfg['db_password'],
               '-Ddb.url=jdbc:postgresql://postgres:5432/alfresco',
               '-Dsolr.secureComms=secret', '-Dsolr.sharedSecret=' + cfg['search_secret']]
    # The repository still uses the shared secret for content transformations requested by batch-indexing.
    options += ['-Dindex.subsystem.name=elasticsearch', '-Delasticsearch.createIndexIfNotExists=true',
                '-Delasticsearch.host=elasticsearch', '-Delasticsearch.port=9200']
    options += [f'-Dshare.host={name}', f'-Dshare.port={value}', f'-Dshare.protocol={scheme}',
                f'-Dalfresco.host={name}', f'-Dalfresco.port={value}', f'-Dalfresco.protocol={scheme}',
                '-Dcsrf.filter.enabled=false', f'-Daos.baseUrlOverwrite={access_url(cfg)}/alfresco/aos',
                '-Dmessaging.broker.url="failover:(nio://activemq:61616)?timeout=3000&jms.useCompression=true"',
                '-Dmessaging.broker.username=admin', '-Dmessaging.broker.password=' + cfg['mq_password'],
                '-Ddeployment.method=DOCKER_COMPOSE', '-DlocalTransform.core-aio.url=http://transform-core-aio:8090/',
                '-Duser.timezone=' + cfg['timezone'], '-XX:MinRAMPercentage=50', '-XX:MaxRAMPercentage=80']
    options += mail_options(cfg.get('mail'))
    return ' '.join(options)


def compose(cfg):
    labels = {LABEL + '.installation': cfg['id']}
    network = ipaddress.ip_network(cfg['subnet'])
    backend = ipaddress.ip_network((int(network.network_address) ^ 16, 28))
    logging = {'driver': 'json-file', 'options': {'max-size': '10m', 'max-file': '3'}}
    images = cfg['images']
    tz = cfg['timezone']
    scheme, name, value = endpoint(cfg)
    origin, referer = csrf_patterns(cfg)
    ready = 'http://localhost:8080/alfresco/api/-default-/public/alfresco/versions/1/probes/-ready-'
    memory = MEMORY_TIERS[cfg.get('memory_tier', 'base')]
    services = {
        'postgres': {'image': images['postgres'], 'mem_limit': memory['postgres'],
            'environment': {'POSTGRES_PASSWORD': cfg['db_password'], 'POSTGRES_USER': 'alfresco',
                            'POSTGRES_DB': 'alfresco', 'PGDATA': '/var/lib/postgresql/data/pgdata', 'TZ': tz},
            'command': ['postgres', '-c', 'max_connections=300', '-c', 'log_min_messages=LOG',
                        '-c', 'shared_buffers=' + memory['shared_buffers']],
            'volumes': ['db:/var/lib/postgresql/data'], 'networks': ['backend'],
            'healthcheck': {'test': ['CMD-SHELL', 'pg_isready -h 127.0.0.1 -d alfresco -U alfresco'],
                            'interval': '10s', 'timeout': '5s', 'retries': 30, 'start_period': '20s'}},
        'activemq': {'image': images['activemq'], 'mem_limit': '1g',
            'environment': {'ACTIVEMQ_ADMIN_LOGIN': 'admin', 'ACTIVEMQ_ADMIN_PASSWORD': cfg['mq_password'], 'TZ': tz},
            'networks': ['backend'],
            'healthcheck': {'test': ['CMD-SHELL', '/opt/activemq/bin/activemq query --objname '
                                     '"type=Broker,brokerName=*,service=Health" | grep -q Good'],
                            'interval': '20s', 'timeout': '30s', 'retries': 15, 'start_period': '30s'}},
        'transform-core-aio': {'image': images['transform-core-aio'], 'mem_limit': memory['transform-core-aio'],
            'environment': {'JAVA_OPTS': '-XX:MinRAMPercentage=50 -XX:MaxRAMPercentage=80',
                            'ACTIVEMQ_URL': 'nio://activemq:61616', 'ACTIVEMQ_USER': 'admin',
                            'ACTIVEMQ_PASSWORD': cfg['mq_password'], 'TZ': tz},
            'networks': ['backend'], 'depends_on': {'activemq': {'condition': 'service_healthy'}},
            'healthcheck': {'test': ['CMD', 'curl', '-f', 'http://localhost:8090/ready'],
                            'interval': '20s', 'timeout': '5s', 'retries': 15, 'start_period': '30s'}},
        'alfresco': {'image': images['alfresco'], 'mem_limit': memory['alfresco'],
            # In JAVA_OPTS, not JAVA_TOOL_OPTIONS: the JVM prints JAVA_TOOL_OPTIONS (with the keystore
            # passwords) to the log, while Tomcat is configured not to log JAVA_OPTS (logArgs="false").
            'environment': {'JAVA_OPTS': keystore_options(cfg) + ' ' + java_options(cfg), 'TZ': tz},
            'volumes': ['content:/usr/local/tomcat/alf_data'], 'networks': ['backend', 'proxy'],
            'depends_on': {'postgres': {'condition': 'service_healthy'},
                           'activemq': {'condition': 'service_healthy'}},
            'healthcheck': {'test': ['CMD', 'curl', '-f', ready],
                            'interval': '15s', 'timeout': '5s', 'retries': 20, 'start_period': '3m'}},
        'share': {'image': images['share'], 'mem_limit': memory['share'],
            'environment': {'CSRF_FILTER_ORIGIN': sed_replacement(origin), 'CSRF_FILTER_REFERER': sed_replacement(referer),
                            'REPO_HOST': 'alfresco', 'REPO_PORT': '8080', 'TZ': tz,
                            'JAVA_OPTS': f'-XX:MinRAMPercentage=50 -XX:MaxRAMPercentage=80 -Dalfresco.host={name} '
                                         f'-Dalfresco.port={value} -Dalfresco.context=alfresco -Dalfresco.protocol={scheme}'},
            'networks': ['backend', 'proxy'], 'depends_on': ['alfresco'],
            'healthcheck': {'test': ['CMD', 'curl', '-f', 'http://localhost:8080/share'],
                            'interval': '20s', 'timeout': '5s', 'retries': 15, 'start_period': '1m'}},
        'content-app': {'image': images['content-app'], 'mem_limit': '128m',
            'environment': {'APP_BASE_SHARE_URL': access_url(cfg) + '/content-app/#/preview/s'},
            'networks': ['proxy'],
            'healthcheck': {'test': ['CMD', 'curl', '-f', 'http://localhost:8080/'],
                            'interval': '15s', 'timeout': '3s', 'retries': 10}},
        'control-center': {'image': images['control-center'], 'mem_limit': '128m',
            'environment': {'APP_CONFIG_PROVIDER': 'ECM', 'APP_CONFIG_AUTH_TYPE': 'BASIC', 'BASE_PATH': './'},
            'networks': ['proxy'],
            'healthcheck': {'test': ['CMD', 'curl', '-f', 'http://localhost:8080/'],
                            'interval': '15s', 'timeout': '3s', 'retries': 10}},
        'proxy': {'image': images['proxy'], 'mem_limit': '256m', 'environment': {'ACDM_CADDY_CONFIG': caddyfile(cfg)},
            'entrypoint': ['/bin/sh', '-ec', 'printf "%s\\n" "$ACDM_CADDY_CONFIG" > /tmp/Caddyfile; '
                           'exec caddy run --config /tmp/Caddyfile --adapter caddyfile'],
            'volumes': ['proxy_data:/data', 'proxy_config:/config'],
            'networks': ['proxy'],
            'ports': [{'target': 80, 'published': str(cfg['http_port']), 'host_ip': cfg['bind']},
                      {'target': 443, 'published': str(cfg['https_port']), 'host_ip': cfg['bind']}],
            'healthcheck': {'test': ['CMD', 'wget', '-q', '-O', '/dev/null', 'http://127.0.0.1/acdm-health'],
                            'interval': '10s', 'timeout': '5s', 'retries': 12}}}
    services['elasticsearch'] = {'image': images['elasticsearch'], 'mem_limit': memory['elasticsearch'],
        'environment': {'xpack.security.enabled': 'false', 'discovery.type': 'single-node',
                        'ES_JAVA_OPTS': f"-Xms{memory['es_heap']} -Xmx{memory['es_heap']}", 'TZ': tz},
        'ulimits': {'memlock': {'soft': -1, 'hard': -1}, 'nofile': {'soft': 65536, 'hard': 65536}},
        'cap_add': ['IPC_LOCK'], 'volumes': ['search:' + SEARCH_MOUNTS['elasticsearch']], 'networks': ['backend'],
        'healthcheck': {'test': ['CMD-SHELL', 'curl -s http://localhost:9200/_cluster/health | grep -Eq \'"status":"(green|yellow)"\''],
                        'interval': '10s', 'timeout': '10s', 'retries': 30, 'start_period': '30s'}}
    services['batch-indexing'] = {'image': images['batch-indexing'], 'mem_limit': '1g',
        'environment': {'JAVA_OPTS': '-Xms256m -Xmx768m', 'TZ': tz,
                        'SPRING_DATASOURCE_URL': 'jdbc:postgresql://postgres:5432/alfresco',
                        'SPRING_DATASOURCE_USERNAME': 'alfresco', 'SPRING_DATASOURCE_PASSWORD': cfg['db_password'],
                        'SPRING_ELASTICSEARCH_REST_URIS': 'http://elasticsearch:9200',
                        'ALFRESCO_ACCEPTEDCONTENTMEDIATYPESCACHE_BASEURL': 'http://transform-core-aio:8090/transform/config',
                        'ALFRESCO_ACS_URL': 'http://alfresco:8080',
                        'ALFRESCO_CONTENT_TRANSFORM_SHAREDSECRET': cfg['search_secret'],
                        'ALFRESCO_REINDEX_CONTINUOUS_SEEDFROMEARLIEST': 'true',
                        'ALFRESCO_REINDEX_CONTINUOUS_MAXGAPAGE': '0',
                        'ALFRESCO_REINDEX_CONTINUOUS_POLLINGINTERVAL': '15s',
                        'ALFRESCO_REINDEX_CONTINUOUS_CATCHUPPOLLINGINTERVAL': '1s',
                        'ALFRESCO_REINDEX_CONTINUOUS_MAXWINDOW': '30m',
                        'ALFRESCO_REINDEX_CONTINUOUS_OVERLAP': '10m'},
        'networks': ['backend'],
        'depends_on': {'elasticsearch': {'condition': 'service_healthy'},
                       'alfresco': {'condition': 'service_healthy'},
                       'transform-core-aio': {'condition': 'service_healthy'}}}
    services['alfresco']['depends_on']['elasticsearch'] = {'condition': 'service_healthy'}
    if cfg.get('keystore'):
        # The image's extension folder belongs to root: write the keystore where Alfresco can read it.
        services['alfresco']['environment']['ACDM_KEYSTORE'] = cfg['keystore']['data']
        services['alfresco']['entrypoint'] = ['/bin/sh', '-ec', 'umask 077; mkdir -p ' + KEYSTORE_PATH.rpartition('/')[0]
                                              + '; printf "%s" "$ACDM_KEYSTORE" | base64 -d > ' + KEYSTORE_PATH
                                              + '; exec catalina.sh run -security']
    for service_name, service in services.items():
        service.update(restart='unless-stopped', logging=logging,
                       labels={**labels, LABEL + '.role': service_name})
        # Compose interpolation must not interpret shell variables, regexes or secrets.
        service['environment'] = {key: str(item).replace('$', '$$') for key, item in service['environment'].items()}
        if isinstance(service.get('entrypoint'), list):
            service['entrypoint'] = [x.replace('$', '$$') for x in service['entrypoint']]
    return {'name': cfg['project'], 'services': services,
            'volumes': {k: {'name': v, 'external': True} for k, v in cfg['volumes'].items()},
            'networks': {'backend': {'internal': True, 'ipam': {'config': [{'subnet': str(backend)}]}, 'labels': labels},
                         'proxy': {'ipam': {'config': [{'subnet': cfg['subnet']}]}, 'labels': labels}}}


def mount_of(cfg, role):
    """(service, path) where a persistent volume is mounted."""
    if role == 'search':
        return cfg['search'], SEARCH_MOUNTS[cfg['search']]
    return MOUNTS[role]


APP_SERVICES = ('alfresco', 'share', 'content-app', 'control-center')


def start_order(cfg):
    """Every service in dependency order; the proxy last."""
    return ['postgres', 'activemq', 'transform-core-aio', 'elasticsearch',
            'alfresco', 'share', 'content-app', 'control-center', 'batch-indexing', 'proxy']
