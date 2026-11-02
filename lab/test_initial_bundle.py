"""Source-specific regressions for the checked-in winter archive snapshot."""

import json
from pathlib import Path

from django.test import SimpleTestCase

from scripts.winter_test_extract import result


class InitialBundleTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        path = Path(__file__).resolve().parents[1] / "initial_data/winter.json"
        cls.data = json.loads(path.read_text(encoding="utf-8"))

    def sheet(self, filename, name):
        file = next(file for file in self.data["files"]
                    if Path(file["path"]).name == filename)
        return next(sheet for sheet in file["sheets"] if sheet["name"] == name)

    def test_series_use_source_codes_and_clear_russian_names(self):
        self.assertEqual(self.data["mixtures"], [
            {"code": "316", "title": "Зимняя улучшенная пена"},
            {"code": "302", "title": "Зимняя пена"},
            {"code": "316-Ф", "title": "Зимняя пена с фреоном"},
        ])

    def test_improved_recipe_values_are_from_the_actual_316_26(self):
        sheet = self.sheet("316 ЗИМНЯЯ УЛУЧШЕННАЯ.xlsx", "316.26")
        recipe = sheet["recipe"]
        self.assertEqual(recipe["recorded_on"], "2026-09-08")
        component = recipe["groups"][0]["components"][0]
        self.assertEqual(component["name"], "Inovol C210")
        self.assertEqual(component["lot_code"], "260613G")
        self.assertEqual(component["name_raw"], "Inovol C210 п.260613G")
        self.assertEqual(component["quantity"], "12.8")
        self.assertEqual(component["properties"][0]["value"], "112")
        self.assertEqual(sheet["cells"]["D4"]["raw_value"], "12.8")
        self.assertEqual(sheet["cells"]["A6:comment"]["raw_value"], "aromatic\n")

    def test_wrong_internal_number_retains_both_candidates(self):
        card = self.sheet("302.xlsx", "302.69")["test"]
        self.assertSetEqual({match["sheet"] for match in card["matches"]},
                            {"302.68", "302.69"})
        self.assertTrue(all(match["status"] == "candidate"
                            for match in card["matches"]))
        for filename, name in (("302.xlsx", "302.31"),
                               ("316 с фреоном 152а.xlsx", "316Ф-6")):
            self.assertEqual(self.sheet(filename, name)["test"]["matches"], [])

    def test_repeat_card_is_separate_and_keeps_the_plan_candidate(self):
        first = self.sheet("316.xlsx", "316.8")["test"]
        repeat = self.sheet("316.xlsx", "316.8 (2)")["test"]
        self.assertNotEqual(first["title"], repeat["title"])
        self.assertEqual(first["matches"], repeat["matches"])
        self.assertEqual(repeat["matches"][0]["sheet"], "316.8")

    def test_fake_100_percent_with_blank_operand_stays_missing(self):
        sheet = self.sheet("316 с фреоном 152а.xlsx", "316Ф-1")
        cell = sheet["cells"]["F16"]
        self.assertEqual(cell["cached_value"], "100")
        book = {"cells": {"F16": {
            "v": cell["cached_value"], "f": cell["formula_text"], "t": "n",
        }}}
        extracted = result(book, "F16", "shrinkage_paper", "Усадка", "%")
        self.assertEqual(extracted["value_state"], "missing_inputs")
        self.assertIsNone(extracted["value_numeric"])

    def test_invalid_year_is_not_silently_interpreted_as_1900(self):
        for name in ("302.50 ст.пол", "302.50 ст.бал"):
            sheet = self.sheet("302.xlsx", name)
            self.assertEqual(sheet["cells"]["C2"]["raw_value"], "202")
            self.assertIsNone(sheet["test"]["recorded_manufactured_on"])
            self.assertIn("202", sheet["test"]["notes"])
