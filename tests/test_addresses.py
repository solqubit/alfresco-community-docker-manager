import unittest

from acdm.config import validate
from acdm.i18n import messages
from acdm.terminal_ui import Terminal
from acdm.views import address_kind, address_label, local_name_notes
from test_foundation import configuration


class LocalAddressTests(unittest.TestCase):
    def setUp(self):
        self.ui = Terminal(messages('es'))
        self.cfg = configuration()
        self.cfg['domains'] += [dict(host='192.168.1.10', kind='local', mode='local', tls='internal', email=''),
                                dict(host='alfresco.lan', kind='local', mode='local', tls='off', email='')]
        self.cfg['primary'] = 'alfresco.lan'
        validate(self.cfg)

    def entry(self, name):
        return next(entry for entry in self.cfg['domains'] if entry['host'] == name)

    def test_kinds(self):
        self.assertEqual(address_kind('192.168.1.10'), 'ip')
        self.assertEqual(address_kind('alfresco.lan'), 'name')

    def test_labels_show_type_and_https_state(self):
        self.assertEqual(address_label(self.ui, self.cfg, self.entry('192.168.1.10')),
                         'https://192.168.1.10 · IP · HTTPS local activo')
        self.assertEqual(address_label(self.ui, self.cfg, self.entry('alfresco.lan')),
                         'http://alfresco.lan · Nombre local · Sin HTTPS local · Principal')

    def test_notes_explain_dns_mdns_and_ca(self):
        self.assertEqual(local_name_notes(self.ui, '192.168.1.10', '192.168.1.10', False), [])
        notes = local_name_notes(self.ui, 'alfresco.lan', '192.168.1.10', True)
        self.assertIn('alfresco.lan → 192.168.1.10', notes[0])
        self.assertIn('CA', notes[-1])
        self.assertTrue(any('.local' in note for note in local_name_notes(self.ui, 'alfresco.local', '192.168.1.10', False)))

    def test_every_language_has_the_new_texts(self):
        english = messages('en')
        for key in ('address_ip', 'address_name', 'field_local_tls', 'local_name_dns',
                    'local_name_mdns', 'local_tls_ca', 'local_tls_notice', 'preview_ip', 'preview_name'):
            self.assertIn(key, english)
            self.assertIn(key, self.ui.msg)


if __name__ == '__main__':
    unittest.main()
