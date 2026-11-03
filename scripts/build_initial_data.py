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
from winter_material_extract import apply_price_completions, material_identity
from winter_formulation_extract import add_formulations


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


def property_value(book, code, address, unit=None, extractor=None):
    numeric = number(value(book, address))
    if numeric is None:
        return None
    if book["cells"].get(address, {}).get("f"):
        if extractor is None:
            return None
        try:
            extractor.evaluate(address)
        except BadValue:
            return None
    return {"code": code, "value": numeric, "unit": unit, "cell": address}


def properties(book, definitions, extractor=None):
    result = [property_value(book, *definition, extractor=extractor)
              for definition in definitions]
    return [item for item in result if item is not None]


def component(book, row, extractor):
    return {
        **material_identity(value(book, f"A{row}")),
        "name_cell": f"A{row}",
        "quantity": number(value(book, f"D{row}")),
        "quantity_cell": f"D{row}",
        "properties": properties(book, [
            ("ohv", f"B{row}", "mg KOH/g"),
            ("source_price_per_kg", f"C{row}", "EUR/kg"),
            ("density_g_ml", f"H{row}", "g/ml"),
        ], extractor),
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
    identity = material_identity(iso if key == "iso" else material)
    conflict = gas_conflict(book, row, end, mass_g)
    invalid_label = key == "iso" and number(iso) is not None
    return {
        "group_label": "Смесь A" if key == "polyol" else None,
        "material_name": None if conflict or invalid_label else (
            identity["name"] or None),
        "name_raw": (iso or "") if key == "iso" else value(book, f"E{row}"),
        "lot_code": identity["lot_code"],
        "lot_notes": identity["lot_notes"],
        "name_cell": "F2" if key == "iso" else f"E{row}",
        "label_cell": f"E{row}", "mass_cell": f"F{row}",
        "mass_g": mass_g, "mass_status": status,
        "gas_label_conflict": conflict,
        "invalid_material_label": invalid_label,
        "properties": filling_properties(book, extractor, row, end, key, conflict),
    }


def filling_properties(book, extractor, row, end, key, conflict):
    definitions = [("source_price_per_kg", f"H{row}", "EUR/kg")]
    density = {"iso": f"B{end + 3}", "dme": f"F{end + 2}",
               "f134a": f"F{end + 3}", "f152a": f"F{end + 3}",
               "prop/ib": f"F{end + 4}"}.get(key)
    if density and not conflict:
        definitions.append(("density_g_ml", density, "g/ml"))
    return properties(book, definitions, extractor)


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
        "Исторические цены компонентов и заправки перенесены как EUR/кг "
        "без пересчёта по указанию пользователя. Исходные подписи валют "
        "сохранены в ячейках Excel; актуальность цен не подтверждена.",
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
    if any(line["invalid_material_label"] for line in filling):
        notes.append(
            "В F2 вместо марки изоцианата записана числовая формула NCO. "
            "Число сохранено в источнике; материал не назначен."
        )
    for address, text in book["comments"].items():
        notes.append(f"Комментарий Excel {address}:\n{text}")
    return "\n\n".join(notes)


def extract_recipe(book, code, title):
    end = next(int(address[1:]) for address, cell in book["cells"].items()
               if address.startswith("A") and cell["v"] == "Summe Polyol")
    extractor = recipe_extractor(book, end)
    components = [component(book, row, extractor) for row in range(4, end)
                  if value(book, f"D{row}") is not None]
    iso = value(book, "F2")
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
        "properties": recipe_properties(book, end, extractor),
    }


def recipe_properties(book, end, extractor):
    return properties(book, [
        ("isocyanate_nco_pct", "I2", "%"),
        ("can_volume_ml", f"F{end + 28}", "ml"),
        ("polyol_ratio", f"C{end + 2}", "weight_parts"),
        ("isocyanate_ratio", f"C{end + 3}", "weight_parts"),
        ("gas_mix_mass_pct", f"B{end + 10}", "%"),
    ], extractor)


def source_file(path, code, title, kind):
    original_path = path.relative_to(ROOT).as_posix()
    physical_path = bundled_source(path)
    formats = styles(physical_path)
    worksheets = []
    for book in read_book(physical_path):
        book["file"] = original_path
        payload = extract_recipe(book, code, title) if kind == "recipe" else (
            extract_test(book, title))
        worksheets.append({
            "name": book["sheet"], "cells": source_cells(book, formats),
            "comments": book["comments"], "merged_ranges": book["merged"],
            kind: payload,
        })
    return {
        "path": original_path,
        "title": f"{title} — {'рецептуры' if kind == 'recipe' else 'испытания'}",
        "sha256": hashlib.sha256(physical_path.read_bytes()).hexdigest(),
        "date_system": "1904" if book["epoch1904"] else "1900",
        "sheets": worksheets,
    }


