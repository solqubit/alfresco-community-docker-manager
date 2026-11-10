"""Interactive terminal and explicit commands share the same guarded operations."""
from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import sys
import traceback

from .config import DEFAULT_HTTP_PORT, DEFAULT_HTTPS_PORT, access_url, host, start_order
from .core import Error, Runner, State, VERSION, read_json
from .console import Console
from .docker import Engine, require_engine
from .i18n import messages, remember_language, remembered_language
from .operations import Manager
from .system import bootstrap, host_info
from .terminal_ui import Terminal

SERVICE_CHOICES = ['alfresco', 'share', 'postgres', 'transform-core-aio', 'activemq', 'elasticsearch',
                   'batch-indexing', 'content-app', 'control-center', 'proxy']


def parser():
    p = argparse.ArgumentParser(description='Alfresco Community Docker Manager')
    p.add_argument('--version', action='version', version=VERSION)
    p.add_argument('--state-dir', default=os.environ.get('ACDM_STATE_DIR', '/opt/alfresco-community-docker-manager'))
    p.add_argument('--lang', choices=['es', 'en'])
    p.add_argument('--plain', action='store_true')
    sub = p.add_subparsers(dest='command')
    sub.add_parser('menu')
    install = sub.add_parser('install')
    install.add_argument('--alfresco-version', help='Exact Alfresco Community version (default: latest official bundle)')
    install.add_argument('--address', default='127.0.0.1')
    install.add_argument('--bind', default='127.0.0.1')
    install.add_argument('--http-port', type=int, default=DEFAULT_HTTP_PORT)
    install.add_argument('--https-port', type=int, default=DEFAULT_HTTPS_PORT)
    install.add_argument('--no-local-https', dest='local_tls', action='store_false',
                         help='Plain HTTP only (local HTTPS with its own CA is the default)')
    install.add_argument('--timezone', help="Default: the server's timezone")
    install.add_argument('--storage', choices=['named', 'directory'], default='directory')
    install.add_argument('--storage-root', default='/opt/alfresco-community-docker-manager/data')
    install.add_argument('--confirm')
    for name in ('status', 'versions', 'check-updates', 'diagnose', 'start', 'stop', 'maintenance', 'export-ca', 'credentials'):
        sub.add_parser(name)
    child = sub.add_parser('update')
    child.add_argument('--target', help='Release tag (vX.Y.Z) or Alfresco version')
    child.add_argument('--confirm')
    child = sub.add_parser('recover')
    child.add_argument('--action', choices=['resume', 'restore'], required=True)
    child.add_argument('--confirm')
    child = sub.add_parser('domain-add')
    child.add_argument('host')
    child.add_argument('--no-local-https', dest='local_tls', action='store_false')
    child.add_argument('--primary', action='store_true')
    child.add_argument('--confirm')
    for name in ('domain-remove', 'domain-primary'):
        child = sub.add_parser(name)
        child.add_argument('host')
        child.add_argument('--confirm')
    child = sub.add_parser('network')
    child.add_argument('--bind')
    child.add_argument('--http-port', type=int)
    child.add_argument('--https-port', type=int)
    child.add_argument('--confirm')
    child = sub.add_parser('logs')
    child.add_argument('service', choices=SERVICE_CHOICES)
    child.add_argument('--tail', type=int, default=100)
    for name in ('mail-set', 'mail-test'):
        child = sub.add_parser(name, help='Outbound email used by Alfresco (password read from stdin)')
        child.add_argument('--host', required=True)
        child.add_argument('--port', type=int, default=587)
        child.add_argument('--security', choices=['starttls', 'ssl', 'none'], default='starttls')
        child.add_argument('--username', default='')
        child.add_argument('--password-stdin', action='store_true')
        child.add_argument('--sender', required=True)
        child.add_argument('--test-to', help='Send a test message here before applying' if name == 'mail-set'
                           else 'Recipient of the test message', required=name == 'mail-test')
        child.add_argument('--confirm')
    child = sub.add_parser('mail-disable')
    child.add_argument('--confirm')
    child = sub.add_parser('update-component')
    child.add_argument('component', choices=['proxy', 'postgres'])
    child.add_argument('--confirm')
    child = sub.add_parser('reset-password')
    child.add_argument('user')
    child.add_argument('--password-stdin', action='store_true', required=True)
    child.add_argument('--confirm')
    child = sub.add_parser('cleanup')
    child.add_argument('--confirm')
    child = sub.add_parser('self-update')
    child.add_argument('--source')
    child.add_argument('--confirm')
    return p


def show(value):
    if value is not None:
        print(json.dumps(value, ensure_ascii=False, indent=2, default=str) if not isinstance(value, str) else value)


def menu(manager, language=None, console=None):
    from .screens import Controller
    Controller(manager, language, console).run()


def mail_settings(args):
    password = sys.stdin.readline().rstrip('\n') if args.password_stdin else ''
    return {'host': args.host, 'port': args.port, 'security': args.security, 'username': args.username,
            'password': password, 'sender': args.sender}


