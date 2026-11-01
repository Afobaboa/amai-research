"""Typed plan calculations. No executable formulas and no laboratory predictions."""
from decimal import Decimal, InvalidOperation, localcontext

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import CalculationRecord, RecipeVersion, default_calculation_operations


OPERATIONS = {
    "mass": {"label": "Масса компонента", "formula": "Масса смеси × доля / 100", "unit": "г", "scope": "Компонент"},
    "cost": {"label": "Стоимость компонента", "formula": "Масса компонента / 1000 × цена за кг", "unit": "₽", "scope": "Компонент"},
    "volume": {"label": "Объем компонента", "formula": "Масса компонента / плотность", "unit": "мл", "scope": "Компонент"},
    "totalCost": {"label": "Стоимость смеси", "formula": "Сумма стоимостей компонентов", "unit": "₽", "scope": "Группа смеси"},
    "density": {"label": "Плотность смеси", "formula": "Масса смеси / сумма объемов компонентов", "unit": "г/мл", "scope": "Группа смеси"},
}
OPERATION_DEFINITIONS = OPERATIONS
PERCENT_TOLERANCE = Decimal("0.001")
DISCLAIMER = "Плотность рассчитана аддитивно для смеси до реакции. Это оценка плана, а не измеренная плотность пены. Цены указаны в ₽/кг."


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
    density = _decimal(lot.effective_density_g_ml if lot else material.density_g_ml if material else None)
    price = _decimal(lot.effective_price_per_kg if lot else material.price_per_kg if material else None)
    return {
        "component_id": component.pk,
        "position": component.position,
        "material_id": material.pk if material else None,
        "name": component.display_name,
        "supplier": lot.supplier or material.supplier if lot else material.supplier if material else "",
        "lot_id": lot.pk if lot else None,
        "lot_code": lot.lot_code if lot else None,
        "lot_status": lot.status if lot else None,
        "quantity_value": _text(_decimal(component.quantity_value)),
        "density_g_ml": _text(density),
        "price_per_kg": _text(price),
        "density_origin": "lot" if lot and lot.density_g_ml is not None else "material" if material else None,
        "price_origin": "lot" if lot and lot.price_per_kg is not None else "material" if material else None,
    }


def _calculate_group(group, batch):
    inputs = [_material_inputs(row) for row in group.components.select_related("unbatched_material", "specified_lot__material").all()]
    quantities = [_decimal(row["quantity_value"]) for row in inputs]
    issues = []
    valid = True
    total = sum((quantity for quantity in quantities if quantity is not None), Decimal(0))
    if not inputs:
        issues.append("В группе нет компонентов.")
        valid = False
    if any(quantity is None or quantity < 0 for quantity in quantities):
        issues.append("Есть неизвестные или некорректные количества. Пропуски не заменяются нулями.")
        valid = False
    if group.quantity_basis == "unknown":
        issues.append("База количества не установлена; расчет масс приостановлен.")
        valid = False
    elif group.quantity_basis == "mass_percent":
        if abs(total - Decimal(100)) > PERCENT_TOLERANCE:
            issues.append(f"Сумма массовых долей {_text(total)}%; для расчета требуется 100% (допуск 0,001%).")
            valid = False
    elif group.quantity_basis == "weight_parts":
        if total <= 0:
            issues.append("Сумма массовых частей должна быть больше нуля.")
            valid = False
    else:
        issues.append("Неизвестная база количества.")
        valid = False
    if group.completeness == "partial":
        issues.append("Состав помечен частичным; полный расчет группы приостановлен.")
        valid = False
    denominator = Decimal(100) if group.quantity_basis == "mass_percent" else total
    rows = []
    for source, quantity in zip(inputs, quantities):
        row_issues = []
        mass = batch * quantity / denominator if valid else None
        density, price = _decimal(source["density_g_ml"]), _decimal(source["price_per_kg"])
        cost = volume = None
        if mass == 0:
            # A zero-mass component contributes exactly zero, even when a price
            # or density has not been supplied. Its missing data remain visible.
            cost = volume = Decimal(0)
        elif mass is not None:
            if price is None or price < 0:
                row_issues.append("Цена не указана; стоимость неизвестна.")
            else:
                cost = mass / Decimal(1000) * price
            if density is None or density <= 0:
                row_issues.append("Плотность не указана; объем неизвестен.")
            else:
                volume = mass / density
        if source["lot_status"] in {"quarantine", "exhausted"}:
            row_issues.append("Партия в карантине или закрыта; проверьте допустимость применения.")
        if row_issues:
            issues.extend(f"{source['name']}: {issue}" for issue in row_issues)
        rows.append({**source, "mass_g": _text(mass), "cost": _text(cost), "volume_ml": _text(volume), "issues": row_issues})
    all_costs = valid and all(row["cost"] is not None for row in rows)
    all_volumes = valid and all(row["volume_ml"] is not None for row in rows)
    cost = sum((_decimal(row["cost"]) for row in rows), Decimal(0)) if all_costs else None
    volume = sum((_decimal(row["volume_ml"]) for row in rows), Decimal(0)) if all_volumes else None
    # Use actual summed mass for a tolerated near-100% group; don't silently
    # normalize an archived percentage composition to force exactly 100%.
    total_mass = sum((_decimal(row["mass_g"]) for row in rows), Decimal(0)) if valid else None
    density = total_mass / volume if volume is not None and volume > 0 else None
    unit_cost = cost * Decimal(1000) / total_mass if cost is not None and total_mass and total_mass > 0 else None
    output = {
        "id": group.pk, "label": group.label, "quantity_basis": group.quantity_basis,
        "completeness": group.completeness, "quantity_total": _text(total),
        "rows": rows, "mass_g": _text(total_mass), "total_cost": _text(cost),
        "cost_per_kg": _text(unit_cost), "volume_ml": _text(volume),
        "density_g_ml": _text(density), "issues": issues, "valid": valid,
    }
    snapshot = {"id": group.pk, "label": group.label, "quantity_basis": group.quantity_basis,
                "completeness": group.completeness, "components": inputs}
    return output, snapshot


