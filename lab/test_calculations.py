from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from .calculations import calculate_recipe, save_calculation
from .models import (
    CalculationRecord, EmployeeProfile, Material, MaterialLot,
    RecipeComponent, RecipeGroup, RecipeTemplate, RecipeVersion, SourceFile,
)


class CalculationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = EmployeeProfile.objects.create(
            full_name="Автор расчетов", role="author")
        cls.first = Material.objects.create(name="Первый полиол")
        cls.second = Material.objects.create(name="Второй полиол")
        cls.first_lot = MaterialLot.objects.create(
            material=cls.first, lot_code='FIRST', density_g_ml=1, price_per_kg=100)
        cls.second_lot = MaterialLot.objects.create(
            material=cls.second, lot_code='SECOND', density_g_ml=2, price_per_kg=200)
        cls.recipe = RecipeVersion.objects.create(
            version_label="WINTER.1", batch_mass_g=1000)
        cls.group = RecipeGroup.objects.create(
            recipe_version=cls.recipe, label="A", quantity_basis="mass_percent",
            completeness="complete")
        cls.row_a = RecipeComponent.objects.create(
            group=cls.group, position=1, specified_lot=cls.first_lot, quantity_value=50)
        cls.row_b = RecipeComponent.objects.create(
            group=cls.group, position=2, specified_lot=cls.second_lot,
            quantity_value=50)

    def group_output(self):
        return calculate_recipe(self.recipe)["groups"][0]

    def test_exact_mass_cost_and_additive_density(self):
        output = self.group_output()
        self.assertEqual(output["mass_g"], "1000")
        self.assertEqual(output["total_cost"], "150")
        self.assertEqual(output["cost_per_kg"], "150")
        self.assertEqual(output["volume_ml"], "750")
        self.assertAlmostEqual(Decimal(output["density_g_ml"]), Decimal(4) / Decimal(3), places=25)
        self.assertEqual(output["rows"][0]["mass_g"], "500")

    def test_batch_price_zero_and_density_override_are_used_without_fallback(self):
        lot = MaterialLot.objects.create(
            material=self.first, lot_code="WINTER-01", density_g_ml=4, price_per_kg=0)
        self.row_a.specified_lot = lot
        self.row_a.save()
        output = self.group_output()
        self.assertEqual(output["rows"][0]["cost"], "0")
        self.assertEqual(output["rows"][0]["density_g_ml"], "4")
        self.assertEqual(output["rows"][0]["price_origin"], "lot")
        self.assertEqual(output["total_cost"], "100")
        self.assertEqual(output["volume_ml"], "375")

    def test_missing_prices_and_density_do_not_become_zero(self):
        MaterialLot.objects.filter(pk=self.first_lot.pk).update(
            density_g_ml=None, price_per_kg=None)
        output = self.group_output()
        self.assertIsNone(output["total_cost"])
        self.assertIsNone(output["density_g_ml"])
        self.assertIsNone(output["rows"][0]["volume_ml"])
        self.assertEqual(output["rows"][1]["cost"], "100")
        self.assertTrue(output["issues"])

    def test_zero_mass_row_contributes_zero_despite_unknown_price_and_density(self):
        MaterialLot.objects.filter(pk=self.first_lot.pk).update(
            density_g_ml=None, price_per_kg=None)
        RecipeComponent.objects.filter(pk=self.row_a.pk).update(quantity_value=0)
        RecipeComponent.objects.filter(pk=self.row_b.pk).update(quantity_value=100)
        output = self.group_output()
        self.assertEqual(output["rows"][0]["mass_g"], "0")
        self.assertEqual(output["rows"][0]["cost"], "0")
        self.assertEqual(output["rows"][0]["volume_ml"], "0")
        self.assertEqual(output["total_cost"], "200")
        self.assertEqual(output["density_g_ml"], "2")

    def test_material_without_selected_lot_has_unknown_price_and_density(self):
        self.row_a.specified_lot = None
        self.row_a.unbatched_material = self.first
        self.row_a.save()
        output = self.group_output()
        row = output['rows'][0]
        self.assertIsNone(row['lot_id'])
        self.assertIsNone(row['price_per_kg'])
        self.assertIsNone(row['density_g_ml'])
        self.assertIsNone(row['cost'])
        self.assertIsNone(row['volume_ml'])
        self.assertIsNone(output['total_cost'])
        self.assertEqual(output['rows'][1]['cost'], '100')

    def test_supplier_comes_only_from_selected_lot(self):
        self.first_lot.supplier = 'Поставщик партии'
        self.first_lot.save()
        self.assertEqual(self.group_output()['rows'][0]['supplier'], 'Поставщик партии')
        self.row_a.specified_lot = None
        self.row_a.unbatched_material = self.first
        self.row_a.save()
        self.assertEqual(self.group_output()['rows'][0]['supplier'], '')

    def test_missing_quantity_stops_group_calculations(self):
        RecipeComponent.objects.filter(pk=self.row_a.pk).update(quantity_value=None)
        output = self.group_output()
        self.assertFalse(output["valid"])
        self.assertIsNone(output["mass_g"])
        self.assertIsNone(output["total_cost"])

    def test_wrong_percentage_sum_unknown_basis_and_partial_composition(self):
        RecipeComponent.objects.filter(pk=self.row_a.pk).update(quantity_value=49)
        self.assertFalse(self.group_output()["valid"])
        RecipeComponent.objects.filter(pk=self.row_a.pk).update(quantity_value=50)
        RecipeGroup.objects.filter(pk=self.group.pk).update(quantity_basis="unknown")
        self.assertFalse(self.group_output()["valid"])
        RecipeGroup.objects.filter(pk=self.group.pk).update(quantity_basis="mass_percent", completeness="partial")
        self.assertFalse(self.group_output()["valid"])

    def test_weight_parts_are_normalized_and_nonpositive_batch_rejected(self):
        RecipeGroup.objects.filter(pk=self.group.pk).update(quantity_basis="weight_parts")
        RecipeComponent.objects.filter(pk=self.row_a.pk).update(quantity_value=1)
        RecipeComponent.objects.filter(pk=self.row_b.pk).update(quantity_value=3)
        output = self.group_output()
        self.assertEqual(output["rows"][0]["mass_g"], "250")
        self.assertEqual(output["rows"][1]["mass_g"], "750")
        for batch in (0, -1, "NaN", "Infinity", None):
            if batch is None:
                continue
            with self.subTest(batch=batch), self.assertRaises(ValidationError):
                calculate_recipe(self.recipe, batch_mass_g=batch)

    def test_saved_inputs_are_immutable_when_catalogue_or_lot_changes(self):
        record = save_calculation(self.recipe, self.author)
        self.assertEqual(record.outputs["groups"][0]["total_cost"], "150")
        self.assertEqual(
            record.inputs["groups"][0]["components"][0]["price_per_kg"], "100")
        MaterialLot.objects.filter(pk=self.first_lot.pk).update(
            price_per_kg=900, density_g_ml=4)
        self.assertEqual(self.group_output()["total_cost"], "550")
        record.refresh_from_db()
        self.assertEqual(record.outputs["groups"][0]["total_cost"], "150")
        self.assertEqual(
            record.inputs["groups"][0]["components"][0]["price_per_kg"], "100")
        with self.assertRaises(ValidationError):
            CalculationRecord.objects.filter(pk=record.pk).update(outputs={})
        with self.assertRaises(ValidationError):
            record.delete()

    def test_recipe_template_references_and_group_consistency_validated(self):
        definition = {"components": [{"group_label": "A", "quantity_basis": "mass_percent", "completeness": "complete", "material": self.first.pk, "quantity_value": "100"}], "filling": [{"group_label": "A", "mass_g": "220"}]}
        template = RecipeTemplate.objects.create(name="Зимняя пена", definition=definition)
        self.assertEqual(template.definition["components"][0]["material"], self.first.pk)
        bad = {**definition, "filling": [{"group_label": "NOT_IN_COMPONENTS", "mass_g": 220}]}
        with self.assertRaises(ValidationError):
            RecipeTemplate.objects.create(name="Bad", definition=bad)
        with self.assertRaises(ValidationError):
            RecipeTemplate.objects.filter(pk=template.pk).update(name="Changed")

    def test_lot_property_constraints_and_note_source_without_fake_hash(self):
        note = SourceFile.objects.create(
            title="Учебная заметка", original_path="Учебная заметка",
            kind="note", notes="Без исходного файла")
        self.assertEqual(note.sha256, "")
        for update in ({"density_g_ml": 0}, {"price_per_kg": -1}):
            with self.assertRaises(IntegrityError), transaction.atomic():
                MaterialLot.objects.filter(pk=self.first_lot.pk).update(**update)
