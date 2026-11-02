"""Fixed template versions and composition editors, using application roles."""
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.shortcuts import get_object_or_404, redirect, render

from .search import filter_search
from .models import ChangeEvent, Material, MaterialLot, RecipeTemplate, RecipeVersion
from .template_forms import (RecipeTemplateForm,
                             TemplateComponentFormSet, TemplateFillingFormSet)
from .views import _initial_recipe, _json, _page, author_required, site_required


def template_snapshot(template):
    data = {'name': template.name, 'version': template.version, 'description': template.description}
    data.update(definition=template.definition, mixture_type=template.mixture_type_id,
                batch_mass_g=str(template.batch_mass_g))
    return data


def _event(request, template, summary):
    ChangeEvent.objects.create(employee=request.employee, entity_type='recipe_template', entity_id=template.pk,
                               action='created', summary=summary, after=_json(template_snapshot(template)))


def _next_version(model, name):
    return (model.objects.filter(name=name).aggregate(value=Max('version'))['value'] or 0) + 1


def _save_template(request, form, template, summary):
    """Concurrent authors may choose the same version after form validation."""
    try:
        with transaction.atomic():
            template.save()
            _event(request, template, summary)
    except ValidationError as error:
        form.add_error(None, ValidationError(error.messages))
    except IntegrityError:
        form.add_error(None, 'Эта версия шаблона уже сохранена другим автором. Укажите другой номер версии и повторите сохранение.')
    else:
        return True
    return False


def _serialize_rows(formset):
    rows = []
    for form in formset:
        data = form.cleaned_data
        if not data or data.get('unused') or data.get('DELETE'):
            continue
        allowed = {'group_label', 'material', 'specified_lot', 'quantity_value', 'quantity_basis', 'completeness'} if formset.prefix == 'components' else {'group_label', 'material', 'specified_lot', 'mass_g'}
        rows.append({key: value.pk if hasattr(value, 'pk') else str(value) if isinstance(value, Decimal) else value
                     for key, value in data.items() if key in allowed})
    return rows


def recipe_definition(recipe):
    components, filling = _initial_recipe(recipe)
    component_keys = {'group_label', 'quantity_basis', 'completeness', 'material', 'specified_lot', 'quantity_value'}
    filling_keys = {'group_label', 'material', 'specified_lot', 'mass_g'}
    return _json({'components': [{key: value for key, value in row.items() if key in component_keys} for row in components],
                  'filling': [{key: value for key, value in row.items() if key in filling_keys} for row in filling]})


def definition_display(definition):
    """Add readable references without mutating the fixed stored definition."""
    groups = {}
    for row in definition.get('components', []):
        label = row.get('group_label', 'A')
        group = groups.setdefault(label, {'label': label, 'basis': row.get('quantity_basis'), 'completeness': row.get('completeness', 'unknown'), 'components': []})
        material = Material.objects.filter(pk=row.get('material')).first() if row.get('material') else None
        lot = MaterialLot.objects.select_related('material').filter(pk=row.get('specified_lot')).first() if row.get('specified_lot') else None
        group['components'].append({**row, 'quantity_value': Decimal(row['quantity_value']) if row.get('quantity_value') not in (None, '') else None,
                                    'material_label': str(lot.material if lot else material) if lot or material else 'Не указан',
                                    'lot_label': lot.lot_code if lot else None})
    filling = []
    for row in definition.get('filling', []):
        material = Material.objects.filter(pk=row.get('material')).first() if row.get('material') else None
        lot = MaterialLot.objects.select_related('material').filter(pk=row.get('specified_lot')).first() if row.get('specified_lot') else None
        filling.append({**row, 'mass_g': Decimal(row['mass_g']) if row.get('mass_g') not in (None, '') else None,
                        'label': ('Группа ' + row['group_label']) if row.get('group_label') else str(lot or material or 'Не указан')})
    return list(groups.values()), filling


@site_required
def recipe_template_list(request):
    templates = RecipeTemplate.objects.select_related(
        'mixture_type', 'created_by',
    ).all()
    q = request.GET.get('q', '').strip()
    if q:
        templates = filter_search(templates, (
            'name', 'description', 'mixture_type__title',
        ), q)
    page = _page(request, templates.order_by('name', '-version', '-pk'))
    return render(request, 'lab/templates/recipe_list.html', {
        'templates': page, 'page_obj': page, 'q': q,
    })


@site_required
def recipe_template_detail(request, pk):
    template = get_object_or_404(RecipeTemplate.objects.select_related('mixture_type', 'created_by'), pk=pk)
    groups, filling = definition_display(template.definition)
    return render(request, 'lab/templates/recipe_detail.html', {'template': template, 'groups': groups, 'filling': filling,
        'events': ChangeEvent.objects.filter(entity_type='recipe_template', entity_id=pk).select_related('employee')})


@author_required
def recipe_template_create(request, source_pk=None, recipe_pk=None):
    source = get_object_or_404(RecipeTemplate, pk=source_pk) if source_pk else None
    recipe = get_object_or_404(RecipeVersion, pk=recipe_pk) if recipe_pk else None
    initial = {'version': 1, 'batch_mass_g': Decimal('1000')}
    definition = {'components': [], 'filling': []}
    if source:
        initial.update(name=source.name, version=_next_version(RecipeTemplate, source.name), description=source.description,
                       mixture_type=source.mixture_type_id,
                       batch_mass_g=source.batch_mass_g)
        definition = source.definition
    elif recipe:
        initial.update(name=recipe.title or ('Шаблон ' + recipe.version_label), mixture_type=recipe.mixture_type_id,
                       batch_mass_g=recipe.batch_mass_g)
        definition = recipe_definition(recipe)
    form = RecipeTemplateForm(request.POST or None, initial=initial)
    components = TemplateComponentFormSet(request.POST or None, prefix='components', initial=definition.get('components', []))
    filling = TemplateFillingFormSet(request.POST or None, prefix='filling', initial=definition.get('filling', []))
    if request.method == 'POST':
        valid = form.is_valid() & components.is_valid() & filling.is_valid()
        if valid:
            definition = {'components': _serialize_rows(components), 'filling': _serialize_rows(filling)}
            if not definition['components']:
                form.add_error(None, 'Добавьте хотя бы один компонент шаблона.')
            if not form.errors:
                template = form.save(commit=False)
                template.definition = definition
                template.created_by = request.employee
                try:
                    template.full_clean()
                except ValidationError as error:
                    form.add_error(None, ValidationError(error.messages))
                else:
                    if _save_template(request, form, template, f'Создан шаблон рецептуры «{template.name}», версия {template.version}'):
                        messages.success(request, 'Версия шаблона рецептуры сохранена.', extra_tags='form-draft-saved')
                        return redirect('recipe_template_detail', pk=template.pk)
    return render(request, 'lab/templates/recipe_form.html', {'form': form, 'component_formset': components, 'filling_formset': filling,
        'source': source, 'recipe': recipe, 'title': 'Новая версия шаблона рецептуры' if source else 'Новый шаблон рецептуры'})
