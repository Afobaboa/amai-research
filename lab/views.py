import json
from functools import wraps
from decimal import Decimal
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.hashers import check_password
from django.core.paginator import Paginator
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import Max, Q
from django.forms.models import model_to_dict
from django.http import HttpResponseForbidden, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.utils import timezone

from .search import filter_search
from .forms import (
    ComponentFormSet, ConditioningForm, EmployeeForm,
    FillingFormSet, ObservationForm, PasswordForm, RecipeForm,
    TestSessionForm, RecipePropertyFormSet,
)
from .models import (
    ChangeEvent, ConditioningEvent, EmployeeProfile, FillingPlanLine, Material,
    Metric, Observation, RecipeComponent, RecipeGroup, RecipeMatch, RecipeVersion,
    Result, SourceFile, TestRun, TestSession, RecipeProperty,
    RecipeTemplate, CalculationRecord, MaterialLot,
)


def site_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.session.get("site_authenticated"):
            return redirect(reverse("login") + "?" + urlencode({"next": request.get_full_path()}))
        request.employee = EmployeeProfile.objects.filter(pk=request.session.get("employee_id"), is_active=True).first()
        if request.employee is None:
            return redirect("choose_employee")
        return view(request, *args, **kwargs)
    return wrapped


def author_required(view):
    @site_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if request.employee.role != "author":
            return HttpResponseForbidden("Ваша роль — читатель. Создавать и изменять записи может автор.")
        return view(request, *args, **kwargs)
    return wrapped


def _json(value):
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))


def _audit(request, instance, action, summary, before=None, after=None):
    return ChangeEvent.objects.create(
        employee=request.employee,
        entity_type="recipe" if isinstance(instance, RecipeVersion) else "test",
        entity_id=instance.pk, action=action, summary=summary,
        before=_json(before or {}), after=_json(after or {}),
    )


def _recipe_snapshot(recipe):
    data = model_to_dict(recipe)
    data["groups"] = [{**model_to_dict(group), "components": [model_to_dict(row) for row in group.components.all()]} for group in recipe.groups.all()]
    data["filling_plan"] = [model_to_dict(row) for row in recipe.filling_plan.all()]
    data["properties"] = [model_to_dict(row) for row in _recipe_properties(recipe)]
    data['calculation_ids'] = list(recipe.calculations.values_list('pk', flat=True))
    return _json(data)


def _card_snapshot(card):
    data = model_to_dict(card)
    data["runs"] = [model_to_dict(row) for row in card.runs.all()]
    data["recipe_matches"] = [model_to_dict(row) for row in card.recipe_matches.all()]
    return _json(data)


def _page(request, queryset):
    return Paginator(queryset, 20).get_page(request.GET.get("page"))


def _events_for(kind, pk):
    return ChangeEvent.objects.filter(entity_type=kind, entity_id=pk).select_related("employee")


def login_view(request):
    if request.session.get("site_authenticated"):
        return redirect("choose_employee" if not request.session.get("employee_id") else "dashboard")
    form = PasswordForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        password_hash = getattr(settings, "SITE_PASSWORD_HASH", "")
        if password_hash and check_password(form.cleaned_data["password"], password_hash):
            request.session.cycle_key()
            request.session["site_authenticated"] = True
            return redirect("choose_employee")
        form.add_error("password", "Неверный пароль.")
    return render(request, "lab/login.html", {"form": form})


def choose_employee(request):
    if not request.session.get("site_authenticated"):
        return redirect("login")
    form = EmployeeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        request.session.cycle_key()
        request.session["employee_id"] = form.cleaned_data["employee"].pk
        return redirect("dashboard")
    return render(request, "lab/choose_employee.html", {"form": form, "employees": form.fields["employee"].queryset})


@require_POST
def logout_view(request):
    request.session.flush()
    return redirect("login")


@site_required
def dashboard(request):
    cards = TestSession.objects.select_related("created_by").prefetch_related("recipe_matches__recipe_version")
    return render(request, "lab/dashboard.html", {
        "recipes_count": RecipeVersion.objects.count(), "cards_count": cards.count(),
        "active_count": cards.filter(status="active").count(), "completed_count": cards.filter(status="completed").count(),
        "recent_cards": cards.order_by("-created_at", "-pk")[:6],
        "recent_events": ChangeEvent.objects.select_related("employee").order_by("-created_at", "-pk")[:7],
        "recent_recipes": RecipeVersion.objects.select_related("mixture_type").order_by("-created_at", "-pk")[:5],
    })


