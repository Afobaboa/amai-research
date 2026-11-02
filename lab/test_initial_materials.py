"""Catalog import preserves source identity and remains safe on repeated seeds."""
from copy import deepcopy
from decimal import Decimal

from django.test import TestCase

from . import models as lab
from .initial_data import ArchiveLoader
from .initial_materials import ArchiveMaterials


def recipe_data():
    return {
        'mixture_code': 'W', 'version_label': 'W.1', 'title': 'Зимняя',
        'recorded_on': '2026-01-01', 'notes': '', 'properties': [],
        'groups': [{'label': 'A', 'basis': 'mass_percent',
                    'completeness': 'complete', 'components': [{
                        'name': 'Полиол', 'name_raw': '  Полиол \n п.A  ',
                        'lot_code': 'A', 'name_cell': 'A4', 'quantity_cell': 'D4',
                        'quantity': '100', 'properties': [{
                            'code': 'source_price_per_kg', 'value': '2.5',
                            'unit': 'EUR/kg', 'cell': 'C4',
                        }],
                    }]}],
        'filling': [{'material_name': None, 'name_raw': '31.25',
                     'invalid_material_label': True, 'label_cell': 'E34',
                     'mass_cell': 'F34', 'mass_g': '363',
                     'mass_status': 'recorded', 'properties': []}],
    }


def bundle_data():
    raw = {'A4': '  Полиол \n п.A  ', 'D4': '100', 'C4': '2.5',
           'E34': 'ISO', 'F34': '363'}
    cells = {address: {'cell_kind': 's', 'raw_value': value,
                       'formula_text': None, 'cached_value': None,
                       'number_format': 'General'}
             for address, value in raw.items()}
    return {
        'schema_version': 2, 'price_currency': 'EUR',
        'mixtures': [{'code': 'W', 'title': 'Зимняя'}],
        'files': [{'path': 'archive/winter.xlsx', 'sha256': 'a' * 64,
                   'title': 'Источник', 'sheets': [{'name': 'W.1',
                       'cells': cells, 'recipe': recipe_data()}]}],
    }


class ArchiveMaterialSafetyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = lab.EmployeeProfile.objects.create(
            full_name='Импорт: автор', role='author',
        )

    def setUp(self):
        self.data = bundle_data()

    def load(self, data=None):
        return ArchiveLoader(self.author).load(data or self.data)

    def legacy_data(self):
        data = deepcopy(self.data)
        recipe = data['files'][0]['sheets'][0]['recipe']
        component = recipe['groups'][0]['components'][0]
        component.update(name='Полиол п.A', name_raw='Полиол п.A', lot_code=None)
        recipe['filling'][0].update(material_name='31.25',
                                    invalid_material_label=False)
        return data

    def offer_price(self, catalog, material, value, date='2026-11-01'):
        cell = lab.SourceCell.objects.get(address='C4')
        rank = (date, 'archive/winter.xlsx', 'W.1')
        catalog.offer(material, 'price_per_kg', rank, {
            'value': value, 'source_cell_id': cell.pk,
            'recorded_on': date, 'rank': list(rank),
        })

    def counts(self):
        return tuple(model.objects.count() for model in (
            lab.Material, lab.MaterialLot, lab.RecipeVersion,
            lab.RecipeComponent, lab.FillingPlanLine, lab.ChangeEvent,
        ))

    def test_fresh_and_repeated_import_have_identical_catalog_and_events(self):
        self.load()
        material = lab.Material.objects.get(name='Полиол')
        self.assertEqual(material.lots.get().price_per_kg, Decimal('2.5'))
        self.assertEqual(material.lots.get().price_currency, 'EUR')
        self.assertEqual(material.lots.get().lot_code, 'A')
        before = self.counts()
        self.load()
        self.assertEqual(self.counts(), before)

    def test_custom_numeric_name_and_unrelated_alias_survive_fresh_seed(self):
        custom = lab.Material.objects.create(name='31.25')
        alias = lab.Material.objects.create(name='Полиол п.A')
        self.load()
        self.load()
        self.assertTrue(lab.Material.objects.filter(pk=custom.pk).exists())
        alias.refresh_from_db()
        self.assertEqual(alias.name, 'Полиол п.A')
        self.assertTrue(lab.Material.objects.filter(name='Полиол').exists())

    def test_legacy_whitespace_alias_and_known_invalid_source_are_corrected(self):
        self.load(self.legacy_data())
        original = lab.Material.objects.get(name='Полиол п.A')
        invalid = lab.Material.objects.get(name='31.25')
        self.load()
        original.refresh_from_db()
        self.assertEqual(original.name, 'Полиол')
        self.assertEqual(lab.RecipeComponent.objects.get().specified_lot.material,
                         original)
        self.assertFalse(lab.Material.objects.filter(pk=invalid.pk).exists())
        before = self.counts()
        self.load()
        self.assertEqual(self.counts(), before)

    def test_audited_explicit_price_is_preserved_even_at_original_value(self):
        self.load()
        material = lab.MaterialLot.objects.get(material__name='Полиол')
        lab.ChangeEvent.objects.create(
            employee=self.author, entity_type='lot', entity_id=material.pk,
            action='updated', summary='Автор явно выбрал цену',
            before={'price_per_kg': '3'}, after={'price_per_kg': '2.5'},
        )
        catalog = ArchiveMaterials(self.data, self.author)
        self.offer_price(catalog, material, '4')
        catalog.finish()
        material.refresh_from_db()
        self.assertEqual(material.price_per_kg, Decimal('2.5'))

    def test_older_source_cannot_lower_latest_imported_catalog_baseline(self):
        self.load()
        material = lab.MaterialLot.objects.get(material__name='Полиол')
        catalog = ArchiveMaterials(self.data, self.author)
        self.offer_price(catalog, material, '4')
        catalog.finish()
        catalog = ArchiveMaterials(self.data, self.author)
        self.offer_price(catalog, material, '1', date='2026-02-01')
        catalog.finish()
        material.refresh_from_db()
        self.assertEqual(material.price_per_kg, Decimal('4'))

    def test_alias_notes_are_preserved_after_source_rows_are_rebound(self):
        self.load(self.legacy_data())
        alias = lab.Material.objects.get(name='Полиол п.A')
        alias.notes = 'Примечание сотрудника'
        alias.save()
        target = lab.Material.objects.create(name='Полиол')
        self.load()
        target.refresh_from_db()
        self.assertEqual(target.notes, 'Примечание сотрудника')
        self.assertFalse(lab.Material.objects.filter(pk=alias.pk).exists())
        before = self.counts()
        self.load()
        self.assertEqual(self.counts(), before)

    def test_alias_with_user_lot_is_retained_without_repeat_merge_events(self):
        self.load(self.legacy_data())
        alias = lab.Material.objects.get(name='Полиол п.A')
        lab.MaterialLot.objects.create(material=alias, lot_code='USER-LOT')
        lab.Material.objects.create(name='Полиол')
        self.load()
        before = self.counts()
        self.load()
        self.assertEqual(self.counts(), before)
        self.assertTrue(lab.Material.objects.filter(pk=alias.pk).exists())

    def test_user_cleared_material_binding_is_preserved_on_repeat_import(self):
        self.load()
        component = lab.RecipeComponent.objects.get()
        component.specified_lot = None
        component.unbatched_material = None
        component.save(update_fields=['specified_lot', 'unbatched_material'])
        before = self.counts()
        self.load()
        self.load()
        component.refresh_from_db()
        self.assertIsNone(component.specified_lot_id)
        self.assertIsNone(component.unbatched_material_id)
        self.assertEqual(component.quantity_value, Decimal('100'))
        self.assertEqual(self.counts(), before)

    def test_user_replaced_iso_with_group_is_preserved_on_repeat_import(self):
        row = self.data['files'][0]['sheets'][0]['recipe']['filling'][0]
        row.update(material_name='MDI', name_raw='MDI',
                   invalid_material_label=False)
        self.load()
        line = lab.FillingPlanLine.objects.get()
        self.assertEqual(line.unbatched_material.name, 'MDI')
        group = lab.RecipeGroup.objects.get()
        line.unbatched_material = None
        line.input_group = group
        line.save(update_fields=['unbatched_material', 'input_group'])
        before = self.counts()
        self.load()
        self.load()
        line.refresh_from_db()
        self.assertEqual(line.input_group_id, group.pk)
        self.assertIsNone(line.unbatched_material_id)
        self.assertIsNone(line.specified_lot_id)
        self.assertEqual(line.mass_g, Decimal('363'))
        self.assertEqual(self.counts(), before)
