"""Automatic pre-update backups: PostgreSQL dump and HTTPS certificates. Documents are never
copied: updates do not modify the content store, so the SQL dump is enough to roll back."""
from __future__ import annotations

import contextlib
import gzip
import json
import os
import posixpath
from pathlib import Path, PurePosixPath
import secrets
import shutil
import subprocess
import tarfile
import tempfile
import threading

from .core import Error, atomic, human, progress_values, sha, stamp, step, visual_stage

REQUIRED = {'manifest.json', 'db.sql.gz', 'proxy.tar'}


def members(archive):
    seen = set()
    total = 0
    items = archive.getmembers()
    if len(items) > 5_000_000:
        raise Error('Too many archive entries')
    for item in items:
        path = PurePosixPath(item.name)
        if (path.is_absolute() or '..' in path.parts or '\\' in item.name or '\x00' in item.name
                or any(ord(c) < 32 for c in item.name) or not (item.isdir() or item.isfile() or item.issym() or item.islnk())
                or str(path) in seen):
            raise Error('Unsafe archive entry: ' + repr(item.name))
        seen.add(str(path))
        if item.issym() or item.islnk():
            target = item.linkname
            resolved = PurePosixPath(posixpath.normpath(posixpath.join(str(path.parent) if item.issym() else '', target)))
            if PurePosixPath(target).is_absolute() or '\\' in target or any(ord(c) < 32 for c in target) or '..' in resolved.parts:
                raise Error('Unsafe archive link: ' + repr(item.name))
        if getattr(item, 'sparse', None):
            raise Error('Sparse archive entries are not accepted')
        total += item.size
        if total > 2 ** 46:
            raise Error('Archive expanded size exceeds supported limit')
    return items


def stream_out(args, path, *, compress=False, timeout=3600):
    """Bound memory; avoid password argv and ensure interrupted children exit."""
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen([str(x) for x in args], stdout=subprocess.PIPE, stderr=errors)
        timer = threading.Timer(timeout, process.kill)
        timer.start()
        try:
            with (gzip.open(path, 'wb') if compress else Path(path).open('wb')) as target:
                shutil.copyfileobj(process.stdout, target, length=1024 * 1024)
            process.stdout.close()
            if process.wait() != 0:
                errors.seek(0)
                raise Error(errors.read(2200).decode(errors='replace') or 'Backup stream failed')
            os.chmod(path, 0o600)
        finally:
            timer.cancel()
            if process.poll() is None:
                process.kill()
                process.wait()


def extract_checked(path, destination):
    try:
        return _extract_checked(path, destination)
    except (tarfile.TarError, EOFError, KeyError, TypeError, ValueError) as exc:
        raise Error('Respaldo inválido o incompleto / Invalid or incomplete backup') from exc


def _extract_checked(path, destination):
    with tarfile.open(path, 'r:') as archive:
        items = members(archive)
        names = {m.name for m in items}
        if names != REQUIRED or any(not m.isfile() for m in items):
            raise Error('Invalid backup members')
        if next(m for m in items if m.name == 'manifest.json').size > 1024 * 1024:
            raise Error('Backup manifest exceeds supported size')
        archive.extractall(destination, members=items, **({'filter': 'data'} if hasattr(tarfile, 'data_filter') else {}))
    manifest = json.loads((Path(destination) / 'manifest.json').read_text())
    if manifest.get('format') != 1:
        raise Error('Unsupported backup format')
    from .config import validate
    validate(manifest['configuration'])
    if set(manifest['files']) != names - {'manifest.json'}:
        raise Error('Backup file list mismatch')
    for name in names - {'manifest.json'}:
        item = Path(destination) / name
        expected = manifest['files'].get(name, {})
        if item.stat().st_size != expected.get('bytes') or sha(item) != expected.get('sha256'):
            raise Error('Backup checksum mismatch: ' + name)
    with tarfile.open(Path(destination) / 'proxy.tar') as inner:
        members(inner)
    with gzip.open(Path(destination) / 'db.sql.gz', 'rb') as sql:
        if not sql.read(100):
            raise Error('Empty SQL backup')
        for _ in iter(lambda: sql.read(1024 * 1024), b''):
            pass
    return manifest


@contextlib.contextmanager
def unpack(state, path):
    path = Path(path).absolute()
    if not path.is_file() or path.is_symlink():
        raise Error('El respaldo no es un archivo normal / Backup is not a regular file')
    if shutil.disk_usage(state.path / 'backups').free < path.stat().st_size + 32 * 1024 ** 2:
        raise Error('Espacio insuficiente para verificar el respaldo / Insufficient staging space to verify the backup')
    with tempfile.TemporaryDirectory(prefix='.verify-', dir=state.path / 'backups') as work:
        with visual_stage('Verificar integridad del respaldo / Verify backup integrity'):
            manifest = extract_checked(path, work)
        yield Path(work), manifest


def clean_leftovers(state):
    folder = state.path / 'backups'
    for path in folder.glob('*.partial'):
        if path.is_file() and not path.is_symlink():
            path.unlink()
    for path in [*folder.glob('.backup-*'), *folder.glob('.verify-*')]:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)


