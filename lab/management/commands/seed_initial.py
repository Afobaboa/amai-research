"""Initialize real winter data and the requested staff profiles."""
from django.core.management.base import BaseCommand, CommandError
from django.db.models.deletion import ProtectedError

from lab.initial_data import seed_initial


class Command(BaseCommand):
    help = 'Перенести проверенные зимние данные Excel и заменить прежний демонабор.'

    def handle(self, *args, **options):
        try:
            counts = seed_initial()
        except ProtectedError as error:
            raise CommandError(
                'Учебные записи используются другими документами. '
                'Изменения отменены; сначала проверьте эти связи.'
            ) from error
        self.stdout.write(self.style.SUCCESS(
            'Начальные данные готовы: '
            f"новых рецептур — {counts['recipes']}, "
            f"карточек — {counts['tests']}, "
            f"источников — {counts['sources']}. "
            f"Удалено учебных рецептур — {counts['removed_recipes']}, "
            f"карточек — {counts['removed_tests']}."
        ))
