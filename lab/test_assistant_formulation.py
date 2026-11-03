"""The assistant sees the same recipe documents and calculations as the card."""
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from .assistant_tools import MAX_OUTPUT, execute_tool
from .models import CalculationRecord, Material, MaterialLot, RecipeGroup, RecipeVersion
from .recipe_math import calculate_formulation


def document_keys(value):
    if isinstance(value, dict):
        keys = {key.removesuffix('_display') for key in value}
        return keys | set().union(*(document_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(document_keys(item) for item in value))
    return set()


class AssistantFormulationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        bundle = json.loads((Path(__file__).resolve().parents[1]
                             / 'initial_data/winter.json').read_text())
        cls.archive_plans = [
            sheet['recipe']['formulation']
            for file in bundle['files'] for sheet in file['sheets']
            if sheet.get('recipe', {}).get('formulation')
        ]
        cls.plan = next(
            sheet['recipe']['formulation']
            for file in bundle['files'] for sheet in file['sheets']
            if sheet['name'] == '316.18' and 'recipe' in sheet
        )
        cls.material = Material.objects.create(name='Полиол для проверки')
        cls.lot = MaterialLot.objects.create(
            material=cls.material, lot_code='NOW', density_g_ml='9',
            price_per_kg='999', price_currency='USD',
        )
        cls.plan['polyol'][0].update(material_id=cls.material.pk, lot_id=cls.lot.pk)
        cls.recipe = RecipeVersion.objects.create(
            version_label='316.18', formulation=cls.plan, batch_mass_g=350,
        )

    def read(self, section='summary', **arguments):
        result = execute_tool('get_lab_record', {
            'entity': 'recipes', 'id': self.recipe.pk, 'section': section,
            **arguments,
        })
        self.assertNotIn('error', result)
        return result

    def snapshot(self, purpose='publication', result=None):
        return CalculationRecord.objects.create(
            recipe_version=self.recipe,
            inputs={'formulation': deepcopy(self.plan)},
            outputs={
                'purpose': purpose,
                'formulation': result or calculate_formulation(self.plan),
            },
        )

    @patch('lab.recipe_math.calculate_formulation')
    def test_summary_and_search_remain_compact_without_running_calculations(
            self, calculate):
        summary = self.read()
        self.assertIn('formulation', summary['available_sections'])
        self.assertIn('formulation_calculation', summary['available_sections'])
        self.assertNotIn('formulation', summary)
        self.assertNotIn('formulation_calculation', summary)
        search = execute_tool('search_lab_data', {'entity': 'recipes'})
        self.assertNotIn('formulation', search['items'][0])
        self.assertLess(len(json.dumps(summary, ensure_ascii=False)), 3000)
        calculate.assert_not_called()

    def test_draft_uses_saved_document_ignoring_old_snapshot_and_current_lot(self):
        self.snapshot()
        changed = deepcopy(self.plan)
        changed['settings']['batch_mass_g']['value'] = '700'
        self.recipe.formulation = changed
        self.recipe.save(update_fields=['formulation'])
        expected = calculate_formulation(changed)
        data = self.read('formulation_calculation')['formulation_calculation']
        self.assertFalse(data['fixed_result'])
        self.assertIsNone(data['calculation_id'])
        self.assertIsNone(data['calculation_url'])
        self.assertIsNone(data['created_at'])
        self.assertEqual(data['purpose'], 'preview')
        self.assertEqual(
            data['result']['polyol']['mass_g'], expected['polyol']['mass_g'],
        )
        first = data['result']['polyol']['rows']['items'][0]
        self.assertEqual(
            first['density_g_ml']['value'],
            changed['polyol'][0]['density_g_ml']['value'],
        )
        self.assertNotEqual(
            Decimal(first['density_g_ml']['value']), self.lot.density_g_ml,
        )
        self.assertEqual(first['price_currency'], 'EUR')
        self.assertEqual(
            data['result']['filling']['mass_g'], expected['filling']['mass_g'],
        )

    def test_published_and_archived_use_first_publication_snapshot(self):
        self.snapshot_mass('manual', '1')
        fixed = self.snapshot_mass('publication', '777')
        self.snapshot_mass('publication', '888')
        self.snapshot_mass('manual', '999')
        for status in ('published', 'archived'):
            with self.subTest(status=status):
                self.recipe.status = status
                self.recipe.formulation['settings']['batch_mass_g']['value'] = '700'
                self.recipe.save(update_fields=['status', 'formulation'])
                with patch('lab.recipe_math.calculate_formulation') as calculate:
                    data = self.read('formulation_calculation')[
                        'formulation_calculation'
                    ]
                calculate.assert_not_called()
                self.assertTrue(data['fixed_result'])
                self.assertEqual(data['purpose_display'], 'Расчёт при публикации')
                self.assertEqual(data['calculation_id'], fixed.pk)
                self.assertEqual(data['calculation_url'], f'/calculations/{fixed.pk}/')
                self.assertTrue(data['created_at'])
                self.assertEqual(data['result']['filling']['mass_g']['value'], '777')
                self.assertIn('inputs.formulation', data['note'])

    def snapshot_mass(self, purpose, value):
        return self.snapshot(purpose, {
            'filling': {'mass_g': {'value': value, 'reason': ''}},
        })

    def test_non_draft_without_publication_uses_first_saved_calculation(self):
        first = self.snapshot('manual')
        self.snapshot('manual', {'filling': {'mass_g': {'value': '999', 'reason': ''}}})
        self.recipe.status = 'archived'
        self.recipe.save(update_fields=['status'])
        result = self.read('formulation_calculation')['formulation_calculation']
        self.assertTrue(result['fixed_result'])
        self.assertEqual(result['calculation_id'], first.pk)
        self.assertEqual(result['purpose_display'], 'Сохранённый расчёт')

    def test_legacy_saved_calculation_without_modern_output_falls_back_to_preview(self):
        CalculationRecord.objects.create(
            recipe_version=self.recipe, inputs={'recipe_id': self.recipe.pk},
            outputs={'purpose': 'publication'},
        )
        self.recipe.status = 'published'
        self.recipe.save(update_fields=['status'])
        result = self.read('formulation_calculation')['formulation_calculation']
        self.assertFalse(result['fixed_result'])
        self.assertIsNone(result['calculation_id'])
        self.assertEqual(result['purpose'], 'preview')
        self.assertEqual(result['result']['filling']['mass_g']['value'], '790.9')

    def missing_density_plan(self):
        plan = deepcopy(self.plan)
        plan['polyol'][2]['density_g_ml'] = {
            'value': None, 'reason': 'Паспорт не получен', 'source_cell': 'H4',
            'source_sheet': '316.17', 'origin': 'carried_forward',
            'provenance_note': 'Из предыдущего листа', 'unpublished_key': 'hidden',
        }
        plan['settings']['iso_ratio_mode'] = 'index'
        plan['settings']['created_by'] = 42
        plan['polyol'][0]['private_note'] = 'hidden'
        return plan

    def test_missing_values_and_provenance_survive_without_unknown_fields(self):
        plan = self.missing_density_plan()
        self.recipe.formulation = plan
        self.recipe.save(update_fields=['formulation'])
        data = self.read('formulation')
        document = data['formulation']
        density = document['polyol']['items'][2]['density_g_ml']
        self.assertIsNone(density['value'])
        self.assertEqual(density['reason'], 'Паспорт не получен')
        self.assertEqual(density['origin_display'], 'Перенесено с предыдущего листа')
        self.assertEqual(density['source_sheet'], '316.17')
        self.assertEqual(density['source_cell'], 'H4')
        self.assertEqual(density['provenance_note'], 'Из предыдущего листа')
        self.assertEqual(document['gases']['items'][1]['quantity']['value'], '0')
        self.assertEqual(
            document['settings']['iso_ratio_mode_display'],
            'Задать ISO index, рассчитать A:B',
        )
        self.assertNotIn('created_by', document['settings'])
        self.assertNotIn('private_note', document['polyol']['items'][0])
        self.assertNotIn('unpublished_key', density)
        self.assertEqual(
            data['field_labels']['reason'], 'Причина отсутствия / пояснение',
        )
        result = self.read('formulation_calculation')[
            'formulation_calculation'
        ]['result']
        self.assertIsNone(result['filling']['mass_g']['value'])
        self.assertIn('Паспорт не получен', result['filling']['mass_g']['reason'])

    def test_every_document_and_calculated_row_is_reachable_with_pagination(self):
        plan = deepcopy(self.plan)
        plan['polyol'] = [deepcopy(plan['polyol'][0]) for _ in range(43)]
        for index, row in enumerate(plan['polyol']):
            row.update(id=f'row-{index}', name=f'Компонент {index}')
            row['quantity'] = {'value': '1', 'reason': ''}
        self.recipe.formulation = plan
        self.recipe.save(update_fields=['formulation'])
        inputs, outputs, offset = [], [], 0
        while offset is not None:
            page = self.read('formulation', offset=offset, limit=17)[
                'formulation'
            ]['polyol']
            inputs.extend(row['id'] for row in page['items'])
            result = self.read('formulation_calculation', offset=offset, limit=17)
            rows = result['formulation_calculation']['result']['polyol']['rows']
            outputs.extend(row['id'] for row in rows['items'])
            self.assertEqual(rows['total'], 43)
            offset = page['next_offset']
        self.assertEqual(inputs, [f'row-{i}' for i in range(43)])
        self.assertEqual(outputs, inputs)
        gas_tail = self.read('formulation', offset=2, limit=2)['formulation']['gases']
        self.assertEqual(gas_tail['total'], 3)
        self.assertEqual(len(gas_tail['items']), 1)
        filling = self.read('formulation_calculation', offset=2, limit=2)
        gas_page = filling['formulation_calculation']['result']['filling']['gases']
        self.assertEqual(len(gas_page['items']), 1)

    def test_large_documents_keep_valid_pages_under_the_output_budget(self):
        plan = deepcopy(self.plan)
        plan['polyol'] = [deepcopy(plan['polyol'][0]) for _ in range(43)]
        for index, row in enumerate(plan['polyol']):
            row['id'] = f'large-{index}'
            row['material_reason'] = 'Причина ' * 700
        self.recipe.formulation = plan
        self.recipe.save(update_fields=['formulation'])
        observed, offset = [], 0
        while offset is not None:
            data = self.read('formulation', offset=offset, limit=40)
            self.assertLessEqual(len(json.dumps(data, ensure_ascii=False)), MAX_OUTPUT)
            page = data['formulation']['polyol']
            self.assertEqual(page['total'], 43)
            self.assertTrue(page['items'])
            observed.extend(row['id'] for row in page['items'])
            offset = page['next_offset']
        self.assertEqual(observed, [f'large-{i}' for i in range(43)])

    def test_legacy_recipe_keeps_sections_without_inventing_input_document(self):
        self.recipe.formulation = {}
        self.recipe.save(update_fields=['formulation'])
        RecipeGroup.objects.create(
            recipe_version=self.recipe, label='Архивная группа',
        )
        summary = self.read()
        self.assertNotIn('formulation', summary['available_sections'])
        self.assertNotIn('formulation_calculation', summary['available_sections'])
        all_data = self.read('all')
        self.assertEqual(all_data['groups']['items'][0]['label'], 'Архивная группа')
        self.assertNotIn('formulation', all_data)
        self.assertIn('error', execute_tool('get_lab_record', {
            'entity': 'recipes', 'id': self.recipe.pk, 'section': 'formulation',
        }))

    def test_explicit_reads_and_preview_never_write_to_database(self):
        original = deepcopy(self.recipe.formulation)
        with CaptureQueriesContext(connection) as queries:
            self.read('formulation')
            self.read('formulation_calculation')
        self.assertTrue(queries.captured_queries)
        self.assertTrue(all(query['sql'].lstrip().upper().startswith('SELECT')
                            for query in queries.captured_queries))
        self.assertEqual(CalculationRecord.objects.count(), 0)
        self.recipe.refresh_from_db()
        self.assertEqual(self.recipe.formulation, original)

    def test_all_published_formulation_keys_have_russian_labels(self):
        for section in ('formulation', 'formulation_calculation'):
            data = self.read(section, limit=40)
            keys = document_keys(data[section])
            missing = keys - data['field_labels'].keys()
            self.assertEqual(missing, set(), f'Missing formulation labels: {missing}')
            self.assertEqual(
                data['field_labels']['batch_mass_g'],
                'Batch — масса смеси для расчёта навесок, г',
            )

    def test_all_archive_documents_keep_known_keys_and_readable_labels(self):
        self.assertEqual(len(self.archive_plans), 135)
        for plan in self.archive_plans:
            with self.subTest(sheet=plan.get('source_sheet')):
                self.recipe.formulation = plan
                self.recipe.save(update_fields=['formulation'])
                result = calculate_formulation(plan)
                self.assert_document_keys('formulation', plan)
                self.assert_document_keys('formulation_calculation', result)

    def assert_document_keys(self, section, original):
        data = self.read(section, limit=40)
        keys = document_keys(data[section])
        self.assertEqual(document_keys(original) - keys, set())
        for key in keys:
            label = data['field_labels'].get(key, '')
            self.assertTrue(any('А' <= char <= 'я' or char in 'Ёё' for char in label),
                            f'Missing Russian label for {key}: {label}')
