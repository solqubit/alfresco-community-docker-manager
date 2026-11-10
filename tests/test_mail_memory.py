import copy
import shutil
import subprocess
import unittest

from acdm.config import compose, memory_tier, validate
from acdm.core import Error
from test_foundation import configuration

GIB = 1024 ** 3
MAIL = {'host': 'smtp.example.com', 'port': 587, 'security': 'starttls', 'username': 'alfresco@example.com',
        'password': 'p@ss w"o\'rd $HOME `x`', 'sender': 'alfresco@example.com'}


def java_opts(cfg):
    return compose(cfg)['services']['alfresco']['environment']['JAVA_OPTS'].replace('$$', '$')


class MemoryTests(unittest.TestCase):
    def test_tiers_follow_server_ram(self):
        self.assertEqual(memory_tier(int(7.7 * GIB)), 'base')
        self.assertEqual(memory_tier(int(15.6 * GIB)), 'medium')
        self.assertEqual(memory_tier(int(31.3 * GIB)), 'large')

    def test_limits_grow_with_the_tier(self):
        cfg = configuration()
        base = compose(cfg)['services']
        cfg['memory_tier'] = 'large'
        large = compose(cfg)['services']
        self.assertEqual(base['alfresco']['mem_limit'], '2g')
        self.assertEqual(large['alfresco']['mem_limit'], '8g')
        self.assertIn('-Xmx2g', large['elasticsearch']['environment']['ES_JAVA_OPTS'])
        self.assertIn('shared_buffers=1GB', large['postgres']['command'])

    def test_unknown_tier_is_rejected(self):
        cfg = configuration()
        cfg['memory_tier'] = 'huge'
        with self.assertRaises(Error):
            validate(cfg)


class MailTests(unittest.TestCase):
    def test_no_mail_by_default(self):
        self.assertNotIn('-Dmail.host', java_opts(configuration()))

    def test_mail_settings_reach_alfresco(self):
        cfg = configuration()
        cfg['mail'] = dict(MAIL)
        validate(cfg)
        options = java_opts(cfg)
        for expected in ('-Dmail.host=smtp.example.com', '-Dmail.port=587', '-Dmail.smtp.starttls.enable=true',
                         '-Dmail.from.default=alfresco@example.com', '-Dmail.smtp.auth=true'):
            self.assertIn(expected, options)

    @unittest.skipUnless(shutil.which('sh'), 'sh unavailable')
    def test_password_survives_catalina_shell_evaluation(self):
        cfg = configuration()
        cfg['mail'] = dict(MAIL)
        # catalina.sh runs: eval exec java ... "$JAVA_OPTS" ...
        result = subprocess.run(['sh', '-c', 'eval "set -- $JAVA_OPTS"; for a in "$@"; do printf "%s\\n" "$a"; done'],
                                env={'JAVA_OPTS': java_opts(cfg), 'PATH': '/usr/bin:/bin'},
                                capture_output=True, text=True, check=True)
        self.assertIn('-Dmail.password=' + MAIL['password'], result.stdout.splitlines())

    def test_invalid_mail_settings_are_rejected(self):
        for key, value in (('security', 'tls13'), ('sender', 'not-an-address'), ('host', 'smtp.example.com:587'),
                           ('password', 'line\nbreak')):
            cfg = configuration()
            cfg['mail'] = dict(MAIL, **{key: value})
            with self.assertRaises(Error, msg=key):
                validate(cfg)

    def test_no_authentication_when_username_is_empty(self):
        cfg = configuration()
        cfg['mail'] = dict(MAIL, username='', password='', security='none', port=25)
        options = java_opts(cfg)
        self.assertIn('-Dmail.smtp.auth=false', options)
        self.assertNotIn('-Dmail.password', options)


class KeystoreTests(unittest.TestCase):
    KEYSTORE = {'data': 'A' * 860 + '==', 'store_password': 'S' * 32, 'key_password': 'K' * 32}

    def test_image_default_keystore_without_own(self):
        alfresco = compose(configuration())['services']['alfresco']
        self.assertIn('mp6yc0UD9e', alfresco['environment']['JAVA_OPTS'])
        self.assertNotIn('JAVA_TOOL_OPTIONS', alfresco['environment'])
        self.assertNotIn('entrypoint', alfresco)

    def test_own_keystore_is_written_and_used(self):
        cfg = configuration()
        cfg['keystore'] = dict(self.KEYSTORE)
        validate(cfg)
        alfresco = compose(cfg)['services']['alfresco']
        options = alfresco['environment']['JAVA_OPTS']
        self.assertNotIn('JAVA_TOOL_OPTIONS', alfresco['environment'])
        self.assertNotIn('mp6yc0UD9e', options)
        self.assertIn('-Dmetadata-keystore.password=' + 'S' * 32, options)
        self.assertIn('/tmp/acdm-keystore/keystore', options)
        self.assertEqual(alfresco['environment']['ACDM_KEYSTORE'], self.KEYSTORE['data'])
        self.assertIn('exec catalina.sh run -security', alfresco['entrypoint'][-1])

    def test_settings_never_drop_the_keystore(self):
        from pathlib import Path
        import tempfile
        from unittest.mock import patch
        from acdm.core import State
        from acdm.operations import Manager
        with tempfile.TemporaryDirectory() as folder:
            state = State(Path(folder) / 'state').open()
            try:
                cfg = configuration()
                cfg['keystore'] = dict(self.KEYSTORE)
                cfg['status'] = 'ready'
                state.activate(state.revision(cfg))
                stale = copy.deepcopy(cfg)
                stale.pop('keystore')
                engine = type('E', (), {'cfg': cfg})()
                with patch.object(Manager, 'ready', return_value=engine), patch('acdm.operations.Engine'), \
                        patch.object(Manager, 'detected_tier', return_value='base'):
                    Manager(state).settings(stale, 'APPLY')
                self.assertEqual(state.load()['keystore'], self.KEYSTORE)
            finally:
                state.lock_file.close()


if __name__ == '__main__':
    unittest.main()
