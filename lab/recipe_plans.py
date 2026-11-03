"""Stable recipe input documents and the legacy relational projections."""
from copy import deepcopy
from decimal import Decimal

from .models import (
    FillingPlanLine, RecipeComponent, RecipeGroup, RecipeProperty, SourceCell,
)
from .recipe_editor_forms import value_cell


def new_plan(batch='1000'):
    return {
        'schema_version': 1,
        'settings': {
            'batch_mass_g': value_cell(batch), 'polyol_parts': value_cell('100'),
            'isocyanate_parts': value_cell(), 'iso_index': value_cell(),
            'iso_ratio_mode': 'parts', 'gas_mass_pct': value_cell(),
            'fill_volume_ml': value_cell(), 'isocyanate_present': True,
            'isocyanate_absence_reason': '',
        },
        'polyol': [], 'isocyanate': {}, 'gases': [],
    }


def _property_values(owner):
    values = {}
    codes = {'source_price_per_kg': 'price_per_kg', 'isocyanate_nco_pct': 'nco_pct'}
    for prop in owner.properties.all():
        name = codes.get(prop.property_code, prop.property_code)
        values[name] = value_cell(prop.value)
        if prop.source_cell_id:
            values[name]['source_cell'] = prop.source_cell.address
    return values


def legacy_row(row):
    material, lot = row.material if hasattr(row, 'material') else (
        row.specified_lot.material if row.specified_lot_id else row.unbatched_material
    ), row.specified_lot
    values = {
        'id': 'legacy-' + str(row.pk), 'material_id': getattr(material, 'pk', None),
        'lot_id': getattr(lot, 'pk', None), 'name': row.display_name,
        'material_reason': '' if material else 'Не указан в прежней записи',
        'lot_reason': '' if lot else 'Партия не указана в прежней записи',
        'price_currency': lot.price_currency if lot else 'EUR',
    }
    for name in ('density_g_ml', 'price_per_kg'):
        value = getattr(lot, name, None)
        values[name] = value_cell(value, '' if value is not None else 'Не указано в прежней записи')
    values.update(_property_values(row))
    values['quantity'] = value_cell(getattr(row, 'quantity_value', None))
    return values


def recipe_plan(recipe=None):
    if recipe and recipe.formulation:
        return deepcopy(recipe.formulation)
    plan = new_plan(str(recipe.batch_mass_g) if recipe else '1000')
    if recipe:
        plan['polyol'] = [legacy_row(row) for group in recipe.groups.all()
                          for row in group.components.select_related(
                              'specified_lot__material', 'unbatched_material')]
    return plan


def decimal_value(cell):
    value = cell.get('value') if isinstance(cell, dict) else cell
    return Decimal(value) if value is not None else None


def row_binding(row):
    if row.get('lot_id'):
        return {'specified_lot_id': row['lot_id']}
    return {'unbatched_material_id': row.get('material_id')}


def _source_cell(recipe, cell):
    address = cell.get('source_cell') if isinstance(cell, dict) else cell
    if not address or not recipe.source_sheet_id:
        return None
    sheet = cell.get('source_sheet') if isinstance(cell, dict) else None
    return SourceCell.objects.filter(
        sheet__file_id=recipe.source_sheet.file_id,
        sheet__name=sheet or recipe.source_sheet.name, address=address,
    ).first()


def _project_properties(recipe, owner, field, row):
    codes = {'price_per_kg': ('source_price_per_kg', row.get('price_currency', 'EUR') + '/kg'),
             'density_g_ml': ('density_g_ml', 'g/ml'),
             'ohv': ('ohv', 'mg KOH/g'), 'nco_pct': ('isocyanate_nco_pct', '%')}
    for name, (code, unit) in codes.items():
        value = decimal_value(row.get(name, {}))
        if value is not None:
            cell = row[name]
            RecipeProperty.objects.create(
                **{field: owner}, property_code=code, value=value, unit_code=unit,
                source_cell=_source_cell(recipe, cell),
                origin=cell.get('origin') if cell.get('origin') == 'carried_forward'
                else 'recorded', provenance_note=cell.get('provenance_note', ''),
            )


def replace_recipe_rows(recipe, result):
    from django.db.models import Q
    RecipeProperty.objects.filter(
        Q(recipe_version=recipe) | Q(group__recipe_version=recipe) |
        Q(component__group__recipe_version=recipe) |
        Q(filling_plan_line__recipe_version=recipe),
    ).delete()
    recipe.filling_plan.all().delete()
    RecipeComponent.objects.filter(group__recipe_version=recipe).delete()
    recipe.groups.all().delete()
    group = RecipeGroup.objects.create(
        recipe_version=recipe, label='Смесь A', quantity_basis='mass_percent',
        completeness='complete' if _complete(recipe.formulation) else 'partial',
    )
    _project_polyol(recipe, group)
    _project_filling(recipe, group, result)


def _complete(plan):
    quantities = [decimal_value(row.get('quantity', {})) for row in plan['polyol']]
    return bool(quantities) and all(value is not None for value in quantities) and (
        abs(sum(quantities) - 100) <= Decimal('0.001'))


def _project_polyol(recipe, group):
    for position, row in enumerate(recipe.formulation['polyol'], 1):
        component = RecipeComponent.objects.create(
            group=group, position=position, **row_binding(row),
            quantity_value=decimal_value(row.get('quantity', {})),
            name_cell=_source_cell(recipe, row.get('name_cell')),
            quantity_cell=_source_cell(recipe, row.get('quantity', {})),
        )
        _project_properties(recipe, component, 'component', row)


def _fill_line(recipe, result_row, row=None, group=None):
    mass = decimal_value(result_row.get('mass_g', {}))
    line = FillingPlanLine.objects.create(
        recipe_version=recipe, input_group=group,
        **(row_binding(row) if row is not None else {}), mass_g=mass,
        mass_status='calculation_checked' if mass is not None else 'unknown',
    )
    if row:
        _project_properties(recipe, line, 'filling_plan_line', row)


def _project_filling(recipe, group, result):
    filling = result.get('filling', {})
    _fill_line(recipe, filling.get('polyol', {}), group=group)
    plan = recipe.formulation
    if plan['settings'].get('isocyanate_present'):
        _fill_line(recipe, filling.get('isocyanate', {}), plan['isocyanate'])
    gas_results = filling.get('gases', [])
    for index, row in enumerate(plan['gases']):
        _fill_line(recipe, gas_results[index] if index < len(gas_results) else {}, row)
