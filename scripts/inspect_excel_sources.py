#!/usr/bin/env python3
"""Inventory original XLSX and copy selected cell evidence without Parquet.

Only the output JSON is written. XML cached values are preserved, never treated
as verified calculations. No business entities or independent experiments are
counted by this script.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import posixpath
import re
import xml.etree.ElementTree as ET
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[2]
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
CASES = [
    ("recipe_316_26", "Разработка рецептур/316 ЗИМНЯЯ УЛУЧШЕННАЯ.xlsx", "316.26",
     ["A1:J6", "A13:I21", "E32:I40", "E49:G49"]),
    ("stp_40", "Разработка рецептур/STP рецептура.xlsx", "40",
     ["A1:D1", "A3:F17", "A19:G37"]),
    ("stp_43", "Разработка рецептур/STP рецептура.xlsx", "43",
     ["A1:D1", "A3:F17", "A19:G37"]),
    ("stp_template_labels", "Разработка рецептур/STP рецептура.xlsx", "2",
     ["A1:D16"]),
    ("recipe_matrix", "Разработка рецептур/Архив/801 рецептура.xlsx", "Лист1",
     ["A1:L22", "A38:L40"]),
    ("recipe_gas_conflict", "Разработка рецептур/316 рецептура с фреоном 152а.xlsx", "316Ф-2",
     ["A22:I26", "A36:I38", "B56:F57"]),
    ("test_316_8", "Испытания разработки рецептур/316.xlsx", "316.8",
     ["A2:I11", "A13:F22", "A24:I27", "A34:I36"]),
    ("test_316_8_second", "Испытания разработки рецептур/316.xlsx", "316.8 (2)",
     ["A2:I11", "A13:F22", "A24:I27", "A34:I36"]),
    ("formula_missing_input", "Испытания разработки рецептур/316.xlsx", "316.17 стар.пол.",
     ["A2:G4", "A14:F16", "A25:I27", "A34:I36"]),
    ("aging_blend", "Испытания разработки рецептур/Проверка нового сырья/HJC.xlsx", "830 ст.пол",
     ["A2:G11", "A14:F18"]),
    ("aging_blend_and_can", "Испытания разработки рецептур/Проверка нового сырья/HJC.xlsx", "830 ст.пол+ст.бал",
     ["A2:G11", "A14:F18"]),
    ("aging_sequence", "Испытания разработки рецептур/302.xlsx", "302.19(ст.бал)",
     ["A2:G11", "A14:F18"]),
    ("aging_duration", "Испытания разработки рецептур/302.xlsx", "302.19(ст.бал1,5)",
     ["A2:G11", "A14:F18"]),
    ("external_product", "Испытания разработки рецептур/Сторонние образцы.xlsx", "экопласт ",
     ["A2:G11", "A14:F16", "A18:I25"]),
    ("dispensing_rows", "Испытания разработки рецептур/105.xlsx", "105.1",
     ["A2:G4", "A18:I21"]),
    ("external_replicates", "Испытания разработки рецептур/Физ.мехи/Analysis 1st Round -  2024R11P15 PU FOAMS RUSSIA - KUDO.xlsx", "L1-MU-M",
     ["A1:H10", "A43:H49"]),
    ("thermal_journal", "Испытания разработки рецептур/Физ.мехи/Теплопроводность /Журнал опредления коэффициента теплопроводности.xlsx", "Лист1",
     ["A1:P14"]),
]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text_nodes(node: ET.Element) -> str:
    return "".join(n.text or "" for n in node.findall(".//m:t", NS))


def shared_strings(z: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    return [text_nodes(n) for n in ET.fromstring(z.read("xl/sharedStrings.xml"))]


def sheets(z: ZipFile) -> tuple[str, list[tuple[str, str]]]:
    book = ET.fromstring(z.read("xl/workbook.xml"))
    props = book.find("m:workbookPr", NS)
    epoch = "1904" if props is not None and props.get("date1904") in {"1", "true"} else "1900"
    rels = {n.get("Id"): n.get("Target") for n in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    entries = []
    for sheet in book.findall("m:sheets/m:sheet", NS):
        target = rels[sheet.get(f"{{{REL}}}id")]
        member = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
        entries.append((sheet.get("name"), member))
    return epoch, entries


def cells(xml: ET.Element, strings: list[str]) -> dict[str, dict]:
    result = {}
    for cell in xml.findall(".//m:sheetData/m:row/m:c", NS):
        v = cell.find("m:v", NS)
        f = cell.find("m:f", NS)
        t = cell.get("t", "n")
        raw = v.text if v is not None else None
        value = strings[int(raw)] if t == "s" and raw is not None else text_nodes(cell) if t == "inlineStr" else raw
        if value is None and f is None:
            continue
        result[cell.get("r")] = {
            "xml_type": t,
            "style_index": cell.get("s"),
            "value_raw": value if f is None else None,
            "xml_v": raw,
            "formula_raw": f.text if f is not None else None,
            "formula_attributes": dict(f.attrib) if f is not None else None,
            "cached_value_unvalidated": value if f is not None else None,
        }
    return result


def col_number(label: str) -> int:
    value = 0
    for char in label:
        value = value * 26 + ord(char) - 64
    return value


def col_name(number: int) -> str:
    label = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        label = chr(remainder + 65) + label
    return label


def addresses(ranges: list[str]) -> list[str]:
    found = set()
    for span in ranges:
        start, end = span.split(":") if ":" in span else (span, span)
        a, b = re.fullmatch(r"([A-Z]+)(\d+)", start), re.fullmatch(r"([A-Z]+)(\d+)", end)
        for row in range(int(a[2]), int(b[2]) + 1):
            for col in range(col_number(a[1]), col_number(b[1]) + 1):
                found.add((row, col))
    return [f"{col_name(col)}{row}" for row, col in sorted(found)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "LIMS/analysis/excel-source-evidence.json")
    args = parser.parse_args()
    source = ROOT / "Пена/pena"
    files = sorted(source.rglob("*.xlsx"))
    cases_by_source = {}
    for case_id, relative, sheet, ranges in CASES:
        cases_by_source.setdefault((relative, sheet), []).append((case_id, ranges))
    inventory, examples = [], []
    counts = Counter()
    for path in files:
        relative = path.relative_to(source).as_posix()
        before = digest(path)
        book_counts = Counter()
        with ZipFile(path) as z:
            strings = shared_strings(z)
            epoch, entries = sheets(z)
            for name, member in entries:
                xml = ET.fromstring(z.read(member))
                raw = cells(xml, strings)
                labels = "\n".join(c["value_raw"] or "" for c in raw.values() if c["xml_type"] in {"s", "inlineStr", "str"}).casefold()
                # Structural signatures only; they do not identify experiments.
                signature = "recipe_template" if "recipe for polyol" in labels and "summe polyol" in labels else "test_card_template" if "время отлипа" in labels and ("произведен" in labels or "№ баллона" in labels) else "empty" if not raw else "other"
                book_counts[signature] += 1
                counts[signature] += 1
                for case_id, ranges in cases_by_source.get((relative, name), []):
                    examples.append({
                        "case_id": case_id, "file": path.relative_to(ROOT).as_posix(),
                        "sha256": before, "sheet": name, "date_system": epoch,
                        "ranges": ranges,
                        "cells": {a: raw.get(a) for a in addresses(ranges)},
                        "merged_ranges": [n.get("ref") for n in xml.findall("m:mergeCells/m:mergeCell", NS)],
                    })
        if digest(path) != before:
            raise RuntimeError(f"Source changed during read: {path}")
        inventory.append({"file": path.relative_to(ROOT).as_posix(), "sha256": before,
                          "sheet_count": len(entries), "structural_signatures": dict(sorted(book_counts.items()))})
    found_ids = {example["case_id"] for example in examples}
    expected_ids = {case[0] for case in CASES}
    if found_ids != expected_ids:
        raise RuntimeError(f"Missing source examples: {sorted(expected_ids - found_ids)}")
    report = {
        "report_version": "1.0.0",
        "command": "Пена/.venv/bin/python LIMS/scripts/inspect_excel_sources.py",
        "source_truth": "Original XLSX files under Пена/pena; no Parquet or prior reports are read.",
        "scope": "All workbook and worksheet XML; targeted cell evidence. Structural signatures are not semantic validation or counts of independent experiments.",
        "formula_policy": "Copy formula text, XML type and cached value separately; do not calculate or validate formula results.",
        "blank_policy": "A null cell means no stored value/formula at the requested address; merged-cell continuation is not an independent value.",
        "summary": {"workbooks": len(files), "unique_file_sha256": len({item['sha256'] for item in inventory}),
                    "worksheets": sum(item["sheet_count"] for item in inventory),
                    "structural_signatures": dict(sorted(counts.items())),
                    "targeted_examples": len(examples), "targeted_workbooks": len({example['file'] for example in examples})},
        "workbooks": inventory, "examples": sorted(examples, key=lambda item: item["case_id"]),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
