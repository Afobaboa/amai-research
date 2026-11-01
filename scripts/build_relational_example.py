#!/usr/bin/env python3
"""Build illustrative relational rows directly from selected original XLSX.

Writes only the approved 20-table pilot core under LIMS/examples/relational.
Does not connect to a database, read Parquet, or create physical samples from
archival card labels. The optional process extension is not generated or run.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET
from zipfile import ZipFile

sys.dont_write_bytecode = True
from inspect_excel_sources import NS, cells, digest, shared_strings, sheets
from openpyxl.styles.numbers import BUILTIN_FORMATS
from openpyxl.utils.datetime import CALENDAR_MAC_1904, CALENDAR_WINDOWS_1900, from_excel


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "LIMS/examples/relational"
FILES = {
    1: "Пена/pena/Разработка рецептур/316 ЗИМНЯЯ УЛУЧШЕННАЯ.xlsx",
    2: "Пена/pena/Разработка рецептур/STP рецептура.xlsx",
    3: "Пена/pena/Испытания разработки рецептур/316.xlsx",
    4: "Пена/pena/Испытания разработки рецептур/302.xlsx",
    5: "Пена/pena/Испытания разработки рецептур/Проверка нового сырья/HJC.xlsx",
}
SHEETS = {
    101: (1, "316.8"), 102: (1, "316.26"),
    103: (2, "40"), 104: (2, "43"),
    201: (3, "316.8"), 202: (3, "316.8 (2)"),
    203: (3, "316.17 стар.пол."), 204: (4, "302.19(ст.бал)"),
    205: (5, "830 ст.пол+ст.бал"),
}
ORDER = [
    "source_file", "source_sheet", "source_cell", "mixture_type",
    "recipe_version", "recipe_group", "material", "material_lot",
    "recipe_component", "filling_plan_line", "recipe_property", "test_session",
    "recipe_match", "test_run", "metric", "session_condition", "result",
    "result_dependency", "observation", "conditioning_event",
]


def address_order(address: str) -> tuple[int, str]:
    column, row = re.fullmatch(r"([A-Z]+)(\d+)", address).groups()
    return int(row), column


def sql_literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (int, Decimal)):
        return str(value)
    if isinstance(value, date):
        return "DATE '" + value.isoformat() + "'"
    return "'" + str(value).replace("'", "''") + "'"


def main() -> None:
    data = {table: [] for table in ORDER}
    raw = {}
    formats = {}
    epochs = {}
    for file_id, relative in FILES.items():
        path = ROOT / relative
        before = digest(path)
        with ZipFile(path) as z:
            strings = shared_strings(z)
            epoch, entries = sheets(z)
            epochs[file_id] = epoch
            styles = ET.fromstring(z.read("xl/styles.xml"))
            custom = {int(n.get("numFmtId")): n.get("formatCode")
                      for n in styles.findall("m:numFmts/m:numFmt", NS)}
            num_formats = [custom.get(int(n.get("numFmtId", "0")),
                                      BUILTIN_FORMATS.get(int(n.get("numFmtId", "0")), "unknown"))
                           for n in styles.findall("m:cellXfs/m:xf", NS)]
            members = dict(entries)
            for sheet_id, (owner, name) in SHEETS.items():
                if owner != file_id:
                    continue
                raw[sheet_id] = cells(ET.fromstring(z.read(members[name])), strings)
                formats[sheet_id] = num_formats
                data["source_sheet"].append(dict(id=sheet_id, file_id=file_id, name=name))
        if digest(path) != before:
            raise RuntimeError(f"Source changed during read: {relative}")
        data["source_file"].append(dict(id=file_id, original_path=relative,
                                        sha256=before, date_system=epoch))

    cell_ids = {}
    def source(sheet_id: int, address: str) -> int:
        key = sheet_id, address
        if key not in cell_ids:
            c = raw[sheet_id].get(address)
            idx = len(cell_ids) + 1
            cell_ids[key] = idx
            formula = None if c is None or c["formula_attributes"] is None else c["formula_raw"] or ""
            style = int(c["style_index"] or 0) if c else 0
            data["source_cell"].append(dict(
                id=idx, sheet_id=sheet_id, address=address,
                cell_kind=c["xml_type"] if c else "blank",
                raw_value=c["value_raw"] if c else None,
                formula_text=formula,
                cached_value=c["cached_value_unvalidated"] if c else None,
                number_format=formats[sheet_id][style] if formats[sheet_id] else None,
            ))
        return cell_ids[key]

    for sheet_id in SHEETS:
        for address in sorted(raw[sheet_id], key=address_order):
            source(sheet_id, address)

    def value(sheet_id: int, address: str):
        c = raw[sheet_id].get(address)
        return c["value_raw"] if c else None

    def number(sheet_id: int, address: str) -> Decimal:
        c = raw[sheet_id].get(address)
        if c is None or c["formula_attributes"] is not None or c["xml_type"] != "n":
            raise ValueError(f"Not a recorded numeric value: {sheet_id}!{address}")
        result = Decimal(c["value_raw"])
        if not result.is_finite():
            raise ValueError(f"Non-finite value: {sheet_id}!{address}")
        return result

    def recorded_date(sheet_id: int, address: str) -> date:
        epoch = epochs[SHEETS[sheet_id][0]]
        origin = CALENDAR_MAC_1904 if epoch == "1904" else CALENDAR_WINDOWS_1900
        return from_excel(float(number(sheet_id, address)), origin).date()

    def add(table: str, **fields) -> int:
        idx = len(data[table]) + 1
        data[table].append(dict(id=idx, **fields))
        return idx

    data["mixture_type"] = [
        dict(id=1, code="316", title="Зимняя пена — рабочая трактовка типа смеси"),
        dict(id=2, code="STP", title="STP — название семейства книги"),
    ]
    materials, lots = {}, {}
    def material(name: str) -> int:
        if name not in materials:
            materials[name] = add("material", name=name)
        return materials[name]

    def material_reference(label: str) -> dict:
        # Only literal separation of an explicit lot suffix. No synonym matching,
        # manufacturer inference, transliteration, or proof of actual consumption.
        match = re.fullmatch(r"(.+?)\s+[пП]\.\s*(\S+)", label)
        if match:
            name, lot_code = match.groups()
            mid = material(name)
            key = mid, lot_code
            if key not in lots:
                lots[key] = add("material_lot", material_id=mid, lot_code=lot_code)
            return dict(unbatched_material_id=None, specified_lot_id=lots[key])
        return dict(unbatched_material_id=material(label), specified_lot_id=None)

    def prop(owner: str, owner_id: int, code: str, val: Decimal,
             unit: str | None, sheet_id: int, address: str):
        fields = dict(recipe_version_id=None, group_id=None, component_id=None,
                      filling_plan_line_id=None)
        fields[owner] = owner_id
        add("recipe_property", **fields, property_code=code, value=val,
            unit_code=unit, source_cell_id=source(sheet_id, address))

    version_sheet = {}
    for sheet_id, label in [(101, "8"), (102, "26"), (103, "40"), (104, "40")]:
        standard = sheet_id in (101, 102)
        revision = add("recipe_version", mixture_type_id=1 if standard else 2,
                       version_label=label, recorded_on=recorded_date(sheet_id, "A1"),
                       source_sheet_id=sheet_id)
        version_sheet[revision] = sheet_id
        group = add("recipe_group", recipe_version_id=revision,
                    label="Recipe for polyol", quantity_basis="mass_percent",
                    completeness="unknown")
        for row in range(4, 21 if standard else 18):
            name_address, dose_address = f"A{row}", f"{'D' if standard else 'B'}{row}"
            if value(sheet_id, dose_address) is None:
                continue
            label_raw = value(sheet_id, name_address)
            if not isinstance(label_raw, str):
                raise ValueError(f"Missing material label: {sheet_id}!{name_address}")
            component = add("recipe_component", group_id=group, position=row,
                            **material_reference(label_raw), name_cell_id=source(sheet_id, name_address),
                            quantity_value=number(sheet_id, dose_address),
                            quantity_cell_id=source(sheet_id, dose_address))
            if standard and value(sheet_id, f"B{row}") is not None:
                prop("component_id", component, "ohv", number(sheet_id, f"B{row}"),
                     "mg_KOH/g", sheet_id, f"B{row}")
        scale = "G3" if standard else "C3"
        # The scale is recorded, but no independently proven unit is assigned.
        prop("group_id", group, "preparation_scale", number(sheet_id, scale), None, sheet_id, scale)
        gas_pct, volume = ("B31", "G32") if standard else ("B28", "G29")
        prop("recipe_version_id", revision, "gas_mass_pct", number(sheet_id, gas_pct),
             "mass_percent", sheet_id, gas_pct)
        prop("recipe_version_id", revision, "target_fill_volume", number(sheet_id, volume),
             "mL", sheet_id, volume)
        if standard:
            prop("recipe_version_id", revision, "nominal_can_volume", number(sheet_id, "F49"),
                 "mL", sheet_id, "F49")
        fill_rows = [33, 34, 36, 37, 38] if standard else [30, 33, 34, 35]
        for row in fill_rows:
            label_address = f"E{row}"
            refs = dict(unbatched_material_id=None, specified_lot_id=None)
            group_ref = group if row == (33 if standard else 30) else None
            if standard and row == 34:
                refs = material_reference(value(sheet_id, "F2"))
            elif group_ref is None and not (not standard and row == 34):
                refs = material_reference(value(sheet_id, label_address))
            line = add("filling_plan_line", recipe_version_id=revision, input_group_id=group_ref,
                       **refs, label_cell_id=source(sheet_id, label_address), mass_g=None,
                       mass_cell_id=source(sheet_id, f"F{row}"), mass_status="calculation_unchecked")
            if standard and row in (33, 34):
                address = "C23" if row == 33 else "C24"
                prop("filling_plan_line_id", line, "reference_ab_mass",
                     number(sheet_id, address), "g", sheet_id, address)
            if standard and row == 34:
                prop("filling_plan_line_id", line, "nco", number(sheet_id, "I2"),
                     "mass_percent", sheet_id, "I2")
            if standard and row in (36, 37, 38):
                address = {36: "G23", 37: "G24", 38: "G25"}[row]
                prop("filling_plan_line_id", line, "reference_gas_mass_pct",
                     number(sheet_id, address), "mass_percent", sheet_id, address)
            elif not standard and row in (33, 34, 35):
                address = {33: "G20", 34: "G21", 35: "G22"}[row]
                prop("filling_plan_line_id", line, "reference_gas_mass_pct",
                     number(sheet_id, address), "mass_percent", sheet_id, address)

    metric_specs = [
        ("ambient_temperature", "Температура окружающей среды", "degC", "numeric"),
        ("relative_humidity", "Относительная влажность", "percent", "numeric"),
        ("tack_free", "Время отлипа", "min", "numeric"),
        ("cut_time", "Время резки", "min", "numeric"),
        ("can_mass_before_release", "Масса до выпуска", "g", "numeric"),
        ("can_mass_after_release", "Масса после выпуска", "g", "numeric"),
        ("released_mass", "Масса выпуска", "g", "numeric"),
        ("shrinkage", "Усадка", "percent", "numeric"),
        ("density", "Плотность ядра", "g/dm3", "numeric"),
        ("calculated_yield", "Расчётный выход", "L", "numeric"),
        ("shakeability", "Встряхиваемость по исходной шкале", None, "ordinal"),
    ]
    metric_ids = {}
    for code, label, unit, kind in metric_specs:
        metric_ids[code] = add("metric", code=code, label=label, unit_code=unit, value_kind=kind)

    def result(run: int, code: str, sheet_id: int, address: str, *, numeric=None,
               text=None, origin="recorded", state="present", calc=None) -> int:
        return add("result", test_run_id=run, metric_id=metric_ids[code],
                   numeric_value=numeric, text_value=text, origin=origin,
                   value_state=state, source_cell_id=source(sheet_id, address), calculation_code=calc)

    for sheet_id in [201, 202, 203, 204, 205]:
        identifier = "B3" if value(sheet_id, "B3") is not None else "B4"
        session = add("test_session", source_sheet_id=sheet_id,
                      identifier_cell_id=source(sheet_id, identifier),
                      recorded_manufactured_on=recorded_date(sheet_id, "C2"),
                      recorded_tested_on=recorded_date(sheet_id, "F2"))
        for code, address in [("ambient_temperature", "B9"), ("relative_humidity", "B10")]:
            if value(sheet_id, address) is not None:
                add("session_condition", session_id=session, metric_id=metric_ids[code],
                    numeric_value=number(sheet_id, address), text_value=None,
                    source_cell_id=source(sheet_id, address))
        paper_header = next((a for a, c in raw[sheet_id].items()
                             if c["value_raw"] and "ТЕСТ НА БУМАГЕ" in c["value_raw"]), None)
        paper = add("test_run", session_id=session,
                    scope="paper", method_code=None,
                    scope_cell_id=source(sheet_id, paper_header) if paper_header else None)
        for code, address in [("tack_free", "B14"), ("cut_time", "B15")]:
            if value(sheet_id, address) is not None:
                result(paper, code, sheet_id, address, numeric=number(sheet_id, address))
        if sheet_id in (201, 202):
            data["recipe_match"].append(dict(
                session_id=session, recipe_version_id=1, status="candidate",
                reason=("Совпадает обозначение 316.8; дата карточки раньше даты найденного листа плана."
                        if sheet_id == 201 else "Совпадает обозначение 316.8; фактический состав не установлен.")))
            checks = add("test_run", session_id=session,
                         scope="can_checks", method_code=None, scope_cell_id=source(sheet_id, "A6"))
            before = result(checks, "can_mass_before_release", sheet_id, "C6", numeric=number(sheet_id, "C6"))
            after = result(checks, "can_mass_after_release", sheet_id, "E6", numeric=number(sheet_id, "E6"))
            if raw[sheet_id]["G6"]["formula_raw"] != "C6-E6":
                raise ValueError("Unexpected mass subtraction formula")
            released = result(checks, "released_mass", sheet_id, "G6",
                              numeric=number(sheet_id, "C6") - number(sheet_id, "E6"),
                              origin="calculated", calc="subtract_two_g_values_v1")
            data["result_dependency"].extend([
                dict(result_id=released, input_result_id=before, role="before"),
                dict(result_id=released, input_result_id=after, role="after"),
            ])
            # No inferred length units or formula-cache promotion for these.
            for code, address in [("shrinkage", "F16"), ("density", "B25"), ("calculated_yield", "B27")]:
                result(paper, code, sheet_id, address, origin="calculated", state="formula_unchecked")
            for sequence, row in enumerate(range(18, 23), start=1):
                text = value(sheet_id, f"B{row}")
                if text is not None:
                    add("observation", test_run_id=paper, sequence_no=sequence,
                        text_value=text, source_cell_id=source(sheet_id, f"B{row}"))
        elif sheet_id == 203:
            # Explicitly preserve the blank operand as evidence too.
            source(sheet_id, "B16")
            result(paper, "shrinkage", sheet_id, "F16", origin="calculated", state="missing_inputs")
        elif sheet_id == 205:
            checks = add("test_run", session_id=session,
                         scope="can_checks", method_code=None, scope_cell_id=source(sheet_id, "A11"))
            result(checks, "shakeability", sheet_id, "B11", text=value(sheet_id, "B11"))
        if sheet_id in (204, 205):
            for step, target in [(1, "blend"), (2, "can")]:
                add("conditioning_event", session_id=session,
                    target_kind=target, step_no=step, kind="aging",
                    # B4 includes '1 мес', but its allocation between the two
                    # aging stages has not been confirmed by the laboratory.
                    duration_value=None, duration_unit=None,
                    temperature_c=None, source_cell_id=source(sheet_id, "B4"))

    # Source assertions independently check the examples most vulnerable to
    # accidental invention or promotion of a formula cache into a result.
    assert number(201, "B14") == 5 and number(202, "B14") == 6
    assert recorded_date(201, "F2") < recorded_date(101, "A1")
    assert value(103, "D1") == value(104, "D1") == "STP 40"
    assert number(103, "B28") == 15 and number(104, "B28") == 16
    assert value(203, "B16") is None and raw[203]["F16"]["cached_value_unvalidated"] == "100"
    assert value(205, "B11") == "5-"
    assert "1 мес к.к." in value(204, "B4")
    assert len(data) == 20
    assert not {"experiment", "preparation_run", "sample", "preparation_input"} & data.keys()
    for table in ["recipe_component", "result", "session_condition"]:
        assert data[table]
    for version in data["recipe_version"]:
        group_ids = {g["id"] for g in data["recipe_group"] if g["recipe_version_id"] == version["id"]}
        total = sum(c["quantity_value"] for c in data["recipe_component"] if c["group_id"] in group_ids)
        # XLSX stores binary-float artifacts such as 1.1000000000000001.
        # Preserve them; use a check tolerance, never normalize the doses.
        assert abs(total - Decimal(100)) <= Decimal("1e-10"), (version["id"], total)
    for row in data["filling_plan_line"]:
        assert row["mass_g"] is None
    for row in data["result"]:
        if row["value_state"] in {"formula_unchecked", "missing_inputs"}:
            assert row["numeric_value"] is None
    for file in data["source_file"]:
        assert digest(ROOT / file["original_path"]) == file["sha256"]

    sql = ["-- Generated from original XLSX by LIMS/scripts/build_relational_example.py.",
           "-- Illustrative rows only; no database connection was made.",
           "-- Run after schema.sql in an empty demonstration database.",
           "BEGIN;", "SET LOCAL standard_conforming_strings = on;", ""]
    for table in ORDER:
        rows = data[table]
        if not rows:
            sql.append(f"-- {table}: no established archival facts; deliberately zero rows.")
            continue
        columns = list(rows[0])
        for row in rows:
            assert list(row) == columns, (table, row)
        sql.append(f"INSERT INTO lims_example.{table} ({', '.join(columns)}) VALUES")
        sql.append(",\n".join("(" + ", ".join(sql_literal(row[c]) for c in columns) + ")" for row in rows) + ";")
        sql.append("")
    sql.append("COMMIT;\n")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "data.sql").write_text("\n".join(sql), encoding="utf-8")
    snapshot = {
        "source_truth": "Original XLSX only. No Parquet or previous report was read.",
        "decimal_encoding": "Decimal numeric values are JSON strings to preserve precision; SQL uses numeric literals.",
        "normalization_policy": "Only explicit п./П. lot suffixes are separated lexically. Catalog entries are an example mapping of names, not chemical equivalence, supplier confirmation, or actual consumption.",
        "scope_policy": "can_checks is an analytical grouping of card fields, not an invented test method. All method_code are NULL. Physical-object and manufacturing links are outside the 20-table pilot core.",
        "calculation_policy": "Only C6-E6 is independently recalculated from recorded g values. Other formula caches remain source evidence, not accepted results or plan masses.",
        "aging_policy": "The raw 302.19 label contains 1 мес к.к.; its allocation to a specific aging stage is not confirmed. Both event durations remain NULL.",
        "row_counts": {table: len(rows) for table, rows in data.items()},
        "rows": data,
    }
    (OUT / "rows.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    def md_cell(value):
        if value is None:
            return "NULL"
        return str(value).replace("|", "\\|").replace("\n", "<br>")
    display_sources = {(101, "A4"), (101, "D4"), (101, "B4"), (101, "F33"),
                       (103, "D1"), (104, "D1"), (201, "B14"), (202, "B14"),
                       (201, "G6"), (203, "B16"), (203, "F16")}
    display_ids = {cell_ids[key] for key in display_sources}
    lines = ["# Конкретные строки демонстрационной БД", "",
             "Сгенерировано из первичных Excel. ID назначены только для этого примера; это не лабораторные номера.", "",
             "Все числа приведены без скрытой перенормировки и округления. Длинные десятичные хвосты — запись XLSX.", "",
             "Для source_cell показана выборка; все ячейки находятся в rows.json и data.sql. Здесь только 20 таблиц ядра пилота; физический процесс вынесен в optional-process-extension.sql и в эти строки не входит.", ""]
    for table in ORDER:
        rows = data[table]
        if table == "source_cell":
            rows = [row for row in rows if row["id"] in display_ids]
        lines.extend([f"## {table}", ""])
        if not rows:
            lines.extend(["0 строк: первичные Excel не установили соответствующие факты.", ""])
            continue
        columns = list(rows[0])
        lines.append("| " + " | ".join(columns) + " |")
        lines.append("| " + " | ".join("---" for _ in columns) + " |")
        lines.extend("| " + " | ".join(md_cell(row[col]) for col in columns) + " |" for row in rows)
        lines.append("")
    (OUT / "tables.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(snapshot["row_counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
