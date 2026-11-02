"""Archive calculations use row evidence and keep currencies separate."""

from decimal import Decimal

from django.test import TestCase

from .calculations import calculate_recipe, save_calculation
from .models import (
    EmployeeProfile, Material, MaterialLot, RecipeComponent, RecipeGroup,
    RecipeProperty, RecipeVersion, SourceCell, SourceFile, SourceSheet,
)


class ArchiveCalculationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = EmployeeProfile.objects.create(full_name='Автор', role='author')
        source = SourceFile.objects.create(original_path='archive.xlsx')
        cls.sheet = SourceSheet.objects.create(file=source, name='Зимняя рецептура')
        cls.recipe = RecipeVersion.objects.create(
            version_label='ARCHIVE.1', source_sheet=cls.sheet, batch_mass_g=1000,
        )
        cls.group = RecipeGroup.objects.create(
            recipe_version=cls.recipe, label='A', quantity_basis='mass_percent',
            completeness='complete',
        )
        cls.first, cls.first_price = cls.archive_component(1, '60', '2.50', '1')
        cls.second, cls.second_price = cls.archive_component(2, '40', '4', '2')
        cls.lot = cls.first.specified_lot

    @classmethod
    def archive_component(cls, position, quantity, price, density):
        row = position + 3
        material = Material.objects.create(name=f'Материал {position}')
        lot = MaterialLot.objects.create(
            material=material, lot_code=f'SOURCE-{position}',
            price_per_kg=999, price_currency='RUB', density_g_ml=9,
        )
        name_cell = cls.cell(f'A{row}', material.name)
        component = RecipeComponent.objects.create(
            group=cls.group, position=position, specified_lot=lot,
            name_cell=name_cell, quantity_value=Decimal(quantity),
        )
        price_property = RecipeProperty.objects.create(
            component=component, property_code='source_price_per_kg',
            value=Decimal(price), unit_code='EUR/kg',
            source_cell=cls.cell(f'C{row}', price),
        )
        RecipeProperty.objects.create(
            component=component, property_code='density_g_ml',
            value=Decimal(density), unit_code='g/ml',
            source_cell=cls.cell(f'H{row}', density),
        )
        return component, price_property

    @classmethod
    def cell(cls, address, value):
        return SourceCell.objects.create(
            sheet=cls.sheet, address=address,
            cell_kind='s' if address.startswith('A') else 'n', raw_value=value,
        )

    def output(self):
        return calculate_recipe(self.recipe)['groups'][0]

    def test_source_price_density_override_later_lot_catalogue_edits(self):
        MaterialLot.objects.update(
            price_per_kg=54321, price_currency='RUB', density_g_ml=5,
        )
        output = self.output()
        first = output['rows'][0]
        self.assertEqual(Decimal(first['price_per_kg']), Decimal('2.50'))
        self.assertEqual(Decimal(first['density_g_ml']), Decimal('1'))
        self.assertEqual(first['price_currency'], 'EUR')
        self.assertEqual(first['price_origin'], 'source')
        self.assertEqual(first['density_origin'], 'source')
        self.assertEqual(first['price_source_cell_id'], self.first_price.source_cell_id)
        self.assertEqual(Decimal(output['total_cost']), Decimal('3.10'))
        self.assertEqual(Decimal(output['density_g_ml']), Decimal('1.25'))

    def test_eur_totals_use_exact_row_mass_price_and_density(self):
        output = self.output()
        self.assertEqual(output['currency'], 'EUR')
        self.assertEqual(output['currency_symbol'], '€')
        self.assertEqual(Decimal(output['mass_g']), Decimal('1000'))
        self.assertEqual(Decimal(output['total_cost']), Decimal('3.10'))
        self.assertEqual(Decimal(output['cost_per_kg']), Decimal('3.10'))
        self.assertEqual(Decimal(output['volume_ml']), Decimal('800'))
        self.assertEqual(Decimal(output['density_g_ml']), Decimal('1.25'))
        self.assertEqual([Decimal(row['cost']) for row in output['rows']],
                         [Decimal('1.50'), Decimal('1.60')])

    def test_saved_snapshot_keeps_source_currency_after_catalogue_changes(self):
        record = save_calculation(self.recipe, self.author)
        MaterialLot.objects.update(price_currency='RUB', price_per_kg=54321)
        self.first_price.unit_code = 'USD/kg'
        self.first_price.save()
        record.refresh_from_db()
        inputs = record.inputs['groups'][0]['components']
        self.assertEqual({row['price_currency'] for row in inputs}, {'EUR'})
        self.assertEqual(inputs[0]['price_source_cell_id'],
                         self.first_price.source_cell_id)
        output = record.outputs['groups'][0]
        self.assertEqual(output['currency'], 'EUR')
        self.assertEqual(Decimal(output['total_cost']), Decimal('3.10'))
        self.assertEqual(Decimal(inputs[0]['price_per_kg']), Decimal('2.50'))

    def test_missing_historical_price_does_not_fall_back_to_lot_catalogue(self):
        self.first_price.delete()
        Material.objects.filter(pk=self.lot.material_id).update(
            name='Полиол без исторической цены',
        )
        MaterialLot.objects.filter(pk=self.lot.pk).update(
            price_per_kg=200, price_currency='EUR',
        )
        output = self.output()
        first = output['rows'][0]
        self.assertEqual(first['name'], 'Полиол без исторической цены')
        self.assertIsNone(first['price_per_kg'])
        self.assertIsNone(first['price_currency'])
        self.assertIsNone(first['cost'])
        self.assertEqual(first['price_origin'], 'source')
        self.assertIsNone(output['total_cost'])
        self.assertEqual(Decimal(output['rows'][1]['cost']), Decimal('1.60'))
        self.assertEqual(Decimal(output['volume_ml']), Decimal('800'))

    def test_mixed_positive_mass_currencies_block_only_cost_totals(self):
        self.second_price.unit_code = 'RUB/kg'
        self.second_price.save()
        output = self.output()
        self.assertIsNone(output['total_cost'])
        self.assertIsNone(output['cost_per_kg'])
        self.assertIsNone(output['currency'])
        self.assertEqual([row['price_currency'] for row in output['rows']],
                         ['EUR', 'RUB'])
        self.assertEqual([Decimal(row['cost']) for row in output['rows']],
                         [Decimal('1.50'), Decimal('1.60')])
        self.assertEqual(Decimal(output['density_g_ml']), Decimal('1.25'))
        self.assertTrue(any('разных валютах' in issue for issue in output['issues']))

    def test_zero_mass_other_currency_does_not_block_eur_total(self):
        RecipeComponent.objects.filter(pk=self.first.pk).update(quantity_value=100)
        RecipeComponent.objects.filter(pk=self.second.pk).update(quantity_value=0)
        self.second_price.unit_code = 'RUB/kg'
        self.second_price.save()
        output = self.output()
        self.assertEqual(output['currency'], 'EUR')
        self.assertEqual(Decimal(output['total_cost']), Decimal('2.50'))
        self.assertEqual(Decimal(output['rows'][1]['cost']), Decimal('0'))
        self.assertEqual(Decimal(output['density_g_ml']), Decimal('1'))
        self.assertFalse(any('разных валютах' in issue for issue in output['issues']))
