"""Installation, reversible settings, guarded updates, backups and recovery."""
from __future__ import annotations

import contextlib
import copy
import ipaddress
import json
from pathlib import Path
import secrets
import shutil
import urllib.parse

from . import archive
from .catalog import Catalog, same_major
from . import mailer
from .config import (access_url, applications, engine_of, host, image_tag, initial, memory_tier, port, prefer_first_public,
                     public_host, roles, start_order, validate, validate_mail)
from .core import Error, Runner, confirm, human, read_json, stage, version_tuple
from .docker import Engine
from .maintenance import Maintenance
from . import requirements
from .system import host_info, host_timezone


class AdminAuthError(Error):
    """The stored administrator password no longer matches Alfresco."""


def tag_numbers(tag):
    import re
    return tuple(int(part) for part in re.findall(r'[0-9]+', tag)[:4])


def bundle_changes(cfg, bundle):
    """Components whose tag a release bundle changes; Caddy follows its own updates and a
    newer PostgreSQL patch installed later is never downgraded."""
    changes = {}
    for role in set(roles(bundle['images'])) | set(roles(cfg)):
        if role == 'proxy':
            continue
        old = cfg['images'].get(role)
        new = bundle['images'].get(role)
        old_tag = image_tag(old) if old else ''
        new_tag = image_tag(new) if new else ''
        if old_tag == new_tag:
            continue
        if role == 'postgres' and same_major(old, new) and tag_numbers(old_tag) >= tag_numbers(new_tag):
            continue
        changes[role] = [old_tag, new_tag]
    return changes


