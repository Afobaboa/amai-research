"""Typed plan calculations. No executable formulas and no laboratory predictions."""
from decimal import Decimal, InvalidOperation, localcontext

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import CalculationRecord, RecipeVersion, currency_symbol


OPERATIONS = {
    "mass": {"label": "Масса компонента", "formula": "Масса смеси × доля / 100", "unit": "г", "scope": "Компонент"},
    "cost": {"label": "Стоимость компонента", "formula": "Масса компонента / 1000 × цена за кг", "unit": "Валюта цены", "scope": "Компонент"},
    "volume": {"label": "Объем компонента", "formula": "Масса компонента / плотность", "unit": "мл", "scope": "Компонент"},
    "totalCost": {"label": "Стоимость смеси", "formula": "Сумма стоимостей компонентов", "unit": "Валюта цены", "scope": "Группа смеси"},
    "density": {"label": "Плотность смеси", "formula": "Масса смеси / сумма объемов компонентов", "unit": "г/мл", "scope": "Группа смеси"},
}
OPERATION_DEFINITIONS = OPERATIONS
PERCENT_TOLERANCE = Decimal("0.001")
DISCLAIMER = (
    "Плотность рассчитана аддитивно для смеси до реакции. Это оценка плана, "
    "а не измеренная плотность пены. Для архивных строк используются цены "
    "и плотности исходной рецептуры. Валюты не пересчитываются."
)


