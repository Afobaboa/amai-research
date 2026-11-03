"""Extract the editable inputs of the winter Excel formulation worksheets."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re


MISSING = "Значение не указано в исходном Excel."
NO_LOT = "Партия не указана в исходном Excel."


def decimal_text(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not number.is_finite():
        return None
    number = number.quantize(Decimal('0.00000001'), rounding=ROUND_HALF_UP)
    return format(number, 'f').rstrip('0').rstrip('.') if number else '0'


def numeric_cell(cells, address):
    source = cells.get(address, {})
    raw = source.get("raw_value")
    formula = source.get("formula_text")
    value = decimal_text(source.get("cached_value") if formula else raw)
    result = {"value": value, "reason": "" if value is not None else MISSING,
              "source_cell": address}
    if formula:
        result.update({"source_formula": formula, "origin": "source_formula"})
    return result


def not_applicable(address=None):
    return {"value": None, "reason": "Не применяется к этой строке.",
            "source_cell": address, "applicable": False}


def property_cell(row, code, address):
    prop = next((p for p in row.get("properties", []) if p["code"] == code), None)
    if prop is None:
        return {"value": None, "reason": MISSING, "source_cell": address}
    result = {"value": decimal_text(prop["value"]), "reason": "",
              "source_cell": prop.get("cell", address)}
    for key in ("origin", "source_sheet", "source_path", "provenance_note"):
        if prop.get(key):
            result[key] = prop[key]
    return result


def round_digits(cells, address):
    formula = cells.get(address, {}).get("formula_text") or ""
    match = re.search(r"ROUND\(.+,\s*(-?\d+)\s*\)$", formula, re.I)
    return int(match[1]) if match else None


def material_row(row, identity, position):
    name = row.get("name") or row.get("material_name")
    reason = "" if name else "Материал не указан в исходном Excel."
    if row.get("invalid_material_label"):
        reason = "Вместо марки материала в Excel записано числовое значение."
    if row.get("gas_label_conflict"):
        reason = ("Противоречие в Excel: строка заправки подписана F134a, "
                  "а блок плотностей — F152a. Марка не установлена.")
    return {"id": identity, "material_id": None, "lot_id": None,
            "name": name or row.get("name_raw") or "Не установлен",
            "material_reason": reason, "lot_reason": "" if row.get(
                "lot_code") else NO_LOT,
            "lot_code": row.get("lot_code"), "position": position,
            "name_cell": row.get("name_cell"), "price_currency": "EUR"}


def polyol_row(row, cells, position):
    number = int(row["quantity_cell"][1:])
    result = material_row(row, "polyol-" + row["name_cell"], position)
    result.update({
        "quantity": numeric_cell(cells, row["quantity_cell"]),
        "ohv": property_cell(row, "ohv", f"B{number}"),
        "density_g_ml": property_cell(row, "density_g_ml", f"H{number}"),
        "price_per_kg": property_cell(row, "source_price_per_kg", f"C{number}"),
        "nco_pct": not_applicable(), "pressure_bar": not_applicable(),
        "functionality": functionality_cell(cells, f"K{number}"),
        "mass_round_digits": round_digits(cells, f"G{number}"),
        "ohv_round_digits": round_digits(cells, f"E{number}"),
    })
    if result["ohv"]["value"] is None and not cells.get(
        f"I{number}", {},
    ).get("formula_text"):
        result["ohv"] = not_applicable(f"B{number}")
    add_manual_weighted_price(result, cells, number)
    return result


def price_anchor(row):
    return {'id': row['id'], 'name': row['name'],
            'quantity': row['quantity']['value'],
            'price_per_kg': row['price_per_kg']['value']}


def add_manual_weighted_price(row, cells, number):
    address = f'F{number}'
    value = decimal_text(cells.get(address, {}).get('raw_value'))
    quantity, price = row['quantity']['value'], row['price_per_kg']['value']
    if value is None or quantity is None or price is None:
        return
    expected = Decimal(quantity) * Decimal(price)
    if abs(Decimal(value) - expected) <= Decimal('0.00000001'):
        return
    row['manual_weighted_price'] = numeric_cell(cells, address)
    row['manual_weighted_price']['provenance_note'] = (
        'В Excel вклад стоимости задан вручную и отличается от цены × Recipe.'
    )
    row['manual_price_anchors'] = price_anchor(row)


def functionality_cell(cells, address):
    source = cells.get(address, {})
    header = cells.get('K3', {}).get('raw_value') or ''
    if 'FUNCTIONALITY' not in header.upper():
        result = source_only_functionality(source, address)
        result['reason'] = 'В этом листе столбец K относится к стоимости.'
        result['source_role'] = 'cost_share'
        return result
    if source.get('raw_value') is not None:
        return numeric_cell(cells, address)
    result = source_only_functionality(source, address)
    if source.get('formula_text'):
        result['provenance_note'] = (
            'Итоговая формула в столбце K не является свойством компонента.'
        )
    return result


def source_only_functionality(source, address):
    result = not_applicable(address)
    if source.get('formula_text'):
        result['source_formula'] = source['formula_text']
        result['source_cached_value'] = decimal_text(source.get('cached_value'))
    if source.get('raw_value') is not None:
        result['source_raw_value'] = decimal_text(source['raw_value'])
    return result


def isocyanate_row(row, cells, end):
    result = material_row(row, "isocyanate", 1)
    result.update({
        "quantity": not_applicable(), "ohv": not_applicable(),
        "density_g_ml": property_cell(row, "density_g_ml", f"B{end + 3}"),
        "price_per_kg": property_cell(row, "source_price_per_kg", f"H{end + 13}"),
        "nco_pct": numeric_cell(cells, "I2"),
        "pressure_bar": not_applicable(), "functionality": not_applicable(),
        "mass_round_digits": 1, "ohv_round_digits": None,
    })
    return result


def pressure_cell(cells, address):
    formula = cells.get(address, {}).get("formula_text") or ""
    match = re.fullmatch(r"\$?G\$?\d+\s*\*\s*([\d.]+)\s*/\s*100", formula)
    if match:
        return {"value": match[1], "reason": "", "source_cell": address,
                "source_formula": formula, "origin": "source_formula_constant"}
    return {"value": None, "reason": "Давление не выделено из формулы Excel.",
            "source_cell": address}


def gas_row(row, cells, end, position):
    number = end + position + 1
    result = material_row(row, "gas-" + row["label_cell"], position)
    result.update({
        "quantity": numeric_cell(cells, f"G{number}"),
        "ohv": not_applicable(), "nco_pct": not_applicable(),
        "density_g_ml": numeric_cell(cells, f"F{number}"),
        "price_per_kg": property_cell(row, "source_price_per_kg",
                                      f"H{end + 14 + position}"),
        "pressure_bar": pressure_cell(cells, f"H{number}"),
        "functionality": not_applicable(), "mass_round_digits": 1,
        "ohv_round_digits": None,
    })
    if row.get("gas_label_conflict"):
        result["density_g_ml"]["provenance_note"] = result["material_reason"]
    return result


def formulation_settings(cells, end):
    iso_formula = cells.get(f"C{end + 3}", {}).get("formula_text")
    return {
        "batch_mass_g": numeric_cell(cells, "G3"),
        "polyol_parts": numeric_cell(cells, f"C{end + 2}"),
        "isocyanate_parts": numeric_cell(cells, f"C{end + 3}"),
        "iso_index": numeric_cell(cells, f"I{end + 3}"),
        "iso_ratio_mode": "index" if iso_formula else "parts",
        "gas_mass_pct": numeric_cell(cells, f"B{end + 10}"),
        "fill_volume_ml": numeric_cell(cells, f"G{end + 11}"),
        "isocyanate_present": True, "isocyanate_absence_reason": "",
        "ohv_extra_contributions": extra_ohv(cells, end),
    }


def extra_ohv(cells, end):
    contributions = []
    for row in range(4, end):
        source = cells.get(f'E{row}', {})
        value = decimal_text(source.get('raw_value'))
        quantity = cells.get(f'D{row}', {})
        if value is not None and Decimal(value) != 0 and not (
            quantity.get('raw_value') is not None or quantity.get('formula_text')
        ):
            cell = numeric_cell(cells, f'E{row}')
            cell['provenance_note'] = (
                'Ручной вклад OHV в строке без количества компонента. '
                'Сохранён отдельно для воспроизведения исходного итога Excel.'
            )
            contributions.append(cell)
    return contributions


def extract_formulation(sheet, recipe):
    cells = sheet["cells"]
    end = next(int(a[1:]) for a, cell in cells.items()
               if a.startswith("A") and cell.get("raw_value") == "Summe Polyol")
    polyol = recipe["groups"][0]["components"]
    filling = recipe["filling"]
    result = {
        "schema_version": 1, "settings": formulation_settings(cells, end),
        "polyol": [polyol_row(row, cells, position)
                   for position, row in enumerate(polyol, 1)],
        "isocyanate": isocyanate_row(filling[1], cells, end),
        "gases": [gas_row(row, cells, end, position)
                  for position, row in enumerate(filling[2:], 1)],
        "source_sheet": sheet["name"],
    }
    add_manual_polyol_price(result, cells, end)
    return result


def add_manual_polyol_price(plan, cells, end):
    address = f'H{end + 12}'
    value = decimal_text(cells.get(address, {}).get('raw_value'))
    if value is None:
        return
    price = numeric_cell(cells, address)
    price['provenance_note'] = 'Цена смеси A в исходном Excel задана вручную.'
    plan['settings']['manual_polyol_price_per_kg'] = price
    plan['settings']['manual_polyol_price_anchors'] = [
        price_anchor(row) for row in plan['polyol']
    ]


def add_formulations(files):
    """Run after reviewed property completions; leave primary cells unchanged."""
    for source in files:
        for sheet in source["sheets"]:
            recipe = sheet.get("recipe")
            if recipe:
                recipe["formulation"] = extract_formulation(sheet, recipe)
