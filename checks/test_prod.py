"""Exercise production failure boundaries without Docker or a real deployment."""
import importlib.util
import fcntl
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'prod.py'
SPEC = importlib.util.spec_from_file_location('prod', SCRIPT)
prod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prod)


class ProductionTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        (self.root / '.env').write_text('SECRET_KEY=test-only\n')
        self.calls = []
        self.failure = None
        self.branch = 'main'
        self.dirty = ''
        self.help = '--wait --wait-timeout'
        self.patch('ROOT', self.root)
        self.patch('git_output', self.git_output)
        self.patch('run', self.run_command)
        cleanup_patch = patch.object(prod.subprocess, 'run')
        self.cleanup = cleanup_patch.start()
        self.addCleanup(cleanup_patch.stop)

    def patch(self, name, value):
        p = patch.object(prod, name, value)
        result = p.start()
        self.addCleanup(p.stop)
        return result

    def git_output(self, *args):
        if args[0] == 'branch':
            return self.branch
        if args[0] == 'status':
            return self.dirty
        return 'new-sha' if args[-1] == 'FETCH_HEAD' else 'old-sha'

    def run_command(self, *args, **kwargs):
        self.calls.append(args)
        if self.failure and self.failure(args):
            raise subprocess.CalledProcessError(23, args)
        output = kwargs.get('stdout')
        if hasattr(output, 'write'):
            output.write(b'backup contents')
        return subprocess.CompletedProcess(args, 0, self.help)

    def index(self, *parts):
        return next(i for i, call in enumerate(self.calls)
                    if all(part in call for part in parts))

    def backup(self):
        return next((self.root / 'backups').iterdir())

    def test_success_orders_build_backup_migration_health_and_proxy(self):
        prod.deploy()
        self.assertLess(self.index('build'), self.index('stop'))
        self.assertLess(self.index('stop'), self.index('pg_restore'))
        self.assertLess(self.index('pg_restore'), self.index('--entrypoint', 'tar'))
        self.assertLess(self.index('--entrypoint', 'tar'), self.index('run', 'initialize'))
        self.assertLess(self.index('run', 'initialize'), self.index('up', 'web'))
        self.assertLess(self.index('up', 'web'), self.index('up', 'caddy'))
        self.assertIn('--wait', self.calls[self.index('up', 'web')])
        self.assertIn('--no-recreate', self.calls[self.index('up', 'db')])
        self.assertTrue((self.backup() / 'COMPLETE').exists())
        self.assertEqual((self.backup() / '.env').stat().st_mode & 0o777, 0o600)
        self.cleanup.assert_not_called()

    def test_dirty_checkout_and_wrong_branch_never_touch_docker(self):
        for branch, dirty in [('feature', ''), ('main', ' M file'), ('main', '?? new')]:
            self.branch, self.dirty = branch, dirty
            with self.assertRaises(RuntimeError):
                prod.deploy()
            self.assertEqual(self.calls, [])

    def test_ahead_or_divergent_main_never_builds_or_stops(self):
        self.failure = lambda args: 'merge-base' in args
        with self.assertRaises(subprocess.CalledProcessError):
            prod.deploy()
        self.assertFalse(any('build' in call or 'stop' in call for call in self.calls))

    def test_old_compose_fails_before_fetch(self):
        self.help = 'up help without wait support'
        with self.assertRaises(RuntimeError):
            prod.deploy()
        self.assertFalse(any('fetch' in call for call in self.calls))

    def test_missing_environment_never_touches_docker(self):
        (self.root / '.env').unlink()
        with self.assertRaises(RuntimeError):
            prod.deploy()
        self.assertEqual(self.calls, [])

    def test_concurrent_deploy_is_rejected(self):
        with (self.root / 'prod.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(prod, 'git_output', return_value=str(self.root)):
                with self.assertRaisesRegex(RuntimeError, 'уже выполняется'):
                    prod.main()
        self.assertEqual(self.calls, [])

    def test_build_failure_keeps_existing_site_running(self):
        self.failure = lambda args: 'build' in args
        with self.assertRaises(subprocess.CalledProcessError):
            prod.deploy()
        self.assertFalse(any('stop' in call for call in self.calls))
        self.cleanup.assert_not_called()

    def test_backup_failure_never_migrates(self):
        self.failure = lambda args: 'pg_restore' in args
        with self.assertRaises(subprocess.CalledProcessError):
            prod.deploy()
        self.assertFalse((self.backup() / 'COMPLETE').exists())
        self.assertFalse(any('initialize' in call and 'run' in call for call in self.calls))
        self.cleanup.assert_called_once()

    def test_failed_migration_leaves_backup_and_does_not_start_new_code(self):
        self.failure = lambda args: 'run' in args and args[-1] == 'initialize'
        with self.assertRaises(subprocess.CalledProcessError):
            prod.deploy()
        self.assertTrue((self.backup() / 'COMPLETE').exists())
        self.assertFalse(any('up' in call and 'web' in call for call in self.calls))
        self.cleanup.assert_called_once()

    def test_failed_web_health_never_opens_proxy(self):
        self.failure = lambda args: 'up' in args and 'web' in args
        with self.assertRaises(subprocess.CalledProcessError):
            prod.deploy()
        self.assertFalse(any('up' in call and 'caddy' in call for call in self.calls))
        self.cleanup.assert_called_once()


if __name__ == '__main__':
    unittest.main()
