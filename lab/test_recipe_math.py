"""Compare the filling calculator with original worksheet evidence."""
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import unittest

from .recipe_math import calculate_formulation


def archive_recipes():
    path = Path(__file__).resolve().parents[1] / 'initial_data' / 'winter.json'
    data = json.loads(path.read_text())
    return [sheet for file in data['files'] for sheet in file['sheets']
            if sheet.get('recipe', {}).get('formulation')]


class RecipeMathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recipes = archive_recipes()

    def plan(self, version='316.18'):
        sheet = next(row for row in self.recipes if row['name'] == version)
        return deepcopy(sheet['recipe']['formulation'])

    def assert_number(self, cell, expected, tolerance='0.000001'):
        self.assertIsNotNone(cell['value'], cell.get('reason'))
        self.assertLessEqual(abs(Decimal(cell['value']) - Decimal(str(expected))),
                             Decimal(tolerance))

    def assert_source(self, result, cells, address):
        source = cells[address]
        value = source.get('cached_value') or source.get('raw_value')
        self.assert_number(result, value)

    def filling_checks(self, output, end):
        rows = [output['filling']['polyol'], output['filling']['isocyanate'],
                *output['filling']['gases']]
        checks = []
        for row, offset in zip(rows, (12, 13, 15, 16, 17)):
            checks.extend([(row['mass_g'], f'F{end + offset}'),
                           (row['volume_ml'], f'G{end + offset}')])
        checks.extend([(output['filling']['mass_g'], f'F{end + 19}'),
                       (output['filling']['volume_ml'], f'G{end + 19}')])
        return checks

    def test_all_135_archive_filling_masses_and_volumes_match_excel(self):
        self.assertEqual(len(self.recipes), 135)
        for sheet in self.recipes:
            with self.subTest(recipe=sheet['name']):
                cells = sheet['cells']
                end = next(int(a[1:]) for a, c in cells.items()
                           if c.get('raw_value') == 'Summe Polyol')
                result = calculate_formulation(sheet['recipe']['formulation'])
                for cell, address in self.filling_checks(result, end):
                    self.assert_source(cell, cells, address)
                self.assert_source(result['polyol']['mass_g'], cells, f'G{end}')
                self.assert_source(result['polyol']['ohv'], cells, f'E{end}')
                self.assert_source(result['polyol']['density_g_ml'], cells,
                                   f'H{end}')

    def test_screenshot_316_18_has_original_per_can_results(self):
        output = calculate_formulation(self.plan())
        self.assert_number(output['filling']['polyol']['mass_g'], '260.4')
        self.assert_number(output['filling']['isocyanate']['mass_g'], '377.2')
        self.assertEqual([r['mass_g']['value'] for r in
                          output['filling']['gases']], ['71.2', '0', '82.1'])
        self.assert_number(output['filling']['mass_g'], '790.9')
        self.assert_number(output['filling']['volume_ml'], '800')
        self.assert_number(output['filling']['cost'], '74.44431206')
        self.assert_number(output['gases']['pressure_bar'], '5.25514')

    def test_archive_can_cost_and_indices_match_excel_except_documented_fix(self):
        for sheet in self.recipes:
            with self.subTest(recipe=sheet['name']):
                cells = sheet['cells']
                end = next(int(a[1:]) for a, c in cells.items()
                           if c.get('raw_value') == 'Summe Polyol')
                output = calculate_formulation(sheet['recipe']['formulation'])
                self.assert_source(output['isocyanate']['iso_index'], cells,
                                   f'I{end + 3}')
                self.assert_source(output['gases']['pressure_bar'], cells,
                                   f'H{end + 5}')
                if sheet['name'] == '302.84':
                    self.assert_number(output['polyol']['price_per_kg'], '3.88343')
                    self.assert_number(output['filling']['cost'], '2.459691038')
                else:
                    self.assert_source(output['polyol']['price_per_kg'], cells,
                                       f'H{end + 12}')
                    self.assert_source(output['filling']['cost'], cells,
                                       f'I{end + 19}')

    def test_batch_rounding_is_separate_from_can_filling(self):
        plan = self.plan('316.26')
        first = calculate_formulation(plan)
        plan['settings']['batch_mass_g']['value'] = '700'
        second = calculate_formulation(plan)
        self.assert_number(first['polyol']['mass_g'], '350.2')
        self.assertNotEqual(first['polyol']['mass_g'], second['polyol']['mass_g'])
        self.assertEqual(first['filling'], second['filling'])

    def test_manual_ratio_changes_iso_index_without_changing_filling(self):
        plan = self.plan()
        first = calculate_formulation(plan)
        plan['isocyanate']['nco_pct']['value'] = '25'
        second = calculate_formulation(plan)
        self.assertNotEqual(first['isocyanate']['iso_index'],
                            second['isocyanate']['iso_index'])
        self.assertEqual(first['filling'], second['filling'])

    def test_index_mode_recalculates_isocyanate_ratio_and_filling(self):
        plan = self.plan('302.11')
        self.assertEqual(plan['settings']['iso_ratio_mode'], 'index')
        first = calculate_formulation(plan)
        plan['isocyanate']['nco_pct']['value'] = '25'
        second = calculate_formulation(plan)
        self.assert_number(first['isocyanate']['iso_index'],
                           second['isocyanate']['iso_index']['value'])
        self.assertNotEqual(first['isocyanate']['parts'],
                            second['isocyanate']['parts'])
        self.assertNotEqual(first['filling']['isocyanate']['mass_g'],
                            second['filling']['isocyanate']['mass_g'])

    def test_missing_density_propagates_reason_without_losing_batch_masses(self):
        plan = self.plan()
        row = next(r for r in plan['polyol'] if r['quantity']['value'] == '38.5')
        row['density_g_ml'] = {'value': None, 'reason': 'Паспорт не получен.'}
        output = calculate_formulation(plan)
        self.assert_number(output['polyol']['mass_g'], '350.2')
        self.assertIsNone(output['filling']['mass_g']['value'])
        self.assertIn('Паспорт не получен.', output['filling']['mass_g']['reason'])

    def test_zero_gas_needs_no_price_or_density_to_contribute_zero(self):
        plan = self.plan()
        row = plan['gases'][1]
        self.assert_number(row['quantity'], '0')
        row['density_g_ml'] = {'value': None, 'reason': 'Не используется.'}
        row['price_per_kg'] = {'value': None, 'reason': 'Не используется.'}
        output = calculate_formulation(plan)
        self.assert_number(output['filling']['gases'][1]['mass_g'], '0')
        self.assert_number(output['filling']['gases'][1]['cost'], '0')
        self.assert_number(output['filling']['mass_g'], '790.9')

    def test_mixed_currency_blocks_cost_but_not_filling_mass(self):
        plan = self.plan()
        plan['gases'][0]['price_currency'] = 'USD'
        output = calculate_formulation(plan)
        self.assert_number(output['filling']['mass_g'], '790.9')
        self.assertIsNone(output['filling']['cost']['value'])
        self.assertIn('разных валютах', output['filling']['cost']['reason'])

    def test_absent_isocyanate_does_not_require_nco_or_density(self):
        plan = self.plan()
        plan['settings']['isocyanate_present'] = False
        plan['settings']['isocyanate_absence_reason'] = 'Не используется.'
        plan['isocyanate'] = {}
        output = calculate_formulation(plan)
        self.assert_number(output['filling']['isocyanate']['mass_g'], '0')
        self.assertIsNotNone(output['filling']['mass_g']['value'])
        self.assertFalse(any('NCO' in issue for issue in output['issues']))
        self.assertFalse(output['isocyanate']['nco_pct']['applicable'])

    def test_calculated_input_values_are_ignored_and_input_is_not_modified(self):
        plan = self.plan()
        plan['filling'] = {'mass_g': {'value': '1'}}
        plan['polyol'][0]['mass_g'] = {'value': '999999'}
        original = deepcopy(plan)
        output = calculate_formulation(plan)
        self.assert_number(output['filling']['mass_g'], '790.9')
        self.assertEqual(plan, original)

    def test_invalid_percentages_and_nonfinite_inputs_stop_dependent_results(self):
        for value in ('101', '-1', 'NaN', 'Infinity'):
            with self.subTest(value=value):
                plan = self.plan()
                plan['settings']['gas_mass_pct']['value'] = value
                output = calculate_formulation(plan)
                self.assertIsNone(output['filling']['mass_g']['value'])
                self.assertTrue(output['filling']['mass_g']['reason'])

    def test_incomplete_polyol_composition_is_not_a_complete_mass(self):
        plan = self.plan()
        plan['polyol'].pop()
        output = calculate_formulation(plan)
        self.assertIsNone(output['polyol']['mass_g']['value'])
        self.assertIsNone(output['filling']['mass_g']['value'])
        self.assertIn('100%', output['filling']['mass_g']['reason'])

    def test_manual_ohv_source_contribution_is_preserved_and_explained(self):
        output = calculate_formulation(self.plan('316.1'))
        self.assert_number(output['polyol']['ohv'], '138.5')
        self.assert_number(output['polyol']['extra_ohv'], '19')
        self.assertIn('E11', output['polyol']['source_warning'])
        self.assertIn(output['polyol']['source_warning'], output['issues'])

    def test_equivalent_ratios_keep_iso_index(self):
        plan = self.plan()
        first = calculate_formulation(plan)
        plan['settings']['polyol_parts']['value'] = '50'
        plan['settings']['isocyanate_parts']['value'] = '72.5'
        second = calculate_formulation(plan)
        self.assert_number(first['isocyanate']['iso_index'],
                           second['isocyanate']['iso_index']['value'])

    def test_functionality_ignores_source_summary_formulas(self):
        output = calculate_formulation(self.plan('316.1'))
        self.assert_number(output['polyol']['functionality'],
                           '2.77444794952681388')

    def test_manual_polyol_price_is_preserved_until_composition_changes(self):
        plan = self.plan('316.1')
        first = calculate_formulation(plan)
        self.assert_number(first['polyol']['price_per_kg'], '204.78482')
        self.assertIn('ручная цена', first['polyol']['source_warning'])
        plan['polyol'][0]['price_per_kg']['value'] = '100'
        second = calculate_formulation(plan)
        self.assertNotEqual(first['polyol']['price_per_kg'],
                            second['polyol']['price_per_kg'])
        self.assertIn('не применяется', second['polyol']['source_warning'])

    def test_manual_component_cost_is_preserved_until_its_inputs_change(self):
        plan = self.plan('302.31.1')
        first = calculate_formulation(plan)
        self.assert_number(first['polyol']['price_per_kg'], '68.82141')
        row = next(r for r in plan['polyol'] if r.get('manual_weighted_price'))
        row['price_per_kg']['value'] = '2'
        second = calculate_formulation(plan)
        self.assertNotEqual(first['polyol']['price_per_kg'],
                            second['polyol']['price_per_kg'])
        self.assertIn('не применяется', second['polyol']['source_warning'])

    def test_manual_component_cost_requires_valid_material_and_lot_anchors(self):
        plan = self.plan('302.31.1')
        row = next(r for r in plan['polyol'] if r.get('manual_weighted_price'))
        row['material_id'] = 1
        row['lot_id'] = 1
        row['manual_price_anchors'].update({'material_id': 1, 'lot_id': 1})
        first = calculate_formulation(plan)
        row['lot_id'] = 2
        second = calculate_formulation(plan)
        self.assertNotEqual(first['polyol']['price_per_kg'],
                            second['polyol']['price_per_kg'])
        self.assertIn('не применяется', second['polyol']['source_warning'])
