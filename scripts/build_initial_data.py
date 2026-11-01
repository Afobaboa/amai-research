#!/usr/bin/env python3
"""Build the initial winter-foam dataset from six original Excel workbooks.

Build with Пена/.venv/bin/python; Excel and analysis dependencies are build-only.
The application reads the checked-in JSON and never accesses the archive.
"""

from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import sys
from xml.etree import ElementTree as ET
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "Пена/scripts"))
from inspect_archive import read_book
from import_archive import BadValue, Extractor
from openpyxl.styles.numbers import BUILTIN_FORMATS
from pena_matching import match_identifier

from winter_test_extract import extract_test


NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
SERIES = [
    ("316", "Зимняя улучшенная пена",
     "316 ЗИМНЯЯ УЛУЧШЕННАЯ.xlsx", "316.xlsx"),
    ("302", "Зимняя пена", "302 рецептура ЗИМНЯЯ.xlsx", "302.xlsx"),
    ("316-Ф", "Зимняя пена с фреоном",
     "316 рецептура с фреоном 152а.xlsx", "316 с фреоном 152а.xlsx"),
]
GASES = {
    "dme": "Диметиловый эфир (DME)",
    "prop/ib": "Пропан-изобутан",
    "f134a": "Фреон R134a",
    "f152a": "Фреон R152a",
}


def number(value):
    if value is None:
        return None
    try:
        result = Decimal(str(value).strip().replace(",", "."))
    except InvalidOperation:
        return None
    return str(result) if result.is_finite() else None


def clean_name(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def styles(path):
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read("xl/styles.xml"))
    custom = {
        int(node.get("numFmtId")): node.get("formatCode")
        for node in root.findall("m:numFmts/m:numFmt", NS)
    }
    return [
        custom.get(int(node.get("numFmtId", "0")), BUILTIN_FORMATS.get(
            int(node.get("numFmtId", "0")), "General"))
        for node in root.findall("m:cellXfs/m:xf", NS)
    ]


def source_cells(book, formats):
    cells = {
        address: {
            "cell_kind": cell["t"],
            "raw_value": None if cell["f"] else cell["v"],
            "formula_text": cell["f"],
            "cached_value": cell["v"] if cell["f"] else None,
            "number_format": formats[int(cell.get("s") or 0)],
        }
        for address, cell in book["cells"].items()
    }
    for address, text in book["comments"].items():
        cells[f"{address}:comment"] = {
            "cell_kind": "comment", "raw_value": text,
            "formula_text": None, "cached_value": None,
            "number_format": None,
        }
    return cells


def value(book, address):
    return book["cells"].get(address, {}).get("v")


def property_value(book, code, address, unit=None):
    numeric = number(value(book, address))
    if numeric is None or book["cells"].get(address, {}).get("f"):
        return None
    return {"code": code, "value": numeric, "unit": unit, "cell": address}


def properties(book, definitions):
    result = [property_value(book, *definition) for definition in definitions]
    return [item for item in result if item is not None]


def component(book, row):
    return {
        "name": clean_name(value(book, f"A{row}")),
        "name_cell": f"A{row}",
        "quantity": number(value(book, f"D{row}")),
        "quantity_cell": f"D{row}",
        "properties": properties(book, [
            ("ohv", f"B{row}", "mg KOH/g"),
            ("source_price_per_kg", f"C{row}", "source_currency/kg"),
            ("density_g_ml", f"H{row}", "g/ml"),
        ]),
    }


def recipe_extractor(book, end):
    zero_blanks = [
        f"D{row}" for row in range(4, end)
        if value(book, f"D{row}") is None
        and number(value(book, f"G{row}")) is not None
        and Decimal(number(value(book, f"G{row}"))) == 0
    ]
    return Extractor(book, recipe=True, zero_blanks=zero_blanks)


def checked_mass(book, extractor, address):
    cell = book["cells"].get(address, {})
    if not cell.get("f"):
        numeric = number(cell.get("v"))
        return numeric, "recorded" if numeric is not None else "unknown"
    try:
        numeric = extractor.evaluate(address)
    except BadValue:
        return None, "calculation_unchecked"
    if numeric < 0:
        return None, "calculation_unchecked"
    return number(cell.get("v")), "calculation_checked"


def filling_line(book, extractor, row, iso, end):
    label = clean_name(value(book, f"E{row}"))
    key = label.casefold()
    mass_g, status = checked_mass(book, extractor, f"F{row}")
    material = GASES.get(key)
    if key == "iso":
        material = iso or None
    conflict = gas_conflict(book, row, end, mass_g)
    return {
        "group_label": "Смесь A" if key == "polyol" else None,
        "material_name": None if conflict else material,
        "label_cell": f"E{row}", "mass_cell": f"F{row}",
        "mass_g": mass_g, "mass_status": status,
        "gas_label_conflict": conflict,
    }