def bundled_source(original):
    directory = ROOT / "LIMS/initial_data/sources"
    manifest = json.loads((directory / "manifest.json").read_text("utf-8"))
    logical_path = original.relative_to(ROOT).as_posix()
    entry = next(item for item in manifest
                 if item["original_path"] == logical_path)
    path = directory / f"{entry['sha256']}.xlsx"
    if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
        raise ValueError(f"Bundled source checksum mismatch: {logical_path}")
    return path


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
    price_completions = apply_price_completions(files)
    add_formulations(files)
    return {
        "schema_version": 2,
        "price_currency": "EUR",
        "price_currency_policy": "user_override_no_conversion",
        "price_completions": price_completions,
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
    candidates = match["candidate_version_ids"]
    status = "confirmed" if len(candidates) == 1 else "candidate"
    reason = match_reason(test, match["matching_method"], status)
    return [{"path": versions[key]["path"], "sheet": versions[key]["sheet"],
             "status": status, "reason": reason} for key in candidates]


def match_reason(test, method, status):
    rule = {
        "exact_full_identifier": "полное совпадение номера",
        "verified_condition_or_repeat_base": "номер без пометки выдержки или повтора",
        "verified_source_bound_condition_punctuation":
            "проверенный вариант пунктуации пометки выдержки",
    }.get(method, "проверенный вариант обозначения")
    reason = (
        f"Поиск плановой рецептуры по записи {test['identifier']!r}. "
        f"Основание: {rule}. "
        "Проверяются исходные номера F1 и обозначения листов. "
    )
    if status == "confirmed":
        return reason + (
            "Однозначная связь с плановой рецептурой; "
            "не подтверждает фактическое изготовление."
        )
    return reason + (
        "В источниках несколько подходящих листов; выбор не сделан."
    )


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
                validate_material_properties(component, sheet["name"])
            if len(recipe["filling"]) != 5:
                raise ValueError(f"Incomplete fill plan: {sheet['name']}")
            for line in recipe["filling"]:
                validate_material_properties(line, sheet["name"])


def validate_material_properties(row, sheet):
    if row["lot_code"] and len(row["lot_code"]) > 160:
        raise ValueError(f"Invalid lot code: {sheet}")
    for prop in row["properties"]:
        if prop["code"] == "source_price_per_kg":
            if prop["unit"] != "EUR/kg" or Decimal(prop["value"]) < 0:
                raise ValueError(f"Invalid historical price: {sheet}")
        if prop["code"] == "density_g_ml" and Decimal(prop["value"]) <= 0:
            raise ValueError(f"Invalid material density: {sheet}")


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


def material_report(dataset):
    names, lots, counts = set(), set(), Counter()
    missing_prices, invalid_labels, currency_headers = [], [], []
    for file in dataset["files"]:
        for sheet in file["sheets"]:
            recipe = sheet.get("recipe")
            if recipe is None:
                continue
            reference = {"file": file["path"], "sheet": sheet["name"]}
            for cell in sheet["cells"].values():
                if cell["raw_value"] == "Price, руб /kg":
                    currency_headers.append(reference)
            groups = [("component", recipe["groups"][0]["components"]),
                      ("filling", recipe["filling"])]
            for kind, rows in groups:
                for row in rows:
                    material_counts(kind, row, names, lots, counts)
                    if row.get("invalid_material_label"):
                        invalid_labels.append(reference)
                    if kind == "component" and not any(
                            p["code"] == "source_price_per_kg"
                            for p in row["properties"]):
                        missing_prices.append({**reference,
                                               "cell": "C" + row["name_cell"][1:],
                                               "name_cell": row["name_cell"],
                                               "material": row["name_raw"]})
    return {"counts": {"materials": len(names), "lots": len(lots), **counts},
            "missing_component_prices": missing_prices,
            "invalid_material_labels": invalid_labels,
            "non_euro_headers_overridden_by_user": currency_headers}


def material_counts(kind, row, names, lots, counts):
    name = row.get("name", row.get("material_name"))
    if name:
        names.add(name)
    if name and row["lot_code"]:
        lots.add((name, row["lot_code"]))
    for prop in row["properties"]:
        counts[f"{kind}_{prop['code']}"] += 1


def report_counts(files, sheets, recipes, tests):
    states = Counter(line["mass_status"] for recipe in recipes
                     for line in recipe["filling"])
    results = [result for test in tests for run in test["runs"]
               for result in run["results"]]
    return {"files": len(files), "sheets": len(sheets),
            "recipes": len(recipes), "tests": len(tests),
            "recipe_match_states": dict(Counter(
                match["status"] for test in tests for match in test["matches"])),
            "source_cells": sum(len(s["cells"]) for s in sheets),
            "excel_comments": sum(len(s["comments"]) for s in sheets),
            "components": sum(len(g["components"]) for r in recipes
                              for g in r["groups"]),
            "conditions": sum(len(t["conditions"]) for t in tests),
            "results": len(results),
            "result_states": dict(Counter(r["value_state"] for r in results)),
            "observations": sum(len(run["observations"])
                                for t in tests for run in t["runs"]),
            "filling_mass_states": dict(states)}


def report_for(dataset):
    sheets = [sheet for file in dataset["files"] for sheet in file["sheets"]]
    recipes = [sheet["recipe"] for sheet in sheets if "recipe" in sheet]
    tests = [sheet["test"] for sheet in sheets if "test" in sheet]
    return {
        "schema_version": 2,
        "command": "Пена/.venv/bin/python LIMS/scripts/build_initial_data.py",
        "files": [{key: file[key] for key in ("path", "sha256", "title")}
                  for file in dataset["files"]],
        "counts": report_counts(dataset["files"], sheets, recipes, tests),
        "material_import": material_report(dataset),
        "price_completions": dataset["price_completions"],
        **recipe_anomalies(dataset), **test_anomalies(dataset),
        "policy": report_policy(),
    }


def report_policy():
    return (
        "Source cells retained; no invented or cache-only values. "
        "Historical prices are EUR/kg by explicit user instruction, "
        "without conversion; original currency headers retained. "
        "Unique recipe matches are confirmed document links; ambiguous matches "
        "remain candidates. These links are not proof of manufacture."
    )


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
