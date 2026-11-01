"""Exercise the automatic initializer without touching development databases."""
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
INITIALIZE = ROOT / 'deploy' / 'initialize.sh'
PYTHON = ROOT / '.venv' / 'bin' / 'python'


@unittest.skipUnless(shutil.which('sh'), 'The container initializer requires sh.')
class InitializeFailureTests(unittest.TestCase):
    def run_initializer(self, failure=''):
        with tempfile.TemporaryDirectory(prefix='lims-init-order-') as directory:
            scratch = Path(directory)
            executable = scratch / 'python'
            executable.write_text(
                '#!/bin/sh\n'
                'printf "%s\\n" "$*" >> "$DEPLOY_TEST_LOG"\n'
                'if [ "${DEPLOY_TEST_FAIL:-}" = "$2" ]; then exit 23; fi\n'
            )
            executable.chmod(0o755)
            log = scratch / 'commands.log'
            env = dict(os.environ, DEPLOY_TEST_LOG=str(log),
                       DEPLOY_TEST_FAIL=failure)
            env['PATH'] = str(scratch) + os.pathsep + env.get('PATH', '')
            result = subprocess.run(
                ['sh', str(INITIALIZE)], cwd=ROOT, env=env,
                text=True, capture_output=True, timeout=10,
            )
            return result.returncode, log.read_text().splitlines()

    def test_seed_only_runs_after_migrations_succeed(self):
        code, commands = self.run_initializer()
        self.assertEqual(code, 0)
        self.assertEqual(commands, [
            'manage.py migrate --noinput', 'manage.py seed_initial',
        ])

    def test_failed_migration_never_runs_seed(self):
        code, commands = self.run_initializer('migrate')
        self.assertEqual(code, 23)
        self.assertEqual(commands, ['manage.py migrate --noinput'])

    def test_failed_seed_never_signals_success_to_compose(self):
        code, commands = self.run_initializer('seed_initial')
        self.assertEqual(code, 23)
        self.assertEqual(commands[-1], 'manage.py seed_initial')


@unittest.skipUnless(shutil.which('sh') and PYTHON.is_file(),
                     'Requires sh and the local Python environment.')
class InitializeDatabaseTests(unittest.TestCase):
    def run_initializer(self, database):
        env = dict(os.environ, DJANGO_SETTINGS_MODULE='config.local',
                   LIMS_LOCAL_DB=str(database))
        env.pop('PYTHONPATH', None)
        for key in tuple(env):
            if key.startswith(('CLOUD_RU_', 'ASSISTANT_')):
                env.pop(key)
        env['PATH'] = str(PYTHON.parent) + os.pathsep + env.get('PATH', '')
        result = subprocess.run(
            ['sh', str(INITIALIZE)], cwd=ROOT, env=env,
            text=True, capture_output=True, timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def counts(self, database):
        with sqlite3.connect(database) as connection:
            return {table: connection.execute(
                'SELECT COUNT(*) FROM lab_' + table,
            ).fetchone()[0] for table in (
                'recipeversion', 'testsession', 'sourcefile', 'sourcecell',
                'material', 'changeevent', 'employeeprofile', 'recipematch',
            )}

    def edit_material(self, database):
        with sqlite3.connect(database) as connection:
            material_id = connection.execute(
                'SELECT MIN(id) FROM lab_material',
            ).fetchone()[0]
            connection.execute('UPDATE lab_material SET notes=? WHERE id=?', (
                'Рабочая запись сотрудника', material_id,
            ))
        return material_id

    def test_empty_database_is_ready_and_restart_preserves_counts_and_edits(self):
        with tempfile.TemporaryDirectory(prefix='lims-init-database-') as directory:
            database = Path(directory) / 'database.sqlite3'
            self.run_initializer(database)
            before = self.counts(database)
            self.assertEqual(before['recipeversion'], 135)
            self.assertEqual(before['testsession'], 167)
            self.assertEqual(before['sourcefile'], 6)
            self.assertEqual(before['employeeprofile'], 2)
            material_id = self.edit_material(database)
            self.run_initializer(database)
            self.assertEqual(self.counts(database), before)
            with sqlite3.connect(database) as connection:
                self.assertEqual(connection.execute(
                    'SELECT notes FROM lab_material WHERE id=?', (material_id,),
                ).fetchone()[0], 'Рабочая запись сотрудника')


if __name__ == '__main__':
    unittest.main()
