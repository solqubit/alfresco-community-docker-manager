"""Alfresco Community release bundles from the official acs-deployment repository.

Each acs-deployment release publishes docker-compose/community-compose.yaml with the
exact images Alfresco tested together. A bundle is that set of images (plus Caddy).
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import time
import urllib.error
import urllib.request

from .config import REPOSITORIES, check_image, engine_of, image_parts, image_tag, normalize_image, validate_images
from .core import Error, ROOT, atomic, read_json, version_tuple

RELEASES = 'https://api.github.com/repos/Alfresco/acs-deployment/releases?per_page=40'
COMPOSE = 'https://raw.githubusercontent.com/Alfresco/acs-deployment/{tag}/docker-compose/community-compose.yaml'
PACKAGED = ROOT / 'data' / 'bundles.json'
PROXY_SERIES = '2'


def fetch(url, headers=None, timeout=40):
    request = urllib.request.Request(url, headers={'User-Agent': 'Alfresco-Community-Docker-Manager/0.1', **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except (OSError, urllib.error.URLError) as exc:
        raise Error(f'No se pudo consultar / Cannot retrieve: {url.split("?")[0]}') from exc


def parse_compose(text):
    """Images of the services this manager runs, by role; Kibana and Traefik are left out."""
    lookup = {repository: role for role, values in REPOSITORIES.items() for repository in values if role != 'proxy'}
    images = {}
    for ref in re.findall(r'^\s*image:\s*["\']?([^\s"\'#]+)', text, re.M):
        ref = normalize_image(ref)
        repository = image_parts(ref)[0]
        role = lookup.get(repository)
        if role:
            images[role] = ref
    return images


def bundle_key(bundle):
    """Order bundles by Alfresco version, then by Share version and release tag."""
    def numbers(value):
        return tuple(int(part) for part in re.findall(r'[0-9]+', value)[:4])
    return (version_tuple(bundle['version']), numbers(image_tag(bundle['images']['share'])), numbers(bundle['tag']))


def check_bundle(bundle):
    version_tuple(bundle['version'])
    if image_tag(bundle['images']['alfresco']) != bundle['version']:
        raise Error('Bundle version mismatch')
    validate_images(bundle['images'], pinned=False)
    return bundle


class Catalog:
    def __init__(self, cache):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)

    def get(self, key, url, ttl=900):
        path = self.cache / (key + '.json')
        if path.exists() and time.time() - path.stat().st_mtime < ttl:
            return read_json(path)
        value = json.loads(fetch(url))
        atomic(path, value)
        return value

    def bundle(self, tag):
        """Release bundles are immutable: cache them without expiry."""
        if not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', tag):
            raise Error('Invalid release tag')
        path = self.cache / f'bundle-{tag}.json'
        if path.exists():
            try:
                return check_bundle(read_json(path))
            except (Error, KeyError, TypeError, ValueError):
                path.unlink()
        images = parse_compose(fetch(COMPOSE.format(tag=tag)).decode('utf-8', errors='replace'))
        images['proxy'] = 'caddy:' + PROXY_SERIES
        bundle = {'tag': tag, 'version': image_tag(images.get('alfresco', 'x:0')), 'images': images,
                  'search': None}
        try:
            bundle['search'] = engine_of(images)
            check_bundle(bundle)
        except (Error, KeyError) as exc:
            raise Error(f'Paquete no compatible: {tag} / Unsupported bundle: {tag}') from exc
        atomic(path, bundle)
        return bundle

    def packaged(self):
        """Bundles shipped with the manager, used when GitHub is unreachable."""
        values = read_json(PACKAGED)['bundles']
        return sorted((check_bundle(dict(value)) for value in values), key=bundle_key, reverse=True)

    def bundles(self, *, refresh=False, limit=6):
        """Newest bundle of each Alfresco version, newest first. Falls back to the packaged list."""
        try:
            releases = self.get('releases', RELEASES, ttl=0 if refresh else 3600)
            tags = [item['tag_name'] for item in releases
                    if not item.get('draft') and not item.get('prerelease')
                    and re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', item.get('tag_name', ''))]
            found = {}
            for tag in tags[:16]:
                try:
                    bundle = self.bundle(tag)
                except Error:
                    continue
                best = found.get(bundle['version'])
                if best is None or bundle_key(bundle) > bundle_key(best):
                    found[bundle['version']] = bundle
                if len(found) >= limit:
                    break
            if not found:
                raise Error('No bundles found')
            result = sorted(found.values(), key=bundle_key, reverse=True)
            return [dict(item, stale=False) for item in result]
        except (Error, OSError, ValueError, KeyError, TypeError):
            return [dict(item, stale=True) for item in self.packaged()]

    def latest_bundle(self, *, refresh=False):
        return self.bundles(refresh=refresh)[0]

    def find(self, version):
        """The newest bundle for an exact Alfresco version (X.Y.Z)."""
        version_tuple(version)
        for bundle in self.bundles():
            if bundle['version'] == version:
                return bundle
        raise Error(f'No hay un paquete oficial para Alfresco {version} / No official bundle for Alfresco {version}')

    def latest_proxy(self):
        """Newest stable Caddy 2.x.y tag from Docker Hub; the series tag when unavailable."""
        try:
            data = self.get('caddy-tags', 'https://hub.docker.com/v2/repositories/library/caddy/tags?page_size=100&name=2.')
            versions = [row['name'] for row in data.get('results', [])
                        if re.fullmatch(r'2\.[0-9]+\.[0-9]+', row.get('name', ''))]
            if versions:
                return 'caddy:' + max(versions, key=version_tuple)
        except (Error, ValueError, KeyError, TypeError):
            pass
        return 'caddy:' + PROXY_SERIES

    def postgres_patch(self, ref):
        """Newest postgres X.Y of the same major as ref, or ref itself."""
        tag = image_tag(ref)
        major = tag.split('.')[0]
        if not major.isdigit():
            return normalize_image(ref).split('@')[0]
        try:
            data = self.get(f'postgres-{major}', f'https://hub.docker.com/v2/repositories/library/postgres/tags?page_size=100&name={major}.')
            versions = [row['name'] for row in data.get('results', [])
                        if re.fullmatch(rf'{major}\.[0-9]+', row.get('name', ''))]
            if versions:
                best = max(versions, key=lambda v: tuple(map(int, v.split('.'))))
                current = tuple(map(int, tag.split('.'))) if re.fullmatch(r'[0-9]+\.[0-9]+', tag) else (0,)
                if tuple(map(int, best.split('.'))) > current:
                    return 'postgres:' + best
        except (Error, ValueError, KeyError, TypeError):
            pass
        return 'postgres:' + tag


def same_major(a, b):
    return image_tag(a).split('.')[0] == image_tag(b).split('.')[0]


__all__ = ['Catalog', 'fetch', 'parse_compose', 'bundle_key', 'check_bundle', 'check_image', 'same_major']
