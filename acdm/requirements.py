"""Server requirements checked before installing (disk headroom is also part of the health checks).

Measured on Alfresco 26.2: the containers use about 6.7 GiB at rest and may grow to their
10 GiB limits; Linux needs about 1 GiB more. The images take about 8 GiB unpacked plus about
3 GiB of compressed copies (Docker 29 containerd store): about 12 GiB with data and logs. An
update keeps the previous images until «Free up space» (up to about 10 GiB more). The
pre-update backup is only the SQL dump and certificates. Elasticsearch stops indexing at 85 %
disk usage, so the disk must stay below it.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil

from .core import human

GIB = 1024 ** 3
# A server sold with 8 GB reports roughly 7.6-7.8 GiB to Linux; accept it as 8 GB.
MINIMUM_MEMORY = int(7.5 * GIB)
RECOMMENDED_MEMORY = 15 * GIB
# Room for the installation itself (about 12 GiB) plus a margin.
MINIMUM_DISK = 15 * GIB
# Below this, Alfresco fits but an update needs «Free up space» first. A 50 GB disk is recommended.
COMFORTABLE_DISK = 35 * GIB
MINIMUM_DATA_DISK = 5 * GIB
IMAGES = 12 * GIB
UPDATE_MARGIN = 2 * GIB
SEARCH_LIMIT = 0.85
ARCHITECTURES = ('x86_64', 'amd64', 'aarch64', 'arm64')

# Bilingual command-line messages, by the note of a blocking or low-memory row.
MESSAGES = {
    'req_memory_low': ('Alfresco necesita al menos 8 GB de RAM; con %s es probable que no pueda arrancar por falta de memoria',
                       'Alfresco needs at least 8 GB of RAM; with %s it will probably fail to start for lack of memory'),
    'req_disk_error': ('Se necesitan al menos 15 GB libres para instalar Alfresco; hay %s',
                       'At least 15 GB of free disk is needed to install Alfresco; %s available'),
    'req_disk_full': ('Tras instalar, el disco quedaría por encima del 85 %% (%s libres) y la búsqueda no podría indexar',
                      'After installing the disk would exceed 85 %% (%s free) and search could not index'),
    'req_data_disk_error': ('La carpeta de datos necesita al menos 5 GB libres; hay %s',
                            'The data folder needs at least 5 GB free; %s available'),
    'req_architecture_error': ('Arquitectura no compatible: %s (se necesita amd64 o arm64)',
                               'Unsupported architecture: %s (amd64 or arm64 required)'),
}


def existing(path):
    """Nearest existing folder, so a data folder not yet created can be measured."""
    path = Path(path)
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def item(key, value, status, note='', *values):
    return {'key': key, 'value': value, 'status': status, 'note': note, 'values': list(values)}


def evaluate(memory, cpus, architecture, docker_root, data_root=None, usage=shutil.disk_usage, device=None):
    """Requirement rows: status is ok, warning (works with limits) or error (blocks installation)."""
    device = device or (lambda path: os.stat(path).st_dev)
    rows = []
    if not memory:
        rows.append(item('memory', '?', 'warning', 'req_memory_unknown'))
    elif memory < MINIMUM_MEMORY:
        # Not blocking: the administrator decides, but is told Alfresco will probably not start.
        rows.append(item('memory', human(memory), 'warning', 'req_memory_low'))
    elif memory < RECOMMENDED_MEMORY:
        rows.append(item('memory', human(memory), 'warning', 'req_memory_warning'))
    else:
        rows.append(item('memory', human(memory), 'ok', 'req_memory_ok'))
    rows.append(item('cpu', str(cpus or '?'), 'ok' if (cpus or 0) >= 2 else 'warning',
                     'req_cpu_ok' if (cpus or 0) >= 2 else 'req_cpu_warning'))
    rows.append(item('architecture', architecture or '?', 'ok' if architecture in ARCHITECTURES else 'error',
                     'req_architecture_ok' if architecture in ARCHITECTURES else 'req_architecture_error'))
    root = existing(docker_root)
    disk = usage(root)
    if disk.free < MINIMUM_DISK:
        rows.append(item('disk', human(disk.free), 'error', 'req_disk_error'))
    elif (disk.used + IMAGES) / disk.total >= SEARCH_LIMIT:
        # Elasticsearch would refuse to index from the first day.
        rows.append(item('disk', human(disk.free), 'error', 'req_disk_full'))
    elif disk.free < COMFORTABLE_DISK:
        rows.append(item('disk', human(disk.free), 'warning', 'req_disk_warning'))
    else:
        rows.append(item('disk', human(disk.free), 'ok', 'req_disk_ok'))
    if data_root:
        data = existing(data_root)
        if device(data) != device(root):
            free = usage(data).free
            rows.append(item('data_disk', human(free), 'error' if free < MINIMUM_DATA_DISK else 'ok',
                             'req_data_disk_error' if free < MINIMUM_DATA_DISK else 'req_data_disk_ok'))
    return rows


def message(row):
    return ' / '.join(text % row['value'] for text in MESSAGES[row['note']])


def errors(rows):
    """Bilingual messages of the blocking rows, for the command line."""
    return [message(row) for row in rows if row['status'] == 'error']


def low_memory(rows):
    """Bilingual warning when RAM is under the 8 GB minimum (installation is still allowed)."""
    return next((message(row) for row in rows if row['note'] == 'req_memory_low'), '')


def update_space(path, needed, usage=shutil.disk_usage):
    """(enough, free) for downloading `needed` bytes of new images while staying below 85 %."""
    disk = usage(existing(path))
    enough = disk.free >= needed + UPDATE_MARGIN and (disk.used + needed) / disk.total < SEARCH_LIMIT
    return enough, disk.free


def usage_ratio(path, usage=shutil.disk_usage):
    disk = usage(existing(path))
    return disk.used / disk.total if disk.total else 0.0