def gas_conflict(book, row, end, mass):
    if mass is None or Decimal(mass) == 0:
        return False
    label = clean_name(value(book, f"E{row}")).casefold()
    density_label = clean_name(value(book, f"E{end + 3}")).casefold()
    return label in {"f134a", "f152a"} and label != density_label


def recipe_notes(book, internal, filling):
    notes = [
        "Перенесено из исходного Excel. Это плановая рецептура; "
        "изготовление по ней автоматически не подтверждается.",
        "Исторические цены сохранены в свойствах компонентов без "
        "назначения валюты: в исходном столбце указано только Price.",
    ]
    if clean_name(internal) != clean_name(book["sheet"]):
        notes.append(
            f"Обозначение листа: {book['sheet']}; "
            f"внутренний номер F1: {internal}. Оба значения сохранены."
        )
    if any(line["gas_label_conflict"] for line in filling):
        notes.append(
            "Противоречие марки фреона в исходнике: подпись строки массы "
            "F134a не совпадает с F152a в блоке плотностей. Численная "
            "масса проверена по формуле; материал не назначен."
        )
    for address, text in book["comments"].items():
        notes.append(f"Комментарий Excel {address}:\n{text}")
    return "\n\n".join(notes)


def extract_recipe(book, code, title):
    end = next(int(address[1:]) for address, cell in book["cells"].items()
               if address.startswith("A") and cell["v"] == "Summe Polyol")
    extractor = recipe_extractor(book, end)
    components = [component(book, row) for row in range(4, end)
                  if value(book, f"D{row}") is not None]
    iso = clean_name(value(book, "F2"))
    filling = [filling_line(book, extractor, end + offset, iso, end)
               for offset in (12, 13, 15, 16, 17)]
    recorded_on = extractor.date("recorded_on", "A1")
    return {
        "mixture_code": code, "mixture_title": title,
        "version_label": book["sheet"],
        "identifier_raw": value(book, "F1"),
        "title": f"{title} — {book['sheet']}",
        "recorded_on": recorded_on.isoformat() if recorded_on else None,
        "batch_mass_g": number(value(book, "G3")),
        "notes": recipe_notes(book, value(book, "F1"), filling),
        "groups": [{"label": "Смесь A", "basis": "mass_percent",
                    "completeness": "complete", "components": components}],
        "filling": filling,
        "properties": recipe_properties(book, end),
    }


def recipe_properties(book, end):
    return properties(book, [
        ("isocyanate_nco_pct", "I2", "%"),
        ("can_volume_ml", f"F{end + 28}", "ml"),
        ("polyol_ratio", f"C{end + 2}", "weight_parts"),
        ("isocyanate_ratio", f"C{end + 3}", "weight_parts"),
        ("gas_mix_mass_pct", f"B{end + 10}", "%"),
    ])


def source_file(path, code, title, kind):
    formats = styles(path)
    worksheets = []
    for book in read_book(path):
        book["file"] = path.relative_to(ROOT).as_posix()
        payload = extract_recipe(book, code, title) if kind == "recipe" else (
            extract_test(book, title))
        worksheets.append({
            "name": book["sheet"], "cells": source_cells(book, formats),
            "comments": book["comments"], "merged_ranges": book["merged"],
            kind: payload,
        })
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "title": f"{title} — {'рецептуры' if kind == 'recipe' else 'испытания'}",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "date_system": "1904" if book["epoch1904"] else "1900",
        "sheets": worksheets,
    }


def build_dataset():
    files = []
    source = ROOT / "Пена/pena"
    for code, title, recipes, tests in SERIES:
        files.append(source_file(
            source / "Разработка рецептур" / recipes, code, title, "recipe"))
        files.append(source_file(
            source / "Испытания разработки рецептур" / tests,
            code, title, "test"))
    add_recipe_matches(files)
    return {
        "schema_version": 1,
        "mixtures": [{"code": code, "title": title}
                     for code, title, _, _ in SERIES],
        "files": files,
    }


def matching_index(files):
    index, versions = {}, {}
    for file in files:
        for sheet in file["sheets"]:
            recipe = sheet.get("recipe")
            if recipe is None:
                continue
            key = f"{file['path']}#{sheet['name']}"
            versions[key] = {
                "sum_status": "within_95_105", "path": file["path"],
                "sheet": sheet["name"],
            }
            for alias in (sheet["name"], recipe["identifier_raw"]):
                alias = re.sub(r"\s+", "", str(alias)).casefold()
                index.setdefault(alias, set()).add(key)
    return index, versions


def match_test(test, source, index, versions):
    identifier = re.sub(r"\s+", "", test["identifier"]).casefold()
    match = match_identifier(
        identifier, test["identifier"], index, versions,
        source_file=source.removeprefix("Пена/pena/"))
    rule = {
        "exact_full_identifier": "полное совпадение номера",
        "verified_condition_or_repeat_base": "номер без пометки выдержки или повтора",
        "verified_source_bound_condition_punctuation":
            "проверенный вариант пунктуации пометки выдержки",
    }.get(match["matching_method"], "проверенный вариант обозначения")
    reason = (
        f"Поиск плановой рецептуры по записи {test['identifier']!r}. "
        f"Основание: {rule}. "
        "Проверяются исходные номера F1 и обозначения листов. "
        "Это автоматический кандидат, не подтверждение изготовления."
    )
    if len(match["candidate_version_ids"]) > 1:
        reason += " В источниках несколько подходящих листов; выбор не сделан."
    return [{"path": versions[key]["path"], "sheet": versions[key]["sheet"],
             "status": "candidate", "reason": reason}
            for key in match["candidate_version_ids"]]