def _decimal(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return value if value.is_finite() else None


def _text(value):
    if value is None:
        return None
    text = format(value, "f")
    return (text.rstrip("0").rstrip(".") if "." in text else text) or "0"


def _material_inputs(component):
    material = component.material
    lot = component.specified_lot if component.specified_lot_id else None
    inputs = {
        "component_id": component.pk,
        "position": component.position,
        "material_id": material.pk if material else None,
        "name": component.display_name,
        "supplier": lot.supplier or material.supplier if lot else material.supplier if material else "",
        "lot_id": lot.pk if lot else None,
        "lot_code": lot.lot_code if lot else None,
        "lot_status": lot.status if lot else None,
        "quantity_value": _text(_decimal(component.quantity_value)),
    }
    inputs.update(_catalog_inputs(material, lot))
    if component.name_cell_id and component.group.recipe_version.source_sheet_id == (
        component.name_cell.sheet_id
    ):
        inputs.update(_historical_inputs(component))
    inputs['currency_symbol'] = currency_symbol(inputs['price_currency'] or '')
    return inputs


def _catalog_inputs(material, lot):
    density = lot.density_g_ml if lot else None
    price = lot.price_per_kg if lot else None
    currency = lot.price_currency if lot else None
    return {
        'density_g_ml': _text(_decimal(density)),
        'price_per_kg': _text(_decimal(price)), 'price_currency': currency,
        'density_origin': 'lot' if lot else None,
        'price_origin': 'lot' if lot else None,
    }


def _historical_inputs(component):
    props = {prop.property_code: prop for prop in component.properties.all()
             if _historical_property_allowed(prop, component.name_cell)}
    price = props.get('source_price_per_kg')
    density = props.get('density_g_ml')
    currencies = {'EUR/kg': 'EUR', 'RUB/kg': 'RUB', 'USD/kg': 'USD'}
    currency = currencies.get(price.unit_code) if price else None
    valid_density = density and density.unit_code in {'g/ml', 'г/мл'}
    return {
        'price_per_kg': _text(price.value) if currency else None,
        'price_currency': currency, 'price_origin': 'source',
        'price_source_cell_id': price.source_cell_id if price else None,
        'price_provenance_note': price.provenance_note if price else '',
        'price_source_origin': price.origin if price else None,
        'density_g_ml': _text(density.value) if valid_density else None,
        'density_origin': 'source',
        'density_source_cell_id': density.source_cell_id if density else None,
    }


def _historical_property_allowed(prop, name_cell):
    if not prop.source_cell_id:
        return False
    if prop.source_cell.sheet_id == name_cell.sheet_id:
        return True
    return (prop.origin == 'carried_forward' and bool(prop.provenance_note)
            and prop.source_cell.sheet.file_id == name_cell.sheet.file_id)


def _group_currency(rows, issues):
    currencies = {row['price_currency'] for row in rows
                  if _decimal(row['mass_g']) not in (None, Decimal(0))
                  and row['price_per_kg'] is not None}
    if len(currencies) > 1:
        issues.append('Цены указаны в разных валютах; итоговая стоимость '
                      'не рассчитана. Требуется единая валюта.')
        return None
    return next(iter(currencies), None)


def _quantity_state(group, inputs):
    quantities = [_decimal(row["quantity_value"]) for row in inputs]
    issues = []
    total = sum((q for q in quantities if q is not None), Decimal(0))
    if not inputs:
        issues.append("В группе нет компонентов.")
    if any(quantity is None or quantity < 0 for quantity in quantities):
        issues.append("Есть неизвестные или некорректные количества. "
                      "Пропуски не заменяются нулями.")
    if group.quantity_basis == "unknown":
        issues.append("База количества не установлена; расчет масс приостановлен.")
    elif group.quantity_basis == "mass_percent":
        if abs(total - Decimal(100)) > PERCENT_TOLERANCE:
            issues.append(f"Сумма массовых долей {_text(total)}%; для расчета "
                          "требуется 100% (допуск 0,001%).")
    elif group.quantity_basis == "weight_parts":
        if total <= 0:
            issues.append("Сумма массовых частей должна быть больше нуля.")
    else:
        issues.append("Неизвестная база количества.")
    if group.completeness == "partial":
        issues.append("Состав помечен частичным; полный расчет группы приостановлен.")
    return quantities, total, not issues, issues


def _calculate_row(source, mass):
    issues = []
    density = _decimal(source['density_g_ml'])
    price = _decimal(source['price_per_kg'])
    cost = volume = None
    if mass == 0:
        cost = volume = Decimal(0)
    elif mass is not None:
        if price is None or price < 0:
            issues.append('Цена не указана; стоимость неизвестна.')
        else:
            cost = mass / Decimal(1000) * price
        if density is None or density <= 0:
            issues.append('Плотность не указана; объем неизвестен.')
        else:
            volume = mass / density
    if source['lot_status'] in {'quarantine', 'exhausted'}:
        issues.append('Партия в карантине или закрыта; '
                      'проверьте допустимость применения.')
    return {**source, 'mass_g': _text(mass), 'cost': _text(cost),
            'volume_ml': _text(volume), 'issues': issues}


def _sum_rows(rows, field, valid):
    values = [_decimal(row[field]) for row in rows]
    if valid and all(value is not None for value in values):
        return sum(values, Decimal(0))
    return None


def _group_totals(rows, valid, issues):
    currency = _group_currency(rows, issues)
    cost = _sum_rows(rows, 'cost', valid and currency is not None)
    volume = _sum_rows(rows, 'volume_ml', valid)
    total_mass = _sum_rows(rows, 'mass_g', valid)
    density = total_mass / volume if volume is not None and volume > 0 else None
    unit_cost = (cost * Decimal(1000) / total_mass
                 if cost is not None and total_mass and total_mass > 0 else None)
    return {
        'rows': rows, 'mass_g': _text(total_mass), 'total_cost': _text(cost),
        'cost_per_kg': _text(unit_cost), 'volume_ml': _text(volume),
        'density_g_ml': _text(density), 'issues': issues, 'valid': valid,
        'currency': currency, 'currency_symbol': currency_symbol(currency or ''),
    }


def _calculate_group(group, batch):
    components = group.components.select_related(
        'unbatched_material', 'specified_lot__material', 'name_cell',
        'group__recipe_version',
    ).prefetch_related('properties__source_cell')
    inputs = [_material_inputs(row) for row in components]
    quantities, total, valid, issues = _quantity_state(group, inputs)
    denominator = Decimal(100) if group.quantity_basis == 'mass_percent' else total
    rows = [_calculate_row(source, batch * quantity / denominator if valid else None)
            for source, quantity in zip(inputs, quantities)]
    issues.extend(f"{row['name']}: {issue}" for row in rows for issue in row['issues'])
    metadata = {'id': group.pk, 'label': group.label,
                'quantity_basis': group.quantity_basis,
                'completeness': group.completeness}
    output = {**metadata, 'quantity_total': _text(total),
              **_group_totals(rows, valid, issues)}
    return output, {**metadata, 'components': inputs}


def _calculation_settings():
    """Keep the saved snapshot format while always enabling every operation."""
    return {
        'id': None, 'name': 'Расчёт состава', 'version': 1,
        'operations': list(OPERATIONS),
    }


def _filling_snapshot(recipe):
    rows = recipe.filling_plan.select_related(
        'input_group', 'unbatched_material', 'specified_lot__material',
    )
    return [{'id': row.pk, 'name': row.display_name,
             'mass_g': _text(_decimal(row.mass_g)), 'mass_status': row.mass_status,
             'lot_id': row.specified_lot_id,
             'material_id': row.specified_lot.material_id if row.specified_lot_id
             else row.unbatched_material_id, 'group_id': row.input_group_id}
            for row in rows]


def calculate_recipe(recipe, batch_mass_g=None):
    """Calculate each group independently, keeping filling plans separate."""
    batch = _decimal(recipe.batch_mass_g if batch_mass_g is None else batch_mass_g)
    if batch is None or batch <= 0:
        raise ValidationError("Масса смеси должна быть конечной и больше нуля.")
    settings = _calculation_settings()
    outputs, snapshots = [], []
    with localcontext() as context:
        context.prec = 32
        for group in recipe.groups.all():
            output, snapshot = _calculate_group(group, batch)
            outputs.append(output)
            snapshots.append(snapshot)
    issues = [f"Группа {group['label']}: {issue}"
              for group in outputs for issue in group['issues']]
    if not outputs:
        issues.append("В рецептуре нет групп состава.")
    inputs = {
        "recipe_id": recipe.pk, "version_label": recipe.version_label,
        "batch_mass_g": _text(batch), "template": settings,
        "groups": snapshots,
        "filling_plan": _filling_snapshot(recipe),
    }
    return {"batch_mass_g": _text(batch), "template": settings, "groups": outputs,
            "issues": issues, "disclaimer": DISCLAIMER, "inputs": inputs}


@transaction.atomic
def save_calculation(recipe, employee, batch_mass_g=None, *, purpose="manual"):
    """Persist an append-only snapshot. The calling UI records its audit event."""
    if purpose not in {"manual", "publication"}:
        raise ValidationError("Неизвестное назначение сохраненного расчета.")
    locked = RecipeVersion.objects.select_for_update().get(pk=recipe.pk)
    data = calculate_recipe(locked, batch_mass_g=batch_mass_g)
    data["purpose"] = purpose
    return CalculationRecord.objects.create(
        recipe_version=locked,
        inputs=data.pop("inputs"), outputs=data, created_by=employee,
    )
