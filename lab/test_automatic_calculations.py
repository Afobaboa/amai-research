"""Automatic calculations and preservation of previously saved results."""
from copy import deepcopy

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from .assistant_tools import ENTITIES, execute_tool
from .calculations import save_calculation
from .models import (
    CalculationRecord, ChangeEvent, EmployeeProfile, Material, MaterialLot,
    RecipeComponent, RecipeGroup, RecipeVersion,
)


class AutomaticCalculationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = EmployeeProfile.objects.create(full_name='Автор расчётов', role='author')
        material = Material.objects.create(name='Компонент')
        lot = MaterialLot.objects.create(
            material=material, lot_code='AUTO', density_g_ml=2, price_per_kg=100,
        )
        cls.recipe = RecipeVersion.objects.create(version_label='AUTO.1', status='published')
        group = RecipeGroup.objects.create(
            recipe_version=cls.recipe, label='A', quantity_basis='mass_percent',
            completeness='complete',
        )
        RecipeComponent.objects.create(
            group=group, position=1, specified_lot=lot, quantity_value=100,
        )

    def setUp(self):
        session = self.client.session
        session.update({'site_authenticated': True, 'employee_id': self.author.pk})
        session.save()

    def test_all_calculations_work_without_template_selection(self):
        for route in ('recipe_create', 'recipe_template_create'):
            page = self.client.get(reverse(route))
            self.assertNotIn('calculation_template', page.context['form'].fields)
            self.assertNotContains(page, 'Шаблоны расчётов')

        record = save_calculation(self.recipe, self.author)
        group = record.outputs['groups'][0]
        self.assertEqual(group['rows'][0]['mass_g'], '1000')
        self.assertEqual(group['rows'][0]['cost'], '100')
        self.assertEqual(group['volume_ml'], '500')
        self.assertEqual(group['total_cost'], '100')
        self.assertEqual(group['cost_per_kg'], '100')
        self.assertEqual(group['density_g_ml'], '2')
        self.assert_all_results_visible(record)
        result = execute_tool('get_lab_record', {
            'entity': 'recipes', 'id': self.recipe.pk, 'section': 'calculations',
        })
        self.assertEqual(result['calculations']['items'][0]['id'], record.pk)

    def assert_all_results_visible(self, record):
        for route, pk in (('recipe_detail', self.recipe.pk), ('calculation_detail', record.pk)):
            page = self.client.get(reverse(route, args=[pk]))
            self.assertEqual(page.context['calculation'], record.outputs)
            for text in ('МАССА, Г', 'ОБЪЁМ, МЛ', 'Стоимость группы', 'Цена смеси за кг', 'Аддитивная плотность'):
                self.assertContains(page, text)
            self.assertNotContains(page, 'Шаблоны расчётов')

    def test_legacy_operation_selection_does_not_hide_or_recalculate_saved_results(self):
        data = deepcopy(save_calculation(self.recipe, self.author).outputs)
        data['template'] = {'id': 123, 'name': 'Старый шаблон', 'version': 2, 'operations': ['density']}
        data['purpose'] = 'publication'
        record = CalculationRecord.objects.create(
            recipe_version=self.recipe, inputs={'template': deepcopy(data['template'])}, outputs=data,
        )
        MaterialLot.objects.update(price_per_kg=999, density_g_ml=4)
        self.assert_all_results_visible(record)
        record.refresh_from_db()
        self.assertEqual(record.outputs, data)

    def test_retired_template_pages_and_assistant_registry_are_removed(self):
        self.assertNotIn('calculation_templates', ENTITIES)
        for path in ('/calculation-templates/', '/calculation-templates/new/',
                     '/calculation-templates/1/', '/calculation-templates/1/fork/'):
            self.assertEqual(self.client.get(path).status_code, 404)
            self.assertEqual(self.client.post(path, {'operations': ['density']}).status_code, 404)
        ChangeEvent.objects.create(
            entity_type='calculation_template', entity_id=123, action='created',
            summary='Старый шаблон', after={'operations': ['density']},
        )
        history = self.client.get(reverse('history'))
        self.assertContains(history, 'Архивный шаблон расчёта #123')
        self.assertNotContains(history, '/calculation-templates/')


class CalculationTemplateRemovalMigrationTests(TransactionTestCase):
    migrate_from = [('lab', '0011_distinguish_initial_metric_labels')]
    migrate_to = [('lab', '0012_remove_calculation_templates')]

    def test_migration_preserves_recipes_and_saved_calculation_snapshots(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        latest = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        old = executor.loader.project_state(self.migrate_from).apps
        template = old.get_model('lab', 'CalculationTemplate').objects.create(
            name='Архивный расчёт', operations=['density'],
        )
        recipe = old.get_model('lab', 'RecipeVersion').objects.create(
            version_label='LEGACY.1', calculation_template=template,
        )
        recipe_template = old.get_model('lab', 'RecipeTemplate').objects.create(
            name='Архивная рецептура', calculation_template=template,
            definition={'components': [], 'filling': []},
        )
        inputs = {'batch_mass_g': '1000', 'template': {'name': template.name, 'operations': ['density']}}
        outputs = {'groups': [{'total_cost': '125', 'density_g_ml': '1.5'}], 'template': deepcopy(inputs['template'])}
        record = old.get_model('lab', 'CalculationRecord').objects.create(
            recipe_version=recipe, template=template, inputs=inputs, outputs=outputs,
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        current = executor.loader.project_state(self.migrate_to).apps
        saved = current.get_model('lab', 'CalculationRecord').objects.get(pk=record.pk)
        self.assertEqual(saved.inputs, inputs)
        self.assertEqual(saved.outputs, outputs)
        self.assertEqual(saved.recipe_version_id, recipe.pk)
        self.assertEqual(current.get_model('lab', 'RecipeVersion').objects.get(pk=recipe.pk).version_label, 'LEGACY.1')
        self.assertEqual(current.get_model('lab', 'RecipeTemplate').objects.get(pk=recipe_template.pk).name, 'Архивная рецептура')
        self.assertNotIn('lab_calculationtemplate', connection.introspection.table_names())
