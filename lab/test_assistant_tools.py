"""Tools return laboratory evidence without write or filesystem capabilities."""
import json
from decimal import Decimal
from pathlib import Path
import tempfile

from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from .assistant_tools import MAX_OUTPUT, execute_tool
from .models import (
    EmployeeProfile, Material, MaterialLot, Metric, Observation, RecipeComponent,
    RecipeGroup, RecipeMatch, RecipeVersion, Result, SessionCondition, SourceCell,
    SourceFile, SourceSheet, TestRun, TestSession,
)


class LaboratoryAssistantToolTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.employee = EmployeeProfile.objects.create(full_name='Не передавать профиль сотрудника')
        cls.material = Material.objects.create(name='Полиол А', density_g_ml=Decimal('1.10'), price_per_kg=0)
        cls.lot = MaterialLot.objects.create(material=cls.material, lot_code='А-01')
        cls.source = SourceFile.objects.create(title='Протокол', original_path='Архив/опыт.xlsx')
        cls.sheet = SourceSheet.objects.create(file=cls.source, name='Испытания')
        cls.cell = SourceCell.objects.create(sheet=cls.sheet, address='C12', cell_kind='number', raw_value='42')
        cls.recipe = RecipeVersion.objects.create(version_label='R.1', status='published', created_by=cls.employee, source_sheet=cls.sheet)
        cls.group = RecipeGroup.objects.create(recipe_version=cls.recipe, label='A', quantity_basis='mass_percent', completeness='complete')
        RecipeComponent.objects.create(group=cls.group, position=1, specified_lot=cls.lot, quantity_value=100)
        cls.test = TestSession.objects.create(identifier='E-01', status='completed', source_sheet=cls.sheet)
        RecipeMatch.objects.create(session=cls.test, recipe_version=cls.recipe, status='candidate', reason='Совпал номер; состав не подтверждён')
        cls.test_run = TestRun.objects.create(session=cls.test, scope='При -10 °C', method_code='M-1')
        cls.metric = Metric.objects.create(code='VOLUME', label='Выход', unit_code='л')
        cls.condition_metric = Metric.objects.create(code='TEMP', label='Температура', unit_code='°C')
        SessionCondition.objects.create(session=cls.test, metric=cls.condition_metric, numeric_value=-10)
        cls.result = Result.objects.create(test_run=cls.test_run, metric=cls.metric, numeric_value=42, source_cell=cls.cell)
        Result.objects.create(test_run=cls.test_run, metric=cls.metric, numeric_value=0)
        Result.objects.create(test_run=cls.test_run, metric=cls.metric, origin='calculated', value_state='missing_inputs')
        Observation.objects.create(test_run=cls.test_run, sequence_no=1, text_value='Мелкая ровная ячейка')

    def test_recipe_detail_has_plan_composition_and_provenance_without_employee(self):
        data = execute_tool('get_lab_record', {'entity': 'recipes', 'id': self.recipe.pk})
        self.assertEqual(data['components']['items'][0]['quantity_value'], '100.00000000')
        self.assertEqual(data['components']['items'][0]['specified_lot__material__name'], 'Полиол А')
        self.assertEqual(data['source']['sheet'], 'Испытания')
        source = execute_tool('get_lab_record', {'entity': 'source_cells', 'id': self.cell.pk})
        self.assertEqual(source['source']['address'], 'C12')
        self.assertEqual(source['url'], f'/sources/{self.source.pk}/')
        self.assertEqual(data['test_links']['items'][0]['status'], 'candidate')
        self.assertEqual(data['url'], f'/recipes/{self.recipe.pk}/')
        self.assertNotIn('created_by', json.dumps(data))
        self.assertNotIn(self.employee.full_name, json.dumps(data, ensure_ascii=False))

    def test_test_card_preserves_missing_zero_units_context_and_link_status(self):
        data = execute_tool('get_lab_record', {'entity': 'tests', 'id': self.test.pk})
        self.assertEqual(data['conditions']['items'][0]['numeric_value'], '-10.00000000')
        self.assertEqual(data['recipe_links']['items'][0]['status'], 'candidate')
        self.assertEqual(data['observations']['items'][0]['text_value'], 'Мелкая ровная ячейка')
        rows = data['results']['items']
        self.assertEqual(rows[0]['source']['address'], 'C12')
        self.assertEqual(rows[0]['metric__unit_code'], 'л')
        self.assertEqual(Decimal(rows[1]['numeric_value']), Decimal('0'))
        self.assertIsNone(rows[2]['numeric_value'])
        self.assertEqual(rows[2]['value_state'], 'missing_inputs')

    def test_numeric_metric_filter_sort_and_material_recipe_links(self):
        data = execute_tool('search_lab_data', {'entity': 'results', 'metric_code': 'VOLUME',
            'min_value': '1', 'max_value': '45', 'order': 'value_desc', 'material_id': self.material.pk})
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['items'][0]['id'], self.result.pk)
        self.assertEqual(data['items'][0]['url'], f'/tests/{self.test.pk}/')
        cards = execute_tool('search_lab_data', {'entity': 'tests', 'metric_code': 'VOLUME', 'min_value': '40', 'recipe_id': self.recipe.pk})
        self.assertEqual(cards['total'], 1)
        recipes = execute_tool('search_lab_data', {'entity': 'recipes', 'material_id': self.material.pk})
        self.assertEqual(recipes['total'], 1)
        by_observation = execute_tool('search_lab_data', {'entity': 'tests', 'q': 'Мелкая'})
        self.assertEqual(by_observation['total'], 1)

    def test_cyrillic_search_is_case_insensitive_across_related_fields(self):
        queries = [('materials', 'пОлИоЛ', 1), ('recipes', 'полиол', 1),
                   ('tests', 'мЕЛКАЯ', 1), ('results', 'вЫхОд', 3), ('sources', 'прОТОКОЛ', 1)]
        for entity, query, expected in queries:
            with self.subTest(entity=entity, query=query):
                data = execute_tool('search_lab_data', {'entity': entity, 'q': query})
                self.assertEqual(data['total'], expected)
        self.assertEqual(Material.objects.get(pk=self.material.pk).name, 'Полиол А')

    def test_pagination_reports_exact_total_and_continuation(self):
        for index in range(44):
            Material.objects.create(name=f'Material-{index}')
        first = execute_tool('search_lab_data', {'entity': 'materials', 'limit': 40})
        self.assertEqual(first['total'], 45)
        self.assertEqual(len(first['items']), 40)
        self.assertTrue(first['truncated'])
        tail = execute_tool('search_lab_data', {'entity': 'materials', 'limit': 40, 'offset': first['next_offset']})
        self.assertEqual(len(tail['items']), 5)
        self.assertFalse(tail['truncated'])
        self.assertIsNone(tail['next_offset'])

    def test_every_entity_detail_and_search_is_read_only(self):
        from .assistant_tools import ENTITIES
        with CaptureQueriesContext(connection) as queries:
            for entity in ENTITIES:
                data = execute_tool('search_lab_data', {'entity': entity})
                self.assertNotIn('error', data, entity)
                for item in data['items']:
                    detail = execute_tool('get_lab_record', {'entity': entity, 'id': item['id']})
                    self.assertNotIn('error', detail, entity)
                    json.dumps(detail)
        for query in queries:
            self.assertTrue(query['sql'].lstrip().upper().startswith('SELECT'), query['sql'])

    def test_invalid_arguments_are_errors_and_sql_is_literal_text(self):
        cases = [
            ('run_sql', {'query': 'DELETE FROM lab_material'}),
            ('search_lab_data', {'entity': 'employees'}),
            ('search_lab_data', {'entity': 'materials', 'limit': 41}),
            ('search_lab_data', {'entity': 'materials', 'limit': True}),
            ('search_lab_data', {'entity': 'materials', 'offset': -1}),
            ('search_lab_data', {'entity': 'materials', 'filter': {'supplier': 'A'}}),
            ('search_lab_data', {'entity': 'results', 'order': 'value_asc'}),
            ('search_lab_data', {'entity': 'results', 'min_value': 'NaN'}),
            ('search_lab_data', {'entity': 'results', 'min_value': '2', 'max_value': '1'}),
            ('get_lab_record', {'entity': 'materials', 'id': True}),
            ('get_lab_record', {'entity': 'materials', 'id': 99999}),
        ]
        for name, arguments in cases:
            with self.subTest(name=name, args=arguments):
                self.assertIn('error', execute_tool(name, arguments))
        result = execute_tool('search_lab_data', {'entity': 'materials', 'q': "'; DROP TABLE lab_material; --"})
        self.assertEqual(result['total'], 0)
        self.assertEqual(Material.objects.count(), 1)

    def test_large_data_is_explicitly_bounded(self):
        self.test.notes = 'A' * 90000
        self.test.save(update_fields=['notes'])
        for index in range(1, 43):
            Observation.objects.create(test_run=self.test_run, sequence_no=index + 1, text_value='Б' * 5000)
        data = execute_tool('get_lab_record', {'entity': 'tests', 'id': self.test.pk})
        self.assertLessEqual(len(json.dumps(data, ensure_ascii=False)), MAX_OUTPUT)
        self.assertEqual(data['observations']['total'], 43)
        self.assertTrue(data['observations']['truncated'])
        self.assertIn('Текст сокращён', data['notes'])

    def test_nested_recipe_and_test_collections_can_be_read_completely(self):
        for index in range(42):
            RecipeComponent.objects.create(group=self.group, position=index + 2,
                unbatched_material=self.material, quantity_value=0)
            Observation.objects.create(test_run=self.test_run, sequence_no=index + 2,
                text_value='Наблюдение ' + str(index))
        first = execute_tool('get_lab_record', {'entity': 'recipes', 'id': self.recipe.pk, 'limit': 40})
        self.assertEqual(first['components']['total'], 43)
        self.assertEqual(first['components']['offset'], 0)
        self.assertEqual(first['components']['next_offset'], 40)
        tail = execute_tool('get_lab_record', {'entity': 'recipes', 'id': self.recipe.pk,
            'offset': 40, 'limit': 40, 'section': 'components'})
        self.assertEqual(len(tail['components']['items']), 3)
        self.assertIsNone(tail['components']['next_offset'])
        self.assertNotIn('groups', tail)
        observed, offset = [], 0
        while offset is not None:
            page = execute_tool('get_lab_record', {'entity': 'tests', 'id': self.test.pk,
                'section': 'observations', 'offset': offset, 'limit': 17})['observations']
            observed.extend(row['id'] for row in page['items'])
            offset = page['next_offset']
        self.assertEqual(observed, list(Observation.objects.order_by('id').values_list('id', flat=True)))
        self.assertIn('error', execute_tool('get_lab_record', {'entity': 'tests', 'id': self.test.pk, 'section': 'components'}))

    def test_output_budget_trimming_never_skips_related_rows(self):
        for index in range(42):
            Observation.objects.create(test_run=self.test_run, sequence_no=index + 2, text_value='О' * 5000)
        observed, offset = [], 0
        while offset is not None:
            page = execute_tool('get_lab_record', {'entity': 'tests', 'id': self.test.pk,
                'section': 'observations', 'offset': offset, 'limit': 40})
            self.assertNotIn('error', page)
            observations = page['observations']
            self.assertGreater(len(observations['items']), 0)
            observed.extend(row['id'] for row in observations['items'])
            offset = observations['next_offset']
        self.assertEqual(observed, list(Observation.objects.order_by('id').values_list('id', flat=True)))

    def test_combined_recipe_material_and_status_filters_use_the_same_link(self):
        other_material = Material.objects.create(name='Другой материал')
        other_recipe = RecipeVersion.objects.create(version_label='OTHER')
        group = RecipeGroup.objects.create(recipe_version=other_recipe, label='B')
        RecipeComponent.objects.create(group=group, position=1, unbatched_material=other_material, quantity_value=100)
        RecipeMatch.objects.create(session=self.test, recipe_version=other_recipe, status='confirmed', reason='Проверено')
        for entity in ('tests', 'results'):
            with self.subTest(entity=entity):
                wrong_material = execute_tool('search_lab_data', {'entity': entity,
                    'recipe_id': self.recipe.pk, 'material_id': other_material.pk})
                self.assertEqual(wrong_material['total'], 0)
                wrong_status = execute_tool('search_lab_data', {'entity': entity,
                    'recipe_id': self.recipe.pk, 'match_status': 'confirmed'})
                self.assertEqual(wrong_status['total'], 0)
                matching = execute_tool('search_lab_data', {'entity': entity,
                    'recipe_id': self.recipe.pk, 'material_id': self.material.pk, 'match_status': 'candidate'})
                self.assertEqual(matching['total'], 1 if entity == 'tests' else 3)
        # The two range comparisons must also match the very same result.
        ranged = execute_tool('search_lab_data', {'entity': 'tests', 'metric_code': 'VOLUME',
            'min_value': '1', 'max_value': '40'})
        self.assertEqual(ranged['total'], 0)


