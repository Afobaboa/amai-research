"""Local-development isolation checks. Run with stdlib unittest.

The settings inspection runs in a child interpreter and never connects to a DB.
"""
import json
import importlib.util
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
PYTHON = str(VENV_PYTHON if VENV_PYTHON.exists() else Path(sys.executable))

INSPECT_SETTINGS = """
import json
from django.conf import settings
from django.contrib.auth.hashers import check_password
print(json.dumps({
    'debug': settings.DEBUG,
    'demo': settings.DEMO_MODE,
    'engine': settings.DATABASES['default']['ENGINE'],
    'database': str(settings.DATABASES['default']['NAME']),
    'hosts': settings.ALLOWED_HOSTS,
    'csrf_origins': settings.CSRF_TRUSTED_ORIGINS,
    'local_password': check_password('lims-demo', settings.SITE_PASSWORD_HASH),
    'secure_cookie': settings.SESSION_COOKIE_SECURE,
    'https_redirect': settings.SECURE_SSL_REDIRECT,
}))
"""


def inspect_profile(**extra):
    env = dict(os.environ)
    env.update({
        'DJANGO_SETTINGS_MODULE': 'config.local',
        'DEBUG': '0',
        'DEMO_MODE': '0',
        'DATABASE_URL': 'postgresql://cloud-user:unused@invalid.example/production',
        'SQLITE_PATH': '/never-touch-production.sqlite3',
        'SITE_PASSWORD_HASH': 'unused-production-password-hash',
        'ALLOWED_HOSTS': 'production.example',
        'CSRF_TRUSTED_ORIGINS': 'https://production.example',
    })
    env.pop('LIMS_LOCAL_DB', None)
    env.update(extra)
    result = subprocess.run(
        [PYTHON, '-c', INSPECT_SETTINGS], cwd=ROOT, env=env,
        text=True, capture_output=True, timeout=20,
    )
    if result.returncode:
        raise AssertionError('Local settings did not load: ' + result.stderr)
    return json.loads(result.stdout)


class LocalProfileTests(unittest.TestCase):
    def test_inherited_production_configuration_cannot_select_remote_database(self):
        values = inspect_profile()
        self.assertEqual(values['engine'], 'django.db.backends.sqlite3')
        self.assertTrue(Path(values['database']).resolve().is_relative_to(ROOT / 'var'))
        self.assertNotEqual(values['database'], '/never-touch-production.sqlite3')

    def test_local_login_and_http_work_despite_production_environment(self):
        values = inspect_profile()
        self.assertTrue(values['debug'])
        self.assertTrue(values['demo'])
        self.assertTrue(values['local_password'])
        self.assertIn('127.0.0.1', values['hosts'])
        self.assertNotIn('production.example', values['hosts'])
        self.assertNotIn('https://production.example', values['csrf_origins'])
        self.assertFalse(values['secure_cookie'])
        self.assertFalse(values['https_redirect'])

    def test_explicit_scratch_database_stays_separate_and_settings_do_not_create_it(self):
        with tempfile.TemporaryDirectory(prefix='lims-local-check-') as directory:
            scratch = Path(directory) / 'isolated.sqlite3'
            values = inspect_profile(LIMS_LOCAL_DB=str(scratch))
            self.assertEqual(Path(values['database']).resolve(), scratch.resolve())
            self.assertFalse(scratch.exists(), 'Inspecting settings must not open/create a database.')


class LocalRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('lims_local_runner', ROOT / 'scripts' / 'local.py')
        cls.runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.runner)

    def test_child_configuration_does_not_modify_the_parent_environment(self):
        inherited = {
            'DATABASE_URL': 'postgresql://cloud-user:unused@invalid.example/production',
            'DJANGO_SETTINGS_MODULE': 'config.settings',
            'SITE_PASSWORD_HASH': 'production-hash',
            'SQLITE_PATH': '/never-touch-production.sqlite3',
            'SECRET_KEY': 'production-secret',
            'ALLOWED_HOSTS': 'production.example',
            'CSRF_TRUSTED_ORIGINS': 'https://production.example',
            'DEBUG': '0',
            'DEMO_MODE': '0',
        }
        explicit_database = Path(tempfile.gettempdir()) / 'explicit-local.sqlite3'
        with mock.patch.dict(os.environ, inherited):
            before = dict(os.environ)
            env = self.runner.build_environment(explicit_database)
            self.assertEqual(dict(os.environ), before)
        self.assertEqual(env['DJANGO_SETTINGS_MODULE'], 'config.local')
        self.assertEqual(env['LIMS_LOCAL_DB'], str(explicit_database))
        self.assertEqual(env['DEBUG'], '1')
        for key in ('DATABASE_URL', 'SITE_PASSWORD_HASH', 'SQLITE_PATH', 'SECRET_KEY',
                    'ALLOWED_HOSTS', 'CSRF_TRUSTED_ORIGINS'):
            self.assertNotIn(key, env)

    def test_backend_failure_removes_only_its_scratch_directory(self):
        scratch_paths = []

        def fail_manage(args, *, database_path):
            scratch_paths.append(Path(database_path))
            self.assertTrue(Path(database_path).parent.exists())
            raise subprocess.CalledProcessError(2, ['manage.py', *args])

        with tempfile.TemporaryDirectory(prefix='lims-working-db-') as directory:
            working = Path(directory) / 'local.sqlite3'
            working.write_bytes(b'working database must remain untouched')
            with mock.patch.dict(os.environ, {'LIMS_LOCAL_DB': str(working)}), \
                    mock.patch.object(self.runner, 'run_manage', side_effect=fail_manage), \
                    mock.patch.object(self.runner.subprocess, 'run') as child:
                with self.assertRaises(subprocess.CalledProcessError):
                    self.runner.test_backend()
                child.assert_not_called()
            self.assertEqual(working.read_bytes(), b'working database must remain untouched')
        self.assertEqual(len(scratch_paths), 1)
        self.assertFalse(scratch_paths[0].parent.exists())

    def test_backend_commands_share_a_temporary_database_and_remove_it_after_success(self):
        calls = []

        def record_manage(args, *, database_path):
            calls.append((args, Path(database_path)))
            self.assertTrue(Path(database_path).parent.exists())

        with mock.patch.object(self.runner, 'run_manage', side_effect=record_manage), \
                mock.patch.object(self.runner.subprocess, 'run') as child:
            self.runner.test_backend()
        self.assertEqual([args[0] for args, _ in calls], ['check', 'makemigrations', 'test'])
        self.assertEqual(len({path for _, path in calls}), 1)
        self.assertFalse(calls[0][1].parent.exists())
        child.assert_called_once()
        self.assertEqual(child.call_args.kwargs['env']['DJANGO_SETTINGS_MODULE'], 'config.local')


    def test_demo_upgrade_keeps_recoverable_database_backup_when_migration_fails(self):
        with tempfile.TemporaryDirectory(prefix='lims-upgrade-check-') as directory:
            project = Path(directory)
            database = project / 'selected.sqlite3'
            with sqlite3.connect(database) as connection:
                connection.execute('create table user_data (note text)')
                connection.execute('insert into user_data values (?)', ('Сохранённая пользовательская запись',))
            failure = subprocess.CalledProcessError(1, ['manage.py', 'migrate'])
            with mock.patch.object(self.runner, 'ROOT', project), \
                    mock.patch.dict(os.environ, {'LIMS_LOCAL_DB': str(database)}), \
                    mock.patch.object(self.runner, 'run_manage', side_effect=failure), \
                    mock.patch('builtins.print'):
                with self.assertRaises(subprocess.CalledProcessError):
                    self.runner.upgrade_demo()
            backups = list((project / 'var' / 'backups').glob('*.sqlite3'))
            self.assertEqual(len(backups), 1)
            for path in (database, backups[0]):
                with sqlite3.connect(path) as connection:
                    self.assertEqual(connection.execute('select note from user_data').fetchone()[0],
                                     'Сохранённая пользовательская запись')


if __name__ == '__main__':
    unittest.main()
