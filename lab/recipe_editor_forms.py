"""The three recipe sections, with explicit missing-value explanations."""
from copy import deepcopy
from decimal import Decimal
from uuid import uuid4

from django import forms
from django.forms import formset_factory

from .forms import LIMSDecimalField, RecipeForm
from .models import Material, MaterialLot
from .widgets import ComponentLotSelect, ServerHiddenInput


SETTING_LABELS = {
    'batch_mass_g': 'Batch — масса смеси для расчёта навесок, г',
    'polyol_parts': 'Массовые части смеси A',
    'isocyanate_parts': 'Массовые части изоцианата',
    'iso_index': 'ISO index',
    'gas_mass_pct': 'Газовая смесь в общей заправке, % масс.',
    'fill_volume_ml': 'Расчётный объём заполнения баллона, мл',
}
ROW_LABELS = {
    'quantity': 'Recipe, % масс.', 'ohv': 'OHV, мг KOH/г',
    'nco_pct': 'NCO, %', 'density_g_ml': 'Плотность, г/мл',
    'price_per_kg': 'Цена за кг', 'pressure_bar': 'Давление по исходнику',
}
ROW_NUMBERS = {
    'polyol': ('quantity', 'ohv', 'density_g_ml', 'price_per_kg'),
    'isocyanate': ('nco_pct', 'density_g_ml', 'price_per_kg'),
    'gases': ('quantity', 'density_g_ml', 'price_per_kg', 'pressure_bar'),
}


def value_cell(value=None, reason=''):
    if value is not None:
        value = format(Decimal(str(value)), 'f')
        if '.' in value:
            value = value.rstrip('0').rstrip('.')
    return {'value': value, 'reason': reason}


def flat_initial(values):
    output = {}
    for name, value in values.items():
        if isinstance(value, dict) and 'value' in value:
            output[name] = value_cell(value['value'])['value']
            output[name + '_reason'] = value.get('reason', '')
        else:
            output[name] = value
    return output


def add_number(form, name, label, initial, minimum=0):
    form.fields[name] = LIMSDecimalField(
        label=label, required=False, min_value=Decimal(str(minimum)),
        initial=initial.get(name),
    )
    form.fields[name + '_reason'] = forms.CharField(
        label='Причина отсутствия: ' + label, required=False,
        initial=initial.get(name + '_reason', ''),
    )


def validate_value(form, data, name):
    if name in form.errors:
        return
    value, reason = data.get(name), data.get(name + '_reason', '')
    if value is None and not reason:
        form.add_error(name + '_reason', 'Укажите значение или причину отсутствия.')
    elif value is not None and reason:
        form.add_error(name + '_reason', 'Укажите либо значение, либо причину.')


class RecipeMetadataForm(RecipeForm):
    class Meta(RecipeForm.Meta):
        fields = ['mixture_type', 'version_label', 'title', 'recorded_on',
                  'status', 'notes']

    batch_mass_g = None