@site_required
def recipe_list(request):
    recipes = RecipeVersion.objects.select_related("mixture_type", "created_by")
    q, status = request.GET.get("q", "").strip(), request.GET.get("status", "")
    if q:
        recipes = filter_search(recipes, (
            "version_label", "title", "mixture_type__code", "notes",
        ), q)
    if status in {"draft", "published", "archived"}:
        recipes = recipes.filter(status=status)
    page = _page(request, recipes.order_by("-created_at", "-pk"))
    return render(request, "lab/recipe_list.html", {
        "recipes": page, "page_obj": page, "q": q, "status": status,
    })


@site_required
def recipe_detail(request, pk):
    from .calculations import calculate_recipe
    recipe = get_object_or_404(RecipeVersion.objects.select_related("mixture_type", "created_by", "source_sheet__file"), pk=pk)
    if recipe.formulation:
        from .recipe_editor_views import formulation_detail
        return formulation_detail(request, recipe)
    records = recipe.calculations.select_related('created_by')
    saved_calculation = None
    if recipe.status != 'draft':
        saved_calculation = records.filter(outputs__purpose='publication').order_by('created_at', 'pk').first() or records.order_by('created_at', 'pk').first()
    return render(request, "lab/recipe_detail.html", {
        "recipe": recipe,
        "groups": recipe.groups.prefetch_related("components__unbatched_material", "components__specified_lot__material"),
        "filling_lines": recipe.filling_plan.select_related("input_group", "unbatched_material", "specified_lot__material"),
        "matches": recipe.session_matches.select_related("session"),
        "events": _events_for("recipe", pk),
        'properties': _recipe_properties(recipe),
        'calculation': saved_calculation.outputs if saved_calculation else calculate_recipe(recipe),
        'saved_calculation': saved_calculation, 'calculation_records': records,
        'can_edit_recipe': recipe.status == 'draft' and not recipe.session_matches.exists(),
    })


def _recipe_properties(recipe):
    return RecipeProperty.objects.filter(Q(recipe_version=recipe) | Q(group__recipe_version=recipe) | Q(component__group__recipe_version=recipe) | Q(filling_plan_line__recipe_version=recipe)).select_related('group', 'component__group', 'filling_plan_line', 'source_cell__sheet')


def _initial_recipe(source):
    components, filling = [], []
    if source:
        for group in source.groups.all():
            for row in group.components.all():
                components.append({"group_label": group.label, "quantity_basis": group.quantity_basis, 'completeness': group.completeness, "material": row.unbatched_material_id, "specified_lot": row.specified_lot_id, "quantity_value": row.quantity_value, 'name_cell': row.name_cell_id, 'quantity_cell': row.quantity_cell_id})
        for row in source.filling_plan.select_related("input_group"):
            filling.append({"group_label": row.input_group.label if row.input_group else "", "material": row.unbatched_material_id, "specified_lot": row.specified_lot_id, "mass_g": row.mass_g, 'mass_status': row.mass_status, 'label_cell': row.label_cell_id, 'mass_cell': row.mass_cell_id})
    return components, filling


def _initial_properties(recipe):
    if not recipe:
        return []
    filling_positions = {pk: i for i, pk in enumerate(recipe.filling_plan.values_list('pk', flat=True), 1)}
    rows = []
    for prop in _recipe_properties(recipe):
        target, label, position = 'recipe', '', None
        if prop.group_id:
            target, label = 'group', prop.group.label
        elif prop.component_id:
            target, label, position = 'component', prop.component.group.label, prop.component.position
        elif prop.filling_plan_line_id:
            target, position = 'filling', filling_positions[prop.filling_plan_line_id]
        rows.append({
            'target': target, 'group_label': label, 'position': position,
            'property_code': prop.property_code, 'value': prop.value,
            'unit_code': prop.unit_code, 'source_cell': prop.source_cell,
            'origin': prop.origin, 'provenance_note': prop.provenance_note,
        })
    return rows


def _template_snapshot(template):
    return _json({'id': template.pk, 'name': template.name, 'version': template.version,
                  'definition': template.definition, 'mixture_type': template.mixture_type_id,
                  'batch_mass_g': template.batch_mass_g}) if template else {}


def _validate_plan(form, rows, fills, properties):
    groups, group_rows = _plan_groups(form, rows)
    for row in fills:
        if row.get('group_label') and row['group_label'] not in groups:
            form.add_error(None, f"В составе отсутствует группа "
                           f"{row['group_label']} из плана заправки.")
    _validate_property_targets(form, properties, group_rows, fills)
    _validate_source_cells(form, [*rows, *fills, *properties])
    _validate_group_completeness(form, groups, group_rows)
    _validate_publication(form, rows, fills)
    return groups


