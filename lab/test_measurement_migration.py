"""An existing experiment upgrades without changing evidence or result IDs."""
from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class MeasurementMigrationTests(TransactionTestCase):
    previous = [('lab', '0013_measurement_groups')]
    current = [('lab', '0015_remove_legacy_conditions')]

    def setUp(self):
        executor = MigrationExecutor(connection)
        self.latest = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(self.latest))
        executor.migrate(self.previous)
        self.old = executor.loader.project_state(self.previous).apps

    def model(self, name):
        return self.old.get_model('lab', name)

    def create_evidence(self):
        source = self.model('SourceFile').objects.create(title='Исходный протокол')
        sheet = self.model('SourceSheet').objects.create(file=source, name='Лист')
        self.cell = self.model('SourceCell').objects.create(
            sheet=sheet, address='C6', raw_value='0',
        )
        self.card = self.model('TestSession').objects.create(
            identifier='MIGRATION', status='completed', source_sheet=sheet,
        )
        self.metric = self.model('Metric').objects.create(
            code='CUSTOM_TEMP', label='Температура сотрудника', unit_code='°C',
        )
        self.text_metric = self.model('Metric').objects.create(
            code='CUSTOM_TEXT', label='Общая заметка', value_kind='text',
        )

    def create_legacy_records(self):
        self.run = self.model('TestRun').objects.create(
            session=self.card, scope='Параметры карточки', scope_cell=self.cell,
        )
        self.result = self.model('Result').objects.create(
            test_run=self.run, metric=self.metric, numeric_value=7,
            source_cell=self.cell,
        )
        self.calculated = self.model('Result').objects.create(
            test_run=self.run, metric=self.metric, numeric_value=14,
            origin='calculated', calculation_code='C6*2', source_cell=self.cell,
        )
        self.model('ResultDependency').objects.create(
            result=self.calculated, input_result=self.result, role='input',
        )
        self.model('Observation').objects.create(
            test_run=self.run, sequence_no=1, text_value='  Дословно\n ',
            source_cell=self.cell,
        )
        for metric, values in ((self.metric, {'numeric_value': 0}),
                               (self.text_metric, {'text_value': '  Условие\n '})):
            self.model('SessionCondition').objects.create(
                session=self.card, metric=metric, source_cell=self.cell, **values,
            )

    def test_upgrade_preserves_values_sources_observations_and_dependencies(self):
        self.create_evidence()
        self.create_legacy_records()
        executor = MigrationExecutor(connection)
        executor.migrate(self.current)
        current = executor.loader.project_state(self.current).apps
        run = current.get_model('lab', 'TestRun').objects.get(pk=self.run.pk)
        self.assertEqual(run.scope, 'Основные параметры')
        self.assertEqual(run.scope_cell_id, self.cell.pk)
        self.assertEqual(run.measurement_group.code, 'basic_parameters')
        self.assertEqual(run.results.count(), 4)
        self.assertEqual(run.results.get(pk=self.result.pk).numeric_value, Decimal(7))
        self.assertEqual(run.results.get(text_value='  Условие\n ').source_cell_id, self.cell.pk)
        self.assertEqual(run.results.get(numeric_value=0).metric_id, self.metric.pk)
        self.assertEqual(run.observations.get().text_value, '  Дословно\n ')
        calculated = run.results.get(pk=self.calculated.pk)
        self.assertEqual(calculated.calculation_code, 'C6*2')
        self.assertEqual(calculated.dependencies.get().input_result_id, self.result.pk)
        self.assertEqual(run.session.status, 'completed')
        self.assertNotIn('lab_sessioncondition', connection.introspection.table_names())
