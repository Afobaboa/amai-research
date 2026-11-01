"""Compatibility entry point: the retired command now loads real data."""
from .seed_initial import Command as InitialCommand


class Command(InitialCommand):
    help = 'Устаревшее имя seed_initial: загрузить реальные зимние данные.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--yes', '--confirm-demo', dest='confirm_demo', action='store_true',
            help='Устаревший параметр совместимости; учебные данные не создаются.',
        )
        parser.add_argument('--upgrade-winter', action='store_true')