class AssistantProjectFileTests(SimpleTestCase):
    def test_real_templates_and_docs_are_readable_with_line_numbers(self):
        listing = execute_tool('list_project_files', {'query': 'base.html'})
        self.assertTrue(any(item['path'] == 'lab/templates/lab/base.html' for item in listing['items']))
        data = execute_tool('read_project_file', {'path': 'lab/templates/lab/base.html', 'start_line': 2, 'line_count': 2})
        self.assertEqual([row['number'] for row in data['lines']], [2, 3])
        self.assertTrue(data['truncated'])
        self.assertEqual(data['next_line'], 4)
        self.assertIn('README.md', [item['path'] for item in execute_tool('list_project_files', {})['items']])

    def test_configuration_credentials_uploads_and_traversal_are_not_accessible(self):
        for path in ('.env', '.env.example', 'config/settings.py', 'config/local.py', 'media/file.txt',
                     '/etc/passwd', '../README.md', 'lab/templates/lab/../../../.env',
                     'lab/assistant_tools.py', 'lab/models.py'):
            with self.subTest(path=path):
                self.assertIn('error', execute_tool('read_project_file', {'path': path}))

    def test_symlink_escape_and_symlink_directory_are_excluded(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            target = Path(outside) / 'secret.md'
            target.write_text('private-api-key', encoding='utf-8')
            (root / 'README.md').symlink_to(target)
            (root / 'docs').mkdir()
            (root / 'docs/alias.md').symlink_to(target)
            (root / 'lab/templates').mkdir(parents=True)
            (root / 'lab/templates/lab').symlink_to(Path(outside), target_is_directory=True)
            with override_settings(BASE_DIR=root):
                self.assertEqual(execute_tool('list_project_files', {})['items'], [])
                self.assertIn('error', execute_tool('read_project_file', {'path': 'README.md'}))

    def test_long_minified_source_can_be_read_to_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'lab/static/lab').mkdir(parents=True)
            code = 'x' * 12000 + '\nlast line'
            (root / 'lab/static/lab/app.js').write_text(code)
            chunks = []
            with override_settings(BASE_DIR=root):
                line, column = 1, 1
                while line is not None:
                    result = execute_tool('read_project_file', {'path': 'lab/static/lab/app.js', 'start_line': line, 'start_column': column})
                    self.assertNotIn('error', result)
                    chunks.extend(item['text'] for item in result['lines'])
                    line, column = result['next_line'], result['next_column']
                self.assertEqual(''.join(chunks), code.replace('\n', ''))
                self.assertFalse(result['truncated'])
