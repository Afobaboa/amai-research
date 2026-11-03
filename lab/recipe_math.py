"""Decimal calculations for the polyol, isocyanate and gas recipe blocks.

Only typed numerical inputs are evaluated. The rounding stages follow the
original filling worksheets; spreadsheet text is never executable code.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext


HUNDRED = Decimal(100)
THOUSAND = Decimal(1000)
TOLERANCE = Decimal('0.001')


@dataclass(frozen=True)
class _Value:
    value: Decimal | None
    reason: str = ''
    applicable: bool = True


def _number(raw, label):
    cell = raw if isinstance(raw, dict) else {'value': raw}
    value = cell.get('value')
    if value is None or value == '' or isinstance(value, bool):
        return _Value(None, cell.get('reason') or f'Не указано: {label}.')
    try:
        number = Decimal(str(value).replace(',', '.'))
    except (InvalidOperation, ValueError, TypeError):
        return _Value(None, f'Некорректное значение: {label}.')
    if not number.is_finite():
        return _Value(None, f'Некорректное значение: {label}.')
    return _Value(number)


def _get(owner, key, label):
    return _number(owner.get(key), label)


def _reasons(values):
    return ' '.join(dict.fromkeys(v.reason for v in values if v.reason))


def _apply(operation, *values):
    if any(v.value is None for v in values):
        applicable = any(v.applicable for v in values if v.value is None)
        return _Value(None, _reasons(values), applicable)
    try:
        result = operation(*(v.value for v in values))
    except (ArithmeticError, ValueError):
        return _Value(None, 'Расчёт невозможен: проверьте делитель и исходные данные.')
    return _Value(result)


def _round(value, digits=1):
    if digits is None:
        return value
    return _apply(lambda n: n.quantize(Decimal(1).scaleb(-digits),
                                     rounding=ROUND_HALF_UP), value)


def _sum(values):
    values = list(values)
    return _apply(lambda *items: sum(items, Decimal(0)), *values)


def _positive(value, label, *, zero=False):
    if value.value is not None and (value.value < 0 or
                                   (not zero and value.value == 0)):
        return _Value(None, f'{label}: требуется положительное значение.'
                      if not zero else f'{label}: значение не может быть отрицательным.')
    return value


def _quantity(row):
    label = row.get('name') or 'компонент'
    return _positive(_get(row, 'quantity', f'Recipe, {label}'),
                     f'Recipe, {label}', zero=True)


def _percent(value, label):
    value = _positive(value, label, zero=True)
    if value.value is not None and value.value > HUNDRED:
        return _Value(None, f'{label}: значение не может превышать 100%.')
    return value


def _contribution(quantity, operation, *values):
    if quantity.value == 0:
        return _Value(Decimal(0))
    return _apply(operation, quantity, *values)


def _row_property(row, key, label):
    name = row.get('name') or 'компонент'
    value = _get(row, key, f'{label}, {name}')
    return _positive(value, f'{label}, {name}', zero=key != 'density_g_ml')


def _ohv_contribution(row, quantity):
    raw = row.get('ohv')
    if isinstance(raw, dict) and raw.get('applicable') is False:
        return _Value(Decimal(0))
    value = _row_property(row, 'ohv', 'OHV')
    weighted = _contribution(quantity, lambda q, v: q * v / HUNDRED, value)
    return _round(weighted, row.get('ohv_round_digits', 1))


def _stoichiometric(row, quantity, nco):
    raw = row.get('ohv')
    if isinstance(raw, dict) and raw.get('applicable') is False:
        return _Value(Decimal(0))
    ohv = _row_property(row, 'ohv', 'OHV')
    return _contribution(quantity,
                         lambda q, h, n: Decimal(4200) * q * h /
                         (n * Decimal(56100)), ohv, nco)


def _same_number(first, second):
    first = _number(first, '').value
    second = _number(second, '').value
    if first is None or second is None:
        return first is second
    quantum = Decimal('0.00000001')
    return first.quantize(quantum) == second.quantize(quantum)


def _anchor_matches(row, anchor):
    for key, expected in anchor.items():
        current = row.get(key)
        if key in {'quantity', 'price_per_kg'}:
            if not _same_number(current, expected):
                return False
        elif key in {'id', 'name', 'material_id', 'lot_id'}:
            if str(current or '') != str(expected or ''):
                return False
    return True


def _manual_row_price(row, calculated):
    raw = row.get('manual_weighted_price')
    if not raw:
        return calculated, ''
    address = raw.get('source_cell', '?')
    anchors = row.get('manual_price_anchors') or {}
    if not anchors or not _anchor_matches(row, anchors):
        return calculated, (f'Ручная стоимость из {address} не применяется: '
                            'состав, материал, партия или цена изменены.')
    value = _number(raw, 'Ручной вклад стоимости из исходного Excel')
    value = _apply(lambda price: price / HUNDRED, value)
    return value, (f'Использован ручной вклад стоимости из {address}; '
                   'в исходном Excel он задан без формулы.')


def _polyol_row(row, batch, nco):
    quantity = _quantity(row)
    density = _row_property(row, 'density_g_ml', 'Плотность')
    price = _row_property(row, 'price_per_kg', 'Цена за кг')
    mass = _round(_apply(lambda q, b: b * q / HUNDRED, quantity, batch),
                  row.get('mass_round_digits', 1))
    weighted_price, warning = _manual_row_price(row, _contribution(
        quantity, lambda q, p: q * p / HUNDRED, price))
    return {
        'id': row.get('id'), 'name': row.get('name', ''),
        'quantity': quantity, 'mass_g': mass,
        'volume_ml': _contribution(mass, lambda m, d: m / d, density),
        'density_g_ml': density, 'price_per_kg': price,
        'price_currency': row.get('price_currency', ''),
        'cost': _contribution(mass, lambda m, p: m / THOUSAND * p, price),
        'weighted_ohv': _ohv_contribution(row, quantity),
        'stoichiometric_iso_parts': _stoichiometric(row, quantity, nco),
        'specific_volume': _contribution(quantity, lambda q, d: q / d, density),
        'weighted_price': weighted_price, 'source_warning': warning,
    }


def _currency(rows):
    currencies = {r.get('price_currency', '') for r in rows
                  if r['quantity'].value not in (None, Decimal(0))}
    if len(currencies) > 1:
        return '', 'Цены указаны в разных валютах; стоимость не рассчитана.'
    currency = next(iter(currencies), '')
    if currencies and currency not in {'EUR', 'RUB', 'USD'}:
        return '', 'Не указана валюта цены; стоимость не рассчитана.'
    return currency, ''


def _composition_issue(total, rows, label):
    if not rows:
        return f'{label}: состав не указан.'
    if total.value is None:
        return total.reason
    if abs(total.value - HUNDRED) > TOLERANCE:
        return f'{label}: сумма Recipe должна быть 100%.'
    return ''


def _extra_ohv(settings):
    extras = settings.get('ohv_extra_contributions') or []
    values = [_number(raw, 'Дополнительный вклад OHV') for raw in extras]
    return _sum(values)


def _composition_matches(rows, anchors):
    if not anchors or len(rows) != len(anchors):
        return False
    by_id = {str(row.get('id')): row for row in rows}
    return all(str(anchor.get('id')) in by_id and
               _anchor_matches(by_id[str(anchor.get('id'))], anchor)
               for anchor in anchors)


def _manual_polyol_price(settings, rows, calculated):
    raw = settings.get('manual_polyol_price_per_kg')
    if not raw:
        return calculated, ''
    anchors = settings.get('manual_polyol_price_anchors') or []
    address = raw.get('source_cell', '?')
    if not _composition_matches(rows, anchors):
        return calculated, (f'Ручная цена смеси из {address} не применяется: '
                            'состав, материал, партия или цена изменены.')
    return (_number(raw, 'Цена смеси из исходного Excel'),
            f'Использована ручная цена смеси из {address}; '
            'в исходном Excel она задана без формулы.')


def _polyol_prices(plan, settings, rows, composition_issue):
    currency, issue = _currency(rows)
    price = _sum(row['weighted_price'] for row in rows)
    price, warning = _manual_polyol_price(settings, plan.get('polyol', []), price)
    cost = _sum(row['cost'] for row in rows)
    if issue or composition_issue:
        price = cost = _Value(None, issue or composition_issue)
    return {'price_per_kg': price, 'cost': cost,
            'price_currency': currency, 'price_warning': warning}


def _functionality(rows):
    applicable = []
    for row in rows:
        raw = row.get('functionality')
        if isinstance(raw, dict) and raw.get('applicable') is False:
            continue
        if isinstance(raw, dict) and raw.get('source_formula'):
            continue
        if raw is None:
            continue
        quantity = _quantity(row)
        if quantity.value == 0:
            continue
        value = _row_property(row, 'functionality', 'Функциональность')
        applicable.append((quantity, value))
    if not applicable:
        return _Value(None, 'Функциональность не задана.', False)
    weighted = [_apply(lambda q, f: q * f, q, f) for q, f in applicable]
    return _apply(lambda w, q: w / q, _sum(weighted),
                  _sum(q for q, _ in applicable))


def _polyol_extras(plan, settings, rows, price_warning):
    extra = _extra_ohv(settings)
    total_ohv = _sum([*(row['weighted_ohv'] for row in rows), extra])
    extras = settings.get('ohv_extra_contributions') or []
    addresses = ', '.join(str(cell.get('source_cell', '?')) for cell in extras)
    warning = (f'В исходном Excel добавлен вклад OHV {extra.value} из '
               f'{addresses} без количества компонента.' if extras else '')
    warning = ' '.join(filter(None, [warning, price_warning,
                                     *(r['source_warning'] for r in rows)]))
    return {'ohv': _round(total_ohv), 'extra_ohv': extra,
            'functionality': _functionality(plan.get('polyol', [])),
            'source_warning': warning}


def _polyol(plan, settings, nco):
    batch = _positive(_get(settings, 'batch_mass_g', 'Batch, г'), 'Batch, г')
    rows = [_polyol_row(row, batch, nco) for row in plan.get('polyol', [])]
    total = _sum(row['quantity'] for row in rows)
    issue = _composition_issue(total, rows, 'Полиольная смесь')
    pricing = _polyol_prices(plan, settings, rows, issue)
    density = _apply(lambda v: HUNDRED / v,
                     _sum(row['specific_volume'] for row in rows))
    return {
        'rows': rows, 'batch_mass_g': batch, 'quantity_total': total,
        'mass_g': (_Value(None, issue) if issue else
                   _round(_sum(row['mass_g'] for row in rows))),
        'volume_ml': _sum(row['volume_ml'] for row in rows),
        'stoichiometric_iso_parts': _sum(row['stoichiometric_iso_parts']
                                         for row in rows),
        'density_g_ml': _Value(None, issue) if issue else density,
        **pricing, 'issue': issue,
        **_polyol_extras(plan, settings, rows, pricing['price_warning']),
    }


def _isocyanate(plan, settings, polyol, nco):
    row = plan.get('isocyanate') or {}
    present = settings.get('isocyanate_present', True)
    parts = _positive(_get(settings, 'isocyanate_parts', 'Части изоцианата'),
                      'Части изоцианата', zero=True)
    index = _positive(_get(settings, 'iso_index', 'Индекс ISO'), 'Индекс ISO')
    a = _positive(_get(settings, 'polyol_parts', 'Части смеси A'), 'Части смеси A')
    stoich = _apply(lambda s, p: s * p / HUNDRED,
                     polyol['stoichiometric_iso_parts'], a)
    if not present:
        parts = _Value(Decimal(0))
        index = _Value(None, 'Не применяется: изоцианат не используется.', False)
    elif settings.get('iso_ratio_mode', 'parts') == 'index':
        parts = _apply(lambda s, i: s * i, stoich, index)
    else:
        index = _apply(lambda p, s: p / s, parts, stoich)
    density = _row_property(row, 'density_g_ml', 'Плотность')
    price = _row_property(row, 'price_per_kg', 'Цена за кг')
    if not present:
        density = price = index
    return {
        'id': row.get('id'), 'name': row.get('name', ''), 'present': present,
        'absence_reason': settings.get('isocyanate_absence_reason', ''),
        'parts': parts, 'iso_index': index, 'nco_pct': nco,
        'stoichiometric_iso_parts': stoich,
        'nco_equivalent_weight': _apply(lambda n: Decimal(4200) / n, nco),
        'density_g_ml': density, 'price_per_kg': price,
        'price_currency': row.get('price_currency', ''),
    }


def _gas_row(row):
    quantity = _quantity(row)
    density = _row_property(row, 'density_g_ml', 'Плотность')
    pressure = _row_property(row, 'pressure_bar', 'Давление')
    return {
        'id': row.get('id'), 'name': row.get('name', ''), 'quantity': quantity,
        'density_g_ml': density,
        'price_per_kg': _row_property(row, 'price_per_kg', 'Цена за кг'),
        'price_currency': row.get('price_currency', ''),
        'weighted_density': _contribution(quantity,
                                           lambda q, d: q * d / HUNDRED, density),
        'specific_volume': _contribution(quantity, lambda q, d: q / d, density),
        'pressure_bar': pressure,
        'pressure_contribution': _contribution(quantity,
                                                lambda q, p: q * p / HUNDRED,
                                                pressure),
    }


def _gases(plan, settings):
    rows = [_gas_row(row) for row in plan.get('gases', [])]
    raw_total = _sum(row['quantity'] for row in rows)
    total = _round(raw_total)
    gas_pct = _percent(_get(settings, 'gas_mass_pct', 'Доля газовой смеси, %'),
                       'Доля газовой смеси, %')
    issue = ('' if gas_pct.value == 0 else
             _composition_issue(raw_total, rows, 'Газы'))
    density = _round(_sum(row['weighted_density'] for row in rows), 3)
    return {
        'rows': rows, 'quantity_total': total, 'mass_pct': gas_pct,
        'density_g_ml': _Value(None, issue) if issue else density,
        'volume_ml': _round(_sum(row['specific_volume'] for row in rows)),
        'pressure_bar': _sum(row['pressure_contribution'] for row in rows),
        'issue': issue,
    }


def _filling_row(row, mass_pct, density):
    return {
        'id': row.get('id'), 'name': row.get('name', ''), 'mass_pct': mass_pct,
        'density_g_ml': density, 'price_per_kg': row['price_per_kg'],
        'price_currency': row.get('price_currency', ''),
        'reference_volume_ml': _round(_contribution(mass_pct,
                                                    lambda m, d: m / d,
                                                    density)),
    }


def _reference_filling(polyol, iso, gases, settings):
    a = _positive(_get(settings, 'polyol_parts', 'Части смеси A'), 'Части смеси A')
    parts = _round(_sum([a, iso['parts']]))
    pur_pct = _apply(lambda g: HUNDRED - g, gases['mass_pct'])
    a_pct = _round(_apply(lambda p, t, pur: p / t * pur, a, parts, pur_pct))
    b_pct = _round(_contribution(iso['parts'], lambda p, t, pur: p / t * pur,
                                 parts, pur_pct))
    rows = [_filling_row({'name': 'Полиольная смесь A', **polyol},
                         a_pct, _round(polyol['density_g_ml'], 3)),
            _filling_row(iso, b_pct, iso['density_g_ml'])]
    for gas in gases['rows']:
        pct = _round(_contribution(gas['quantity'], lambda q, t, g: q / t * g,
                                   gases['quantity_total'], gases['mass_pct']))
        if gases['mass_pct'].value == 0:
            pct = _Value(Decimal(0))
        elif gases['issue']:
            pct = _Value(None, gases['issue'])
        rows.append(_filling_row(gas, pct, gas['density_g_ml']))
    return rows, a, parts, pur_pct


def _scale_filling(row, target, reference_volume):
    volume = _round(_contribution(row['reference_volume_ml'],
                                   lambda v, t, s: t / s * v,
                                   target, reference_volume))
    mass = _round(_contribution(volume, lambda v, d: v * d,
                                row['density_g_ml']))
    return {
        **row, 'volume_ml': volume, 'mass_g': mass,
        'cost': _contribution(mass, lambda m, p: m / THOUSAND * p,
                              row['price_per_kg']),
    }


def _filling_totals(rows):
    mass = _round(_sum(row['mass_g'] for row in rows))
    volume = _round(_sum(row['volume_ml'] for row in rows))
    currency_rows = [{**row, 'quantity': row['mass_g']} for row in rows]
    currency, issue = _currency(currency_rows)
    cost = _Value(None, issue) if issue else _sum(row['cost'] for row in rows)
    return {
        'mass_g': mass, 'volume_ml': volume, 'cost': cost,
        'price_currency': currency,
        'price_per_kg': _apply(lambda c, m: c * THOUSAND / m, cost, mass),
        'density_g_ml': _apply(lambda m, v: m / v, mass, volume),
        'gas_mass_g': _round(_sum(row['mass_g'] for row in rows[2:])),
        'gas_volume_ml': _round(_sum(row['volume_ml'] for row in rows[2:])),
        'pur_volume_ml': _round(_sum(row['volume_ml'] for row in rows[:2])),
        'pur_cost': _sum(row['cost'] for row in rows[:2]),
        'gas_cost': _sum(row['cost'] for row in rows[2:]),
    }


def _filling(polyol, iso, gases, settings):
    rows, a, parts, pur_pct = _reference_filling(polyol, iso, gases, settings)
    reference_volume = _round(_sum(row['reference_volume_ml'] for row in rows))
    target = _positive(_get(settings, 'fill_volume_ml', 'Объём заполнения, мл'),
                       'Объём заполнения, мл')
    rows = [_scale_filling(row, target, reference_volume) for row in rows]
    ratio_volume = _round(_sum([
        _round(_apply(lambda p, d: p / d, a,
                       _round(polyol['density_g_ml'], 3))),
        _round(_contribution(iso['parts'], lambda p, d: p / d,
                              iso['density_g_ml'])),
    ]))
    return {
        'polyol': rows[0], 'isocyanate': rows[1], 'gases': rows[2:],
        'rows': rows, 'target_volume_ml': target,
        'reference_volume_ml': reference_volume, 'pur_mass_pct': pur_pct,
        'pur_density_g_ml': _round(_apply(lambda p, v: p / v,
                                         parts, ratio_volume), 3),
        **_filling_totals(rows),
    }


def _serialize(value):
    if isinstance(value, _Value):
        number = None if value.value is None else format(value.value, 'f')
        if number and '.' in number:
            number = number.rstrip('0').rstrip('.')
        result = {'value': number or ('0' if value.value == 0 else None),
                  'reason': value.reason}
        if not value.applicable:
            result['applicable'] = False
        return result
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    return value


def _issues(value):
    if isinstance(value, _Value):
        return [value.reason] if value.reason and value.applicable else []
    if isinstance(value, dict):
        return [reason for item in value.values() for reason in _issues(item)]
    if isinstance(value, list):
        return [reason for item in value for reason in _issues(item)]
    return []


def calculate_formulation(plan):
    """Return JSON-ready, read-only results with missing-data explanations."""
    settings = plan.get('settings') or {}
    iso_row = plan.get('isocyanate') or {}
    nco = _positive(_get(iso_row, 'nco_pct', 'NCO, %'), 'NCO, %')
    if not settings.get('isocyanate_present', True):
        nco = _Value(None, 'Не применяется: изоцианат не используется.', False)
    with localcontext() as context:
        context.prec = 32
        polyol = _polyol(plan, settings, nco)
        iso = _isocyanate(plan, settings, polyol, nco)
        gases = _gases(plan, settings)
        output = {'polyol': polyol, 'isocyanate': iso, 'gases': gases,
                  'filling': _filling(polyol, iso, gases, settings)}
        output['issues'] = list(dict.fromkeys(_issues(output)))
        if polyol['source_warning']:
            output['issues'].append(polyol['source_warning'])
        return _serialize(output)