def preflight(engine):
    """Space needed by create(); checked while Alfresco is still online."""
    clean_leftovers(engine.state)
    estimated = engine.db_size()
    # Stream, completed package and validation extraction coexist at the peak.
    budget = max(estimated * 3, 128 * 1024 ** 2) + 256 * 1024 ** 2
    free = shutil.disk_usage(engine.state.path).free
    if free < budget:
        raise Error(f'Espacio insuficiente para el respaldo: {human(free)} libres, {human(budget)} necesarios / '
                    f'Insufficient backup space: {human(free)} free, {human(budget)} required')
    return estimated, budget, free


def create(engine, kind='pre-update'):
    """Caller stops Alfresco and its indexer; PostgreSQL stays up for a consistent dump."""
    state, cfg = engine.state, engine.cfg
    estimated, budget, free = preflight(engine)
    print(f'SQL estimado / estimated: {human(estimated)}; '
          f'espacio de trabajo / working budget: {human(budget)}', flush=True)
    progress_values(sql_estimated_bytes=human(estimated), working_budget=human(budget), disk_free=human(free))
    identity = stamp() + '-' + secrets.token_hex(4)
    destination = state.path / 'backups' / (identity + '.acdm.tar')
    files = {'db.sql.gz', 'proxy.tar'}
    with tempfile.TemporaryDirectory(prefix='.backup-', dir=state.path / 'backups') as work:
        work = Path(work)
        dump = 'PGPASSWORD="$POSTGRES_PASSWORD" exec pg_dump -U alfresco -d alfresco --no-owner --no-privileges'
        sql_args = ['docker', 'compose', '-p', cfg['project'], '-f', engine.revision / 'compose.json',
                    'exec', '-T', 'postgres', 'sh', '-ec', dump]
        with visual_stage('Exportar base de datos / Export database'):
            stream_out(sql_args, work / 'db.sql.gz', compress=True, timeout=max(3600, estimated // (2 * 1024 * 1024)))
        stream_out(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'tar',
                    '--mount', 'type=volume,src=' + cfg['volumes']['proxy_data'] + ',dst=/data,readonly',
                    cfg['images']['postgres'], '--numeric-owner', '-C', '/data', '-cf', '-', '.'], work / 'proxy.tar')
        manifest = {'format': 1, 'id': identity, 'created': stamp(), 'kind': kind,
                    'configuration': cfg, 'excluded': ['/usr/local/tomcat/alf_data (content store)'],
                    'sql_estimated_bytes': estimated,
                    'files': {name: {'bytes': (work / name).stat().st_size, 'sha256': sha(work / name)} for name in files}}
        atomic(work / 'manifest.json', manifest)
        temp = destination.with_suffix('.partial')
        try:
            with tarfile.open(temp, 'w') as archive:
                for name in sorted(files | {'manifest.json'}):
                    archive.add(work / name, arcname=name, recursive=False)
            os.chmod(temp, 0o600)
            with tempfile.TemporaryDirectory(dir=work) as verify:
                extract_checked(temp, verify)
            os.replace(temp, destination)
        finally:
            temp.unlink(missing_ok=True)
    print(f'[+] Respaldo verificado / Verified backup: {destination.name} ({human(destination.stat().st_size)})', flush=True)
    return destination


@step('Importar SQL en el volumen nuevo / Import SQL into the new volume')
def import_sql(engine, path):
    args = ['docker', 'compose', '-p', engine.cfg['project'], '-f', engine.revision / 'compose.json',
            'exec', '-T', 'postgres', 'sh', '-ec',
            'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -q -v ON_ERROR_STOP=1 -U alfresco -d alfresco']
    limit = max(3600, Path(path).stat().st_size // (1024 * 1024))
    with tempfile.TemporaryFile() as errors, gzip.open(path, 'rb') as source:
        process = subprocess.Popen([str(x) for x in args], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors)
        timer = threading.Timer(limit, process.kill)
        timer.start()
        try:
            try:
                shutil.copyfileobj(source, process.stdin, length=1024 * 1024)
                process.stdin.close()
            except BrokenPipeError:
                pass
            if process.wait() != 0:
                errors.seek(0)
                detail = errors.read(2200).decode(errors='replace')
                raise Error(f'Falló la importación SQL: {detail} / SQL restore failed: {detail}')
        finally:
            timer.cancel()
            if process.poll() is None:
                process.kill()
                process.wait()


@step('Restaurar volumen persistente / Restore persistent volume')
def restore_volume(engine, name, path):
    with Path(path).open('rb') as source, tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(['docker', 'run', '--rm', '-i', '--network', 'none', '--entrypoint', 'tar',
            '--mount', 'type=volume,src=' + name + ',dst=/data', engine.cfg['images']['postgres'],
            '--numeric-owner', '-xpf', '-', '-C', '/data'], stdin=source, stdout=errors, stderr=errors)
        try:
            code = process.wait(timeout=max(1800, Path(path).stat().st_size // (5 * 1024 * 1024)))
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait()
            raise Error('La restauración del volumen superó el tiempo límite / Volume restore timed out') from exc
        if code != 0:
            errors.seek(0)
            detail = errors.read(2200).decode(errors='replace')
            raise Error(f'Falló la restauración del volumen: {detail} / Volume restore failed: {detail}')
