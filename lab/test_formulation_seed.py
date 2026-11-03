"""First-deploy recipe inputs retain Excel values and safe catalog bindings."""

from copy import deepcopy
from decimal import Decimal
import json

from django.test import TestCase

from .initial_data import DATA_PATH, seed_initial
from .models import RecipeVersion
from .recipe_seed import seed_recipe_formulation


def archive_recipes():
    data = json.loads(DATA_PATH.read_text())
    return [sheet['recipe'] for source in data['files']
            for sheet in source['sheets'] if sheet.get('recipe')]


class FormulationSeedTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_initial()

    def recipe(self, label='316.18', code='316'):
        return RecipeVersion.objects.get(
            version_label=label, mixture_type__code=code,
        )

    def source_recipe(self, label='316.18', code='316'):
        return next(row for row in archive_recipes()
                    if row['version_label'] == label and row['mixture_code'] == code)

    def test_first_deploy_seeds_every_formulation_without_reading_excel(self):
        recipes = list(RecipeVersion.objects.all())
        self.assertEqual(len(recipes), 135)
        self.assertTrue(all(row.formulation.get('schema_version') == 1
                            for row in recipes))
        self.assertEqual(sum(len(row.formulation['polyol']) for row in recipes),
                         1050)
        self.assertEqual(sum(len(row.formulation['gases']) for row in recipes),
                         405)

    def test_screenshot_inputs_and_catalog_rows_are_seeded(self):
        recipe = self.recipe()
        plan = recipe.formulation
        settings = plan['settings']
        self.assertEqual(settings['batch_mass_g']['value'], '350')
        self.assertEqual(settings['polyol_parts']['value'], '100')
        self.assertEqual(settings['isocyanate_parts']['value'], '145')
        self.assertEqual(settings['gas_mass_pct']['value'], '19.4')
        self.assertEqual(settings['fill_volume_ml']['value'], '800')
        self.assertEqual(plan['gases'][2]['name'], 'Пропан-изобутан')
        self.assertIsNotNone(plan['gases'][2]['material_id'])
        self.assertEqual(plan['gases'][2]['lot_reason'],
                         'Партия не указана в исходном Excel.')
        for payload, component in zip(plan['polyol'],
                                      recipe.groups.first().components.all()):
            self.assertEqual(payload['material_id'], component.material.pk)
            self.assertEqual(payload['lot_id'], component.specified_lot_id)

    def test_index_controlled_sheets_retain_the_original_input_direction(self):
        plans = [row['formulation'] for row in archive_recipes()]
        indexed = [p for p in plans if p['settings']['iso_ratio_mode'] == 'index']
        self.assertEqual(len(indexed), 38)
        for plan in indexed:
            settings = plan['settings']
            self.assertIn('source_formula', settings['isocyanate_parts'])
            self.assertNotIn('source_formula', settings['iso_index'])

    def test_every_absent_numeric_input_has_an_explicit_reason(self):
        def assert_cells(value):
            if isinstance(value, dict):
                if 'value' in value:
                    if value['value'] is None:
                        self.assertTrue(value.get('reason'), value)
                    else:
                        self.assertFalse(value.get('reason'), value)
                for child in value.values():
                    assert_cells(child)
            elif isinstance(value, list):
                for child in value:
                    assert_cells(child)
        for row in archive_recipes():
            assert_cells(row['formulation'])

    def test_previous_sheet_price_keeps_authorized_donor_provenance(self):
        plan = self.recipe('302.84', '302').formulation
        row = next(row for row in plan['polyol'] if row['name'] == 'TEP-Z')
        price = row['price_per_kg']
        self.assertEqual(price['value'], '64.69')
        self.assertEqual(price['source_sheet'], '302.83')
        self.assertEqual(price['source_cell'], 'C12')
        self.assertEqual(price['origin'], 'carried_forward')
        self.assertTrue(price['provenance_note'])

    def test_unknown_freon_identity_is_not_invented_from_numeric_inputs(self):
        plan = self.recipe('316Ф-1', '316-Ф').formulation
        gas = plan['gases'][1]
        self.assertIsNone(gas['material_id'])
        self.assertIn('F134a', gas['material_reason'])
        self.assertIn('F152a', gas['material_reason'])
        self.assertIsNotNone(gas['density_g_ml']['value'])
        self.assertEqual(gas['pressure_bar']['value'], '5.96')
        self.assertIn('F152a', gas['density_g_ml']['provenance_note'])

    def test_summary_formulas_are_not_imported_as_ingredient_functionality(self):
        for raw in archive_recipes():
            for row in raw['formulation']['polyol']:
                cell = row['functionality']
                if cell.get('source_formula'):
                    self.assertFalse(cell['applicable'])
                    self.assertIsNone(cell['value'])
        recipe = self.recipe('316.1')
        tcpp = next(row for row in recipe.formulation['polyol']
                    if row['name'] == 'TCPP')
        self.assertFalse(tcpp['functionality']['applicable'])

    def test_functionality_is_recognized_only_under_its_actual_excel_heading(self):
        dataset = json.loads(DATA_PATH.read_text())
        headers = []
        for source in dataset['files']:
            for sheet in source['sheets']:
                recipe = sheet.get('recipe')
                if not recipe:
                    continue
                header = sheet['cells'].get('K3', {}).get('raw_value') or ''
                headers.append('FUNCTIONALITY' in header.upper())
                if 'FUNCTIONALITY' in header.upper():
                    self.assertTrue(any(row['functionality']['value'] is not None
                                        for row in recipe['formulation']['polyol']))
                    continue
                for row in recipe['formulation']['polyol']:
                    cell = row['functionality']
                    self.assertFalse(cell['applicable'])
                    self.assertIsNone(cell['value'])
                    self.assertEqual(cell['source_role'], 'cost_share')
                    self.assertIn('стоимости', cell['reason'])
        self.assertEqual(sum(headers), 5)
        self.assertEqual(len(headers) - sum(headers), 130)

    def test_orphan_ohv_constants_remain_explicit_source_contributions(self):
        plans = [row['formulation'] for row in archive_recipes()]
        extras = [cell for plan in plans for cell in plan['settings'].get(
            'ohv_extra_contributions', [],
        )]
        self.assertEqual(len(extras), 4)
        self.assertTrue(all(cell['value'] == '19' for cell in extras))
        self.assertTrue(all(cell['source_cell'] == 'E11' for cell in extras))
        self.assertTrue(all(cell['provenance_note'] for cell in extras))
        recipe = self.recipe('316.1')
        self.assertEqual(recipe.formulation['settings']['ohv_extra_contributions'],
                         [extras[0]])

    def test_manual_archive_prices_keep_values_and_catalog_identity_anchors(self):
        plans = [row['formulation'] for row in archive_recipes()]
        manual = [row for plan in plans for row in plan['polyol']
                  if row.get('manual_weighted_price')]
        self.assertEqual(len(manual), 8)
        self.assertEqual(sum('manual_polyol_price_per_kg' in plan['settings']
                             for plan in plans), 4)
        plan = self.recipe('316.1').formulation
        settings = plan['settings']
        self.assertEqual(settings['manual_polyol_price_per_kg']['value'],
                         '204.78482')
        for anchor, row in zip(settings['manual_polyol_price_anchors'],
                               plan['polyol']):
            self.assertEqual(anchor['material_id'], row['material_id'])
            self.assertEqual(anchor['lot_id'], row['lot_id'])
            self.assertEqual(anchor['name'], row['name'])

    def test_repeated_seed_preserves_user_formulation_and_adds_no_duplicates(self):
        recipe = self.recipe()
        changed = deepcopy(recipe.formulation)
        changed['settings']['fill_volume_ml']['value'] = '777'
        recipe.formulation = changed
        recipe.save(update_fields=['formulation'])
        seed_initial()
        recipe.refresh_from_db()
        self.assertEqual(recipe.formulation, changed)
        self.assertEqual(RecipeVersion.objects.count(), 135)

    def test_upgrade_enriches_untouched_legacy_recipe_only_once(self):
        recipe = self.recipe()
        expected = deepcopy(recipe.formulation)
        recipe.formulation = {}
        recipe.save(update_fields=['formulation'])
        seed_recipe_formulation(recipe, self.source_recipe())
        recipe.refresh_from_db()
        self.assertEqual(recipe.formulation, expected)

    def test_upgrade_does_not_replace_modified_legacy_composition(self):
        recipe = self.recipe()
        recipe.formulation = {}
        recipe.save(update_fields=['formulation'])
        component = recipe.groups.first().components.first()
        component.quantity_value += Decimal('1')
        component.save(update_fields=['quantity_value'])
        seed_recipe_formulation(recipe, self.source_recipe())
        recipe.refresh_from_db()
        self.assertEqual(recipe.formulation, {})

    def test_source_enrichment_adds_missing_exceptions_without_resetting_inputs(self):
        recipe = self.recipe('316.1')
        expected = deepcopy(recipe.formulation)
        old = deepcopy(expected)
        for key in ('ohv_extra_contributions', 'manual_polyol_price_per_kg',
                    'manual_polyol_price_anchors'):
            old['settings'].pop(key, None)
        formula = next(row['functionality'] for row in old['polyol']
                       if row['functionality'].get('source_formula'))
        formula['value'] = formula['source_cached_value']
        formula.pop('applicable', None)
        formula['reason'] = ''
        recipe.formulation = old
        recipe.save(update_fields=['formulation'])
        seed_recipe_formulation(recipe, self.source_recipe('316.1'))
        recipe.refresh_from_db()
        self.assertEqual(recipe.formulation, expected)

    def test_source_enrichment_keeps_user_inputs_even_with_missing_exception_keys(self):
        recipe = self.recipe('316.1')
        edited = deepcopy(recipe.formulation)
        edited['settings'].pop('manual_polyol_price_per_kg')
        edited['settings']['fill_volume_ml']['value'] = '777'
        recipe.formulation = edited
        recipe.save(update_fields=['formulation'])
        seed_recipe_formulation(recipe, self.source_recipe('316.1'))
        recipe.refresh_from_db()
        self.assertEqual(recipe.formulation, edited)

    def test_source_enrichment_does_not_overwrite_changed_functionality(self):
        recipe = self.recipe('316.1')
        edited = deepcopy(recipe.formulation)
        edited['settings'].pop('manual_polyol_price_per_kg')
        formula = next(row['functionality'] for row in edited['polyol']
                       if row['functionality'].get('source_formula'))
        formula['value'] = '999'
        formula['reason'] = ''
        recipe.formulation = edited
        recipe.save(update_fields=['formulation'])
        seed_recipe_formulation(recipe, self.source_recipe('316.1'))
        recipe.refresh_from_db()
        self.assertEqual(recipe.formulation, edited)

    def test_source_enrichment_corrects_previous_cost_column_classification(self):
        recipe = self.recipe('316.26')
        expected = deepcopy(recipe.formulation)
        previous = deepcopy(expected)
        for row in previous['polyol']:
            row['functionality'].pop('source_role', None)
            row['functionality']['reason'] = 'Не применяется к этой строке.'
        recipe.formulation = previous
        recipe.save(update_fields=['formulation'])
        seed_recipe_formulation(recipe, self.source_recipe('316.26'))
        recipe.refresh_from_db()
        self.assertEqual(recipe.formulation, expected)