def _plan_groups(form, rows):
    groups, group_rows = {}, {}
    for row in rows:
        label = row['group_label']
        description = (row['quantity_basis'], row.get('completeness') or 'unknown')
        if label in groups and groups[label] != description:
            form.add_error(None, f'Для группы {label} задайте одинаковую базу '
                           'и полноту во всех строках.')
        groups[label] = description
        group_rows.setdefault(label, []).append(row)
    return groups, group_rows


def _validate_property_targets(form, properties, group_rows, fills):
    for row in properties:
        target = row['target']
        label, position = row.get('group_label'), row.get('position')
        if target in ('group', 'component') and label not in group_rows:
            message = f'не найдена группа {label}'
        elif target == 'component' and (
            not position or position > len(group_rows.get(label, []))
        ):
            message = f'не найден компонент №{position} в группе {label}'
        elif target == 'filling' and (not position or position > len(fills)):
            message = f'не найдена строка заправки №{position}'
        else:
            continue
        form.add_error(None, f"Для свойства {row['property_code']} {message}.")


def _validate_group_completeness(form, groups, group_rows):
    for label, values in group_rows.items():
        if groups[label][1] != 'complete':
            continue
        if any(row.get('quantity_value') is None for row in values):
            form.add_error(None, f'Группа {label} помечена полной: заполните '
                           'количества или выберите другую полноту.')
        elif groups[label][0] == 'mass_percent' and abs(sum(
            (row['quantity_value'] for row in values), Decimal(0),
        ) - Decimal(100)) > Decimal('0.001'):
            form.add_error(None, f'Полная группа {label} должна содержать '
                           '100 массовых процентов.')


def _validate_publication(form, rows, fills):
    if form.cleaned_data['status'] != 'published':
        return
    if not rows:
        form.add_error(None, 'Перед публикацией добавьте хотя бы один '
                       'компонент состава.')
    for row in [*rows, *fills]:
        lot = row.get('specified_lot')
        if lot and (lot.status != 'active' or (
            lot.expires_on and lot.expires_on < timezone.localdate()
        )):
            form.add_error(None, f'Партия {lot.lot_code} недоступна для нового '
                           'опубликованного плана: проверьте статус '
                           'и срок годности.')


def _validate_source_cells(form, rows):
    sheet = form.cleaned_data.get('source_sheet')
    if not sheet:
        return
    for row in rows:
        for key in ('source_cell', 'name_cell', 'quantity_cell',
                    'label_cell', 'mass_cell'):
            cell = row.get(key)
            if not cell or cell.sheet_id == sheet.pk:
                continue
            carried = (key == 'source_cell' and
                       row.get('origin') == 'carried_forward' and
                       bool(row.get('provenance_note')) and
                       cell.sheet.file_id == sheet.file_id)
            if not carried:
                form.add_error(None, f'Первичная ячейка {cell} относится к '
                               'другому листу. Выберите лист соответствующего '
                               'источника.')


def _save_properties(recipe, rows, groups, components, fills):
    for row in rows:
        targets = {'recipe_version': None, 'group': None, 'component': None, 'filling_plan_line': None}
        if row['target'] == 'recipe':
            targets['recipe_version'] = recipe
        elif row['target'] == 'group':
            targets['group'] = groups[row['group_label']]
        elif row['target'] == 'component':
            targets['component'] = components[(row['group_label'], row['position'])]
        else:
            targets['filling_plan_line'] = fills[row['position'] - 1]
        RecipeProperty.objects.create(
            **targets, property_code=row['property_code'], value=row['value'],
            unit_code=row.get('unit_code') or None, source_cell=row.get('source_cell'),
            origin=row.get('origin') or 'recorded',
            provenance_note=row.get('provenance_note', ''),
        )


@author_required
def recipe_create(request, source_pk=None, pk=None):
    from .recipe_editor_views import recipe_editor
    return recipe_editor(request, source_pk=source_pk, pk=pk)


@author_required
@require_POST
def recipe_preview(request):
    from .recipe_editor_views import recipe_preview as preview
    return preview(request)


@author_required
@require_POST
def recipe_calculate(request, pk):
    from .calculations import save_calculation
    recipe = get_object_or_404(RecipeVersion, pk=pk)
    with transaction.atomic():
        record = save_calculation(recipe, request.employee)
        _audit(request, recipe, 'calculation_saved', f'Сохранён расчёт {recipe.version_label}', after={'calculation_id': record.pk, 'template': record.outputs.get('template'), 'groups': record.outputs.get('groups')})
    messages.success(request, 'Расчёт сохранён со снимком цен, плотностей и партий. Изменение справочника его не перепишет.')
    return redirect('recipe_detail', pk=pk)


