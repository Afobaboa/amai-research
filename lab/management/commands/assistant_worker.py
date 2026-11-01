import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from lab.assistant_worker import recover_stale, run_next


class Command(BaseCommand):
    help = 'Process persistent assistant jobs. Run separately from the web server.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')

    def handle(self, *args, **options):
        self.stdout.write('Assistant worker started')
        try:
            while True:
                close_old_connections()
                recover_stale()
                worked = run_next()
                if options['once']:
                    break
                if not worked:
                    time.sleep(1)
        except KeyboardInterrupt:
            self.stdout.write('Assistant worker stopped')
