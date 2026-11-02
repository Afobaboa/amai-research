"""Dedicated status actions with the same permissions and history as editing."""
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from .calculations import save_calculation
from .catalog_views import audit, snapshot
from .models import MaterialLot, RecipeVersion, TestSession
from .status_forms import StatusForm
from .views import (
    _audit, _card_snapshot, _initial_properties, _recipe_snapshot,
    _validate_plan, author_required,
)


def _can_change(instance):
    if isinstance(instance, RecipeVersion):
        return (instance.status == 'draft' and
                not instance.session_matches.exists())
    return not isinstance(instance, TestSession) or instance.status != 'completed'


def _recipe_lot_ids(recipe):
    return set(MaterialLot.objects.filter(
        Q(recipe_components__group__recipe_version=recipe) |
        Q(filling_lines__recipe_version=recipe),
    ).values_list('pk', flat=True))


def _lock_instance(model, pk):
    lot_ids = set()
    if model is RecipeVersion:
        lot_ids = _recipe_lot_ids(pk)
        list(MaterialLot.objects.select_for_update().filter(pk__in=lot_ids))
    instance = get_object_or_404(model.objects.select_for_update(), pk=pk)
    return instance, lot_ids


def _recipe_rows(recipe):
    rows = []
    for group in recipe.groups.all():
        for row in group.components.select_related('specified_lot'):
            rows.append({
                'group_label': group.label,
                'quantity_basis': group.quantity_basis,
                'completeness': group.completeness,
                'quantity_value': row.quantity_value,
                'specified_lot': row.specified_lot,
                'name_cell': row.name_cell, 'quantity_cell': row.quantity_cell,
            })
    fills = [{
        'group_label': row.input_group.label if row.input_group else '',
        'specified_lot': row.specified_lot,
        'label_cell': row.label_cell, 'mass_cell': row.mass_cell,
    } for row in recipe.filling_plan.select_related(
        'input_group', 'specified_lot',
    )]
    return rows, fills


def _validate_status(form, instance, lot_ids):
    if form.cleaned_data['current_status'] != instance.status:
        form.add_error(None, 'Статус уже изменён. Обновите страницу.')
        return False
    if isinstance(instance, RecipeVersion):
        if _recipe_lot_ids(instance) != lot_ids:
            form.add_error(None, 'Состав изменён. Обновите страницу.')
            return False
        rows, fills = _recipe_rows(instance)
        form.cleaned_data['source_sheet'] = instance.source_sheet
        _validate_plan(form, rows, fills, _initial_properties(instance))
    return not form.errors


def _save_status(request, instance, form):
    is_recipe = isinstance(instance, RecipeVersion)
    if isinstance(instance, MaterialLot):
        before = snapshot(instance)
    else:
        before = _recipe_snapshot(instance) if is_recipe else _card_snapshot(instance)
    instance.status = form.cleaned_data['status']
    instance.save(update_fields=['status'])
    summary = f'Изменён статус {instance}: {instance.get_status_display()}'
    if is_recipe:
        save_calculation(instance, request.employee, purpose='publication')
    if isinstance(instance, MaterialLot):
        audit(request, instance, 'lot', before, summary)
    else:
        after = _recipe_snapshot(instance) if is_recipe else _card_snapshot(instance)
        _audit(request, instance, 'updated', summary[:255], before, after)


def _change_status(request, model, pk, kind, description):
    instance = get_object_or_404(model, pk=pk)
    if not _can_change(instance):
        return HttpResponseForbidden('Статус этой записи изменять нельзя.')
    form = StatusForm(
        request.POST if request.method == 'POST' else None, instance=instance,
    )
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            instance, lot_ids = _lock_instance(model, pk)
            if not _can_change(instance):
                return HttpResponseForbidden('Статус записи уже изменён.')
            if _validate_status(form, instance, lot_ids):
                _save_status(request, instance, form)
                messages.success(request, 'Статус сохранён.')
                return redirect(f'{kind}_detail', pk=pk)
    return render(request, 'lab/status_form.html', {
        'form': form, 'record': instance, 'description': description,
        'cancel_url': reverse(f'{kind}_detail', args=[pk]),
    })


@author_required
@require_http_methods(['GET', 'POST'])
def recipe_status(request, pk):
    return _change_status(
        request, RecipeVersion, pk, 'recipe',
        'После публикации состав фиксируется. Для его изменения '
        'понадобится создать новую версию рецептуры.',
    )


@author_required
@require_http_methods(['GET', 'POST'])
def test_status(request, pk):
    return _change_status(
        request, TestSession, pk, 'test',
        'После завершения карточка и её результаты будут доступны '
        'только для просмотра.',
    )


@author_required
@require_http_methods(['GET', 'POST'])
def lot_status(request, pk):
    return _change_status(
        request, MaterialLot, pk, 'lot',
        'Партии в карантине и израсходованные партии нельзя выбирать '
        'для новых опубликованных планов.',
    )
