"""Measurement group directory and experiment entry workflows."""
from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Max
from django.forms.models import model_to_dict
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .catalog_views import audit, events, form_page, page
from .measurement_forms import (
    GroupSelectionForm, GroupValuesForm, MeasurementGroupForm,
)
from .models import MeasurementGroup, Observation, Result, TestRun, TestSession
from .search import filter_search
from .views import _audit, _experiment_redirect, author_required, site_required


@site_required
def group_list(request):
    groups = MeasurementGroup.objects.annotate(
        metric_count=Count('metrics', distinct=True),
    ).order_by('name', 'pk')
    q = request.GET.get('q', '').strip()
    if q:
        groups = filter_search(groups, ('name', 'description', 'metrics__label'), q)
    result = page(request, groups)
    return render(request, 'lab/measurements/group_list.html', {
        'groups': result, 'page_obj': result, 'q': q,
    })


@site_required
def group_detail(request, pk):
    group = get_object_or_404(MeasurementGroup, pk=pk)
    return render(request, 'lab/measurements/group_detail.html', {
        'group': group, 'metrics': group.metrics.all(),
        'events': events('measurement_group', pk),
    })


def _group_snapshot(group):
    return {
        'code': group.code, 'name': group.name, 'description': group.description,
        'metrics': list(group.metrics.values_list('pk', flat=True)),
    }


@author_required
def group_save(request, pk=None):
    group = get_object_or_404(MeasurementGroup, pk=pk) if pk else None
    form = MeasurementGroupForm(request.POST or None, instance=group)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            locked = (MeasurementGroup.objects.select_for_update().get(pk=pk)
                      if pk else None)
            before = _group_snapshot(locked) if locked else {}
            group = form.save()
            audit(request, group, 'measurement_group', before,
                  f'Сохранена группа измерений «{group.name}»',
                  after=_group_snapshot(group))
        messages.success(request, 'Группа измерений сохранена.')
        return redirect('measurement_group_detail', pk=group.pk)
    cancel = reverse('measurement_group_detail', args=[pk]) if pk else reverse(
        'measurement_group_list',
    )
    return form_page(request, form,
                     'Редактировать группу измерений' if pk else 'Новая группа измерений',
                     cancel, 'Показатели этой группы будут доступны в экспериментах.')


def _value_data(metric, value, reason=''):
    missing = value in (None, '')
    numeric = metric.value_kind == 'numeric'
    return {
        'numeric_value': value if numeric and not missing else None,
        'text_value': value if not numeric and not missing else None,
        'value_state': 'not_measured' if missing else 'present',
        'not_measured_reason': reason,
    }


def _save_values(run, form):
    for name, metric, result, locked in form.entries:
        value = form.cleaned_data.get(name)
        if locked:
            continue
        values = _value_data(
            metric, value, form.cleaned_data.get(name + '_reason', ''),
        )
        if result:
            Result.objects.filter(pk=result.pk).update(**values)
        else:
            Result.objects.create(
                test_run=run, metric=metric,
                source_cell=form.cleaned_data.get('source_cell'), **values,
            )
    if form.cleaned_data.get('observation'):
        sequence = run.observations.aggregate(n=Max('sequence_no'))['n'] or 0
        Observation.objects.create(
            test_run=run, sequence_no=sequence + 1,
            text_value=form.cleaned_data['observation'],
            source_cell=form.cleaned_data.get('source_cell'),
        )


def _run_snapshot(run):
    return {
        'run': model_to_dict(run),
        'results': [model_to_dict(row) for row in run.results.all()],
        'observations': [model_to_dict(row) for row in run.observations.all()],
    }


def _save_group(request, card, group, run):
    with transaction.atomic():
        card = TestSession.objects.select_for_update().get(pk=card.pk)
        if card.status == 'completed':
            return HttpResponseForbidden('Эксперимент завершён и доступен для просмотра.')
        if run:
            run = TestRun.objects.select_for_update().get(pk=run.pk)
        form = GroupValuesForm(request.POST, group=group, card=card, run=run)
        if not form.is_valid():
            return _entry_page(request, card, group, form, run)
        before = _run_snapshot(run) if run else {}
        if run is None:
            run = TestRun.objects.create(
                session=card, measurement_group=group, scope=group.name,
                method_code=form.cleaned_data.get('method_code') or None,
            )
        _save_values(run, form)
        _audit(request, card, 'measurement_group_updated' if before else 'measurement_group_added',
               f'Сохранена группа измерений «{group.name}»',
               before=before, after=_run_snapshot(run))
    messages.success(request, 'Группа измерений сохранена.', extra_tags='form-draft-saved')
    return _experiment_redirect(card, f'measurement-run-{run.pk}')


def _entry_page(request, card, group, form, run=None, selection=None):
    return render(request, 'lab/measurements/entry_form.html', {
        'card': card, 'group': group, 'form': form, 'run': run,
        'selection': selection,
        'title': 'Заполнить группу измерений' if run else 'Добавить группу измерений',
    })


@author_required
def group_add(request, pk):
    card = get_object_or_404(TestSession, pk=pk)
    if card.status == 'completed':
        return HttpResponseForbidden('Эксперимент завершён и доступен для просмотра.')
    selection = GroupSelectionForm(request.POST if request.method == 'POST' else request.GET or None)
    if not selection.is_valid():
        return _entry_page(request, card, None, None, selection=selection)
    group = selection.cleaned_data['measurement_group']
    if request.method == 'POST':
        return _save_group(request, card, group, None)
    form = GroupValuesForm(group=group, card=card)
    return _entry_page(request, card, group, form)


@author_required
def group_edit(request, pk, run_pk):
    card = get_object_or_404(TestSession, pk=pk)
    if card.status == 'completed':
        return HttpResponseForbidden('Эксперимент завершён и доступен для просмотра.')
    run = get_object_or_404(TestRun, pk=run_pk, session=card)
    if not run.measurement_group_id:
        return HttpResponseForbidden('Обновите базу данных: группа ещё не перенесена.')
    group = run.measurement_group
    if request.method == 'POST':
        return _save_group(request, card, group, run)
    return _entry_page(request, card, group,
                       GroupValuesForm(group=group, card=card, run=run), run)