@site_required
def calculation_detail(request, pk):
    record = get_object_or_404(CalculationRecord.objects.select_related('recipe_version', 'created_by'), pk=pk)
    return render(request, 'lab/calculation_detail.html', {'record': record, 'calculation': record.outputs, 'calculation_inputs': record.inputs})


@site_required
def test_list(request):
    cards = TestSession.objects.select_related("created_by").prefetch_related(
        "recipe_matches__recipe_version",
    )
    q, status = request.GET.get("q", "").strip(), request.GET.get("status", "")
    if q:
        cards = filter_search(cards, (
            "identifier", "title", "notes",
            "recipe_matches__recipe_version__version_label",
        ), q)
    if status in {"draft", "active", "completed"}:
        cards = cards.filter(status=status)
    page = _page(request, cards.order_by("-created_at", "-pk"))
    return render(request, "lab/test_list.html", {
        "cards": page, "page_obj": page, "q": q, "status": status,
    })


def legacy_experiment_url(request, tail):
    tail = tail.replace('/results/new/', '/measurement-groups/new/')
    url = '/experiments/' + tail
    if request.META.get('QUERY_STRING'):
        url += '?' + request.META['QUERY_STRING']
    return redirect(url, permanent=True, preserve_request=True)


def _measurement_runs(card):
    runs = list(card.runs.select_related('measurement_group').prefetch_related(
        'results__metric', 'observations', 'measurement_group__metrics',
    ))
    for run in runs:
        seen = {result.metric_id for result in run.results.all()}
        run.missing_metrics = [metric for metric in run.measurement_group.metrics.all()
                               if metric.pk not in seen] if run.measurement_group_id else []
    return runs


@site_required
def test_detail(request, pk):
    card = get_object_or_404(TestSession.objects.select_related("created_by", "source_sheet__file"), pk=pk)
    return render(request, "lab/test_detail.html", {
        "card": card, "runs": _measurement_runs(card),
        "conditioning_events": card.conditioning_events.all(),
        "can_edit_card": card.status != "completed",
        "matches": card.recipe_matches.select_related("recipe_version__mixture_type"), "events": _events_for("test", pk),
    })


def _save_recipe_match(card, recipe):
    previous = card.recipe_matches.filter(status='confirmed').first()
    if previous and (not recipe or previous.recipe_version_id != recipe.pk):
        previous.status = 'rejected'
        previous.save(update_fields=['status'])
    if recipe:
        RecipeMatch.objects.update_or_create(
            session=card, recipe_version=recipe, defaults={'status': 'confirmed'},
        )


def _save_experiment(request, form, card):
    with transaction.atomic():
        locked = TestSession.objects.select_for_update().get(pk=card.pk) if card else None
        if locked and locked.status == 'completed':
            return HttpResponseForbidden('Эксперимент уже завершён.')
        before = _card_snapshot(locked) if locked else {}
        saved = form.save(commit=False)
        if not saved.pk:
            saved.created_by = request.employee
        saved.full_clean()
        saved.save()
        _save_recipe_match(saved, form.cleaned_data.get('recipe'))
        _audit(request, saved, 'updated' if card else 'created',
               f"Сохранён эксперимент {saved.identifier or saved.pk}",
               before=before, after=_card_snapshot(saved))
    messages.success(request, 'Эксперимент сохранён.', extra_tags='form-draft-saved')
    return redirect('test_detail', pk=saved.pk)


@author_required
def test_create(request, pk=None):
    card = get_object_or_404(TestSession, pk=pk) if pk else None
    if card and card.status == 'completed':
        return HttpResponseForbidden('Завершённый эксперимент доступен для просмотра.')
    match = card.recipe_matches.filter(status='confirmed').first() if card else None
    initial = {'recipe': match.recipe_version_id} if match else {}
    if not card and request.GET.get('recipe'):
        initial['recipe'] = request.GET['recipe']
    form = TestSessionForm(request.POST or None, instance=card, initial=initial)
    if request.method == 'POST' and form.is_valid():
        return _save_experiment(request, form, card)
    return render(request, 'lab/test_form.html', {
        'form': form, 'card': card,
        'title': 'Редактирование эксперимента' if card else 'Новый эксперимент',
    })


