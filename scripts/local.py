#!/usr/bin/env python3
"""Portable entry points for local development and isolated backend checks."""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
import tempfile
import venv
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
PYTHON = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def build_environment(database_path=None):
    """Keep local children independent of a developer's cloud configuration."""
    env = os.environ.copy()
    for key in ('DATABASE_URL', 'SITE_PASSWORD_HASH', 'SHARED_PASSWORD_HASH', 'SQLITE_PATH',
                'SECRET_KEY', 'ALLOWED_HOSTS', 'CSRF_TRUSTED_ORIGINS', 'PYTHONPATH'):
        env.pop(key, None)
    env.update(DJANGO_SETTINGS_MODULE='config.local', DEBUG='1', DEMO_MODE='1', PYTHONUNBUFFERED='1')
    if database_path is not None:
        env['LIMS_LOCAL_DB'] = str(database_path)
    return env


def run_manage(args, *, database_path=None):
    if not PYTHON.is_file():
        raise SystemExit('Сначала выполните make setup (или python3 scripts/local.py setup).')
    subprocess.run([str(PYTHON), 'manage.py', *args], cwd=ROOT,
                   env=build_environment(database_path), check=True)


def setup():
    if not PYTHON.is_file():
        venv.EnvBuilder(with_pip=True).create(ROOT / '.venv')
    subprocess.run([str(PYTHON), '-m', 'pip', 'install', '-r', 'requirements.txt'],
                   cwd=ROOT, check=True)
    init_demo()


def init_demo():
    run_manage(['migrate', '--noinput'])
    run_manage(['seed_demo', '--confirm-demo'])


def upgrade_demo():
    """Back up the selected local SQLite before schema and fixture upgrades."""
    database = Path(os.environ.get('LIMS_LOCAL_DB', str(ROOT / 'var' / 'local.sqlite3'))).expanduser()
    if not database.is_absolute():
        database = ROOT / database
    if database.is_file():
        backups = ROOT / 'var' / 'backups'
        backups.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
        backup = backups / f'{database.stem}-before-winter-{stamp}-{uuid4().hex[:8]}.sqlite3'
        with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as original:
            with sqlite3.connect(backup) as destination:
                original.backup(destination)
        print(f'Резервная копия: {backup}', flush=True)
    run_manage(['migrate', '--noinput'])
    run_manage(['seed_demo', '--confirm-demo', '--upgrade-winter'])


def test_backend():
    with tempfile.TemporaryDirectory(prefix='lims-backend-') as scratch:
        database_path = Path(scratch) / 'backend.sqlite3'
        run_manage(['check'], database_path=database_path)
        run_manage(['makemigrations', '--check', '--dry-run'], database_path=database_path)
        run_manage(['test', 'lab', '--noinput'], database_path=database_path)
    subprocess.run([str(PYTHON), '-m', 'unittest', 'discover', '-s', 'checks', '-p', 'test_*.py'],
                   cwd=ROOT, env=build_environment(), check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['setup', 'init-demo', 'upgrade-demo', 'dev', 'test-backend', 'check', 'manage'])
    parser.add_argument('--port', type=int, default=8000)
    args, extra = parser.parse_known_args()
    if args.command != 'manage' and extra:
        parser.error('Неизвестные аргументы: ' + ' '.join(extra))
    if args.command == 'setup':
        setup()
    elif args.command == 'init-demo':
        init_demo()
    elif args.command == 'upgrade-demo':
        upgrade_demo()
    elif args.command == 'dev':
        if not 1 <= args.port <= 65535:
            parser.error('Порт должен находиться в диапазоне 1–65535.')
        print(f'Фронт и бэкенд: http://127.0.0.1:{args.port}/\nЛокальный пароль: lims-demo', flush=True)
        run_manage(['runserver', f'127.0.0.1:{args.port}'])
    elif args.command == 'test-backend':
        test_backend()
    elif args.command == 'check':
        run_manage(['check'])
        run_manage(['makemigrations', '--check', '--dry-run'])
    else:
        run_manage(extra)


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode)
    except KeyboardInterrupt:
        raise SystemExit(130)
