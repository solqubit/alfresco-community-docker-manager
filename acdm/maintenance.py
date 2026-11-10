"""Health checks run from the menu, HTTPS certificates, cleanup and self-update."""
import ast
import datetime as dt
import json
from pathlib import Path
import re
import shutil
import ssl
import time

from . import archive, requirements
from .core import LABEL, Error, ROOT, Runner, VERSION, atomic, confirm, version_tuple
from .docker import Engine


class Maintenance:
    def maintenance(self):
        result = {'checked': dt.datetime.now(dt.timezone.utc).isoformat(), 'errors': [], 'warnings': []}
        engine = Engine(self.state)
        result['status'] = engine.status()
        if self.state.pending:
            result['errors'].append('Hay una operación pendiente: usa Recuperar / Pending operation requires recovery')
        elif result['status'].get('ready'):
            # Independent checks: a registry outage must not hide an expiring certificate.
            def check(function):
                try:
                    function()
                except (Error, OSError, ValueError, KeyError) as exc:
                    result['errors'].append(str(exc))

            def service():
                engine.check_mount()
                engine.verify()

            def updates():
                found = self.checks()
                result['updates'] = found
                if found['updates']:
                    newest = found['updates'][0]
                    result['warnings'].append(f"Actualización disponible: Alfresco {newest['version']} ({newest['tag']}) / "
                                              f"Update available: Alfresco {newest['version']} ({newest['tag']})")
                if found['catalog_offline']:
                    result['warnings'].append('Catálogo de versiones no disponible; se usan los paquetes incluidos / '
                                              'Release catalog unavailable; using the packaged bundles')

            def backup():
                # Only pre-update backups exist; verify the newest one when there is any.
                backups = sorted((self.state.path / 'backups').glob('*.acdm.tar'))
                if backups and self.state.locked:
                    with archive.unpack(self.state, backups[-1]):
                        result['backup'] = {'name': backups[-1].name, 'valid': True}

            def certificates():
                result['certificates'] = self.check_certificates(engine)

            for function in (service, updates, backup, certificates):
                check(function)
        else:
            result['errors'].append('Alfresco está detenido o no está listo / Alfresco is stopped or not ready')
        try:
            # Elasticsearch stops indexing at 85 % disk usage and blocks the index at 95 %.
            for folder in {self.docker_root(), engine.cfg.get('storage_root') if engine.cfg['storage'] == 'directory' else None} - {None}:
                ratio = requirements.usage_ratio(folder)
                if ratio >= 0.80:
                    result['warnings' if ratio < 0.85 else 'errors'].append(
                        'Disco al %d %% en %s: la búsqueda deja de indexar al 85 %%; libera espacio / '
                        'Disk at %d %% on %s: search stops indexing at 85 %%; free up space' % (ratio * 100, folder, ratio * 100, folder))
        except OSError:
            pass
        result['free_bytes'] = shutil.disk_usage(self.state.path).free
        if result['free_bytes'] < 4 * 1024 ** 3:
            result['errors'].append('Menos de 4 GiB libres para el gestor / Less than 4 GiB free on manager filesystem')
        atomic(self.state.path / 'last-maintenance.json', result)
        if not self.state.locked:
            return result
        for path in sorted((self.state.path / 'logs').glob('*.log'))[:-30]:
            if path.is_file() and not path.is_symlink() and path != self.state.log_path:
                path.unlink()
        return result

    def wait_certificate(self, name, timeout=90):
        """After adding a public domain: a 308 redirect alone does not prove HTTPS works.
        False means not issued yet (DNS, ports 80/443 or the tunnel); Caddy keeps retrying."""
        engine = Engine(self.state)
        until = time.monotonic() + timeout
        while True:
            if any(name in item['names'] for item in self.read_certificates(engine)):
                return True
            if time.monotonic() > until:
                return False
            time.sleep(5)

    def cleanup_candidates(self):
        """Volumes this installation no longer uses (left by restores and updates), Docker images
        earlier revisions used and configuration revisions older than the five newest."""
        if self.state.pending:
            raise Error('Hay una operación pendiente: usa Recuperar / Pending operation: use recover')
        cfg = self.state.load()
        runner = Runner(self.state)
        names = runner.run(['docker', 'volume', 'ls', '--quiet', '--filter',
                            f'label={LABEL}.installation={cfg["id"]}']).stdout.split()
        volumes = sorted(name for name in names if name not in set(cfg['volumes'].values()))
        current = (self.state.path / 'current').resolve()
        revisions = sorted(path for path in (self.state.path / 'revisions').iterdir()
                           if path.is_dir() and not path.is_symlink())
        old = [path for path in revisions[:-5] if path != current]
        used = set()
        for path in revisions:
            try:
                used.update(json.loads((path / 'config.json').read_text(encoding='utf-8'))['images'].values())
            except (OSError, ValueError, KeyError, AttributeError):
                continue
        active = set(cfg['images'].values())
        active_ids = {runner.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', ref], check=False).stdout.strip()
                      for ref in active}
        images = []
        for ref in sorted(used - active):
            found = runner.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', ref], check=False)
            if found.returncode == 0 and found.stdout.strip() not in active_ids:
                images.append(ref)
        return {'volumes': volumes, 'revisions': [path.name for path in old], 'images': images}

    def cleanup(self, supplied=None):
        found = self.cleanup_candidates()
        if not found['volumes'] and not found['revisions'] and not found['images']:
            return {'removed_volumes': [], 'removed_revisions': 0, 'removed_images': []}
        confirm('CLEANUP', supplied, ', '.join(found['volumes'] + found['images']))
        engine = Engine(self.state)
        root = Path(engine.cfg['storage_root']) / engine.cfg['id']
        active = {engine.volume_path(role).resolve() for role in engine.cfg['volumes']} if engine.cfg['storage'] == 'directory' else set()
        removed, kept = [], []
        for name in found['volumes']:
            info = engine.inspect('volume', name)
            if not info:
                continue
            engine.owned(info, 'volume')
            if engine.runner.run(['docker', 'volume', 'rm', name], check=False).returncode:
                kept.append(name)
                continue
            removed.append(name)
            device = (info.get('Options') or {}).get('device')
            if device:
                folder = Path(device).resolve()
                if folder.is_relative_to(root.resolve()) and folder not in active and folder.is_dir():
                    shutil.rmtree(folder)
        for name in found['revisions']:
            shutil.rmtree(self.state.path / 'revisions' / name)
        images = []
        for ref in found['images']:
            reference = ref.split('@')[0].rpartition(':')[0] + '@' + ref.split('@', 1)[1]
            if engine.runner.run(['docker', 'image', 'rm', reference], check=False, timeout=300).returncode:
                kept.append(ref)
            else:
                images.append(ref)
        return {'removed_volumes': removed, 'removed_revisions': len(found['revisions']),
                'removed_images': images, 'in_use': kept}

    @staticmethod
    def read_certificates(engine):
        result = engine.runner.run(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'sh',
                    '--mount', 'type=volume,src=' + engine.cfg['volumes']['proxy_data'] + ',dst=/data,readonly',
                    engine.cfg['images']['proxy'], '-ec',
                    'if [ -d /data/caddy ]; then find /data/caddy -type f -name "*.crt" -exec cat {} \\; ; fi'], check=False)
        certificates = []
        for index, pem in enumerate(re.findall(r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', result.stdout, re.S)):
            path = engine.state.path / 'cache' / f'certificate-{index}.pem'
            atomic(path, pem)
            try:
                decoded = ssl._ssl._test_decode_cert(str(path))
            finally:
                path.unlink()
            names = [value for kind, value in decoded.get('subjectAltName', []) if kind in ('DNS', 'IP Address')]
            remaining = int(ssl.cert_time_to_seconds(decoded['notAfter']) - time.time())
            certificates.append({'names': names, 'seconds_remaining': remaining, 'days_remaining': round(remaining / 86400, 2)})
        return certificates

    @classmethod
    def check_certificates(cls, engine):
        certificates = cls.read_certificates(engine)
        missing = [e['host'] for e in engine.cfg['domains'] if e['tls'] != 'off'
                   and not any(e['host'] in c['names'] for c in certificates)]
        if missing:
            raise Error('Certificado aún no emitido: %s / Certificate not yet issued: %s' % ((', '.join(missing),) * 2))
        for entry in engine.cfg['domains']:
            if entry['tls'] != 'off':
                threshold = 3600 if entry['tls'] == 'internal' else 7 * 86400
                valid = [c['seconds_remaining'] for c in certificates if entry['host'] in c['names']]
                if not valid or max(valid) < threshold:
                    raise Error('Certificado a punto de caducar; revisa los registros de Caddy: %s / Certificate is near expiry; inspect Caddy logs: %s' % ((entry['host'],) * 2))
        return certificates

    def self_update(self, source=None, supplied=None):
        source = Path(source).resolve() if source else ROOT
        for name in ('acdm', 'locales', 'data', 'VERSION', 'LICENSE', 'start.sh'):
            if not (source / name).exists():
                raise Error('Descarga primero el repositorio del gestor e indica su ruta / Download the manager checkout first and select --source /path/to/checkout')
        if source == (self.state.path / 'manager').resolve():
            raise Error('Indica la ruta de una copia nueva del gestor, no la instalada / Download the new manager checkout and use self-update --source /path/to/checkout')
        for path in (source / 'acdm').rglob('*.py'):
            try:
                ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
            except SyntaxError as exc:
                raise Error('La copia nueva tiene un error de sintaxis; se conserva la instalada: %s / New manager source has a syntax error; installed code preserved: %s' % (path, path)) from exc
        for path in [*(source / 'locales').glob('*.json'), source / 'data' / 'bundles.json']:
            try:
                json.loads(path.read_text(encoding='utf-8'))
            except ValueError as exc:
                raise Error('Archivo de datos inválido: %s / Invalid data file: %s' % (path, path)) from exc
        target = (source / 'VERSION').read_text().strip()
        if version_tuple(target) < version_tuple(VERSION):
            raise Error('Volver a una versión anterior del gestor requiere revisión manual / Manager downgrade requires manual review')
        Runner(self.state).run(['bash', '-n', source / 'start.sh'])
        confirm('UPDATE-MANAGER', supplied, f'Manager {VERSION} -> {target}; source: {source}')
        self.state.installed_code(source)
        return {'manager': target, 'previous_code': str(self.state.path / 'manager-previous')}