def _card_has_foreign_sources(card, sheet):
    return (Result.objects.filter(test_run__session=card, source_cell__isnull=False).exclude(source_cell__sheet=sheet).exists()
            or Observation.objects.filter(test_run__session=card, source_cell__isnull=False).exclude(source_cell__sheet=sheet).exists()
            or card.conditioning_events.filter(source_cell__isnull=False).exclude(source_cell__sheet=sheet).exists())


def _source_changed(card, cell):
    return bool(cell and card.source_sheet_id and cell.sheet_id != card.source_sheet_id)


@author_required
def observation_add(request, pk):
    card = get_object_or_404(TestSession, pk=pk)
    if card.status == "completed":
        return HttpResponseForbidden("Завершённый эксперимент доступен только для просмотра.")
    form = ObservationForm(
        request.POST or None, card=card, initial={"run": request.GET.get("run")},
    )
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            card = TestSession.objects.select_for_update().get(pk=pk)
            if card.status == "completed":
                return HttpResponseForbidden("Карточка уже завершена.")
            if _source_changed(card, form.cleaned_data.get('source_cell')):
                return HttpResponse('Источник карточки изменился. Обновите форму и проверьте первичную ячейку.', status=409)
            run = form.cleaned_data["run"]
            seq = (run.observations.aggregate(value=Max("sequence_no"))["value"] or 0) + 1
            observation = Observation.objects.create(test_run=run, sequence_no=seq, text_value=form.cleaned_data["text_value"], source_cell=form.cleaned_data.get('source_cell'))
            _audit(request, card, "observation_added", "Добавлено наблюдение", after={"observation": model_to_dict(observation)})
        messages.success(request, "Наблюдение сохранено дословно.")
        return redirect("test_detail", pk=pk)
    return render(request, "lab/observation_form.html", {"form": form, "card": card, "title": "Добавить наблюдение"})


@author_required
def conditioning_add(request, pk):
    card = get_object_or_404(TestSession, pk=pk)
    if card.status == "completed":
        return HttpResponseForbidden("Завершённый эксперимент доступен только для просмотра.")
    form = ConditioningForm(request.POST or None)
    if card.source_sheet_id:
        form.fields["source_cell"].widget.attrs[
            "data-source-sheet"
        ] = str(card.source_sheet_id)
    if request.method == 'POST' and form.is_valid():
        cell = form.cleaned_data.get('source_cell')
        if cell and card.source_sheet_id and cell.sheet_id != card.source_sheet_id:
            form.add_error('source_cell', 'Ячейка должна принадлежать листу источника карточки.')
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            card = TestSession.objects.select_for_update().get(pk=pk)
            if card.status == "completed":
                return HttpResponseForbidden("Карточка уже завершена.")
            event = form.save(commit=False)
            if _source_changed(card, event.source_cell):
                return HttpResponse('Источник карточки изменился. Обновите форму и проверьте первичную ячейку.', status=409)
            event.session = card
            event.step_no = (card.conditioning_events.aggregate(value=Max("step_no"))["value"] or 0) + 1
            event.full_clean()
            event.save()
            _audit(request, card, "conditioning_added", "Записана обработка", after={"conditioning": model_to_dict(event)})
        messages.success(request, "Обработка записана.")
        return redirect("test_detail", pk=pk)
    return render(request, "lab/conditioning_form.html", {"form": form, "card": card, "title": "Записать обработку"})


@site_required
def history(request):
    events = ChangeEvent.objects.select_related("employee").order_by(
        "-created_at", "-pk",
    )
    q, entity = request.GET.get("q", "").strip(), request.GET.get("entity", "")
    if q:
        events = filter_search(events, ("summary", "employee__full_name"), q)
    if entity in {
        'recipe', 'test', 'source', 'material', 'material_category', 'lot',
        'metric', 'mixture_type', 'recipe_template', 'calculation_template',
    }:
        events = events.filter(entity_type=entity)
    page = _page(request, events)
    return render(request, "lab/history.html", {
        "events": page, "page_obj": page, "q": q, "entity": entity,
    })


@site_required
def sources(request):
    return render(request, "lab/sources.html", {"sources": SourceFile.objects.prefetch_related("sheets"), "materials": Material.objects.all(), "metrics": Metric.objects.all()})


@site_required
def source_cell_choices(request):
    from .models import SourceCell
    sheet = request.GET.get('sheet', '')
    if not sheet.isdigit():
        return JsonResponse({'cells': []})
    cells = SourceCell.objects.filter(sheet_id=sheet).order_by('pk')
    return JsonResponse({'cells': [
        {'id': cell.pk, 'label': f'{cell.sheet.name}!{cell.address}'}
        for cell in cells.select_related('sheet')[:1000]
    ]})