class RecipeSettingsForm(forms.Form):
    iso_ratio_mode = forms.ChoiceField(
        label='Способ задания соотношения A:B',
        choices=[('parts', 'Задать A:B, рассчитать ISO index'),
                 ('index', 'Задать ISO index, рассчитать A:B')],
    )
    isocyanate_present = forms.BooleanField(
        label='В рецептуре используется изоцианат', required=False, initial=True,
    )
    isocyanate_absence_reason = forms.CharField(
        label='Причина отсутствия изоцианата', required=False,
    )

    def __init__(self, *args, plan=None, **kwargs):
        self.original = (plan or {}).get('settings', {})
        kwargs['initial'] = flat_initial(self.original)
        super().__init__(*args, **kwargs)
        self._ignore_derived_input()
        for name, label in SETTING_LABELS.items():
            minimum = '0.00000001' if name in (
                'batch_mass_g', 'fill_volume_ml', 'polyol_parts') else 0
            add_number(self, name, label, kwargs['initial'], minimum)
        self.fields['gas_mass_pct'].max_value = Decimal(100)

    def _ignore_derived_input(self):
        if not self.is_bound:
            return
        mode = self.data.get(self.add_prefix('iso_ratio_mode'), 'parts')
        name = 'isocyanate_parts' if mode == 'index' else 'iso_index'
        self.data = self.data.copy()
        self.data.pop(self.add_prefix(name), None)
        self.data.pop(self.add_prefix(name + '_reason'), None)

    def clean(self):
        data = super().clean()
        names = ['batch_mass_g', 'polyol_parts', 'gas_mass_pct', 'fill_volume_ml']
        if data.get('isocyanate_present'):
            names.append('iso_index' if data.get('iso_ratio_mode') == 'index'
                         else 'isocyanate_parts')
        elif not data.get('isocyanate_absence_reason'):
            self.add_error('isocyanate_absence_reason', 'Укажите причину.')
        for name in names:
            validate_value(self, data, name)
        gas = data.get('gas_mass_pct')
        if gas is not None and gas > 100:
            self.add_error('gas_mass_pct', 'Доля газа не может превышать 100%.')
        return data

    def plan_values(self):
        values = deepcopy(self.original)
        values.update({name: self.cleaned_data.get(name) for name in (
            'iso_ratio_mode', 'isocyanate_present', 'isocyanate_absence_reason')})
        for name in SETTING_LABELS:
            values[name] = value_cell(self.cleaned_data.get(name),
                                     self.cleaned_data.get(name + '_reason', ''))
            original = self.original.get(name, {})
            if _same_value(original.get('value'), values[name]['value']):
                values[name].update({k: v for k, v in original.items()
                                     if k not in ('value', 'reason')})
        return values


def _same_value(left, right):
    if left is None or right is None:
        return left is None and right is None
    return Decimal(str(left)) == Decimal(str(right))


