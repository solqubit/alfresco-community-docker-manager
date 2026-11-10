"""Human-readable terminal rows. Machine-readable CLI output stays in cli.py."""
from pathlib import Path
import textwrap

from .core import human
from .config import access_url, applications, image_tag, roles
from .terminal_ui import clean


SERVICES = {'alfresco': 'Alfresco Repository', 'share': 'Alfresco Share', 'postgres': 'PostgreSQL',
            'transform-core-aio': 'Transform Core AIO', 'activemq': 'ActiveMQ', 'elasticsearch': 'Elasticsearch',
            'batch-indexing': 'Batch Indexing',
            'content-app': 'Alfresco Content App', 'control-center': 'Control Center', 'proxy': 'Caddy / HTTPS'}


def service_names(cfg):
    return {role: SERVICES[role] for role in roles(cfg)}


def domain_summary(ui, cfg):
    def addresses(kind):
        values = [access_url(cfg, entry) for entry in cfg['domains'] if entry['kind'] == kind]
        return ', '.join(values[:2]) + (' …' if len(values) > 2 else '') if values else ui.t('domains_not_configured')
    return '\n'.join((ui.t('domains_summary_primary', access_url(cfg)),
                      ui.t('domains_summary_local', addresses('local'))))


def address_kind(entry_or_host):
    """ip or name (local hostname such as alfresco.lan)."""
    import ipaddress
    entry = entry_or_host if isinstance(entry_or_host, dict) else {'host': entry_or_host}
    try:
        ipaddress.IPv4Address(entry['host'])
        return 'ip'
    except ValueError:
        return 'name'


def address_label(ui, cfg, entry):
    """https://alfresco.lan · Local name · Local HTTPS on · Primary."""
    parts = [access_url(cfg, entry), ui.t('address_' + address_kind(entry)),
             ui.t('tls_on' if entry['tls'] != 'off' else 'tls_off')]
    if entry['host'] == cfg['primary']:
        parts.append(ui.t('domains_current_primary'))
    return ' · '.join(parts)


def local_name_notes(ui, name, server_ip, tls):
    """What the administrator still has to do outside the manager for a local address."""
    notes = []
    if address_kind(name) == 'name':
        notes.append(ui.t('local_name_dns', name, server_ip))
        if name.endswith('.local'):
            notes.append(ui.t('local_name_mdns'))
    if tls:
        notes.append(ui.t('local_tls_ca'))
    return notes


def domain_actions(ui):
    rows = []
    for key in ('add_local', 'remove', 'primary', 'local_tls', 'ca', 'network'):
        tone = {'remove': 'warning', 'primary': 'ok', 'ca': 'accent'}.get(key, '')
        rows.append((key, [(ui.t(key), tone), ('  ' + ui.t('domains_tag_' + key), 'dim>')], tone))
    return rows


def local(ui, text):
    """Lifecycle messages are 'español / English'; show only the interface language."""
    parts = str(text).split(' / ')
    if len(parts) != 2:
        return str(text)
    return parts[1] if ui.msg.get('locale') == 'en' else parts[0]


def operation_summary(ui, pending):
    action = pending.get('action', '')
    phase = pending.get('phase', '')
    names = [ui.msg.get('op_' + action, action), ui.msg.get('phase_' + phase, phase)]
    return ui.t('pending_summary', *names)


def update_rows(ui, cfg, available):
    rows = []
    for item in available.get('updates') or []:
        label = (f"Alfresco {cfg['version']} → {item['version']}" if item['version'] != cfg['version']
                 else ui.t('updates_bundle_label', item['version']))
        rows.append(('bundle:' + item['tag'], [(label, 'ok' if not rows else 'accent'),
                                               ('  ' + item['tag'] + ' · ' + ui.t('updates_tag_bundle'), 'dim>')], ''))
    for role, item in (available.get('components') or {}).items():
        if isinstance(item, dict) and item.get('image_changed'):
            rows.append(('component:' + role, [(f'{SERVICES[role]} {item["current"]} → {item["available"]}', ''),
                                               ('  ' + ui.t('updates_tag_component'), 'dim>')], ''))
    return rows


def bundle_detail(ui, item):
    rows = [(ui.t('updates_changes'), 'title')]
    for role, (old, new) in sorted(item['changes'].items()):
        rows.append((f"{SERVICES.get(role, role)}: {old or '—'} → {new or '—'}", 'accent'))
    return rows


def release_rows(ui, bundles):
    result = []
    for index, bundle in enumerate(bundles):
        tag = ui.t('release_latest') if index == 0 else ui.t('release_previous')
        label = f"Alfresco {bundle['version']} · {tag} · {bundle['tag']} · {ui.t('search_' + bundle['search'])}"
        result.append((bundle['version'], label, 'ok' if index == 0 else 'accent'))
    return result


