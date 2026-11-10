"""Server requirements checked before installing (and disk headroom checked daily).

Measured on Alfresco 26.2: the containers use about 6.7 GiB at rest and may grow to their
10 GiB limits; Linux needs about 1 GiB more. Images take about 11 GiB and an update keeps
the previous ones until «Free up space». Elasticsearch stops indexing at 85 % disk usage.
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
MINIMUM_DISK = 30 * GIB
RECOMMENDED_DISK = 50 * GIB
MINIMUM_DATA_DISK = 5 * GIB
IMAGES = 12 * GIB
SEARCH_LIMIT = 0.85
ARCHITECTURES = ('x86_64', 'amd64', 'aarch64', 'arm64')

MESSAGES = {
    'memory': ('Alfresco necesita al menos 8 GB de RAM; con %s es probable que no pueda arrancar por falta de memoria',
               'Alfresco needs at least 8 GB of RAM; with %s it will probably fail to start for lack of memory'),
    'disk': ('Se necesitan al menos 30 GB libres para las imágenes y los datos; hay %s',
             'At least 30 GB of free disk is needed for images and data; %s available'),
    'data_disk': ('La carpeta de datos necesita al menos 5 GB libres; hay %s',
                  'The data folder needs at least 5 GB free; %s available'),
    'architecture': ('Arquitectura no compatible: %s (se necesita amd64 o arm64)',
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
        rows.append(item('disk', human(disk.free), 'error', 'req_disk_error', human(disk.free)))
    elif (disk.used + IMAGES) / disk.total >= SEARCH_LIMIT:
        rows.append(item('disk', human(disk.free), 'warning', 'req_disk_full'))
    elif disk.free < RECOMMENDED_DISK:
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


def errors(rows):
    """Bilingual messages of the blocking rows, for the command line."""
    return [' / '.join(text % row['value'] for text in MESSAGES[row['key']])
            for row in rows if row['status'] == 'error']


def low_memory(rows):
    """Bilingual warning when RAM is under the 8 GB minimum (installation is still allowed)."""
    return next((' / '.join(text % row['value'] for text in MESSAGES['memory'])
                 for row in rows if row['note'] == 'req_memory_low'), '')


def usage_ratio(path, usage=shutil.disk_usage):
    disk = usage(existing(path))
    return disk.used / disk.total if disk.total else 0.0
