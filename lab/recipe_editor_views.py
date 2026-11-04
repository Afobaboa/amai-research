"""Recipe editing and stateless previews share validation and calculation."""
from copy import deepcopy

from django import forms
from django.contrib import messages
from django.db import transaction
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .models import MaterialLot, RecipeVersion
from .recipe_editor_forms import (
    GasFormSet, PolyolFormSet, RecipeMetadataForm, RecipeRowForm,
    RecipeSettingsForm,
)
from .recipe_plans import decimal_value, recipe_plan, replace_recipe_rows


def _original_rows(plan):
    rows = [*plan.get('polyol', []), *plan.get('gases', []),
            plan.get('isocyanate', {})]
    return {row['id']: row for row in rows if row.get('id')}


class EditorForms:
    def __init__(self, data, plan):
        originals = _original_rows(plan)
        self.settings = RecipeSettingsForm(data, prefix='settings', plan=plan)
        self.polyol = PolyolFormSet(
            data, prefix='polyol', initial=plan.get('polyol', []),
            form_kwargs={'kind': 'polyol', 'original_rows': originals},
        )
        self.isocyanate = RecipeRowForm(
            data, prefix='isocyanate', initial=plan.get('isocyanate', {}),
            kind='isocyanate', original_rows=originals,
        )
        self.gases = GasFormSet(
            data, prefix='gases', initial=plan.get('gases', []),
            form_kwargs={'kind': 'gases', 'original_rows': originals},
        )

    def is_valid(self):
        valid = self.settings.is_valid()
        valid = self.polyol.is_valid() & self.gases.is_valid() & valid
        if self.settings.cleaned_data.get('isocyanate_present'):
            valid = self.isocyanate.is_valid() & valid
        return valid

    def _rows(self, formset):
        rows = []
        for form in formset:
            if form.cleaned_data.get('unused'):
                continue
            if form.cleaned_data.get('DELETE') or not form.has_changed():
                if form.empty_permitted:
                    continue
            if form.cleaned_data.get('DELETE'):
                continue
            rows.append(form.plan_values())
        return rows

    def plan(self):
        present = self.settings.cleaned_data.get('isocyanate_present')
        return {
            'schema_version': 1, 'settings': self.settings.plan_values(),
            'polyol': self._rows(self.polyol), 'gases': self._rows(self.gases),
            'isocyanate': self.isocyanate.plan_values() if present else {},
        }

    def errors(self):
        return {'settings': self.settings.errors, 'polyol': self.polyol.errors,
                'isocyanate': self.isocyanate.errors if self.isocyanate.is_bound
                and self.settings.cleaned_data.get('isocyanate_present') else {},
                'gases': self.gases.errors}

    def context(self):
        return {'settings_form': self.settings, 'polyol_formset': self.polyol,
                'isocyanate_form': self.isocyanate, 'gas_formset': self.gases}


def _references(request, source_pk=None, pk=None):
    editing = get_object_or_404(RecipeVersion, pk=pk) if pk else None
    source = get_object_or_404(RecipeVersion, pk=source_pk) if source_pk else None
    return editing, source


def _can_edit(recipe):
    return not recipe or (recipe.status == 'draft' and
                          not recipe.session_matches.exists())


def _metadata_initial(source):
    if source:
        return {name: getattr(source, name) for name in (
            'mixture_type', 'title', 'notes', 'recorded_on')} | {
                'version_label': source.version_label + '-новая', 'status': 'draft'}
    return {'status': 'draft'}


def _catalog_values():
    return {str(lot.pk): {
        'material_id': lot.material_id,
        'density_g_ml': str(lot.density_g_ml) if lot.density_g_ml is not None else None,
        'price_per_kg': str(lot.price_per_kg) if lot.price_per_kg is not None else None,
        'price_currency': lot.price_currency,
    } for lot in MaterialLot.objects.all()}


def _calculate(plan):
    from .recipe_math import calculate_formulation
    return calculate_formulation(plan)


def recipe_editor(request, source_pk=None, pk=None):
    editing, source = _references(request, source_pk, pk)
    if not _can_edit(editing):
        return HttpResponseForbidden('Изменять можно только свободный черновик.')
    plan = recipe_plan(editing or source)
    data = request.POST if request.method == 'POST' else None
    form = RecipeMetadataForm(data, instance=editing,
                              initial=_metadata_initial(source))
    editor = EditorForms(data, plan)
    if data is not None:
        valid = editor.is_valid() & form.is_valid()
        new_plan = editor.plan()
        result = _calculate(new_plan)
        _validate_composition(form, new_plan)
        if valid and not form.errors:
            response = _save(request, form, new_plan, result, editing, source)
            if response:
                return response
        plan = new_plan
    return _render(request, form, editor, plan, editing, source)


def _validate_composition(form, plan):
    if not plan['polyol']:
        form.add_error(None, 'Добавьте компоненты базовой смеси.')
        return
    values = [decimal_value(row.get('quantity', {})) for row in plan['polyol']]
    if all(value is not None for value in values) and abs(sum(values) - 100) > 0.001:
        form.add_error(None, 'Сумма Recipe должна быть 100% (допуск 0,001%).')
    gas_values = [decimal_value(row.get('quantity', {})) for row in plan['gases']]
    gas_pct = decimal_value(plan['settings'].get('gas_mass_pct', {}))
    if gas_pct and (not gas_values or (all(value is not None for value in gas_values)
                    and abs(sum(gas_values) - 100) > 0.001)):
        form.add_error(None, 'Сумма долей газовой смеси должна быть 100%.')


