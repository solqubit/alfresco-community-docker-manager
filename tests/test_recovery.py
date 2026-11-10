import copy
from contextlib import nullcontext
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from acdm.core import Error, State, atomic, read_json
from acdm.docker import Engine
from acdm.operations import Manager
from test_foundation import configuration


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.state = State(Path(self.work.name) / 'state').open()
        self.addCleanup(self.work.cleanup)
        self.addCleanup(self.state.lock_file.close)
        self.source_cfg = configuration(1)
        self.source = self.state.revision(self.source_cfg)
        self.state.activate(self.source)
        target_cfg = copy.deepcopy(self.source_cfg)
        latest = configuration(0)
        for key in ('images', 'version', 'search'):
            target_cfg[key] = latest[key]
        target_cfg['volumes']['search'] += '_new'
        self.target_cfg = target_cfg

    def pending_update(self):
        target = self.state.revision(self.target_cfg)
        self.state.begin('update', source=str(self.source), target=str(target))
        self.state.phase('applying')
        return target

    def test_rollback_restores_sql_and_keeps_documents_in_place(self):
        work = self.state.path / 'backups'
        backup = work / 'backup.acdm.tar'
        self.state.begin('update', source=str(self.source), backup=str(backup))
        self.state.phase('applying', backup=str(backup))
        manifest = {'configuration': self.source_cfg, 'files': {'db.sql.gz': {'bytes': 1}, 'proxy.tar': {'bytes': 1}},
                    'sql_estimated_bytes': 1}
        with patch('acdm.operations.archive.unpack', return_value=nullcontext((work, manifest))), \
                patch('acdm.operations.Engine'), patch('acdm.operations.archive.import_sql') as import_sql, \
                patch('acdm.operations.archive.restore_volume') as restore_volume, patch.object(Manager, 'finish'):
            Manager(self.state).recover('restore', 'RESTORE')
        cfg = self.state.load()
        self.assertEqual(cfg['volumes']['content'], self.source_cfg['volumes']['content'])
        self.assertNotEqual(cfg['volumes']['db'], self.source_cfg['volumes']['db'])
        self.assertEqual(restore_volume.call_count, 1)
        import_sql.assert_called_once()
        self.assertIsNone(self.state.pending)

    def test_restore_requires_the_pending_operation_backup(self):
        with self.assertRaisesRegex(Error, 'pending operation backup'):
            Manager(self.state).restore(self.state.path / 'backups' / 'other.acdm.tar', supplied='RESTORE')

    def test_start_regenerates_older_compose_definition(self):
        atomic(self.source / 'compose.json', {'old': 'definition'})
        engine = Engine(self.state)
        with patch.object(engine, 'check_mount'), patch.object(engine, 'owned_containers', return_value=[]), \
                patch.object(engine, 'dc') as dc:
            engine.up()
        saved = read_json(self.source / 'compose.json')
        self.assertEqual(saved['services']['proxy']['networks'], ['proxy'])
        dc.assert_called_once_with('up', '-d', '--no-build', '--remove-orphans', timeout=1800)

    def test_retired_service_removes_its_anonymous_volumes(self):
        target = self.state.revision(self.target_cfg)
        engine = Engine(self.state, self.target_cfg, target)
        old = {'Id': 'old-service', 'Config': {'Labels': {'com.docker.compose.service': 'retired',
                                                    'com.solqubit.acdm.installation': self.source_cfg['id']}}}
        engine.runner = Mock()
        with patch.object(engine, 'check_mount'), patch.object(engine, 'owned_containers', return_value=[old]), \
                patch.object(engine, 'dc'):
            engine.up()
        engine.runner.run.assert_called_once_with(['docker', 'rm', '--force', '--volumes', 'old-service'], timeout=600)

    def test_inherited_anonymous_volume_is_removed_after_retirement(self):
        engine = Engine(self.state, self.target_cfg, self.state.revision(self.target_cfg))
        old = {'Id': 'old-service', 'Config': {'Labels': {'com.docker.compose.service': 'retired',
                                                    'com.solqubit.acdm.installation': self.source_cfg['id']}},
               'Mounts': [{'Type': 'volume', 'Name': 'inherited-anonymous'}]}
        engine.runner = Mock()
        volume = {'Labels': {'com.docker.volume.anonymous': ''}}
        with patch.object(engine, 'check_mount'), patch.object(engine, 'owned_containers', return_value=[old]), \
                patch.object(engine, 'inspect', return_value=volume), patch.object(engine, 'dc'):
            engine.up()
        self.assertEqual(engine.runner.run.call_args_list[1].args[0],
                         ['docker', 'volume', 'rm', 'inherited-anonymous'])
        self.assertEqual(engine.runner.run.call_count, 2)

    def test_anonymous_cleanup_preserves_named_volumes(self):
        engine = Engine(self.state)
        engine.runner = Mock()
        container = {'Config': {'Labels': {'com.solqubit.acdm.installation': self.source_cfg['id']}},
                     'Mounts': [{'Type': 'volume', 'Name': 'named-volume'}]}
        with patch.object(engine, 'inspect', return_value={'Labels': {}}):
            engine.remove_anonymous_volumes([container])
        engine.runner.run.assert_not_called()

    def test_foreign_project_container_blocks_removal(self):
        engine = Engine(self.state)
        engine.runner = Mock()
        engine.runner.run.return_value.stdout = 'foreign-container\n'
        foreign = {'Id': 'foreign-container', 'Config': {'Labels': {'com.solqubit.acdm.installation': 'other'}}}
        with patch.object(engine, 'check_mount'), patch.object(engine, 'inspect', return_value=foreign), \
                patch.object(engine, 'dc') as dc:
            with self.assertRaisesRegex(Error, 'Resource not owned'):
                engine.up()
        dc.assert_not_called()
        self.assertEqual(engine.runner.run.call_count, 1)

    def test_remove_owned_includes_anonymous_volumes_on_full_deletion(self):
        engine = Engine(self.state)
        with patch.object(engine, 'owned_containers'), patch.object(engine, 'dc') as dc, \
                patch.object(engine, 'inspect', return_value=None):
            engine.remove_owned(delete_volumes=True)
        dc.assert_called_once_with('down', '--remove-orphans', '--volumes', timeout=600)

    @patch('acdm.operations.Engine')
    def test_resume_update_activates_target_after_interrupted_stop(self, engine):
        target = self.pending_update()
        with patch.object(Manager, 'finish') as finish:
            Manager(self.state).recover('resume', 'RESUME')
        self.assertEqual((self.state.path / 'current').resolve(), target)
        engine.return_value.stop.assert_called_once_with()
        finish.assert_called_once_with(timeout=5400)
        self.assertIsNone(self.state.pending)

    @patch('acdm.operations.Engine')
    def test_resume_rejects_incomplete_database_migration(self, engine):
        self.target_cfg['volumes']['db'] += '_new'
        target = self.pending_update()
        self.state.activate(target)
        with patch.object(Manager, 'finish') as finish:
            with self.assertRaisesRegex(Error, 'recover --action restore'):
                Manager(self.state).recover('resume', 'RESUME')
        finish.assert_not_called()
        self.assertIsNotNone(self.state.pending)

    @patch('acdm.operations.Engine')
    def test_resume_completed_database_import(self, engine):
        self.target_cfg['volumes']['db'] += '_new'
        target = self.pending_update()
        self.state.activate(target)
        self.state.phase('applying', database_ready=True)
        with patch.object(Manager, 'finish'):
            Manager(self.state).recover('resume', 'RESUME')
        engine.return_value.stop.assert_not_called()
        self.assertIsNone(self.state.pending)