def add_recipe_matches(files):
    index, versions = matching_index(files)
    for file in files:
        for sheet in file["sheets"]:
            test = sheet.get("test")
            if test is not None:
                test["matches"] = match_test(test, file["path"], index, versions)


def validate_dataset(dataset):
    for file in dataset["files"]:
        for sheet in file["sheets"]:
            if "recipe" not in sheet:
                continue
            recipe = sheet["recipe"]
            components = recipe["groups"][0]["components"]
            total = sum(Decimal(c["quantity"]) for c in components)
            if abs(total - 100) > Decimal("0.00000001"):
                raise ValueError(f"Unexpected composition total: {sheet['name']}")
            for component in components:
                if not component["name"] or len(component["name"]) > 255:
                    raise ValueError(f"Invalid material name: {sheet['name']}")
                if Decimal(component["quantity"]) < 0:
                    raise ValueError(f"Negative percentage: {sheet['name']}")
            if len(recipe["filling"]) != 5:
                raise ValueError(f"Incomplete fill plan: {sheet['name']}")


def recipe_anomalies(dataset):
    identifier_conflicts, gas_conflicts = [], []
    for file in dataset["files"]:
        for sheet in file["sheets"]:
            recipe = sheet.get("recipe")
            if recipe is None:
                continue
            reference = {"file": file["path"], "sheet": sheet["name"]}
            if clean_name(recipe["identifier_raw"]) != clean_name(sheet["name"]):
                identifier_conflicts.append({
                    **reference, "identifier_raw": recipe["identifier_raw"]})
            if any(line["gas_label_conflict"] for line in recipe["filling"]):
                gas_conflicts.append(reference)
    return {"identifier_differences": identifier_conflicts,
            "gas_label_conflicts": gas_conflicts}


def test_anomalies(dataset):
    unmatched, ambiguous = [], []
    for file in dataset["files"]:
        for sheet in file["sheets"]:
            test = sheet.get("test")
            if test is None:
                continue
            reference = {"file": file["path"], "sheet": sheet["name"],
                         "identifier": test["identifier"]}
            if not test["matches"]:
                unmatched.append(reference)
            if len(test["matches"]) > 1:
                ambiguous.append({**reference, "matches": test["matches"]})
    return {"unmatched_tests": unmatched, "ambiguous_tests": ambiguous}


def report_for(dataset):
    sheets = [sheet for file in dataset["files"] for sheet in file["sheets"]]
    recipes = [sheet["recipe"] for sheet in sheets if "recipe" in sheet]
    tests = [sheet["test"] for sheet in sheets if "test" in sheet]
    states = Counter(line["mass_status"] for recipe in recipes
                     for line in recipe["filling"])
    results = [result for test in tests for run in test["runs"]
               for result in run["results"]]
    return {
        "schema_version": 1,
        "command": "Пена/.venv/bin/python LIMS/scripts/build_initial_data.py",
        "files": [{key: file[key] for key in ("path", "sha256", "title")}
                  for file in dataset["files"]],
        "counts": {"files": len(dataset["files"]), "sheets": len(sheets),
                   "recipes": len(recipes), "tests": len(tests),
                   "source_cells": sum(len(s["cells"]) for s in sheets),
                   "excel_comments": sum(len(s["comments"]) for s in sheets),
                   "components": sum(len(g["components"]) for r in recipes
                                     for g in r["groups"]),
                   "conditions": sum(len(t["conditions"]) for t in tests),
                   "results": len(results),
                   "result_states": dict(Counter(
                       r["value_state"] for r in results)),
                   "observations": sum(len(run["observations"])
                                       for t in tests for run in t["runs"]),
                   "filling_mass_states": dict(states)},
        **recipe_anomalies(dataset), **test_anomalies(dataset),
        "policy": "Source cells retained; no invented or cache-only values. "
                  "Recipe matches are candidates, not proof of manufacture.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    dataset = build_dataset()
    validate_dataset(dataset)
    target = ROOT / "LIMS/initial_data"
    for name, payload in (("winter.json", dataset),
                          ("winter-report.json", report_for(dataset))):
        content = json.dumps(payload, ensure_ascii=False, indent=(
            2 if name == "winter-report.json" else None), separators=(",", ":"))
        path = target / name
        if args.check:
            if path.read_text(encoding="utf-8") != content + "\n":
                raise SystemExit(f"Outdated bundle: {path}")
        else:
            path.write_text(content + "\n", encoding="utf-8")
    print(json.dumps(report_for(dataset)["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
