"""Expose the full input document through immutable calculation records."""
from copy import deepcopy
from decimal import Decimal

from django.core.exceptions import ValidationError

from .models import currency_symbol
from .recipe_math import calculate_formulation


def _value(owner, key):
    cell = owner.get(key, {})
    return cell.get('value') if isinstance(cell, dict) else cell


def _component(recipe, source, result):
    from .recipe_plans import _source_cell
    currency = source.get('price_currency')
    price = source.get('price_per_kg', {})
    density = source.get('density_g_ml', {})
    return {
        'name': source.get('name', ''), 'lot_id': source.get('lot_id'),
        'lot_code': source.get('lot_code'), 'material_id': source.get('material_id'),
        'quantity_value': _value(source, 'quantity'),
        'mass_g': _value(result, 'mass_g'), 'cost': _value(result, 'cost'),
        'volume_ml': _value(result, 'volume_ml'),
        'density_g_ml': _value(source, 'density_g_ml'),
        'price_per_kg': _value(source, 'price_per_kg'),
        'price_currency': currency, 'currency_symbol': currency_symbol(currency),
        'price_source_cell_id': getattr(_source_cell(recipe, price), 'pk', None),
        'density_source_cell_id': getattr(_source_cell(recipe, density), 'pk', None),
        'price_provenance_note': price.get('provenance_note', ''),
        'price_source_origin': price.get('origin', 'recorded'),
        'density_origin': 'source' if source.get('density_g_ml', {}).get(
            'source_cell') else 'snapshot',
        'price_origin': 'source' if source.get('price_per_kg', {}).get(
            'source_cell') else 'snapshot',
        'issues': [cell['reason'] for cell in result.values()
                   if isinstance(cell, dict) and cell.get('reason')],
    }


def _group(recipe, plan, result):
    polyol = result['polyol']
    currency = polyol.get('price_currency')
    return {
        'label': 'Смесь A', 'quantity_basis': 'mass_percent',
        'completeness': 'complete' if not polyol.get('issue') else 'partial',
        'quantity_total': _value(polyol, 'quantity_total'),
        'rows': [_component(recipe, row, output) for row, output in zip(
            plan['polyol'], polyol['rows'])],
        'mass_g': _value(polyol, 'mass_g'), 'total_cost': _value(polyol, 'cost'),
        'cost_per_kg': _value(polyol, 'price_per_kg'),
        'volume_ml': _value(polyol, 'volume_ml'),
        'density_g_ml': _value(polyol, 'density_g_ml'),
        'currency': currency, 'currency_symbol': currency_symbol(currency),
        'issues': result['issues'], 'valid': not bool(polyol.get('issue')),
    }


def calculate_plan_recipe(recipe, batch_mass_g=None):
    from .calculations import DISCLAIMER, _calculation_settings, _filling_snapshot
    plan = deepcopy(recipe.formulation)
    if batch_mass_g is not None:
        batch = Decimal(str(batch_mass_g))
        if not batch.is_finite() or batch <= 0:
            raise ValidationError('Batch должен быть конечным и больше нуля.')
        plan['settings']['batch_mass_g'] = {'value': str(batch), 'reason': ''}
    result = calculate_formulation(plan)
    group = _group(recipe, plan, result)
    batch = _value(plan['settings'], 'batch_mass_g')
    settings = _calculation_settings()
    inputs = {
        'recipe_id': recipe.pk, 'version_label': recipe.version_label,
        'batch_mass_g': batch, 'template': settings, 'formulation': plan,
        'groups': [{'label': 'Смесь A', 'components': deepcopy(group['rows'])}],
        'filling_plan': _filling_snapshot(recipe),
    }
    return {'batch_mass_g': batch, 'template': settings, 'groups': [group],
            'issues': result['issues'], 'disclaimer': DISCLAIMER,
            'formulation': result, 'inputs': inputs}