def _validate_lots(form, plan):
    rows = [*plan['polyol'], *plan['gases'], plan.get('isocyanate', {})]
    ids = {row['lot_id'] for row in rows if row.get('lot_id')}
    lots = list(MaterialLot.objects.select_for_update().filter(pk__in=ids))
    if form.cleaned_data['status'] != 'published':
        return
    for lot in lots:
        if lot.status != 'active' or (lot.expires_on and
                                     lot.expires_on < timezone.localdate()):
            form.add_error(None, f'Партия {lot.lot_code} закрыта или просрочена.')


@transaction.atomic
def _save(request, form, plan, result, editing, source):
    from .calculations import save_calculation
    from .views import _audit, _recipe_snapshot, _template_snapshot
    _validate_lots(form, plan)
    if form.errors:
        return None
    locked = RecipeVersion.objects.select_for_update().get(pk=editing.pk) if editing else None
    if not _can_edit(locked):
        return HttpResponseForbidden('Черновик уже опубликован или связан с экспериментом.')
    before = _recipe_snapshot(locked) if locked else {}
    recipe = form.save(commit=False)
    recipe.formulation = plan
    recipe.batch_mass_g = decimal_value(plan['settings']['batch_mass_g']) or 1000
    recipe.source_sheet = (editing or source).source_sheet if editing or source else None
    recipe.recipe_template = getattr(editing or source, 'recipe_template', None)
    recipe.template_snapshot = _template_snapshot(recipe.recipe_template)
    recipe.created_by = editing.created_by if editing else request.employee
    recipe.save()
    replace_recipe_rows(recipe, result)
    if recipe.status == 'published':
        save_calculation(recipe, request.employee, purpose='publication')
    _audit(request, recipe, 'updated' if editing else 'created',
           f'Сохранена рецептура {recipe.version_label}', before, _recipe_snapshot(recipe))
    messages.success(request, 'Рецептура сохранена.', extra_tags='form-draft-saved')
    return redirect('recipe_detail', pk=recipe.pk)


def _render(request, form, editor, plan, editing, source):
    context = editor.context() | {
        'form': form, 'recipe': editing, 'source': source,
        'calculation': _calculate(plan), 'catalog_values': _catalog_values(),
        'preview_url': reverse('recipe_preview'),
        'title': 'Редактирование рецептуры' if editing else 'Новая версия рецептуры'
        if source else 'Новая рецептура',
    }
    return render(request, 'lab/recipe_editor.html', context)


def recipe_preview(request):
    source_id = request.POST.get('recipe_id') or request.POST.get('source_id')
    source = get_object_or_404(RecipeVersion, pk=source_id) if source_id else None
    editor = EditorForms(request.POST, recipe_plan(source))
    valid = editor.is_valid()
    return JsonResponse({'calculation': _calculate(editor.plan()),
                         'errors': editor.errors(), 'valid': bool(valid)})


def _detail_calculation(recipe, records):
    saved = None
    if recipe.status != 'draft':
        saved = records.filter(outputs__purpose='publication').order_by(
            'created_at', 'pk').first()
        saved = saved or records.order_by('created_at', 'pk').first()
    result = saved.outputs.get('formulation') if saved else None
    plan = (saved.inputs.get('formulation') if result else None)
    plan = plan or recipe.formulation
    return saved, plan, result or _calculate(plan)


def _readonly_row(form, values):
    for name, key, label in (
        ('material', 'material_id', values.get('name') or 'Материал не указан'),
        ('specified_lot', 'lot_id', values.get('lot_code') or 'Партия не указана'),
    ):
        field = form.fields[name]
        field.widget = forms.Select()
        field.choices = [(values.get(key) or '', label)]
    form.material_url = (reverse('material_detail', args=[values['material_id']])
                         if values.get('material_id') else '')
    form.lot_url = (reverse('lot_detail', args=[values['lot_id']])
                    if values.get('lot_id') else '')


def _readonly_context(recipe, plan):
    metadata = RecipeMetadataForm(instance=recipe)
    metadata.fields['status'].choices = [(recipe.status, recipe.get_status_display())]
    metadata.fields['recorded_on'].widget = forms.DateInput(format='%d.%m.%Y')
    editor = EditorForms(None, plan)
    editor.polyol.extra = editor.gases.extra = 0
    for formset, values in ((editor.polyol, plan['polyol']),
                            (editor.gases, plan['gases'])):
        for form, row in zip(formset, values):
            _readonly_row(form, row)
    _readonly_row(editor.isocyanate, plan.get('isocyanate', {}))
    for form in [metadata, editor.settings, editor.isocyanate,
                 *editor.polyol, *editor.gases]:
        for field in form.fields.values():
            field.disabled = True
            field.required = False
    return editor.context() | {'form': metadata, 'readonly': True,
                               'title': recipe.version_label}


def formulation_detail(request, recipe):
    from .views import _events_for, _recipe_properties
    records = recipe.calculations.all()
    saved, plan, result = _detail_calculation(recipe, records)
    context = _readonly_context(recipe, plan) | {
        'recipe': recipe, 'plan': plan, 'result': result, 'calculation': result,
        'events': _events_for('recipe', recipe.pk),
        'properties': _recipe_properties(recipe),
        'matches': recipe.session_matches.select_related('session'),
        'saved_calculation': saved,
        'fixed_result': bool(saved and saved.outputs.get('formulation')),
        'can_edit_recipe': _can_edit(recipe),
    }
    return render(request, 'lab/recipe_plan_detail.html', context)