def dispatch(manager, args):
    command = args.command
    state = manager.state
    if command == 'install':
        return manager.install(args.alfresco_version, supplied=args.confirm,
            **{key: getattr(args, key) for key in ('address', 'bind', 'local_tls', 'http_port', 'https_port', 'timezone', 'storage', 'storage_root')},
            language=args.lang or remembered_language(state) or 'es')
    if command == 'versions':
        return [{key: item[key] for key in ('tag', 'version', 'search', 'stale')} for item in manager.installation_options()]
    if not state.configured:
        raise Error('Primero instala Alfresco / Install Alfresco first')
    engine = Engine(state)
    if command == 'status':
        return manager.status()
    if command == 'credentials':
        return manager.credentials(engine.cfg)
    if command == 'check-updates':
        return manager.checks()
    if command == 'update':
        return manager.update(args.target, supplied=args.confirm)
    if command == 'recover':
        return manager.recover(args.action, args.confirm)
    if command == 'domain-add':
        manager.add_domain(args.host, tls='internal' if args.local_tls else 'off', primary=args.primary,
                           supplied=args.confirm)
        cfg = state.load()
        return {'access': access_url(cfg, next(e for e in cfg['domains'] if e['host'] == host(args.host)))}
    if command == 'domain-remove':
        return manager.remove_domain(args.host, args.confirm)
    if command in ('domain-primary', 'network'):
        cfg = copy.deepcopy(engine.cfg)
        if command == 'domain-primary':
            cfg['primary'] = host(args.host)
        else:
            for key in ('bind', 'http_port', 'https_port'):
                value = getattr(args, key)
                if value is not None:
                    cfg[key] = value
        return manager.settings(cfg, args.confirm)
    if command == 'start':
        if state.pending:
            raise Error('Hay una operación pendiente: usa Recuperar / Pending operation: use recover')
        if manager.refresh_memory():
            engine = Engine(state)
        engine.up(timeout=3600)
        engine.wait_healthy(*start_order(engine.cfg), timeout=3600)
        return engine.status()
    if command == 'stop':
        return engine.stop()
    if command == 'logs':
        return engine.dc('logs', '--no-color', '--tail', str(min(max(args.tail, 1), 2000)), args.service).stdout
    if command == 'export-ca':
        return str(engine.ca_certificate())
    if command == 'diagnose':
        last = state.path / 'last-maintenance.json'
        return {'host': host_info(), 'state': manager.status(), 'disk_free': shutil.disk_usage(state.path).free,
                'last_maintenance': read_json(last) if last.exists() else None}
    if command == 'maintenance':
        return manager.maintenance()
    if command in ('mail-set', 'mail-test'):
        mail = mail_settings(args)
        if args.test_to:
            manager.test_mail(mail, args.test_to)
        return manager.set_mail(mail, args.confirm) if command == 'mail-set' else {'sent_to': args.test_to}
    if command == 'mail-disable':
        return manager.set_mail(None, args.confirm)
    if command == 'update-component':
        return manager.update_component(args.component, args.confirm)
    if command == 'reset-password':
        password = sys.stdin.readline().rstrip('\n')
        return manager.reset_password(args.user, password, args.confirm)
    if command == 'cleanup':
        return manager.cleanup(args.confirm)
    if command == 'self-update':
        return manager.self_update(args.source, args.confirm)
    raise Error('Unknown command')


def main(argv=None):
    args = parser().parse_args(argv)
    if args.plain:
        os.environ['ACDM_UI'] = 'plain'
    state = None
    console = None
    try:
        if os.name != 'posix':
            raise Error('Run this installer on Linux. Use a Linux Docker runner for Windows tests.')
        state = State(args.state_dir).open(allow_busy=args.command == 'maintenance')
        interactive = args.command in (None, 'menu')
        visual = interactive or (sys.stdin.isatty() and sys.stdout.isatty()
                                 and args.command not in ('reset-password', 'mail-set', 'mail-test'))
        startup_language = os.environ.pop('ACDM_STARTUP_LANGUAGE', None)
        language = args.lang or (startup_language if startup_language in ('es', 'en') else None) or remembered_language(state)
        console = Console(Terminal(messages(language or 'es')), state) if visual else None
        if interactive:
            if language is None:
                language = console.pick('language', [('es', 'Español', ''), ('en', 'English', '')])
                if language is None:
                    return 0
                console.term.msg = messages(language)
            remember_language(state, language)
            console.welcome()
            console.run('prepare_host', lambda: bootstrap(Runner(state)))
        language = language or 'es'
        if console:
            console.run('check_engine', lambda: require_engine(Runner(state)))
        else:
            require_engine(Runner(state))
        manager = Manager(state)
        if interactive:
            menu(manager, language, console)
            return 0
        if console:
            from .screens import Controller
            result = console.run(args.command, lambda: dispatch(manager, args))
            Controller(manager, language, console).report(args.command, result, style='accent')
        else:
            result = dispatch(manager, args)
            show(result)
        if args.command == 'maintenance' and isinstance(result, dict) and result.get('errors'):
            return 2
        return 0
    except EOFError:
        return 0
    except (Error, OSError, ValueError, KeyboardInterrupt) as exc:
        if state and getattr(state, 'log', None):
            with state.log_path.open('a', encoding='utf-8') as log:
                traceback.print_exc(file=log)
            if console is not None:
                console.error(exc, pending=bool(state.pending))
            else:
                print(f'[!] {exc}\nLog: {state.log_path}', file=sys.stderr)
        else:
            print(f'[!] {exc}', file=sys.stderr)
        return 130 if isinstance(exc, KeyboardInterrupt) else 1
    finally:
        if state and getattr(state, 'lock_file', None):
            state.lock_file.close()
