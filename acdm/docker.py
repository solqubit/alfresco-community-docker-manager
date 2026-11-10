"""Docker calls restricted to one labelled Alfresco installation."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import time

from .config import APP_SERVICES, compose, endpoint, mount_of, roles, start_order
from .core import Error, LABEL, Runner, atomic

API = 'http://localhost:8080/alfresco/api/-default-/public/alfresco/versions/1'
READY = API + '/probes/-ready-'


def curl_quote(value):
    """A double-quoted value for a curl configuration file (read from stdin, never argv)."""
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r') + '"'


class Engine:
    def __init__(self, state, cfg=None, revision=None):
        self.state = state
        self.cfg = cfg or state.load()
        self.runner = Runner(state)
        self.revision = Path(revision) if revision else state.path / 'current'

    def dc(self, *args, **kwargs):
        return self.runner.run(['docker', 'compose', '-p', self.cfg['project'],
                                '-f', self.revision / 'compose.json', *args], **kwargs)

    def inspect(self, kind, name):
        result = self.runner.run(['docker', kind, 'inspect', name], check=False)
        return json.loads(result.stdout)[0] if result.returncode == 0 else None

    def owned(self, info, kind='container'):
        labels = info.get('Config', {}).get('Labels', {}) if kind == 'container' else info.get('Labels', {})
        if labels.get(LABEL + '.installation') != self.cfg['id']:
            raise Error('Recurso ajeno / Resource not owned by this installation')

    def owned_containers(self):
        ids = self.runner.run(['docker', 'ps', '--all', '--quiet', '--filter',
                               'label=com.docker.compose.project=' + self.cfg['project']]).stdout.split()
        containers = []
        for cid in ids:
            info = self.inspect('container', cid) or {}
            self.owned(info)
            containers.append(info)
        return containers

    def remove_anonymous_volumes(self, containers):
        for info in containers:
            self.owned(info)
        names = {mount['Name'] for info in containers for mount in info.get('Mounts', [])
                 if mount['Type'] == 'volume'}
        for name in sorted(names):
            info = self.inspect('volume', name)
            if info and 'com.docker.volume.anonymous' in (info.get('Labels') or {}):
                # Compose may inherit an anonymous volume as an explicit mount on recreation.
                # Docker then keeps it even with rm/down --volumes. Its original label survives.
                self.runner.run(['docker', 'volume', 'rm', name])

    def container(self, service, required=True):
        result = self.dc('ps', '--all', '--quiet', service, check=False)
        value = result.stdout.strip()
        if not value:
            if required:
                raise Error(f'Contenedor no disponible: {service} / Container unavailable: {service}')
            return None
        if '\n' in value:
            raise Error('Unexpected replicated service')
        self.owned(self.inspect('container', value) or {})
        return value

    def pull(self, components=None):
        for component in components or roles(self.cfg):
            ref = self.cfg['images'][component]
            if not self.inspect('image', ref):
                self.runner.run(['docker', 'pull', '--quiet', ref], timeout=3600)

    def pin(self, images):
        """Pull tag references and return them pinned to the pulled manifest digest."""
        pinned = {}
        for role, ref in images.items():
            tag_ref = ref.split('@')[0]
            self.runner.run(['docker', 'pull', '--quiet', tag_ref], timeout=3600)
            digests = json.loads(self.runner.run(['docker', 'image', 'inspect', '--format', '{{json .RepoDigests}}',
                                                  tag_ref]).stdout or '[]')
            repository = tag_ref.rpartition(':')[0]
            digest = next((value.split('@', 1)[1] for value in digests
                           if value.split('@', 1)[0] in (repository, 'docker.io/' + repository,
                                                          'docker.io/library/' + repository)), None)
            if not digest:
                raise Error(f'No se obtuvo el digest de {tag_ref} / Missing registry digest for {tag_ref}')
            pinned[role] = tag_ref + '@' + digest
        return pinned

    def volume_path(self, role):
        # Fresh volumes (restores, engine changes) never reuse an older bind directory.
        suffix = self.cfg['volumes'][role][len(self.cfg['project']) + 1:]
        return Path(self.cfg['storage_root']) / self.cfg['id'] / suffix

    def ensure_volumes(self):
        for role, name in self.cfg['volumes'].items():
            info = self.inspect('volume', name)
            if info:
                self.owned(info, 'volume')
                if self.cfg['storage'] == 'directory' and info.get('Options', {}).get('device') != str(self.volume_path(role)):
                    raise Error('Volume path mismatch')
                continue
            args = ['docker', 'volume', 'create', '--label', LABEL + '.installation=' + self.cfg['id'],
                    '--label', LABEL + '.role=' + role]
            directory = self.cfg['storage'] == 'directory'
            if directory:
                if snap_docker(self.runner):
                    raise Error('Docker instalado por Snap no puede usar carpetas del servidor; elige volúmenes Docker '
                                'o instala Docker Engine (docker-ce) / Snap Docker cannot bind server folders; '
                                'choose Docker volumes or install Docker Engine (docker-ce)')
                folder = self.volume_path(role)
                root = Path(self.cfg['storage_root'])
                if any(p.is_symlink() for p in [root, root / self.cfg['id'], folder]):
                    raise Error('Unsafe storage symlink')
                folder.mkdir(parents=True, exist_ok=True, mode=0o750)
                selinux_label(self.runner, folder)
                args += ['--driver', 'local', '--opt', 'type=none', '--opt', 'o=bind', '--opt', 'device=' + str(folder)]
                if role == 'content':
                    self.cfg['data_device'] = folder.stat().st_dev
                    self.cfg['data_fsid'] = filesystem_id(folder)
            self.runner.run(args + [name])
            if directory:
                self.prepare_volume(role, name)

    def prepare_volume(self, role, name):
        """Bind folders start owned by root; give them the owner of the image's own mount path,
        as Docker does automatically for named volumes (Elasticsearch and Alfresco run unprivileged)."""
        service, path = mount_of(self.cfg, role)
        if service in (None, 'postgres', 'proxy'):
            return
        self.runner.run(['docker', 'run', '--rm', '--network', 'none', '--user', '0', '--entrypoint', 'sh',
                         '--mount', f'type=volume,src={name},dst=/acdm-volume', self.cfg['images'][service], '-ec',
                         f'chown "$(stat -c %u:%g {path})" /acdm-volume; chmod 750 /acdm-volume'])

    def check_mount(self):
        if self.cfg['storage'] == 'directory':
            folder = self.volume_path('content')
            if not folder.is_dir() or folder.is_symlink() or self.cfg.get('data_device') is None:
                raise Error('El almacenamiento de documentos no está disponible en su dispositivo original / Content store mount unavailable')
            if self.cfg.get('data_fsid') is not None:
                if filesystem_id(folder) == self.cfg['data_fsid']:
                    return
            elif folder.stat().st_dev == self.cfg['data_device']:
                return
            raise Error('El almacenamiento de documentos no está disponible en su dispositivo original / Content store mount unavailable')

    def check_ports(self, old=None):
        ids = self.runner.run(['docker', 'ps', '--quiet']).stdout.split()
        containers = json.loads(self.runner.run(['docker', 'container', 'inspect', *ids]).stdout) if ids else []
        own = set()
        for container in containers:
            if container.get('Config', {}).get('Labels', {}).get(LABEL + '.installation') == self.cfg['id']:
                for bindings in (container.get('NetworkSettings', {}).get('Ports') or {}).values():
                    own.update(int(binding['HostPort']) for binding in bindings or [])
        for key in ('http_port', 'https_port'):
            if old and old['bind'] == self.cfg['bind'] and old[key] == self.cfg[key]:
                continue
            for container in containers:
                if container.get('Config', {}).get('Labels', {}).get(LABEL + '.installation') == self.cfg['id']:
                    continue
                for bindings in (container.get('NetworkSettings', {}).get('Ports') or {}).values():
                    for binding in bindings or []:
                        if int(binding['HostPort']) == self.cfg[key] and (binding['HostIp'] in ('0.0.0.0', '::', self.cfg['bind']) or self.cfg['bind'] == '0.0.0.0'):
                            raise Error(f"Puerto ocupado por otro contenedor: {self.cfg[key]} / Port owned by another container: {self.cfg[key]}")
            if self.cfg[key] in own:
                continue
            probe = socket.socket()
            try:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind((self.cfg['bind'], self.cfg[key]))
            except OSError as exc:
                where = f"{self.cfg['bind']}:{self.cfg[key]}"
                raise Error(f"IP/puerto no disponible: {where} / IP/port unavailable: {where}") from exc
            finally:
                probe.close()

    def write(self):
        """Compose definition of this configuration, next to its revision."""
        atomic(self.revision / 'compose.json', compose(self.cfg))

    def validate(self):
        self.dc('config', '--quiet')
        # Validate the real Caddy binary before touching the active proxy.
        self.runner.run(['docker', 'run', '--rm', '-i', '--network', 'none', '--entrypoint', 'caddy',
                         '--env', 'HOME=/tmp', self.cfg['images']['proxy'], 'adapt', '--adapter', 'caddyfile', '--config', '-'],
                        input=compose(self.cfg)['services']['proxy']['environment']['ACDM_CADDY_CONFIG'].replace('$$', '$'))

    def up(self, *services, timeout=1800):
        self.check_mount()
        self.write()
        # Compose removes orphan containers but leaves their anonymous image volumes.
        # A service an update retires must also release its anonymous volumes.
        configured_services = set(start_order(self.cfg))
        orphans = [info for info in self.owned_containers()
                   if info['Config']['Labels'].get('com.docker.compose.service') not in configured_services]
        if orphans:
            self.runner.run(['docker', 'rm', '--force', '--volumes', *[info['Id'] for info in orphans]], timeout=600)
            self.remove_anonymous_volumes(orphans)
        self.dc('up', '-d', '--no-build', '--remove-orphans', *services, timeout=timeout)

    def stop(self, *services):
        self.dc('stop', '-t', '90', *services, timeout=600)

    def services(self):
        raw = self.dc('ps', '--all', '--format', 'json', check=False).stdout.strip()
        if not raw:
            return []
        return json.loads(raw) if raw.startswith('[') else [json.loads(line) for line in raw.splitlines() if line.strip()]

    def health(self, service):
        cid = self.container(service, required=False)
        info = self.inspect('container', cid) if cid else None
        if not info:
            return 'missing'
        if not info['State'].get('Running'):
            return info['State'].get('Status', 'stopped')
        return info['State'].get('Health', {}).get('Status', 'running')

    def wait_healthy(self, *services, timeout=1800):
        """Unhealthy is not final: Alfresco may exceed its start period while it creates
        or upgrades its schema. Stopped containers fail at once."""
        until = time.monotonic() + timeout
        while True:
            states = {service: self.health(service) for service in services}
            stopped = [service for service, value in states.items() if value in ('exited', 'dead', 'missing', 'created')]
            if stopped:
                raise Error('Servicio detenido: %s / Service stopped: %s' % ((', '.join(stopped),) * 2))
            pending = [service for service, value in states.items() if value not in ('healthy', 'running')]
            if not pending:
                return
            if time.monotonic() > until:
                raise Error('Los servicios no están listos: %s / Services not ready: %s' % ((', '.join(pending),) * 2))
            time.sleep(5)

    def status(self):
        """Repository readiness as seen from inside its own container."""
        cid = self.container('alfresco', required=False)
        if not cid:
            return {'ready': False, 'container': 'missing'}
        info = self.inspect('container', cid)
        if not info['State']['Running']:
            return {'ready': False, 'container': info['State']['Status']}
        health = info['State'].get('Health', {}).get('Status', 'starting')
        return {'ready': health == 'healthy', 'container': 'running', 'health': health,
                'version': self.cfg['version'], 'search': self.cfg['search']}

    def api(self, method, path, body=None, *, password=None, user='admin', anonymous=False, quiet=True, timeout=120):
        """Repository REST call from inside the Alfresco container. URL, credentials and body
        travel in a curl configuration on stdin: never argv, environment, files or logs."""
        lines = ['url = ' + curl_quote(API + path), 'request = ' + curl_quote(method), 'silent', 'show-error',
                 'header = "Accept: application/json"', 'write-out = "\\n%{http_code}"']
        if not anonymous:
            lines.append('user = ' + curl_quote(f'{user}:{password if password is not None else self.cfg["admin_password"]}'))
        if body is not None:
            lines += ['header = "Content-Type: application/json"', 'data-binary = ' + curl_quote(json.dumps(body))]
        result = self.dc('exec', '-T', 'alfresco', 'curl', '--config', '-', input='\n'.join(lines) + '\n',
                         quiet=quiet, check=False, timeout=timeout)
        output, _, code = result.stdout.rpartition('\n')
        if not code.strip().isdigit():
            raise Error('Alfresco no respondió a la API / Alfresco API did not respond')
        try:
            data = json.loads(output) if output.strip() else {}
        except ValueError:
            data = {}
        return int(code), data

    def verify(self):
        """Every service healthy and the repository and Share reachable through the proxy."""
        self.wait_healthy(*start_order(self.cfg))
        code, _ = self.api('GET', '/probes/-ready-', anonymous=True, quiet=False)
        if code != 200:
            raise Error('Alfresco no está listo / Alfresco is not ready')
        if not self.cfg['maintenance_gate']:
            entry = next(e for e in self.cfg['domains'] if e['host'] == self.cfg['primary'])
            for path, expected in (('/alfresco/api/-default-/public/alfresco/versions/1/probes/-live-', ('200', '308')),
                                   ('/share/page/', ('200', '302', '308'))):
                code = self.probe_http(entry, path)
                if code not in expected:
                    raise Error(f'Alfresco no responde a través del proxy ({path}: {code}) / '
                                f'Alfresco is not reachable through its configured proxy route ({path}: {code})')
        return self.status()

    def probe_http(self, entry, path):
        """HTTP status through the proxy from the application network; redirects not followed."""
        result = self.dc('exec', '-T', 'alfresco', 'curl', '-s', '-o', '/dev/null', '-w', '%{http_code}',
                         '-H', 'Host: ' + entry['host'], 'http://proxy' + path, check=False, timeout=60)
        return result.stdout.strip()

    def sql(self, statement):
        script = 'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -v ON_ERROR_STOP=1 -At -U alfresco -d alfresco'
        return self.dc('exec', '-T', 'postgres', 'sh', '-ec', script, input=statement + '\n')

    def db_size(self):
        return int(self.sql("SELECT pg_database_size('alfresco');").stdout.strip() or 0)

    def check_space(self, required, role='db'):
        result = self.runner.run(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'df',
            '--mount', 'type=volume,src=' + self.cfg['volumes'][role] + ',dst=/acdm-space,readonly',
            self.cfg['images']['postgres'], '-Pk', '/acdm-space'])
        available = int(result.stdout.splitlines()[-1].split()[3]) * 1024
        if available < required:
            raise Error('Espacio insuficiente en los volúmenes / Insufficient volume space')
        return available

    def ca_certificate(self):
        result = self.runner.run(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'cat',
            '--mount', 'type=volume,src=' + self.cfg['volumes']['proxy_data'] + ',dst=/data,readonly',
            self.cfg['images']['proxy'], '/data/caddy/pki/authorities/local/root.crt'], check=False)
        if result.returncode:
            raise Error('La CA local aún no se ha creado / Local CA not available yet')
        dest = self.state.path / 'local-ca.crt'
        atomic(dest, result.stdout, 0o644)
        return dest

    def remove_owned(self, delete_volumes=False):
        containers = self.owned_containers()
        self.dc('down', '--remove-orphans', *(['--volumes'] if delete_volumes else []), timeout=600)
        if delete_volumes:
            self.remove_anonymous_volumes(containers)
            for name in self.cfg['volumes'].values():
                info = self.inspect('volume', name)
                if info:
                    self.owned(info, 'volume')
                    self.runner.run(['docker', 'volume', 'rm', name])

    def app_services(self):
        return [service for service in start_order(self.cfg) if service in APP_SERVICES or service == 'batch-indexing']


def filesystem_id(path):
    return os.statvfs(path).f_fsid


def selinux_label(runner, folder):
    """Bind-mounted folders need the container label on SELinux hosts (Fedora, RHEL, Rocky, Alma)."""
    enforce = Path('/sys/fs/selinux/enforce')
    if not enforce.exists() or not shutil.which('chcon'):
        return
    runner.run(['chcon', '-R', '-t', 'container_file_t', folder], check=False)


def snap_docker(runner):
    root = runner.run(['docker', 'info', '--format', '{{.DockerRootDir}}'], check=False).stdout.strip()
    return root.startswith('/var/snap/') or (shutil.which('docker') or '').startswith('/snap/')


def require_engine(runner):
    """Technical requirements, independent of the distribution that provides them."""
    if runner.run(['docker', 'version', '--format', '{{.Server.Os}}']).stdout.strip() != 'linux':
        raise Error('Docker debe usar contenedores Linux / Docker must use Linux containers')
    if runner.run(['docker', 'compose', 'version'], check=False).returncode:
        raise Error('Falta Docker Compose V2 (docker compose) / Docker Compose V2 plugin missing')
    data = runner.run(['docker', 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}']).stdout.strip()
    if os.environ.get('DOCKER_HOST', data).startswith(('tcp:', 'ssh:')):
        raise Error('Solo Docker local / Only a local Docker Engine is supported')
    architecture = runner.run(['docker', 'info', '--format', '{{.Architecture}}'], check=False).stdout.strip()
    if architecture and architecture not in ('x86_64', 'amd64', 'aarch64', 'arm64'):
        raise Error(f'Arquitectura de Docker no compatible: {architecture} (amd64/arm64) / '
                    f'Unsupported Docker architecture: {architecture} (amd64/arm64)')


__all__ = ['Engine', 'require_engine', 'endpoint']
