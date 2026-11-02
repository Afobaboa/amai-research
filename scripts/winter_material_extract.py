"""Material/lot parsing and explicit reviewed corrections for winter Excel."""

from decimal import Decimal
import re


LOT_MARKER = re.compile(r"\s+\(?\s*п(?:\.|(?=\s*[A-ZА-Я0-9]))\s*", re.I)
CODE_LETTERS = str.maketrans({"С": "C", "Р": "P", "Е": "E"})
NAME_ALIASES = {
    "niax y-16371": "NIAX Y-16371",
    "wannate pm-200": "Wannate pm-200",
    "petol250-3": "Petol 250-3",
    "sr - 234 niax": "SR 234 NIAX",
    "voranol cp1050": "Voranol CP 1050",
}
UNMARKED_LOTS = {
    # 302.28!A5 explicitly labels the same grade and code as п.F359LAJL05.
    "Voranol CP 1050 F359LAJL05": ("Voranol CP 1050", "F359LAJL05"),
}
PRICE_COMPLETIONS = [{
    "source_path": "Пена/pena/Разработка рецептур/302 рецептура ЗИМНЯЯ.xlsx",
    "target_sheet": "302.84", "target_name_cell": "A11", "target_cell": "C11",
    "source_sheet": "302.83", "donor_name_cell": "A12", "cell": "C12",
    "material": "TEP-Z", "target_lot_code": "260409", "donor_lot_code": None,
    "value": "64.69",
}]


def clean_name(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def canonical_name(value):
    name = clean_name(value)
    # Only visually identical letters in a code are folded. Brand names,
    # suppliers, grades and lot numbers are not inferred or merged.
    name = re.sub(
        r"[A-ZА-Я]*\d+[A-ZА-Я0-9-]*",
        lambda match: match[0].translate(CODE_LETTERS), name,
    )
    if name == "DMDEЕ":
        name = "DMDEE"
    name = re.sub(r"\bWanol [CС]\s+(?=2010\b)", "Wanol C", name)
    name = re.sub(r"\bYD-\s+(?=C\d)", "YD-", name)
    return NAME_ALIASES.get(name.casefold(), name)


def material_identity(value):
    raw = str(value or "")
    normalized = clean_name(raw)
    marker = LOT_MARKER.search(normalized)
    if marker is None:
        if normalized in UNMARKED_LOTS:
            name, code = UNMARKED_LOTS[normalized]
            return {"name": name, "name_raw": raw, "lot_code": code,
                    "lot_notes": "Номер без п.; сверено с 302.28!A5."}
        return {"name": canonical_name(normalized), "name_raw": raw,
                "lot_code": None, "lot_notes": ""}
    name = canonical_name(normalized[:marker.start()].rstrip(" ("))
    suffix = normalized[marker.end():]
    code = re.match(r"[^\s()]+", suffix)
    if code is None:
        return {"name": name, "name_raw": raw, "lot_code": None,
                "lot_notes": "В исходнике указано п. без номера партии."}
    notes = suffix[code.end():].strip().strip("()").strip()
    return {"name": name, "name_raw": raw,
            "lot_code": code[0], "lot_notes": notes}


def apply_price_completions(files):
    """Fill only explicitly authorized cells; never change documentary cells."""
    completed = []
    for rule in PRICE_COMPLETIONS:
        source = next(file for file in files if file["path"] == rule["source_path"])
        sheets = {sheet["name"]: sheet for sheet in source["sheets"]}
        target, donor = sheets[rule["target_sheet"]], sheets[rule["source_sheet"]]
        order = [sheet["name"] for sheet in source["sheets"]]
        if order.index(target["name"]) != order.index(donor["name"]) + 1:
            raise ValueError("Price donor is no longer the previous worksheet")
        prop = completed_price_property(target, donor, rule)
        component = next(row for row in target["recipe"]["groups"][0]["components"]
                         if row["name_cell"] == rule["target_name_cell"])
        if any(p["code"] == "source_price_per_kg" for p in component["properties"]):
            raise ValueError(
                "Explicit price completion would replace an existing price")
        component["properties"].append(prop)
        target["recipe"]["notes"] += "\n\n" + prop["provenance_note"]
        completed.append({**rule, "origin": prop["origin"],
                          "provenance_note": prop["provenance_note"], "unit": "EUR/kg"})
    return completed


def completed_price_property(target, donor, rule):
    validate_price_completion(target, donor, rule)
    note = (
        "По указанию пользователя отсутствующая цена TEP-Z в 302.84!C11 "
        "дополнена значением 64,69 €/кг из предыдущего листа 302.83!C12 "
        "(название TEP-Z в A12). В листе-доноре партия не указана; "
        "совпадение партии 260409 не подтверждено. Исходная C11 остаётся пустой."
    )
    return {
        "code": "source_price_per_kg", "value": rule["value"], "unit": "EUR/kg",
        "cell": rule["cell"], "source_sheet": rule["source_sheet"],
        "source_path": rule["source_path"], "target_cell": rule["target_cell"],
        "origin": "carried_forward", "provenance_note": note,
        "inferred_from_previous_sheet": True,
        "donor_lot_code": rule["donor_lot_code"],
    }


def validate_price_completion(target, donor, rule):
    for sheet, name_cell, lot_key in (
            (target, rule["target_name_cell"], "target_lot_code"),
            (donor, rule["donor_name_cell"], "donor_lot_code")):
        identity = material_identity(sheet["cells"][name_cell]["raw_value"])
        if (identity["name"] != rule["material"]
                or identity["lot_code"] != rule[lot_key]):
            raise ValueError("Material or lot changed in explicit price completion")
    blank = target["cells"].get(rule["target_cell"], {})
    if blank.get("raw_value") is not None or blank.get("formula_text"):
        raise ValueError("Explicit price completion target is no longer empty")
    cell = donor["cells"][rule["cell"]]
    if cell.get("formula_text") or cell["raw_value"] is None:
        raise ValueError("Explicit price donor must have a recorded numeric price")
    if Decimal(cell["raw_value"]) != Decimal(rule["value"]):
        raise ValueError("Explicit price donor changed; review the correction")