def calculate_recipe(recipe, template=None, batch_mass_g=None):
    """Return exact-decimal JSON data; calculations do not modify a recipe.

    Every group is calculated independently for the selected batch mass.
    Filling masses are captured as a separate plan and never mixed into the
    100% composition or presented as actual weighed quantities.
    """
    template = template or recipe.calculation_template
    if template is not None:
        template.clean()
    batch = _decimal(recipe.batch_mass_g if batch_mass_g is None else batch_mass_g)
    if batch is None or batch <= 0:
        raise ValidationError("Масса смеси должна быть конечной и больше нуля.")
    selected = list(template.operations) if template else default_calculation_operations()
    template_snapshot = {"id": template.pk if template else None,
                         "name": template.name if template else "Масса, объем и стоимость",
                         "version": template.version if template else 1,
                         "operations": selected}
    outputs, snapshots = [], []
    with localcontext() as context:
        context.prec = 32
        for group in recipe.groups.all():
            output, snapshot = _calculate_group(group, batch)
            outputs.append(output)
            snapshots.append(snapshot)
    issues = [f"Группа {group['label']}: {issue}" for group in outputs for issue in group["issues"]]
    if not outputs:
        issues.append("В рецептуре нет групп состава.")
    inputs = {
        "recipe_id": recipe.pk, "version_label": recipe.version_label,
        "batch_mass_g": _text(batch), "template": template_snapshot,
        "groups": snapshots,
        "filling_plan": [{"id": row.pk, "name": row.display_name, "mass_g": _text(_decimal(row.mass_g)),
                          "mass_status": row.mass_status, "lot_id": row.specified_lot_id,
                          "material_id": row.unbatched_material_id, "group_id": row.input_group_id}
                         for row in recipe.filling_plan.select_related("input_group", "unbatched_material", "specified_lot__material")],
    }
    return {"batch_mass_g": _text(batch), "template": template_snapshot, "groups": outputs,
            "issues": issues, "disclaimer": DISCLAIMER, "inputs": inputs}


@transaction.atomic
def save_calculation(recipe, employee, template=None, batch_mass_g=None, *, purpose="manual"):
    """Persist an append-only snapshot. The calling UI records its audit event."""
    if purpose not in {"manual", "publication"}:
        raise ValidationError("Неизвестное назначение сохраненного расчета.")
    locked = RecipeVersion.objects.select_for_update().get(pk=recipe.pk)
    data = calculate_recipe(locked, template=template, batch_mass_g=batch_mass_g)
    data["purpose"] = purpose
    return CalculationRecord.objects.create(
        recipe_version=locked, template=template or locked.calculation_template,
        inputs=data.pop("inputs"), outputs=data, created_by=employee,
    )
