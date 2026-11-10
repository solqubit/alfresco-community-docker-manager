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

    def test_disk_minimum_blocks(self):
        result = rows(free=20)
        self.assertEqual(status(result, 'disk'), 'error')
        self.assertTrue(any('30 GB' in message for message in errors(result)))

    def test_disk_that_would_pass_elasticsearch_limit_warns(self):
        result = rows(free=40, total=200)
        self.assertEqual(status(result, 'disk'), 'warning')
        self.assertEqual(next(row for row in result if row['key'] == 'disk')['note'], 'req_disk_full')

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
