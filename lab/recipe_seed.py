"""Bind checked-in Excel formulation inputs to the initialized catalog rows."""

from copy import deepcopy
from decimal import Decimal


def same_number(actual, expected):
    if actual is None or expected is None:
        return actual is None and expected is None
    precision = Decimal('0.00000001')
    return Decimal(str(actual)).quantize(precision) == Decimal(
        str(expected),
    ).quantize(precision)


def same_material(owner, expected):
    material = owner.material if hasattr(owner, 'material') else (
        owner.specified_lot.material if owner.specified_lot_id
        else owner.unbatched_material)
    name = expected.get('name') or expected.get('material_name')
    if material is None:
        return not name
    return material.name in {name, expected.get('name_raw')}


def source_rows_match(recipe, data):
    group = recipe.groups.first()
    expected_group = data['groups'][0]
    if recipe.groups.count() != 1 or group.label != expected_group['label']:
        return False
    if group.quantity_basis != expected_group['basis'] or (
        group.completeness != expected_group['completeness']
    ):
        return False
    components = list(group.components.all())
    expected = data['groups'][0]['components']
    filling = list(recipe.filling_plan.all())
    if len(components) != len(expected) or len(filling) != len(data['filling']):
        return False
    pairs = list(zip(components, expected))
    if any(not same_number(row.quantity_value, raw['quantity']) or not
           same_material(row, raw) for row, raw in pairs):
        return False
    return all(same_number(row.mass_g, raw['mass_g']) and (
        bool(row.input_group_id) or same_material(row, raw)
    ) for row, raw in zip(filling, data['filling']))


def bind_row(payload, owner):
    lot = owner.specified_lot if owner.specified_lot_id else None
    material = lot.material if lot else owner.unbatched_material
    payload['material_id'] = material.pk if material else None
    payload['lot_id'] = lot.pk if lot else None
    if material:
        payload['name'] = material.name
        payload['material_reason'] = ''
    if lot:
        payload['lot_reason'] = ''
        payload['lot_code'] = lot.lot_code
    if payload.get('manual_price_anchors'):
        bind_price_anchor(payload['manual_price_anchors'], payload)


def bind_price_anchor(anchor, row):
    for name in ('material_id', 'lot_id', 'name'):
        anchor[name] = row.get(name)


def bind_formulation(recipe, formulation):
    components = list(recipe.groups.first().components.all())
    filling = list(recipe.filling_plan.all())
    for row, component in zip(formulation['polyol'], components):
        bind_row(row, component)
    bind_row(formulation['isocyanate'], filling[1])
    for row, line in zip(formulation['gases'], filling[2:]):
        bind_row(row, line)
    rows = {row['id']: row for row in formulation['polyol']}
    for anchor in formulation['settings'].get('manual_polyol_price_anchors', []):
        bind_price_anchor(anchor, rows[anchor['id']])
    return formulation


def matching_cell(current, expected):
    if not same_number(current.get('value'), expected.get('value')):
        return False
    if current.get('value') is None:
        return current.get('reason', '') == expected.get('reason', '')
    return True


def matching_settings(current, expected):
    fields = ('batch_mass_g', 'polyol_parts', 'isocyanate_parts', 'iso_index',
              'gas_mass_pct', 'fill_volume_ml')
    if not all(matching_cell(current.get(key, {}), expected.get(key, {}))
               for key in fields):
        return False
    return all(current.get(key) == expected.get(key) for key in (
        'iso_ratio_mode', 'isocyanate_present', 'isocyanate_absence_reason',
    ))


def matching_row(current, expected):
    identities = ('id', 'material_id', 'lot_id', 'name', 'material_reason',
                  'lot_reason', 'price_currency', 'mass_round_digits',
                  'ohv_round_digits')
    if any(current.get(key) != expected.get(key) for key in identities):
        return False
    numbers = ('quantity', 'ohv', 'density_g_ml', 'price_per_kg', 'nco_pct',
               'pressure_bar')
    if not all(matching_cell(current.get(key, {}), expected.get(key, {}))
               for key in numbers):
        return False
    return matching_functionality(current.get('functionality', {}),
                                  expected.get('functionality', {}))


def matching_functionality(current, expected):
    if matching_cell(current, expected):
        return True
    if expected.get('source_role') == 'cost_share' and current.get('value') is None:
        if current.get('reason') in (
            'Не применяется к этой строке.', 'Значение не указано в исходном Excel.',
        ):
            return True
    same_formula = (current.get('source_formula') and
                    current.get('source_formula') == expected.get('source_formula'))
    if same_formula:
        return same_number(current.get('value'), expected.get('source_cached_value'))
    return (expected.get('applicable') is False and
            current.get('value') is None and
            current.get('reason') == 'Значение не указано в исходном Excel.')


def source_document_matches(current, expected):
    if current.get('source_sheet') != expected.get('source_sheet'):
        return False
    if not matching_settings(current.get('settings', {}), expected['settings']):
        return False
    for section in ('polyol', 'gases'):
        before, after = current.get(section, []), expected[section]
        if len(before) != len(after) or not all(
            matching_row(a, b) for a, b in zip(before, after)
        ):
            return False
    return matching_row(current.get('isocyanate', {}), expected['isocyanate'])


def add_missing(target, source, keys):
    for key in keys:
        if key not in target and key in source:
            target[key] = deepcopy(source[key])


def enrich_row(current, expected):
    add_missing(current, expected, ('manual_weighted_price', 'manual_price_anchors'))
    functionality = current.get('functionality', {})
    checked = expected.get('functionality', {})
    if checked.get('applicable') is not False:
        return
    if matching_functionality(functionality, checked):
        current['functionality'] = deepcopy(checked)


def enrich_source_document(current, expected):
    result = deepcopy(current)
    add_missing(result['settings'], expected['settings'], (
        'ohv_extra_contributions', 'manual_polyol_price_per_kg',
        'manual_polyol_price_anchors',
    ))
    for section in ('polyol', 'gases'):
        for before, after in zip(result[section], expected[section]):
            enrich_row(before, after)
    return result


def seed_recipe_formulation(recipe, data):
    """Enrich untouched legacy imports once, retaining subsequent user edits."""
    if not data.get('formulation'):
        return
    if not same_number(recipe.batch_mass_g, data.get('batch_mass_g')):
        return
    if not recipe.groups.exists() or not source_rows_match(recipe, data):
        return
    formulation = bind_formulation(recipe, deepcopy(data['formulation']))
    if recipe.formulation:
        if not source_document_matches(recipe.formulation, formulation):
            return
        formulation = enrich_source_document(recipe.formulation, formulation)
        if formulation == recipe.formulation:
            return
    recipe.formulation = formulation
    recipe.save(update_fields=['formulation'])
