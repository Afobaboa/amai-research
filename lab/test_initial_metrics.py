"""Existing metric names can be upgraded without changing result links."""
from importlib import import_module

from django.apps import apps
from django.db import connection
from django.test import TestCase

from .models import Metric


class InitialMetricMigrationTests(TestCase):
    def test_upgrade_renames_standard_labels_and_preserves_custom_labels(self):
        migration = import_module(
            'lab.migrations.0011_distinguish_initial_metric_labels',
        )
        for code, previous, label in migration.RENAMES:
            metric = Metric.objects.create(code=code, label=previous)
            migration.rename_initial_metrics(apps, connection.schema_editor())
            metric.refresh_from_db()
            self.assertEqual(metric.label, label)
            self.assertEqual(metric.code, code)
            metric.label = 'Название сотрудника'
            metric.save(update_fields=['label'])
            migration.rename_initial_metrics(apps, connection.schema_editor())
            metric.refresh_from_db()
            self.assertEqual(metric.label, 'Название сотрудника')
