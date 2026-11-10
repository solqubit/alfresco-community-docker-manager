"""Keyboard workflows: drafts, reviews, status cards and lifecycle operations."""
from __future__ import annotations

import copy
import ipaddress
from pathlib import Path
import tarfile
import traceback
from zoneinfo import ZoneInfo, available_timezones

from .config import DEFAULT_HTTP_PORT, DEFAULT_HTTPS_PORT, access_url, host, image_tag, port, prefer_first_public, public_host
from .console import Console
from .core import Cancelled, Error
from .dashboard import dashboard
from .docker import Engine
from .i18n import messages, remember_language, remembered_language
from .operations import AdminAuthError
from .system import host_info, host_timezone
from .terminal_ui import INFO, SEPARATOR, Terminal
from .views import (SERVICES, address_kind, address_label, bundle_detail, domain_actions, domain_summary, local_name_notes,
                    operation_summary, pair, release_rows, result_rows, service_names, update_rows)


def folder(value):
    path = Path(value).expanduser()
    if not path.is_absolute() or any(ord(char) < 32 for char in value):
        raise Error('Use an absolute folder / Usa una carpeta absoluta')
    return str(path)


class Form:
    """In-memory drafts. Escape never writes configuration or changes services."""
    def __init__(self, console, title, data, fields, *, installed=False, submit=None, bundles=None):
        self.view, self.ui, self.title = console, console.term, title
        # Official release bundles, newest first; an older one demonstrates updates.
        self.bundles = bundles or []
        self.data, self.original = copy.deepcopy(data), copy.deepcopy(data)
        self._fields, self.installed = fields, installed
        self.submit = submit
        self.selected, self.invalid = 0, ''
        self.notice, self.notice_style = self.ui.t('install_form_intro' if submit == 'install' else 'form_intro'), 'accent'
        self.reviewing = False

    @property
    def fields(self):
        return [key for key in self._fields if key != 'storage_root' or self.data.get('storage') != 'named']

    def display(self, key, value=None):
        value = self.data[key] if value is None else value
        if key in ('environment', 'storage', 'publish', 'local_tls'):
            return self.ui.t(('environment_' + value) if key == 'environment' else
                             ('yes' if value else 'no') if key in ('publish', 'local_tls') else value)
        return str(value) if value != '' else self.ui.t('optional' if key == 'email' else 'pending_value')

    def normalize(self, key, value):
        if key.endswith('_port'):
            return port(value)
        if key == 'bind':
            return str(ipaddress.IPv4Address(value))
        if key in ('address', 'name'):
            return (public_host if key == 'name' and self.data.get('public') else host)(value)
        if key == 'timezone':
            ZoneInfo(value)
        if key == 'storage_root':
            return folder(value)
        if key == 'email' and value and ('@' not in value or any(char.isspace() for char in value)):
            raise Error(self.ui.t('invalid_email'))
        return value

    def problem(self, key):
        try:
            self.normalize(key, str(self.data[key]))
        except (Error, ValueError, OSError, KeyError):
            return True
        return key == self.invalid or key in ('http_port', 'https_port') and self.data.get('http_port') == self.data.get('https_port')

    def rows(self, review=False):
        if self.ui.screen is not None:
            self.ui.measure()
        width = getattr(self.ui, 'width', 76)
        names = {key: self.ui.msg.get('field_' + key, self.ui.t(key)) for key in self.fields}
        column = min(width // 2 - 4, max(len(name) for name in names.values()) + 3)
        rows = []
        for key in self.fields:
            invalid = self.problem(key)
            pending = key == 'name' and not self.data[key] and self.invalid != key
            changed = self.data[key] != self.original[key]
            state = self.ui.t('pending_value' if pending else 'field_invalid' if invalid else 'field_changed' if changed else
                              'field_current' if self.installed else 'field_default')
            tone = 'warning' if pending else 'error' if invalid else 'ok' if changed else 'dim'
            # [+] marks only what this draft changed; untouched values stay neutral.
            marker = '[!]' if pending else '[✗]' if invalid else '[+]' if changed else '[·]'
            value = self.display(key)
            room = max(4, width - column - len(state) - 10)
            if len(value) > room:
                value = value[:room - 1] + '…'
            label = [(marker + ' ', 'warning' if pending else 'error' if invalid else 'ok' if changed else 'dim'),
                     (names[key][:column - 2].ljust(column), ''),
                     (value, 'warning' if pending else 'error' if invalid else 'accent'),
                     ('  ' + state, tone + '>')]
            rows.append((key, label, 'error' if invalid and not pending else ''))
        rows += [(SEPARATOR, '', '')]
        action = 'apply' if review or self.submit else 'review'
        label = self.submit or ('apply_changes' if self.installed else 'deploy' if review else 'review')
        rows += [(action, '▶ ' + self.ui.t(label), 'ok')]
        if review:
            rows.append(('edit', '← ' + self.ui.t('return_edit'), 'accent'))
        rows.append(('cancel', '← ' + self.ui.t('cancel'), ''))
        return rows

    def timezone(self):
        zones = sorted(available_timezones())
        while True:
            areas = sorted({zone.split('/')[0] for zone in zones if '/' in zone})
            suggested = self.data.get('timezone', 'UTC')
            area = self.view.pick('timezone_area', [('=' + suggested, '★ ' + suggested, 'ok'), ('UTC', 'UTC', '')]
                                  + [(value, value, '') for value in areas], search=True)
            if area is None or area == 'UTC':
                return area
            if area.startswith('='):
                return area[1:]
            value = self.view.pick('timezone_city', [(zone, zone, '') for zone in zones if zone.startswith(area + '/')],
                                   search=True)
            if value is not None:
                return value

    def edit(self, key):
        data, value = self.data, None
        if key in ('environment', 'storage', 'publish', 'local_tls'):
            values = {'environment': ['lan', 'vps'], 'storage': ['directory', 'named'], 'publish': [False, True],
                      'local_tls': [False, True]}[key]
            value = self.view.pick('field_local_tls' if key == 'local_tls' else key,
                                   [(v, self.display(key, v), 'warning' if key == 'publish' and v else '')
                                    for v in values], selected=values.index(data[key]),
                                   help_text=self.ui.t('environment_help' if key == 'environment' else
                                                       'field_help_local_tls' if key == 'local_tls' else ''))
        elif key == 'version':
            value = self.view.pick('version', release_rows(self.ui, self.bundles),
                                   selected=next((i for i, b in enumerate(self.bundles) if b['version'] == data['version']), 0),
                                   help_text=self.ui.t('field_help_version'))
        elif key == 'timezone':
            value = self.timezone()
        elif key in ('bind', 'address'):
            choices = list(dict.fromkeys([data[key], host_info()['address'], '127.0.0.1']))
            if key == 'bind' and self.installed:
                choices.append('0.0.0.0')
            choices = list(dict.fromkeys(choices))
            value = self.view.pick(key, [(v, v, '') for v in choices if v != '0.0.0.0' or key == 'bind']
                                   + [('manual', self.ui.t('manual_value'), 'accent')])
            if value == 'manual':
                value = self.view.edit(key, data[key], lambda text: self.normalize(key, text),
                                       hint=self.ui.t('field_help_' + key), default=self.original[key])
        else:
            preview = None
            if key == 'name' and not data.get('public'):
                def preview(text):
                    # Tell apart an IP from a local name while it is typed.
                    try:
                        name = host(text)
                    except Error:
                        return ''
                    kind = address_kind(name)
                    hint = self.ui.t('preview_' + kind)
                    return hint + (' ' + self.ui.t('local_name_mdns') if kind == 'name' and name.endswith('.local') else '')
            elif key in ('http_port', 'https_port'):
                def preview(text):
                    try:
                        number = port(text)
                        return self.ui.t('https_reserved', text) if key == 'https_port' else self.ui.t(
                            'access_preview', f'http://{data.get("address", data.get("bind", "127.0.0.1"))}' +
                            (f':{number}' if number != 80 else ''))
                    except (Error, ValueError):
                        return ''
            value = self.view.edit(key, data[key], lambda text: self.normalize(key, text),
                                   hint=self.ui.t('field_help_' + key), default=self.original[key], preview=preview)
        if value is not None:
            self.data[key] = value
            self.invalid = ''
            self.notice, self.notice_style = self.ui.t('field_saved', self.ui.msg.get('field_' + key, self.ui.t(key))), 'ok'
            if key == 'environment' and value == 'vps' and (data.get('bind'), data.get('address')) != ('127.0.0.1',) * 2:
                self.data.update(bind='127.0.0.1', address='127.0.0.1')
                self.notice = self.ui.t('vps_loopback_set')

    def field_help(self, key):
        if key in self.fields:
            full = self.display(key)
            hint = self.ui.msg.get('field_help_' + key, self.ui.t('form_edit_hint'))
            return full + ' · ' + hint
        if key == 'apply' and self.submit == 'install':
            return self.ui.t('install_submit_help')
        return self.ui.t('review_help') if key in ('review', 'apply') else self.ui.t('form_cancel_hint')

    def run(self):
        while True:
            if 'version' in self.data:
                self.ui.context = f'Alfresco Community {self.data["version"]}'
            review = self.reviewing
            def screen():
                rows = self.rows(review)
                return self.ui.menu(self.ui.t('review' if review else self.title), rows,
                                    len(self.fields) + 1 if review else self.selected,
                                    notice=self.ui.t('review_help') if review else self.notice,
                                    notice_style='accent' if review else self.notice_style,
                                    help_text=self.field_help)
            with self.view.prompt():
                choice = self.ui.run(screen)
            if choice is None or choice in ('cancel', 'edit'):
                if review:
                    self.reviewing = False
                    if choice != 'cancel':
                        continue
                if choice == 'edit':
                    continue
                if self.data != self.original:
                    leave = self.view.pick('discard_changes', [
                        ('edit', '← ' + self.ui.t('return_edit'), 'accent'),
                        ('discard', self.ui.t('discard'), 'warning')])
                    if leave != 'discard':
                        continue
                return None
            if choice in ('review', 'apply'):
                invalid = next((key for key in self.fields if self.problem(key)), '')
                if invalid:
                    self.invalid, self.selected = invalid, self.fields.index(invalid)
                    self.notice, self.notice_style = self.ui.t('invalid_field', self.ui.t(invalid)), 'error'
                    self.reviewing = False
                    continue
                if choice == 'apply':
                    return copy.deepcopy(self.data)
                self.reviewing = True
                continue
            self.selected, self.reviewing = self.fields.index(choice), False
            self.edit(choice)


class Controller:
    def __init__(self, manager, language=None, console=None):
        self.manager, self.state = manager, manager.state
        self.language = language or remembered_language(self.state) or 'es'
        self.view = console or Console(Terminal(messages(self.language)), self.state)
        self.term = self.view.term
        self.last = self.notice = ''
        self.notice_style, self.snapshot = 'accent', None

    def pick(self, title, keys, **kwargs):
        return self.view.pick(title, [(key, self.term.t(key), '') for key in keys], **kwargs)

    def menu(self, title, rows, *, help_text='', **kwargs):
        """Submenus end with an explicit Back row; Back and Esc both return None."""
        rows = list(rows) + [(SEPARATOR, '', ''), ('back', '← ' + self.term.t('back'), '')]
        def describe(key):
            if key == 'back':
                return self.term.t('back_help')
            return help_text(key) if callable(help_text) else help_text
        choice = self.view.pick(title, rows, help_text=describe, **kwargs)
        return None if choice == 'back' else choice

    def notice_for(self, text, style='accent'):
        if not self.notice:
            return text, style
        notice, tone = self.notice + ('\n' + text if text else ''), self.notice_style
        self.notice = ''
        return notice, tone

    def report(self, action, data=None, *, notice='', style='ok'):
        if isinstance(data, dict) and (data.get('errors') or data.get('warnings')):
            style = 'warning'
            notice = self.term.t('checks_warning')
        rows = result_rows(self.term, action, data, self.state)
        if isinstance(data, dict) and 'admin_password' in data:
            notice, style = self.term.t('install_done' if action in ('install', 'recover') else 'credentials_notice'), 'ok'
            if action == 'install' and self.state.configured:
                cfg = self.state.load()
                if cfg['environment'] == 'vps':
                    rows += pair(self.term, 'ssh_forward', f'ssh -L 8080:{cfg["bind"]}:{cfg["http_port"]} usuario@servidor')
                # HTTPS is added once Alfresco works: a domain needs DNS and open ports first.
                rows += [('', ''), ('→ ' + self.term.t('install_next_https'), 'accent')]
        rows += pair(self.term, 'private_log', self.state.log_path, 'dim')
        self.view.view(action, rows, notice=notice or self.term.t('operation_done'), style=style)
        self.notice, self.notice_style = notice or self.term.t('operation_done'), style

    def perform(self, action, function, *, context=None, notice='', style='ok'):
        result = self.view.run(action, function, context=context)
        self.report(action, result, notice=notice, style=style)
        return result

    def attempt(self, function):
        try:
            return function()
        except (Cancelled, KeyboardInterrupt, EOFError):
            self.notice, self.notice_style = self.term.t('cancelled'), 'warning'
            if self.state.pending:
                self.notice = self.term.t('pending')
        except (Error, OSError, ValueError, tarfile.TarError, KeyError) as exc:
            with self.state.log_path.open('a', encoding='utf-8') as log:
                traceback.print_exc(file=log)
            self.view.error(exc, pending=bool(self.state.pending))
            self.notice, self.notice_style = self.term.t('operation_failed'), 'error'

    def review_requirements(self):
        """Clear view of the server before installing; errors block, warnings only inform."""
        rows = self.view.run('check_requirements', self.manager.requirements)
        markers = {'ok': '[+]', 'warning': '[!]', 'error': '[✗]'}
        lines = []
        for row in rows:
            label = self.term.t('req_' + row['key'])
            note = self.term.t(row['note'], *row['values']) if row['note'] else ''
            lines.append((INFO, [(markers[row['status']] + ' ', row['status']), (label.ljust(22), ''),
                                 (row['value'].ljust(12), 'accent'), (note, 'dim' if row['status'] == 'ok' else row['status'])],
                          row['status']))
        blocked = any(row['status'] == 'error' for row in rows)
        warned = any(row['status'] == 'warning' for row in rows)
        low_memory = any(row['note'] == 'req_memory_low' for row in rows)
        actions = [('back', '← ' + self.term.t('cancel'), 'accent')]
        if not blocked:
            actions.insert(0, ('continue', '▶ ' + self.term.t('requirements_continue'), 'ok'))
        notice = self.term.t('requirements_blocked' if blocked else 'requirements_memory' if low_memory
                             else 'requirements_warning' if warned else 'requirements_ok')
        # With too little RAM the cursor starts on Cancel: continuing must be a deliberate choice.
        selected = len(lines) + (2 if low_memory and not blocked else 1)
        choice = self.view.pick('requirements', lines + [(SEPARATOR, '', '')] + actions, selected=selected,
                                notice=notice, notice_style='error' if blocked else 'warning' if warned else 'ok',
                                help_text=self.term.t('requirements_help'))
        return choice == 'continue'

    def install(self):
        if not self.review_requirements():
            return
        environment = self.pick('environment', ('lan', 'vps'), help_text=self.term.t('environment_help'))
        if environment is None:
            return
        bundles = self.view.run('lookup_versions', self.manager.installation_options)
        info = host_info()
        address = info['address'] if environment == 'lan' else '127.0.0.1'
        data = dict(version=bundles[0]['version'], environment=environment, bind=address, address=address,
                    http_port=DEFAULT_HTTP_PORT, https_port=DEFAULT_HTTPS_PORT, timezone=host_timezone(),
                    storage='directory', storage_root=str(self.state.path / 'data'))
        form = Form(self.view, 'configure', data, tuple(data), submit='install', bundles=bundles)
        notes = []
        if bundles[0].get('stale'):
            notes.append(self.term.t('catalog_offline'))
        if info['panel_detected']:
            notes.append(self.term.t('ports_existing'))
        if notes:
            form.notice, form.notice_style = '\n'.join(notes), 'warning'
        while True:
            draft = form.run()
            if draft is None:
                return
            try:
                cfg = dict(draft, id='ui-preflight', project='ui-preflight')
                self.view.run('check_ports', lambda: Engine(self.state, cfg).check_ports())
            except (Error, OSError, ValueError) as exc:
                field = next((key for key in ('http_port', 'https_port') if str(draft[key]) in str(exc)), 'bind')
                form.invalid, form.selected = field, form.fields.index(field)
                form.notice, form.notice_style, form.reviewing = self.view.local(str(exc)), 'error', False
                continue
            version = draft.pop('version')
            bundle = next(b for b in bundles if b['version'] == version)
            local_url = f'http://{draft["address"]}' + (f':{draft["http_port"]}' if draft['http_port'] != 80 else '')
            try:
                self.perform('install', lambda: self.manager.install(version, language=self.language, **draft),
                             context=[('Alfresco Community', f'{version} ({bundle["tag"]})'),
                                      (self.term.t('search_engine'), self.term.t('search_' + bundle['search'])),
                                      ('PostgreSQL', image_tag(bundle['images']['postgres'])),
                                      (self.term.t('access_local'), local_url + '/share/')])
            except Cancelled:
                if self.state.configured or self.state.pending:
                    raise
                form.notice, form.notice_style = self.term.t('install_cancelled'), 'accent'
                continue
            return

    def domains(self):
        while True:
            cfg = self.state.load()
            notice, tone = self.notice_for(domain_summary(self.term, cfg))
            action = self.menu('domains', domain_actions(self.term), notice=notice, notice_style=tone,
                               help_text=lambda key: self.term.t('domains_help_' + key))
            if action is None:
                return
            self.attempt(lambda: self.domain_action(action, cfg))

    def domain_action(self, action, cfg):
        if action.startswith('add_'):
            mode = action.removeprefix('add_')
            if not self.domain_directory(action, mode):
                return
            cfg = self.state.load()
            fields = ['name'] + (['email', 'external_port'] if mode != 'local' else ['local_tls'])
            if mode == 'direct':
                fields.append('publish')
            data = dict(name='', public=mode != 'local', email='', external_port=443, publish=False, local_tls=False)
            form = Form(self.view, 'domain_new_' + mode, data, fields, submit='save_access')
            form.notice = self.term.t('domain_new_local_help' if mode == 'local' else 'domain_new_help')
            if prefer_first_public(cfg, mode):
                form.notice += '\n' + self.term.t('public_first_primary')
            draft = form.run()
            if draft is None:
                return
            self.view.run(action, lambda: self.manager.add_domain(draft['name'], mode=mode, email=draft['email'],
                          tls='internal' if mode == 'local' and draft['local_tls'] else 'off',
                          external_port=draft['external_port'], publish_direct=draft['publish']),
                          context=[(self.term.t('name'), draft['name'])])
            name = draft['name']
            url = access_url(self.state.load(), next(entry for entry in self.state.load()['domains'] if entry['host'] == name))
            note = self.term.t('tunnel_help' if mode == 'tunnel' else 'direct_help' if mode == 'direct' else 'domain_added')
            if mode == 'tunnel':
                forward = '127.0.0.1' if cfg['bind'] == '0.0.0.0' else cfg['bind']
                url += f'\nHost: {name}\nForward: http://{forward}:{cfg["http_port"]}'
                if self.state.load()['primary'] == name:
                    note = self.term.t('tunnel_primary_saved')
            style = 'ok'
            if mode == 'local':
                server = cfg['bind'] if cfg['bind'] not in ('0.0.0.0', '127.0.0.1') else host_info()['address']
                notes = local_name_notes(self.term, name, server, draft['local_tls'])
                if notes:
                    note, style = '\n'.join([note] + notes), 'warning' if address_kind(name) == 'name' else 'ok'
            if mode != 'local' and not self.view.run('check_certificate', lambda: self.manager.wait_certificate(name)):
                note, style = note + '\n' + self.term.t('certificate_pending'), 'warning'
            self.report(action, url, notice=note, style=style)
        elif action == 'network':
            keys = ('bind', 'http_port', 'https_port')
            form = Form(self.view, 'network', {key: cfg[key] for key in keys}, keys, installed=True, submit='apply_changes')
            while True:
                draft = form.run()
                if draft is None:
                    return
                candidate = copy.deepcopy(cfg)
                candidate.update(draft)
                try:
                    self.view.run('check_ports', lambda: Engine(self.state, candidate).check_ports(cfg))
                except (Error, OSError, ValueError) as exc:
                    field = next((key for key in keys[1:] if str(draft[key]) in str(exc)), 'bind')
                    form.invalid, form.selected = field, keys.index(field)
                    form.notice, form.notice_style, form.reviewing = self.view.local(str(exc)), 'error', False
                    continue
                self.perform(action, lambda: self.manager.settings(candidate))
                return
        elif action == 'ca':
            self.perform(action, lambda: Engine(self.state).ca_certificate(), notice=self.term.t('ca_help'))
        else:
            entries = [entry for entry in cfg['domains'] if action != 'local_tls' or entry['kind'] == 'local']
            if not entries:
                return self.report(action, self.term.t('no_addresses'), style='warning')
            name = self.view.pick(action, [
                (entry['host'], address_label(self.term, cfg, entry), 'ok' if entry['host'] == cfg['primary'] else '')
                for entry in entries],
                selected=next((i for i, entry in enumerate(entries) if entry['host'] == cfg['primary']), 0),
                notice=self.term.t('local_tls_notice') if action == 'local_tls' else '')
            if name is None:
                return
            if action == 'remove':
                self.perform(action, lambda: self.manager.remove_domain(name), context=[(self.term.t('name'), name)])
            else:
                if action == 'primary':
                    cfg['primary'] = name
                else:
                    entry = next(entry for entry in cfg['domains'] if entry['host'] == name)
                    entry['tls'] = 'internal' if entry['tls'] == 'off' else 'off'
                self.perform(action, lambda: self.manager.settings(cfg), context=[(self.term.t('name'), name)])

    def domain_directory(self, action, mode):
        while True:
            cfg = self.state.load()
            entries = [entry for entry in cfg['domains'] if entry.get('mode', 'local') == mode]
            rows = []
            for entry in entries:
                rows.append(('entry:' + entry['host'], address_label(self.term, cfg, entry), 'ok'))
            if rows:
                rows.append((SEPARATOR, '', ''))
            rows.append(('new', '+ ' + self.term.t('domain_new_' + mode), 'accent'))
            choice = self.menu(action, rows, notice=self.term.t('domain_directory_help' if entries else 'domain_directory_empty'))
            if choice is None:
                return False
            if choice == 'new':
                return True
            self.domain_details(choice.removeprefix('entry:'))

    def domain_details(self, name):
        while True:
            cfg = self.state.load()
            entry = next((entry for entry in cfg['domains'] if entry['host'] == name), None)
            if entry is None:
                return
            rows = [] if cfg['primary'] == name else [('primary', self.term.t('domain_make_primary'), 'ok')]
            rows.append(('remove', self.term.t('remove'), 'warning'))
            notice = self.term.t('domain_saved', address_label(self.term, cfg, entry))
            choice = self.menu('domain_details', rows, notice=notice)
            if choice is None:
                return
            if choice == 'remove':
                self.perform('remove', lambda: self.manager.remove_domain(name), context=[(self.term.t('name'), name)])
                return
            cfg['primary'] = name
            self.perform('primary', lambda: self.manager.settings(cfg), context=[(self.term.t('name'), name)])

    def updates(self):
        while True:
            cfg = self.state.load()
            available = self.view.run('lookup_updates', self.manager.checks)
            rows = update_rows(self.term, cfg, available)
            if not rows:
                self.notice = ''
                note = self.term.t('catalog_offline') if available.get('catalog_offline') else self.term.t('updates_none')
                return self.report('updates', {'current': available['current'], 'bundle': available['bundle'],
                                               'latest': available['latest']}, notice=note, style='ok')
            notice, tone = self.notice_for(self.term.t('updates_scope'))
            def describe(key):
                kind, _, target = key.partition(':')
                if kind == 'component':
                    return self.term.t('components_help')
                item = next(item for item in available['updates'] if item['tag'] == target)
                return ', '.join(f"{role} {old or '—'}→{new or '—'}" for role, (old, new) in sorted(item['changes'].items()))
            choice = self.menu('updates', rows, notice=notice, notice_style=tone, help_text=describe)
            if choice is None:
                self.notice = ''
                return
            self.attempt(lambda: self.apply_update(choice, cfg, available))

    def apply_update(self, choice, cfg, available):
        kind, _, target = choice.partition(':')
        if kind == 'component':
            return self.perform('components', lambda: self.manager.update_component(target),
                                context=[(SERVICES[target], available['components'][target]['available'])])
        item = next(item for item in available['updates'] if item['tag'] == target)
        self.view.view('updates_review', bundle_detail(self.term, item), notice=self.term.t('updates_scope'), style='accent')
        self.perform('update', lambda: self.manager.update(target),
                     context=[('Alfresco', cfg['version'] + ' → ' + item['version']), ('Release', item['tag'])])

    def recover(self):
        pending = self.state.pending or {}
        if not pending:
            return self.report('recover', self.term.t('no_pending'), style='accent')
        rows = [('resume', self.term.t('resume'), 'accent')]
        if pending.get('backup'):
            rows.append(('restore', self.term.t('restore'), 'warning'))
        action = self.menu('recover', rows, notice=operation_summary(self.term, pending),
                           help_text=self.term.t('pending'), notice_style='warning')
        if action is None:
            return
        self.perform('recover', lambda: self.manager.recover(action))

    def logs(self):
        service = None
        while True:
            names = service_names(self.state.load())
            roles = list(names)
            selected = roles.index(service) if service in roles else 0
            service = self.menu('logs', [(role, name, '') for role, name in names.items()],
                                selected=selected, help_text=self.term.t('logs_help'))
            if not service:
                return
            def show(role=service):
                data = self.view.run('logs', lambda: Engine(self.state).dc('logs', '--no-color', '--tail', '300', role).stdout)
                self.view.view('logs', [(line, '') for line in data.splitlines()], newest=True, horizontal=True,
                               notice=SERVICES[role], style='accent')
            self.attempt(show)

    def maintenance(self):
        while True:
            enabled = self.manager.schedule_enabled()
            rows = [('run_checks', self.term.t('run_checks'), 'ok')]
            if enabled is not None:
                rows += [('disable', self.term.t('disable'), 'warning') if enabled else ('enable', self.term.t('enable'), 'accent'),
                         ('schedule_status', self.term.t('schedule_status'), '')]
            state = self.term.t('schedule_unavailable' if enabled is None else 'schedule_on' if enabled else 'schedule_off')
            notice, tone = self.notice_for(state)
            selected = self.menu('maintenance', rows, notice=notice, notice_style=tone,
                                 help_text=self.term.t('maintenance_help'))
            if not selected:
                return
            lookup = {'enable': 'enable', 'disable': 'disable', 'schedule_status': 'status'}
            self.attempt(lambda: self.perform('maintenance', self.manager.maintenance if selected == 'run_checks'
                                              else lambda: self.manager.schedule(lookup[selected])))

    def ask_admin_password(self):
        """The stored admin password stopped working (changed in Share): ask for the current one."""
        password = self.view.edit('admin_current', '', secret=True, hint=self.term.t('admin_current_help'),
                                  normalize=lambda value: value if value else self.password_invalid())
        if password is None:
            return False
        self.view.run('admin_current', lambda: self.manager.remember_admin(password))
        return True

    def reset_password(self):
        while True:
            try:
                users = self.view.run('reset_password', self.manager.users)
                break
            except AdminAuthError:
                if not self.ask_admin_password():
                    return
        user = self.view.pick('password_user', [('user:' + key, key + ' · ' + str(value), '') for key, value in users.items()]
                              + [('manual', self.term.t('manual_value'), 'accent')], search=True,
                              notice=self.term.t('password_user_help'))
        if user == 'manual':
            user = self.view.edit('password_user', '')
        elif user is not None:
            user = user.removeprefix('user:')
        if user is None or not user:
            return
        hint = self.term.t('password_for', user) + '\n' + self.term.t('password_help')
        password = self.view.edit('password_new', '', secret=True, hint=hint,
                                  normalize=lambda value: value if value else self.password_invalid())
        if password is None:
            return
        def repeated(value):
            if value != password:
                raise Error(self.term.t('password_mismatch'))
            return value
        if self.view.edit('password_repeat', '', normalize=repeated, secret=True, hint=hint) is None:
            return
        try:
            self.perform('reset_password', lambda: self.manager.reset_password(user, password),
                         context=[(self.term.t('password_user'), user)], notice=self.term.t('password_reset_done'))
        finally:
            password = None

    def password_invalid(self):
        raise Error(self.term.t('password_empty'))

    def action(self, action):
        if action == 'language':
            selected = self.view.pick('language', [('es', 'Español', ''), ('en', 'English', '')],
                                      selected=int(self.language == 'en'))
            if selected:
                self.language = selected
                self.term.msg = messages(selected)
                remember_language(self.state, selected)
                self.notice, self.notice_style = self.term.t('language_changed'), 'ok'
            return
        if action in ('install', 'domains', 'recover', 'updates', 'logs', 'maintenance', 'reset_password'):
            return getattr(self, action)()
        if action == 'credentials':
            return self.report('credentials', self.manager.credentials(self.state.load()))
        if action == 'self_update':
            source = self.view.edit('source', '', folder, hint=self.term.t('self_update_help'))
            if source:
                self.perform(action, lambda: self.manager.self_update(source))
            return
        if action == 'cleanup':
            found = self.view.run('cleanup', self.manager.cleanup_candidates)
            if not found['volumes'] and not found['revisions'] and not found['images']:
                return self.report('cleanup', self.term.t('cleanup_nothing'), style='accent')
            rows = [(INFO, 'Docker volume · ' + name, '') for name in found['volumes']]
            rows += [(INFO, 'Docker image · ' + ref.split('@', 1)[0], '') for ref in found['images']]
            if found['revisions']:
                rows.append((INFO, self.term.t('cleanup_revisions', len(found['revisions'])), ''))
            rows += [(SEPARATOR, '', ''), ('back', '← ' + self.term.t('cancel'), 'accent'),
                     ('cleanup', self.term.t('cleanup_apply'), 'warning')]
            choice = self.view.pick('cleanup', rows, selected=len(rows) - 2, notice=self.term.t('cleanup_help'),
                                    notice_style='warning', help_text=self.term.t('cleanup_help'))
            if choice == 'cleanup':
                self.perform('cleanup', lambda: self.manager.cleanup(supplied='CLEANUP'))
            return
        if action == 'stop':
            choice = self.view.pick('confirm_stop', [('back', '← ' + self.term.t('back'), 'accent'),
                                                     ('stop', self.term.t('stop'), 'warning')],
                                    notice=self.term.t('stop_help'), notice_style='warning')
            if choice != 'stop':
                return
        from .cli import dispatch
        from argparse import Namespace
        command = 'diagnose' if action == 'status' else action
        self.perform(action, lambda: dispatch(self.manager, Namespace(command=command)),
                     style='accent' if action == 'status' else 'ok')

    def run(self):
        while True:
            if self.state.configured:
                try:
                    self.snapshot = self.view.run('load_status', self.manager.status)
                except (Error, OSError, ValueError):
                    self.snapshot = None
            action = self.term.run(lambda: dashboard(self.term, self.state, last=self.last,
                                   notice=self.notice, notice_style=self.notice_style, status=self.snapshot))
            self.notice = ''
            if action in (None, 'exit'):
                return
            self.last = action
            self.attempt(lambda: self.action(action))