class Manager(Maintenance):
    def __init__(self, state):
        self.state = state
        self.catalog = Catalog(state.path / 'cache')

    def new_subnet(self):
        runner = Runner(self.state)
        ids = runner.run(['docker', 'network', 'ls', '--quiet']).stdout.split()
        networks = []
        if ids:
            for entry in json.loads(runner.run(['docker', 'network', 'inspect', *ids]).stdout):
                networks += [ipaddress.ip_network(c['Subnet']) for c in (entry.get('IPAM', {}).get('Config') or []) if c.get('Subnet')]
        for _ in range(250):
            candidate = ipaddress.ip_network(f'172.{secrets.randbelow(12) + 19}.{secrets.randbelow(250)}.0/28')
            reserved = candidate.supernet(prefixlen_diff=1)
            if not any(reserved.overlaps(n) for n in networks if n.version == 4):
                return str(candidate)
        raise Error('No hay una subred privada libre / No private subnet available')

    def installation_options(self):
        return self.catalog.bundles(refresh=True)

    def docker_root(self):
        """Docker's data folder on this host, where images and Docker volumes live."""
        root = Runner(self.state).run(['docker', 'info', '--format', '{{.DockerRootDir}}'], check=False).stdout.strip()
        # A remote socket (test runners) has no local Docker folder: measure the manager's disk instead.
        return root if root and Path(root).exists() else str(self.state.path)

    def requirements(self, storage='directory', storage_root=None):
        """RAM, CPU, architecture and disk rows for the review shown before installing."""
        info = host_info()
        data = (storage_root or str(self.state.path / 'data')) if storage == 'directory' else None
        return requirements.evaluate(info.get('memory', 0), info.get('cpus', 0), info.get('architecture', ''),
                                     self.docker_root(), data)

    def check_update_space(self, engine, roles):
        """New images coexist with the old ones until «Free up space»: refuse to start an update
        that would fill the disk or pass the 85 % where Elasticsearch stops indexing."""
        needed = 0
        for role in roles:
            info = engine.inspect('image', engine.cfg['images'][role]) if role in engine.cfg['images'] else None
            # A new version is about the size of the current one; Docker 29 also keeps its compressed copy.
            needed += int(info['Size'] * 1.4) if info and info.get('Size') else 2 * requirements.GIB
        enough, free = requirements.update_space(self.docker_root(), needed)
        if not enough:
            raise Error(f'Espacio insuficiente para actualizar: las imágenes nuevas necesitan unos {human(needed)} y hay '
                        f'{human(free)} libres. Usa «Liberar espacio» y vuelve a intentarlo / Not enough disk to update: '
                        f'the new images need about {human(needed)} and {human(free)} is free. Use «Free up space» and retry')
        return needed

    def check_requirements(self, storage='directory', storage_root=None):
        rows = self.requirements(storage, storage_root)
        problems = requirements.errors(rows)
        if problems:
            raise Error('\n'.join(problems))
        warning = requirements.low_memory(rows)
        if warning:
            print('[!] ' + warning, flush=True)
        return rows

    # ------------------------------------------------------------------ install
    @staticmethod
    def detected_tier():
        return memory_tier(host_info().get('memory', 0))

    def refresh_memory(self):
        """Give the containers the limits that match the server RAM (after a VM resize, for example)."""
        cfg = self.state.load()
        tier = self.detected_tier()
        if self.state.pending or cfg.get('memory_tier', 'base') == tier:
            return False
        cfg['memory_tier'] = tier
        self.state.activate(self.state.revision(cfg))
        print(f'[*] Memoria de los servicios ajustada a la RAM del servidor / Service memory adjusted to server RAM: {tier}',
              flush=True)
        return True

    def generate_keystore(self, image):
        """This installation's own metadata keystore instead of the one shared by every image."""
        store, key = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        script = ('cd /tmp && keytool -genseckey -alias metadata -keyalg DESede -keysize 168 -storetype JCEKS '
                  '-keystore ks -storepass "$ACDM_STORE" -keypass "$ACDM_KEY" >/dev/null 2>&1 && base64 -w0 ks')
        # Passwords travel in the environment of this process, never in argv or logs.
        data = Runner(self.state).run(['docker', 'run', '--rm', '--network', 'none', '--env', 'ACDM_STORE',
                                       '--env', 'ACDM_KEY', '--entrypoint', 'sh', image, '-ec', script],
                                      env={'ACDM_STORE': store, 'ACDM_KEY': key}, quiet=True).stdout.strip()
        return {'data': data, 'store_password': store, 'key_password': key}

    def install(self, version=None, *, supplied=None, mail=None, **settings):
        if self.state.configured:
            raise Error('Ya existe una instalación; usa el menú / Installation already configured')
        with stage('Consultar versiones oficiales / Resolve official releases'):
            bundle = self.catalog.find(version) if version else self.catalog.latest_bundle(refresh=True)
            images = dict(bundle['images'])
            images['proxy'] = self.catalog.latest_proxy()
        self.check_requirements(settings.get('storage', 'directory'), settings.get('storage_root'))
        validate_mail(mail)
        if not settings.get('timezone'):
            settings['timezone'] = host_timezone()
        ipaddress.IPv4Address(settings.get('bind', '127.0.0.1'))
        planned = dict(id='preflight', project='acdm_preflight', bind=settings.get('bind', '127.0.0.1'),
                       http_port=port(settings.get('http_port', 80)), https_port=port(settings.get('https_port', 443)))
        print(f"Alfresco {bundle['version']} ({bundle['tag']}); search: {bundle['search']}; "
              f"{settings.get('environment', 'lan')}; storage: {settings.get('storage', 'directory')}")
        confirm('INSTALL', supplied)
        preflight = Engine(self.state, planned)
        preflight.check_ports()
        with stage('Descargar imágenes / Download images'):
            images = preflight.pin(images)
        cfg = initial(images, subnet=self.new_subnet(), **settings)
        cfg['bundle'] = bundle['tag']
        cfg['memory_tier'] = self.detected_tier()
        with stage('Crear almacén de claves propio / Create own metadata keystore'):
            cfg['keystore'] = self.generate_keystore(images['alfresco'])
        if mail:
            cfg['mail'] = mail
        revision = self.state.revision(cfg)
        engine = Engine(self.state, cfg, revision)
        engine.validate()
        engine.ensure_volumes()
        revision = self.state.revision(cfg)
        engine = Engine(self.state, cfg, revision)
        self.state.begin('install', target=str(revision))
        self.state.activate(revision)
        self.state.installed_code()
        return self.complete_install(engine)

    def complete_install(self, engine):
        with stage('Iniciar Alfresco; el primer arranque crea la base de datos / Start Alfresco; first boot creates the database'):
            engine.up(timeout=3600)
            engine.wait_healthy('postgres', 'alfresco', timeout=3600)
        self.state.phase('started')
        with stage('Proteger la cuenta admin / Secure the admin account'):
            self.secure_admin(engine)
        self.state.phase('secured')
        with stage('Abrir el acceso y verificar / Open access and verify'):
            self.finish()
        self.state.done()
        cfg = self.state.load()
        print('[+] Alfresco instalado y verificado / Installed and verified: ' + access_url(cfg))
        return self.credentials(cfg)

    @staticmethod
    def secure_admin(engine):
        """Replace Alfresco's well-known admin/admin before any address is published."""
        password = engine.cfg['admin_password']
        code, _ = engine.api('GET', '/people/-me-', password=password)
        if code == 200:
            return
        code, _ = engine.api('PUT', '/people/-me-', {'oldPassword': 'admin', 'password': password}, password='admin')
        if code != 200:
            raise Error(f'No se pudo cambiar la contraseña inicial de admin (HTTP {code}) / '
                        f'Could not replace the initial admin password (HTTP {code})')

    @staticmethod
    def credentials(cfg):
        return {'applications': applications(cfg), 'user': 'admin', 'admin_password': cfg['admin_password']}

    # ------------------------------------------------------------------ status
    def status(self):
        engine = Engine(self.state)
        return {'version': engine.cfg['version'], 'search': engine.cfg['search'], 'environment': engine.cfg['environment'],
                'access': [dict(e, url=access_url(engine.cfg, e)) for e in engine.cfg['domains']],
                'applications': applications(engine.cfg), 'status': engine.status(),
                'operation': self.state.pending, 'services': engine.services()}

    def ready(self):
        if self.state.pending:
            raise Error('Operación pendiente; usa Recuperar / Pending operation; use recover')
        engine = Engine(self.state)
        if engine.cfg['status'] != 'ready':
            raise Error('La instalación no terminó; usa Recuperar / Installation incomplete; use recover')
        engine.check_mount()
        if not engine.status().get('ready'):
            raise Error('Alfresco no está listo: inícialo o revisa sus registros / Alfresco is not ready: start it or inspect its logs')
        return engine

    # ------------------------------------------------------------------ accounts
    def users(self):
        engine = self.ready()
        code, data = engine.api('GET', '/people?maxItems=1000&orderBy=id')
        if code == 401:
            raise AdminAuthError('La contraseña de admin guardada ya no es válida / The stored admin password is no longer valid')
        if code != 200:
            raise Error(f'No se pudo listar usuarios (HTTP {code}) / Could not list users (HTTP {code})')
        return {item['entry']['id']: item['entry'].get('displayName') or item['entry']['id']
                for item in data.get('list', {}).get('entries', [])}

    def remember_admin(self, password):
        """Store a corrected admin password after Alfresco accepts it."""
        engine = Engine(self.state)
        code, _ = engine.api('GET', '/people/-me-', password=password)
        if code != 200:
            raise Error('Alfresco rechazó esa contraseña de admin / Alfresco rejected that admin password')
        cfg = self.state.load()
        cfg['admin_password'] = password
        self.state.activate(self.state.revision(cfg))

    def reset_password(self, user, password, supplied=None):
        if not user or len(user) > 255 or any(ord(char) < 32 or char in '/\\?#' for char in user):
            raise Error('Identificador de usuario inválido / Invalid user ID')
        if not password or len(password) > 256 or any(ord(char) < 32 for char in password):
            raise Error('La contraseña no puede estar vacía ni contener saltos de línea / Password must not be empty or contain line breaks')
        engine = self.ready()
        confirm('RESET-PASSWORD', supplied, user)
        if user == 'admin':
            code, _ = engine.api('PUT', '/people/-me-', {'oldPassword': engine.cfg['admin_password'], 'password': password})
        else:
            code, _ = engine.api('PUT', '/people/' + urllib.parse.quote(user, safe=''), {'password': password})
        if code == 401:
            raise AdminAuthError('La contraseña de admin guardada ya no es válida / The stored admin password is no longer valid')
        if code == 404:
            raise Error('Usuario no encontrado / User not found')
        if code != 200:
            raise Error(f'No se pudo cambiar la contraseña (HTTP {code}) / Could not change the password (HTTP {code})')
        if user == 'admin':
            cfg = self.state.load()
            cfg['admin_password'] = password
            self.state.activate(self.state.revision(cfg))
        return {'user': user}

    # ------------------------------------------------------------------ gate
    def gate(self, enabled):
        cfg = self.state.load()
        cfg['maintenance_gate'] = enabled
        self.state.activate(self.state.revision(cfg))
        return Engine(self.state)

    def quiesce(self):
        """Close the proxy (maintenance page) and stop everything that writes, except PostgreSQL."""
        engine = self.gate(True)
        engine.up('proxy')
        engine.stop(*[s for s in reversed(start_order(engine.cfg)) if s in ('batch-indexing', 'share',
                                                                             'content-app', 'control-center', 'alfresco')])
        return engine

    def finish(self, timeout=3600):
        """Start everything behind the closed gate, verify, then open the proxy."""
        engine = Engine(self.state)
        if not engine.cfg['maintenance_gate']:
            engine = self.gate(True)
        engine.up(timeout=timeout)
        engine.wait_healthy(*start_order(engine.cfg), timeout=timeout)
        engine.verify()
        gated_revision = (self.state.path / 'current').resolve()
        if self.state.pending:
            self.state.phase(self.state.pending['phase'], traffic_opened=True)
        engine = self.gate(False)
        try:
            engine.up('proxy')
            engine.verify()
        except BaseException:
            self.state.activate(gated_revision)
            try:
                Engine(self.state).up('proxy')
            except BaseException:
                pass
            raise
        engine.cfg['status'] = 'ready'
        self.state.activate(self.state.revision(engine.cfg))
        return engine

    def reopen(self):
        with stage('Restablecer el servicio sin cambios / Restore the service unchanged'):
            self.state.activate(self.state.pending['source'])
            self.finish()
            self.state.done()

    @contextlib.contextmanager
    def reopen_on_failure(self):
        """A failed pre-update backup must not leave Alfresco closed. Interruptions keep the journal for Recover."""
        try:
            yield
        except (Error, OSError) as exc:
            try:
                self.reopen()
            except BaseException:
                pass
            raise exc

    # ------------------------------------------------------------------ rollback
    def restore(self, path, *, supplied=None):
        """Return to the SQL and certificates saved before an update. The content store stays in place:
        updates never modify documents, and files added later are simply unreferenced."""
        current = self.state.load()
        operation = self.state.pending
        if not operation or operation.get('backup') != str(Path(path)):
            raise Error('Solo se restaura el respaldo de la operación pendiente / Only the pending operation backup can be restored')
        confirm('RESTORE', supplied, 'SQL y certificados previos a la actualización / SQL and certificates from before the update')
        with archive.unpack(self.state, path) as (work, manifest):
            saved = manifest['configuration']
            if saved['id'] != current['id']:
                raise Error('El respaldo pertenece a otra instalación / Backup belongs to a different installation')
            if shutil.disk_usage(self.state.path).free < sum(x['bytes'] for x in manifest['files'].values()) + manifest['sql_estimated_bytes'] * 2:
                raise Error('Espacio insuficiente para preparar la restauración / Insufficient restore staging space')
            with stage('Detener Alfresco / Stop Alfresco'):
                Engine(self.state).stop()
            cfg = copy.deepcopy(saved)
            cfg['maintenance_gate'] = True
            suffix = secrets.token_hex(4)
            # Documents are kept in place, on their original device.
            cfg['volumes']['content'] = current['volumes']['content']
            cfg['data_device'] = current.get('data_device')
            if 'data_fsid' in current:
                cfg['data_fsid'] = current['data_fsid']
            else:
                cfg.pop('data_fsid', None)
            for role in ('db', 'search', 'proxy_data', 'proxy_config'):
                cfg['volumes'][role] = f"acdm_{cfg['id']}_{role}_r{suffix}"
            revision = self.state.revision(cfg)
            engine = Engine(self.state, cfg, revision)
            engine.pull()
            engine.ensure_volumes()
            revision = self.state.revision(cfg)
            engine = Engine(self.state, cfg, revision)
            archive.restore_volume(engine, cfg['volumes']['proxy_data'], work / 'proxy.tar')
            self.state.phase('restoring', target=str(revision), backup=str(Path(path)))
            self.state.activate(revision)
            engine.up('postgres')
            engine.wait_healthy('postgres', timeout=600)
            archive.import_sql(engine, work / 'db.sql.gz')
            with stage('Iniciar y verificar; el índice de búsqueda se reconstruye / Start and verify; the search index is rebuilt'):
                self.finish()
            self.state.done()
        return cfg['version']

    # ------------------------------------------------------------------ settings and access
    def settings(self, cfg, supplied=None):
        old_engine = self.ready()
        old = old_engine.cfg
        for key in ('status', 'admin_password', 'data_device', 'data_fsid', 'keystore'):
            if key in old:
                cfg[key] = old[key]
            else:
                cfg.pop(key, None)
        cfg['memory_tier'] = self.detected_tier()
        validate(cfg)
        confirm('APPLY', supplied)
        revision = self.state.revision(cfg)
        new = Engine(self.state, cfg, revision)
        new.check_ports(old)
        new.validate()
        source = (self.state.path / 'current').resolve()
        self.state.begin('settings', source=str(source), target=str(revision))
        try:
            self.state.activate(revision)
            with stage('Aplicar y verificar / Apply and verify'):
                new.up()
                new.verify()
            self.state.done()
        except BaseException:
            self.state.activate(source)
            previous = Engine(self.state)
            try:
                previous.up()
                previous.verify()
                self.state.done()
            except BaseException:
                pass
            raise

    def add_domain(self, name, *, mode='local', tls='off', email='', primary=False, publish_direct=False, external_port=443, supplied=None):
        cfg = self.state.load()
        name = host(name) if mode == 'local' else public_host(name)
        if any(e['host'] == name for e in cfg['domains']):
            raise Error('La dirección ya existe / Address already exists')
        auto_primary = prefer_first_public(cfg, mode)
        cfg['domains'].append(dict(host=name, kind='local' if mode == 'local' else 'public',
                                   mode=mode, tls=tls if mode == 'local' else 'acme', email=email))
        if mode != 'local':
            cfg['domains'][-1]['external_port'] = port(external_port)
        if primary or auto_primary:
            cfg['primary'] = name
        if publish_direct:
            if mode != 'direct':
                raise Error('Publicar TCP 80/443 requiere el modo dominio directo / Publishing TCP 80/443 requires direct-domain mode')
            cfg.update(bind='0.0.0.0', http_port=80, https_port=443)
        self.settings(cfg, supplied)
        return cfg

    def remove_domain(self, name, supplied=None):
        name = host(name)
        cfg = self.state.load()
        remaining = [e for e in cfg['domains'] if e['host'] != name]
        if len(remaining) == len(cfg['domains']):
            raise Error('Dirección no encontrada / Address not found')
        if not remaining:
            raise Error('No se puede quitar la última dirección / Cannot remove the last address')
        code = Engine(self.state).probe_http(remaining[0], '/alfresco/api/-default-/public/alfresco/versions/1/probes/-live-')
        if code not in ('200', '308'):
            raise Error('No se pudo verificar la dirección alternativa / Alternative address not verified')
        cfg['domains'] = remaining
        if cfg['primary'] == name:
            cfg['primary'] = remaining[0]['host']
        self.settings(cfg, supplied)

    # ------------------------------------------------------------------ email
    def test_mail(self, mail, recipient):
        """Send a test message with these settings before they are applied."""
        validate_mail(mail)
        url = access_url(self.state.load()) if self.state.configured else 'Alfresco'
        mailer.test(mail, recipient, url)
        return {'sent_to': recipient}

    def set_mail(self, mail, supplied=None):
        """Apply, change or remove (None) outbound email; Alfresco restarts to read it."""
        validate_mail(mail)
        cfg = self.state.load()
        if mail:
            cfg['mail'] = mail
        else:
            cfg.pop('mail', None)
        self.settings(cfg, supplied)
        return {'mail': 'on' if mail else 'off'}

    # ------------------------------------------------------------------ updates
    def checks(self):
        cfg = self.state.load()
        bundles = self.catalog.bundles(refresh=True)
        current = version_tuple(cfg['version'])
        updates = []
        for bundle in bundles:
            if version_tuple(bundle['version']) < current:
                continue
            changes = bundle_changes(cfg, bundle)
            if changes:
                updates.append({'tag': bundle['tag'], 'version': bundle['version'], 'search': bundle['search'],
                                'changes': changes})
        components = {}
        proxy = self.catalog.latest_proxy()
        components['proxy'] = {'current': image_tag(cfg['images']['proxy']), 'available': image_tag(proxy),
                               'image_changed': tag_numbers(image_tag(proxy)) > tag_numbers(image_tag(cfg['images']['proxy']))}
        postgres = self.catalog.postgres_patch(cfg['images']['postgres'])
        components['postgres'] = {'current': image_tag(cfg['images']['postgres']), 'available': image_tag(postgres),
                                  'image_changed': tag_numbers(image_tag(postgres)) > tag_numbers(image_tag(cfg['images']['postgres']))}
        return {'current': cfg['version'], 'bundle': cfg.get('bundle', '—'), 'latest': bundles[0]['version'],
                'catalog_offline': bool(bundles[0].get('stale')), 'updates': updates, 'components': components}

    def update(self, target=None, *, supplied=None):
        live = self.ready()
        cfg = live.cfg
        available = self.checks()['updates']
        if not available:
            raise Error('No hay actualización disponible / No update available')
        bundle = available[0] if target is None else next(
            (item for item in available if target in (item['tag'], item['version'])), None)
        if bundle is None:
            raise Error('Versión no disponible para actualizar / Version not available as an update')
        images_source = next(b for b in self.catalog.bundles() if b['tag'] == bundle['tag'])['images']
        changes = bundle['changes']
        self.check_update_space(live, [role for role in changes if role in images_source])
        confirm('UPDATE-' + bundle['version'], supplied,
                ', '.join(f'{role} {old or "—"} → {new or "—"}' for role, (old, new) in sorted(changes.items())))
        wanted = {}
        for role in roles(images_source):
            if role == 'proxy':
                continue
            if role in changes:
                wanted[role] = images_source[role]
        with stage('Descargar imágenes nuevas / Download new images'):
            pinned = live.pin(wanted)
        changed = copy.deepcopy(cfg)
        changed['images'] = {role: pinned.get(role, cfg['images'].get(role)) for role in roles(images_source)}
        changed['version'] = image_tag(changed['images']['alfresco'])
        changed['search'] = engine_of(changed['images'])
        changed['bundle'] = bundle['tag']
        changed['memory_tier'] = self.detected_tier()
        changed['maintenance_gate'] = True
        suffix = secrets.token_hex(4)
        database_migration = not same_major(cfg['images']['postgres'], changed['images']['postgres'])
        search_reset = (changed['search'] != cfg['search'] or
                        changed['search'] == 'elasticsearch' and not same_major(cfg['images']['elasticsearch'], changed['images']['elasticsearch']))
        if database_migration:
            changed['volumes']['db'] = f"acdm_{cfg['id']}_db_r{suffix}"
        if search_reset:
            changed['volumes']['search'] = f"acdm_{cfg['id']}_search_r{suffix}"
        validate(changed)
        archive.preflight(live)
        self.state.begin('update', source=str((self.state.path / 'current').resolve()), target_version=changed['version'])
        with self.reopen_on_failure():
            with stage('Respaldo SQL previo / Pre-update SQL backup'):
                backup = archive.create(self.quiesce())
            self.state.phase('backed-up', backup=str(backup))
        revision = self.state.revision(changed)
        new = Engine(self.state, changed, revision)
        new.validate()
        new.ensure_volumes()
        self.state.phase('applying', target=str(revision), backup=str(backup))
        Engine(self.state).stop()
        self.state.activate(revision)
        new = Engine(self.state)
        if database_migration:
            with stage('Migrar PostgreSQL a la nueva versión mayor / Migrate PostgreSQL to the new major'):
                new.up('postgres')
                new.wait_healthy('postgres', timeout=600)
                with archive.unpack(self.state, backup) as (work, _):
                    archive.import_sql(new, work / 'db.sql.gz')
            self.state.phase('applying', database_ready=True)
        with stage('Aplicar y verificar; Alfresco actualiza su esquema / Apply and verify; Alfresco upgrades its schema'):
            self.finish(timeout=5400)
        self.state.done()
        self.prune_backups()
        return {'version': changed['version'], 'bundle': bundle['tag'], 'backup': str(backup),
                'database_migrated': database_migration, 'search_rebuilt': search_reset}

    def update_component(self, component, supplied=None):
        if component not in ('proxy', 'postgres'):
            raise Error('Componente no actualizable por separado / Component follows the Alfresco bundles')
        live = self.ready()
        cfg = live.cfg
        target = self.catalog.latest_proxy() if component == 'proxy' else self.catalog.postgres_patch(cfg['images']['postgres'])
        old = image_tag(cfg['images'][component])
        if tag_numbers(image_tag(target)) <= tag_numbers(old):
            raise Error('El componente ya está actualizado / Component is current')
        self.check_update_space(live, [component])
        confirm('UPDATE-' + component.upper(), supplied, f'{component}: {old} -> {image_tag(target)}')
        changed = copy.deepcopy(cfg)
        changed['images'][component] = live.pin({component: target})[component]
        revision = self.state.revision(changed)
        proposed = Engine(self.state, changed, revision)
        proposed.validate()
        source = (self.state.path / 'current').resolve()
        if component == 'proxy':
            # Caddy holds no data: swap it online and go back if verification fails.
            self.state.begin('component-update', source=str(source), target=str(revision), component=component)
            try:
                self.state.activate(revision)
                proposed.up('proxy')
                Engine(self.state).verify()
                self.state.done()
            except BaseException:
                self.state.activate(source)
                try:
                    Engine(self.state).up('proxy')
                    self.state.done()
                except BaseException:
                    pass
                raise
            return {'component': component, 'version': image_tag(target)}
        archive.preflight(live)
        self.state.begin('component-update', source=str(source), component=component)
        with self.reopen_on_failure():
            backup = archive.create(self.quiesce())
            self.state.phase('backed-up', backup=str(backup))
        changed['maintenance_gate'] = True
        revision = self.state.revision(changed)
        self.state.phase('applying', target=str(revision), backup=str(backup))
        self.state.activate(revision)
        self.finish()
        self.state.done()
        self.prune_backups()
        return {'component': component, 'version': image_tag(target), 'backup': str(backup)}

    # ------------------------------------------------------------------ recovery
    def recover(self, action, supplied=None):
        operation = self.state.pending
        if not operation:
            raise Error('No hay operación pendiente / No pending operation')
        if action == 'restore':
            if not operation.get('backup'):
                raise Error('No hay respaldo de recuperación / No recovery backup available')
            return self.restore(operation['backup'], supplied=supplied)
        if operation['phase'] == 'restoring':
            raise Error('Restauración interrumpida: usa Recuperar → Restaurar para repetirla en volúmenes nuevos / '
                        'Interrupted restore: use recover --action restore to restart into fresh volumes')
        confirm('RESUME', supplied)
        if operation['action'] == 'install':
            engine = Engine(self.state)
            engine.ensure_volumes()
            return self.complete_install(engine)
        if operation['action'] == 'settings' or operation['phase'] in ('prepared', 'backed-up'):
            self.state.activate(operation['source'])
            self.finish()
            self.state.done()
            return {'resumed': operation['action']}
        # A partly applied update resumes with the SAME target images; SQL is never downgraded.
        if operation['phase'] == 'applying' and operation.get('target'):
            target = Path(operation['target'])
            source_cfg = read_json(Path(operation['source']) / 'config.json')
            target_cfg = read_json(target / 'config.json')
            if source_cfg['volumes']['db'] != target_cfg['volumes']['db'] and not operation.get('database_ready'):
                raise Error('Migración SQL interrumpida: usa recover --action restore / '
                            'Interrupted SQL migration: use recover --action restore')
            if (self.state.path / 'current').resolve() != target.resolve():
                Engine(self.state).stop()
                self.state.activate(target)
        self.finish(timeout=5400)
        self.state.done()
        return {'resumed': operation['action']}

    def prune_backups(self):
        """Keep the newest pre-update backups (three by default) so a later rollback stays possible."""
        keep = self.state.load().get('retention', 3)
        for path in sorted((self.state.path / 'backups').glob('*.acdm.tar'))[:-keep]:
            path.unlink()


__all__ = ['Manager', 'AdminAuthError', 'bundle_changes']