class RecipeRowForm(forms.Form):
    row_id = forms.CharField(required=False, widget=ServerHiddenInput)
    material = forms.ModelChoiceField(
        label='Материал', queryset=Material.objects.all(), required=False,
    )
    material_reason = forms.CharField(
        label='Причина отсутствия материала', required=False,
    )
    specified_lot = forms.ModelChoiceField(
        label='Партия', queryset=MaterialLot.objects.select_related('material'),
        required=False, widget=ComponentLotSelect,
    )
    lot_reason = forms.CharField(label='Причина отсутствия партии', required=False)
    price_currency = forms.ChoiceField(
        label='Валюта цены', choices=[('EUR', '€ EUR'), ('RUB', '₽ RUB'),
                                     ('USD', '$ USD')], initial='EUR',
    )

    def __init__(self, *args, kind='polyol', original_rows=None, **kwargs):
        self.kind = kind
        self.original_rows = original_rows or {}
        initial = kwargs.get('initial', {})
        kwargs['initial'] = self._initial_values(initial)
        super().__init__(*args, **kwargs)
        for name in ROW_NUMBERS[kind]:
            add_number(self, name, ROW_LABELS[name], kwargs['initial'])
        if kind == 'gases':
            self.fields['quantity'].label = 'Доля в газовой смеси, % масс.'
        if kind == 'polyol':
            self.fields['ohv_applicable'] = forms.BooleanField(
                label='OHV применяется к компоненту', required=False,
                initial=initial.get('ohv', {}).get('applicable', True),
            )
        self.fields['specified_lot'].widget.material_id = self['material'].value()
        self._mark_snapshot_properties(initial)

    def _mark_snapshot_properties(self, initial):
        original = self.original_rows.get(self['row_id'].value(), initial)
        if str(self['material'].value() or '') != str(original.get('material_id') or ''):
            return
        if str(self['specified_lot'].value() or '') != str(original.get('lot_id') or ''):
            return
        for name in ('density_g_ml', 'price_per_kg'):
            cell = original.get(name, {})
            if cell.get('value') is not None and (cell.get('source_cell') or
                                                  cell.get('origin') == 'lot'):
                self.fields[name].widget.attrs['data-source-value'] = cell['value']

    def _initial_values(self, initial):
        values = flat_initial(initial)
        values['row_id'] = initial.get('id', '')
        values['material'] = initial.get('material_id')
        values['specified_lot'] = initial.get('lot_id')
        return values

    def clean(self):
        data = super().clean()
        if self.kind != 'isocyanate' and self._unused(data):
            data['unused'] = True
            return data
        if self.empty_permitted and not self.has_changed():
            return data
        self._validate_binding(data)
        self._apply_catalog(data)
        for name in ROW_NUMBERS[self.kind]:
            if name == 'ohv' and not data.get('ohv_applicable'):
                continue
            validate_value(self, data, name)
        return data

    def _unused(self, data):
        if data.get('row_id'):
            return False
        ignored = {'row_id', 'price_currency', 'ohv_applicable', 'DELETE'}
        return not any(
            self.data.get(self.add_prefix(name)) not in (None, '')
            for name in self.fields if name not in ignored
        )

    def _validate_binding(self, data):
        material, lot = data.get('material'), data.get('specified_lot')
        if lot and material and lot.material_id != material.pk:
            self.add_error('specified_lot', 'Партия не относится к материалу.')
        if lot and not material:
            data['material'] = material = lot.material
        for key, value in [('material', material), ('lot', lot)]:
            reason = data.get(key + '_reason')
            if value is None and not reason:
                self.add_error(key + '_reason', 'Укажите значение или причину.')
            elif value is not None and reason:
                self.add_error(key + '_reason', 'Для известного значения уберите причину.')

    def _apply_catalog(self, data):
        lot = data.get('specified_lot')
        original = self.original_rows.get(data.get('row_id'), {})
        same = self._same_binding(data, original)
        for name in ('density_g_ml', 'price_per_kg'):
            old = original.get(name, {})
            if same and (old.get('source_cell') or old.get('origin') == 'lot') and old.get('value') is not None:
                data[name] = Decimal(old['value'])
                data[name + '_reason'] = ''
            elif lot and getattr(lot, name) is not None:
                data[name] = getattr(lot, name)
                data[name + '_reason'] = ''
                if name == 'price_per_kg':
                    data['price_currency'] = lot.price_currency
                data.setdefault('_catalog_fields', []).append(name)
        old_price = original.get('price_per_kg', {})
        if same and (old_price.get('source_cell') or old_price.get('origin') == 'lot'):
            data['price_currency'] = original.get('price_currency', 'EUR')

    def _same_binding(self, data, original):
        return (original.get('material_id') == getattr(data.get('material'), 'pk', None)
                and original.get('lot_id') == getattr(data.get('specified_lot'), 'pk', None))

    def plan_values(self):
        data = self.cleaned_data
        original = self.original_rows.get(data.get('row_id'), {})
        same = self._same_binding(data, original)
        row = deepcopy(original) if same else {}
        self._identity(row, data)
        for name in ROW_NUMBERS[self.kind]:
            cell = value_cell(data.get(name), data.get(name + '_reason', ''))
            old = original.get(name, {})
            if same and _same_value(old.get('value'), cell['value']):
                cell.update({k: v for k, v in old.items() if k != 'reason'})
            if name in data.get('_catalog_fields', []):
                cell['origin'] = 'lot'
            row[name] = cell
        if self.kind == 'polyol' and not data.get('ohv_applicable'):
            row['ohv'] = {'value': None, 'reason': 'Не применяется',
                          'applicable': False}
        return row

    def _identity(self, row, data):
        material, lot = data.get('material'), data.get('specified_lot')
        row.update(
            id=data.get('row_id') or uuid4().hex,
            material_id=getattr(material, 'pk', None),
            lot_id=getattr(lot, 'pk', None),
            lot_code=getattr(lot, 'lot_code', None),
            name=material.name if material else row.get('name', 'Материал не указан'),
            material_reason=data.get('material_reason', ''),
            lot_reason=data.get('lot_reason', ''),
            price_currency=data.get('price_currency', 'EUR'),
        )
        row.setdefault('mass_round_digits', 1)
        row.setdefault('ohv_round_digits', 1)


PolyolFormSet = formset_factory(
    RecipeRowForm, extra=1, max_num=100, validate_max=True, can_delete=True,
)
GasFormSet = formset_factory(
    RecipeRowForm, extra=1, max_num=20, validate_max=True, can_delete=True,
)
