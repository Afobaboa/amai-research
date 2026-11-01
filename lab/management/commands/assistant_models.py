"""Inspect the model IDs available to the configured Cloud.ru key."""
from django.core.management.base import BaseCommand, CommandError

from lab.assistant import AssistantError, list_models


class Command(BaseCommand):
    help = 'Проверить ключ Cloud.ru и вывести доступные модели (без вывода ключа).'

    def handle(self, *args, **options):
        try:
            models = list_models()
        except AssistantError as error:
            raise CommandError(str(error)) from None
        for model in sorted(models):
            self.stdout.write(model)
