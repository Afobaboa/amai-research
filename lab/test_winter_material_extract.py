"""Regression evidence for material names, lots, and historical Excel prices."""

import json
from pathlib import Path

from django.test import SimpleTestCase

from scripts.winter_material_extract import (
    PRICE_COMPLETIONS, apply_price_completions, material_identity,
)


class WinterMaterialParsingTests(SimpleTestCase):
    def test_lot_markers_keep_codes_and_original_text(self):
        cases = [
            ("Inovol С307 П.260501А", "Inovol C307", "260501А"),
            ("Wanol R2310 пZG21091611", "Wanol R2310", "ZG21091611"),
            ("Struksilon 8006 ( п.5186930 )", "Struksilon 8006", "5186930"),
            ("Wanol С 2010 п.ZG22070821", "Wanol C2010", "ZG22070821"),
        ]
        for raw, name, code in cases:
            with self.subTest(raw=raw):
                row = material_identity(raw)
                self.assertEqual((row["name"], row["lot_code"]), (name, code))
                self.assertEqual(row["name_raw"], raw)

    def test_dates_and_quality_qualifiers_are_not_part_of_lot_code(self):
        row = material_identity("Petol 250-3 п. 3 от 8.11.2021")
        self.assertEqual(row["lot_code"], "3")
        self.assertEqual(row["lot_notes"], "от 8.11.2021")
        row = material_identity("MATESTAB AK 8872 п. 210326-1-3-4 (мутный)")
        self.assertEqual(row["lot_code"], "210326-1-3-4")
        self.assertEqual(row["lot_notes"], "мутный")

    def test_blank_lot_number_and_distinct_grades_are_not_invented(self):
        row = material_identity("BA-101 п.")
        self.assertEqual(row["name"], "BA-101")
        self.assertIsNone(row["lot_code"])
        self.assertTrue(row["lot_notes"])
        self.assertNotEqual(material_identity("TCPP Nantong")["name"],
                            material_identity("TCPP")["name"])

    def test_unmarked_lot_is_only_resolved_for_verified_archive_variant(self):
        row = material_identity("Voranol CP 1050 F359LAJL05")
        explicit = material_identity("Voranol СР1050 п.F359LAJL05")
        self.assertEqual((row["name"], row["lot_code"]),
                         (explicit["name"], explicit["lot_code"]))
        self.assertIn("302.28!A5", row["lot_notes"])
        self.assertIsNone(material_identity("Other grade CODE123")["lot_code"])


class WinterPriceEvidenceTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        path = Path(__file__).resolve().parents[1] / "initial_data/winter.json"
        cls.data = json.loads(path.read_text(encoding="utf-8"))

    def recipe_sheet(self, name):
        return next(sheet for file in self.data["files"]
                    for sheet in file["sheets"]
                    if sheet["name"] == name and "recipe" in sheet)

    def test_component_prices_include_verified_constant_formulas(self):
        sheet = self.recipe_sheet("302.11")
        component = next(row for row in sheet["recipe"]["groups"][0]["components"]
                         if row["name"] == "NIAX A-1")
        price = next(p for p in component["properties"]
                     if p["code"] == "source_price_per_kg")
        self.assertEqual((price["value"], price["unit"], price["cell"]),
                         ("480.26", "EUR/kg", "C15"))
        self.assertEqual(sheet["cells"]["C15"]["formula_text"], "480.26")
        iso = sheet["recipe"]["filling"][1]
        self.assertEqual(iso["name_raw"], sheet["cells"]["F2"]["raw_value"])

    def test_isocyanate_and_gases_retain_own_price_density_cells(self):
        filling = self.recipe_sheet("316.26")["recipe"]["filling"]
        iso, dme = filling[1], filling[2]
        self.assertEqual(iso["name_cell"], "F2")
        self.assertIn({"code": "density_g_ml", "value": "1.23",
                       "unit": "g/ml", "cell": "B24"}, iso["properties"])
        self.assertIn({"code": "source_price_per_kg", "value": "100.63",
                       "unit": "EUR/kg", "cell": "H34"}, iso["properties"])
        self.assertEqual({p["cell"] for p in dme["properties"]}, {"H36", "F23"})

    def test_tep_price_completion_preserves_actual_donor_and_empty_target(self):
        sheet = self.recipe_sheet("302.84")
        row = next(c for c in sheet["recipe"]["groups"][0]["components"]
                   if c["name"] == "TEP-Z")
        price = next(p for p in row["properties"]
                     if p["code"] == "source_price_per_kg")
        self.assertEqual(price["value"], "64.69")
        self.assertEqual(price["unit"], "EUR/kg")
        self.assertEqual(price["origin"], "carried_forward")
        self.assertEqual((price["source_sheet"], price["cell"]), ("302.83", "C12"))
        self.assertEqual(price["target_cell"], "C11")
        self.assertTrue(price["inferred_from_previous_sheet"])
        self.assertIsNone(price["donor_lot_code"])
        self.assertIn("партия не указана", price["provenance_note"])
        self.assertIn("64,69 €/кг", sheet["recipe"]["notes"])
        self.assertNotIn("C11", sheet["cells"])
        donor = self.recipe_sheet("302.83")
        self.assertEqual(donor["cells"]["C12"]["raw_value"], price["value"])

    def test_conflicting_gas_density_remains_unknown(self):
        gas = self.recipe_sheet("316Ф-1")["recipe"]["filling"][3]
        self.assertIsNone(gas["material_name"])
        self.assertNotIn("density_g_ml", {p["code"] for p in gas["properties"]})

    def test_numeric_nco_formula_does_not_become_material(self):
        sheet = self.recipe_sheet("302.51.1")
        row = sheet["recipe"]["filling"][1]
        self.assertIsNone(row["material_name"])
        self.assertEqual(row["name_raw"], "31.25")
        self.assertTrue(row["invalid_material_label"])
        self.assertEqual(sheet["cells"]["F2"]["formula_text"], "I2")

    def test_currency_override_preserves_original_header(self):
        sheet = self.recipe_sheet("316.1")
        self.assertEqual(self.data["price_currency"], "EUR")
        self.assertEqual(sheet["cells"]["H42"]["raw_value"], "Price, руб /kg")
        prices = [p for row in sheet["recipe"]["groups"][0]["components"]
                  for p in row["properties"] if p["code"] == "source_price_per_kg"]
        self.assertTrue(all(p["unit"] == "EUR/kg" for p in prices))


class ExplicitPriceCompletionTests(SimpleTestCase):
    def fixture(self):
        donor = {"name": "302.83", "cells": {
            "A12": {"raw_value": "TEP-Z "}, "C12": {"raw_value": "64.69"},
        }}
        target = {"name": "302.84", "cells": {
            "A11": {"raw_value": "TEP-Z п.260409"},
        }, "recipe": {"notes": "", "groups": [{"components": [{
            "name_cell": "A11", "properties": [],
        }]}]}}
        return [{"path": PRICE_COMPLETIONS[0]["source_path"],
                 "sheets": [donor, target]}]

    def test_completion_refuses_a_different_donor_material(self):
        files = self.fixture()
        files[0]["sheets"][0]["cells"]["A12"]["raw_value"] = "Shivachlor 008"
        with self.assertRaisesRegex(ValueError, "Material or lot changed"):
            apply_price_completions(files)

    def test_completion_never_overwrites_a_newly_recorded_target_price(self):
        files = self.fixture()
        files[0]["sheets"][1]["cells"]["C11"] = {"raw_value": "99"}
        with self.assertRaisesRegex(ValueError, "no longer empty"):
            apply_price_completions(files)

    def test_changed_donor_value_requires_review(self):
        files = self.fixture()
        files[0]["sheets"][0]["cells"]["C12"]["raw_value"] = "65"
        with self.assertRaisesRegex(ValueError, "donor changed"):
            apply_price_completions(files)