def pair(ui, label, value, tone='accent', width=76):
    """Aligned dim labels and colored values, wrapping paths rather than hiding them."""
    label = ui.msg.get('label_' + label, ui.t(label))
    column = min(25, width // 2)
    label = label if len(label) < column - 1 else label[:column - 3] + '…'
    parts = textwrap.wrap(clean(value), max(12, width - column - 6)) or ['—']
    return [([(label.ljust(column) if i == 0 else ' ' * column, 'dim'), (part, tone)], '')
            for i, part in enumerate(parts)]


def section(ui, name):
    return [('', ''), ('§ ' + ui.t(name), 'title')]


def application_rows(ui, cfg):
    rows = []
    for key, url in applications(cfg).items():
        rows += pair(ui, 'app_' + key, url, 'ok' if key == 'share' else 'accent')
    return rows


def status_rows(ui, data, cfg):
    rows = application_rows(ui, cfg)
    for entry in data.get('access', []):
        rows += pair(ui, 'access_' + entry.get('kind', 'local'), entry.get('url', entry.get('host', '')))
    rows += pair(ui, 'search_engine', ui.t('search_' + cfg['search']))
    state = data.get('status', {})
    rows += pair(ui, 'repository_ready', ui.t('yes' if state.get('ready') else 'no'), 'ok' if state.get('ready') else 'warning')
    if data.get('operation'):
        rows += pair(ui, 'pending_operation', data['operation'].get('action', '—'), 'warning')
    rows += section(ui, 'group_service')
    by_service = {row.get('Service'): row for row in data.get('services', [])}
    for role, name in service_names(cfg).items():
        item = by_service.get(role, {})
        active, health = item.get('State') == 'running', item.get('Health')
        tone = 'warning' if health == 'starting' else 'ok' if active and health != 'unhealthy' else 'error'
        marker = {'ok': '[+]', 'warning': '[!]'}.get(tone, '[✗]')
        version = image_tag(cfg['images'][role]) if role in cfg['images'] else ''
        state_key = 'service_' + ('starting' if health == 'starting' else 'unhealthy' if health == 'unhealthy'
                                else 'running' if active else 'stopped' if item else 'missing')
        rows.append((f'{marker} {name} {version} · {ui.t(state_key)}', tone))
    rows += section(ui, 'paths')
    rows += pair(ui, 'storage', ui.t(cfg.get('storage', 'directory')))
    if cfg.get('storage') == 'directory':
        rows += pair(ui, 'storage_root', cfg.get('storage_root', ''))
    return rows


def credential_rows(ui, result):
    rows = []
    for key, url in (result.get('applications') or {}).items():
        rows += pair(ui, 'app_' + key, url, 'ok' if key == 'share' else 'accent')
    rows += pair(ui, 'admin_user', result.get('user', 'admin'), 'ok')
    rows += pair(ui, 'admin_password', result.get('admin_password', '—'), 'warning')
    rows += [('', ''), (ui.t('admin_password_help'), 'warning')]
    return rows


def result_rows(ui, action, result, state):
    if action in ('status', 'diagnose') and isinstance(result, dict) and 'host' in result:
        host = result.get('host', {})
        rows = status_rows(ui, result.get('state', {}), state.load())
        rows += section(ui, 'server')
        rows += pair(ui, 'os', host.get('os', {}).get('PRETTY_NAME', '—'))
        rows += pair(ui, 'architecture', host.get('architecture', '—'))
        rows += pair(ui, 'memory', human(host.get('memory', 0)))
        rows += pair(ui, 'address', host.get('address', '—'))
        rows += pair(ui, 'disk_free', human(result.get('disk_free', 0)))
        if result.get('last_maintenance'):
            rows += section(ui, 'last_maintenance')
            rows += [(clean(str(result['last_maintenance'].get('checked', ''))), '')]
            rows += [(clean(local(ui, error)), 'warning') for error in result['last_maintenance'].get('errors', [])]
        return rows
    if isinstance(result, dict) and 'admin_password' in result:
        return credential_rows(ui, result)
    if isinstance(result, Path) or isinstance(result, str) and result.startswith('/') and '\n' not in result:
        rows = pair(ui, 'file', result)
        path = Path(result)
        if path.is_file():
            rows += pair(ui, 'size', human(path.stat().st_size))
        return rows
    if isinstance(result, str):
        import re
        if re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', result):
            return pair(ui, 'version', 'Alfresco ' + result, 'ok')
        return [(clean(line), '') for paragraph in result.splitlines() for line in textwrap.wrap(paragraph, 68)]
    # Structured results use translated labels, never JSON or credentials.
    blocked = {'passwords', 'Env', 'Command', 'configuration', 'Config', 'services', 'images'}
    def expand(value, depth=0):
        rows = []
        if isinstance(value, dict):
            for key, item in value.items():
                if key in blocked or any(word in key.casefold() for word in ('password', 'secret', 'token')):
                    continue
                if isinstance(item, (dict, list)) and item:
                    rows += section(ui, key)
                    rows += expand(item, depth + 1)
                elif isinstance(item, bool):
                    rows += pair(ui, key, ui.t('yes' if item else 'no'), 'ok' if item else 'dim')
                elif item not in (None, '', [], {}):
                    shown = human(item) if key.endswith(('_free', '_bytes')) and isinstance(item, int) else str(item)
                    rows += pair(ui, key, shown)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, (dict, list)):
                    rows += expand(item, depth + 1)
                else:
                    for line in textwrap.wrap(clean(local(ui, item)), 68):
                        rows.append(('· ' + line, 'warning' if depth == 1 and action == 'maintenance' else ''))
        else:
            rows += [(clean(value), '')]
        return rows
    return expand(result) or [(ui.t('operation_done'), 'ok')]
