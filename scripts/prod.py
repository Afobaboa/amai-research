#!/usr/bin/env python3
"""Update the local production checkout and Compose services (Linux server)."""
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ['docker', 'compose']


def run(*args, **kwargs):
    print('+ ' + ' '.join(args), flush=True)
    return subprocess.run(args, cwd=ROOT, check=True, **kwargs)


def git_output(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def deploy():
    if git_output('branch', '--show-current') != 'main':
        raise RuntimeError('Переключитесь на main перед make prod.')
    if git_output('status', '--porcelain', '--untracked-files=all'):
        raise RuntimeError('Рабочая копия изменена. Сохраните изменения в Git перед make prod.')
    if not (ROOT / '.env').is_file():
        raise RuntimeError('Сначала заполните .env по образцу .env.example.')
    run(*COMPOSE, 'version')
    help_result = run(*COMPOSE, 'up', '--help', stdout=subprocess.PIPE, text=True)
    if '--wait-timeout' not in help_result.stdout:
        raise RuntimeError('Обновите Docker Compose: нужен up --wait --wait-timeout.')
    previous = git_output('rev-parse', 'HEAD')
    run('git', 'fetch', 'origin', 'main')
    target = git_output('rev-parse', 'FETCH_HEAD')
    # --ff-only alone also accepts a local main ahead of origin/main.
    run('git', 'merge-base', '--is-ancestor', previous, target)
    run('git', 'merge', '--ff-only', target)
    run(*COMPOSE, 'config', '--quiet')
    # Keep serving the old containers throughout build and preflight checks.
    run(*COMPOSE, 'build', 'initialize', 'web', 'assistant-worker')
    run(*COMPOSE, 'run', '--rm', '--no-deps', '--entrypoint', 'python',
        'web', 'manage.py', 'check', '--deploy', '--fail-level', 'ERROR')
    run(*COMPOSE, 'run', '--rm', '--no-deps', 'caddy',
        'caddy', 'validate', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile')
    # Never recreate the database automatically when its image/config changes.
    run(*COMPOSE, 'up', '-d', '--no-recreate', '--wait', '--wait-timeout', '120', 'db')
    backups = ROOT / 'backups'
    backups.mkdir(mode=0o700, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix='prod-', dir=backups))
    (backup / 'release.json').write_text(json.dumps({
        'previous_checkout': previous, 'target': target,
    }, indent=2) + '\n')
    shutil.copyfile(ROOT / '.env', backup / '.env')
    os.chmod(backup / '.env', 0o600)
    print(f'Резервная копия: {backup}', flush=True)
    try:
        # Block all application writers, including in-flight assistant jobs.
        run(*COMPOSE, 'stop', '-t', '300', 'caddy', 'web', 'assistant-worker')
        with (backup / 'database.dump.partial').open('wb') as output:
            run(*COMPOSE, 'exec', '-T', 'db', 'sh', '-c',
                'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc', stdout=output)
        with (backup / 'database.dump.partial').open('rb') as source:
            run(*COMPOSE, 'exec', '-T', 'db', 'pg_restore', '--list',
                stdin=source, stdout=subprocess.DEVNULL)
        (backup / 'database.dump.partial').rename(backup / 'database.dump')
        with (backup / 'media.tar.gz.partial').open('wb') as output:
            run(*COMPOSE, 'run', '--rm', '--no-deps', '--user', 'root',
                '--entrypoint', 'tar', 'web', '-czf', '-', '-C', '/app/media', '.',
                stdout=output)
        (backup / 'media.tar.gz.partial').rename(backup / 'media.tar.gz')
        (backup / 'COMPLETE').touch()
        # A one-off job guarantees migrations run even if initialize already exited.
        run(*COMPOSE, 'run', '--rm', '--no-deps', 'initialize')
        run(*COMPOSE, 'up', '-d', '--no-deps', '--force-recreate',
            '--wait', '--wait-timeout', '180', 'web', 'assistant-worker')
        # Recreate Caddy too: .env and its bind-mounted config may have changed.
        run(*COMPOSE, 'up', '-d', '--no-deps', '--force-recreate',
            '--wait', '--wait-timeout', '60', 'caddy')
    except (Exception, KeyboardInterrupt):
        # Schema changes and seed operations cannot safely be undone by starting
        # an old image. Keep the failed release closed for manual recovery.
        subprocess.run([*COMPOSE, 'stop', '-t', '300', 'caddy', 'web', 'assistant-worker'],
                       cwd=ROOT, check=False)
        print(f'Обновление не завершено; сайт остановлен. Копия: {backup}\n'
              'Только каталог с COMPLETE содержит полную резервную копию.\n'
              'Проверьте ошибку, исправьте причину и повторите make prod.\n'
              'Не запускайте старый код без проверки совместимости схемы БД.',
              file=sys.stderr, flush=True)
        raise
    print(f'Продакшен обновлён до {target}. Резервная копия: {backup}', flush=True)


def main():
    os.umask(0o077)
    # The lock is inside .git so it neither dirties the checkout nor enters builds.
    git_dir = Path(git_output('rev-parse', '--absolute-git-dir'))
    with (git_dir / 'prod.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Другой make prod уже выполняется.') from None
        deploy()


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f'make prod: {error}', file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
