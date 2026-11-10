from collections import namedtuple
import unittest

from acdm import requirements
from acdm.requirements import GIB, evaluate, errors, low_memory

Usage = namedtuple('Usage', 'total used free')


def disk(free, total=200):
    return lambda path: Usage(total * GIB, (total - free) * GIB, free * GIB)


def rows(memory=16, cpus=4, architecture='x86_64', free=100, total=200, data=None, data_free=100):
    usage = disk(free, total)
    if data:
        other = disk(data_free)
        usage_by_path = lambda path: other(path) if str(path).startswith('/tmp') else disk(free, total)(path)
        return evaluate(int(memory * GIB), cpus, architecture, '/', data, usage=usage_by_path,
                        device=lambda path: 2 if str(path).startswith('/tmp') else 1)
    return evaluate(int(memory * GIB), cpus, architecture, '/', None, usage=usage)


def status(result, key):
    return next(row for row in result if row['key'] == key)['status']


class RequirementTests(unittest.TestCase):
    def test_recommended_server_passes(self):
        result = rows()
        self.assertTrue(all(row['status'] == 'ok' for row in result))
        self.assertEqual(errors(result), [])

    def test_low_memory_warns_but_never_blocks(self):
        result = rows(memory=6)
        self.assertEqual(status(result, 'memory'), 'warning')
        self.assertEqual(errors(result), [])
        self.assertIn('8 GB', low_memory(result))
        self.assertIn('no pueda arrancar', low_memory(result))

    def test_eight_gb_server_is_accepted_with_a_recommendation(self):
        # Linux reports about 7.7 GiB on a server sold with 8 GB.
        result = rows(memory=7.7)
        self.assertEqual(status(result, 'memory'), 'warning')
        self.assertEqual(low_memory(result), '')

    def note(self, result, key):
        return next(row for row in result if row['key'] == key)['note']

    def test_disk_minimum_blocks(self):
        result = rows(free=10)
        self.assertEqual(status(result, 'disk'), 'error')
        self.assertTrue(any('15 GB' in message for message in errors(result)))

    def test_disk_that_would_pass_elasticsearch_limit_blocks(self):
        # 160 GB used + 12 GB of installation = 86 % of 200 GB.
        result = rows(free=40, total=200)
        self.assertEqual(status(result, 'disk'), 'error')
        self.assertEqual(self.note(result, 'disk'), 'req_disk_full')
        self.assertTrue(any('85 %' in message for message in errors(result)))

    def test_clean_30_gb_ubuntu_installs_with_update_warning(self):
        # Ubuntu Server with Docker uses about 7 GB of a 30 GB disk.
        result = rows(free=23, total=30)
        self.assertEqual(errors(result), [])
        self.assertEqual(self.note(result, 'disk'), 'req_disk_warning')

    def test_clean_50_gb_disk_is_comfortable(self):
        result = rows(free=43, total=50)
        self.assertEqual(status(result, 'disk'), 'ok')

    def test_update_space_counts_new_images_and_elasticsearch_limit(self):
        disk = lambda path: Usage(50 * GIB, 30 * GIB, 20 * GIB)
        self.assertTrue(requirements.update_space('/', 10 * GIB, usage=disk)[0])
        # 30 + 14 GB = 88 % of the disk: Elasticsearch would stop indexing.
        self.assertFalse(requirements.update_space('/', 14 * GIB, usage=disk)[0])
        # Not enough free space for the download itself.
        small = lambda path: Usage(200 * GIB, 195 * GIB, 5 * GIB)
        self.assertFalse(requirements.update_space('/', 4 * GIB, usage=small)[0])

    def test_update_estimate_uses_changed_components_only(self):
        from unittest.mock import Mock, patch
        from acdm.core import Error
        from acdm.operations import Manager
        manager = Manager.__new__(Manager)
        engine = Mock()
        engine.cfg = {'images': {'share': 'share-image', 'alfresco': 'repo-image'}}
        engine.inspect.side_effect = lambda kind, ref: {'Size': GIB}
        with patch.object(Manager, 'docker_root', return_value='/'), \
                patch('acdm.operations.requirements.update_space', return_value=(True, 50 * GIB)) as space:
            self.assertEqual(manager.check_update_space(engine, ['share']), int(1.4 * GIB))
        space.assert_called_once_with('/', int(1.4 * GIB))
        with patch.object(Manager, 'docker_root', return_value='/'), \
                patch('acdm.operations.requirements.update_space', return_value=(False, GIB)):
            with self.assertRaisesRegex(Error, 'Liberar espacio'):
                manager.check_update_space(engine, ['share', 'alfresco', 'batch-indexing'])

    def test_separate_data_disk_is_checked(self):
        result = rows(data='/tmp', data_free=2)
        self.assertEqual(status(result, 'data_disk'), 'error')

    def test_cpu_is_informative_and_unsupported_architecture_blocks(self):
        self.assertEqual(status(rows(cpus=1), 'cpu'), 'warning')
        self.assertEqual(errors(rows(cpus=1)), [])
        self.assertEqual(status(rows(architecture='armv7l'), 'architecture'), 'error')

    def test_missing_data_folder_is_measured_on_its_parent(self):
        self.assertEqual(requirements.existing('/definitely/not/here/data').anchor,
                         requirements.existing('/').anchor)


if __name__ == '__main__':
    unittest.main()
