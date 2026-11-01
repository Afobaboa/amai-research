"""Material, lot and primary-source workflows, separate from recipe forms."""
import hashlib
import json
from pathlib import PureWindowsPath

from django.contrib import messages
from django.core.paginator import Paginator
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import Q
from django.forms.models import model_to_dict
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .catalog_forms import MaterialForm, MaterialLotForm, MetricForm, MixtureTypeForm, SourceCellForm, SourceForm, SourceSheetForm
from .models import ChangeEvent, Material, MaterialLot, Metric, MixtureType, RecipeVersion, SourceFile, SourceSheet, TestSession
from .views import author_required, site_required


def snapshot(instance):
    data = model_to_dict(instance)
    for key, value in tuple(data.items()):
        if hasattr(value, 'name'):
            data[key] = value.name
    return json.loads(json.dumps(data, cls=DjangoJSONEncoder))


def audit(request, instance, kind, before, summary, after=None):
    ChangeEvent.objects.create(employee=request.employee, entity_type=kind, entity_id=instance.pk,
                               action='updated' if before else 'created', summary=summary[:255],
                               before=before, after=snapshot(instance) if after is None else after)


def events(kind, pk):
    return ChangeEvent.objects.filter(entity_type=kind, entity_id=pk).select_related('employee')


def page(request, queryset):
    return Paginator(queryset, 25).get_page(request.GET.get('page'))


def form_page(request, form, title, cancel_url, description=''):
    return render(request, 'lab/catalog/form.html', {'form': form, 'title': title,
                  'cancel_url': cancel_url, 'description': description})


@site_required
def material_list(request):
    materials = Material.objects.all()
    q = request.GET.get('q', '').strip()
    if q:
        materials = materials.filter(Q(name__icontains=q) | Q(category__icontains=q) | Q(supplier__icontains=q))
    result = page(request, materials)
    return render(request, 'lab/catalog/material_list.html', {'materials': result, 'page_obj': result, 'q': q})


@site_required
def material_detail(request, pk):
    material = get_object_or_404(Material, pk=pk)
    recipes = RecipeVersion.objects.filter(Q(groups__components__unbatched_material=material) |
                                           Q(groups__components__specified_lot__material=material) |
                                           Q(filling_plan__unbatched_material=material) |
                                           Q(filling_plan__specified_lot__material=material)).distinct()
    return render(request, 'lab/catalog/material_detail.html', {'material': material,
                  'lots': material.lots.select_related('source'), 'recipes': recipes,
                  'events': events('material', pk), 'today': timezone.localdate()})


@author_required
def material_save(request, pk=None):
    material = get_object_or_404(Material, pk=pk) if pk else None
    form = MaterialForm(request.POST or None, instance=material)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            before = snapshot(Material.objects.select_for_update().get(pk=pk)) if pk else {}
            material = form.save()
            audit(request, material, 'material', before, f'Сохранён материал {material.name}')
        messages.success(request, 'Материал сохранён. Новые расчёты используют актуальные свойства; сохранённые снимки не меняются.')
        return redirect('material_detail', pk=material.pk)
    return form_page(request, form, 'Редактирование материала' if pk else 'Новый материал',
                     reverse('material_detail', args=[pk]) if pk else reverse('material_list'),
                     'Справочные свойства материала и данные поставщика.')


@site_required
def lot_list(request):
    lots = MaterialLot.objects.select_related('material', 'source')
    q, status = request.GET.get('q', '').strip(), request.GET.get('status', '')
    if q:
        lots = lots.filter(Q(lot_code__icontains=q) | Q(material__name__icontains=q) | Q(supplier__icontains=q))
    if status in {'active', 'quarantine', 'exhausted'}:
        lots = lots.filter(status=status)
    material = request.GET.get('material', '')
    if material.isdigit():
        lots = lots.filter(material_id=material)
    result = page(request, lots.order_by('material__name', '-pk'))
    return render(request, 'lab/catalog/lot_list.html', {'lots': result, 'page_obj': result, 'q': q,
                  'status': status, 'today': timezone.localdate()})


@site_required
def lot_detail(request, pk):
    lot = get_object_or_404(MaterialLot.objects.select_related('material', 'source'), pk=pk)
    components = lot.recipe_components.select_related('group__recipe_version')
    filling = lot.filling_lines.select_related('recipe_version')
    recipes = RecipeVersion.objects.filter(Q(groups__components__specified_lot=lot) |
                                           Q(filling_plan__specified_lot=lot)).distinct()
    cards = TestSession.objects.filter(recipe_matches__recipe_version__in=recipes,
                                       recipe_matches__status='confirmed').distinct()
    return render(request, 'lab/catalog/lot_detail.html', {'lot': lot, 'components': components,
                  'filling_lines': filling, 'recipes': recipes, 'cards': cards,
                  'events': events('lot', pk), 'expired': bool(lot.expires_on and lot.expires_on < timezone.localdate())})


@author_required
def lot_save(request, pk=None):
    lot = get_object_or_404(MaterialLot, pk=pk) if pk else None
    initial = {}
    if request.GET.get('material', '').isdigit():
        initial['material'] = request.GET['material']
    form = MaterialLotForm(request.POST or None, instance=lot, initial=initial)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            before = snapshot(MaterialLot.objects.select_for_update().get(pk=pk)) if pk else {}
            lot = form.save()
            audit(request, lot, 'lot', before, f'Сохранена партия {lot.lot_code} · {lot.material.name}')
        messages.success(request, 'Партия сохранена.')
        return redirect('lot_detail', pk=lot.pk)
    return form_page(request, form, 'Редактирование партии' if pk else 'Новая партия',
                     reverse('lot_detail', args=[pk]) if pk else reverse('lot_list'),
                     'Партия связывает материал, паспорт, свойства и историю использования в планах рецептур.')


@site_required
def source_list(request):
    sources = SourceFile.objects.select_related('created_by').prefetch_related('sheets')
    q, kind = request.GET.get('q', '').strip(), request.GET.get('kind', '')
    if q:
        sources = sources.filter(Q(title__icontains=q) | Q(original_path__icontains=q) | Q(notes__icontains=q))
    if kind in {'file', 'link', 'note'}:
        sources = sources.filter(kind=kind)
    result = page(request, sources.order_by('-created_at', '-pk'))
    return render(request, 'lab/catalog/source_list.html', {'sources': result, 'page_obj': result,
                  'q': q, 'kind': kind})


@site_required
def source_detail(request, pk):
    source = get_object_or_404(SourceFile.objects.select_related('created_by'), pk=pk)
    return render(request, 'lab/catalog/source_detail.html', {'source': source,
                  'sheets': source.sheets.prefetch_related('cells'),
                  'recipes': RecipeVersion.objects.filter(source_sheet__file=source),
                  'cards': TestSession.objects.filter(source_sheet__file=source),
                  'lots': source.lots.select_related('material'), 'events': events('source', pk)})


@author_required
def source_save(request, pk=None):
    source = get_object_or_404(SourceFile, pk=pk) if pk else None
    form = SourceForm(request.POST or None, request.FILES or None, instance=source)
    if request.method == 'POST' and form.is_valid():
        saved_attachment = None
        try:
            with transaction.atomic():
                before = snapshot(SourceFile.objects.select_for_update().get(pk=pk)) if pk else {}
                source = form.save(commit=False)
                upload = form.cleaned_data.get('upload')
                if upload:
                    digest = hashlib.sha256()
                    for chunk in upload.chunks():
                        digest.update(chunk)
                    upload.seek(0)
                    source.sha256 = digest.hexdigest()
                    source.original_path = PureWindowsPath(upload.name).name
                    source.attachment.save(upload.name, upload, save=False)
                    saved_attachment = (source.attachment.storage, source.attachment.name)
                elif not source.original_path:
                    source.original_path = source.url or source.title
                if not pk:
                    source.created_by = request.employee
                source.save()
                audit(request, source, 'source', before, f'Сохранён источник {source.title or source.original_path}')
        except Exception:
            if saved_attachment:
                saved_attachment[0].delete(saved_attachment[1])
            raise
        messages.success(request, 'Источник сохранён. Содержимое файла не импортировалось.')
        return redirect('source_detail', pk=source.pk)
    return form_page(request, form, 'Редактирование источника' if pk else 'Новый источник',
                     reverse('source_detail', args=[pk]) if pk else reverse('source_list'),
                     'Приложите документ, сохраните ссылку или запишите источник вручную.')


@site_required
def source_download(request, pk):
    source = get_object_or_404(SourceFile, pk=pk)
    if not source.attachment:
        raise Http404('Вложение отсутствует.')
    try:
        response = FileResponse(source.attachment.open('rb'), as_attachment=True,
                                filename=PureWindowsPath(source.original_path).name)
    except FileNotFoundError:
        raise Http404('Файл источника не найден.')
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@author_required
def source_sheet_create(request, pk):
    source = get_object_or_404(SourceFile, pk=pk)
    form = SourceSheetForm(request.POST or None, source=source)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            sheet = form.save()
            audit(request, source, 'source', {}, f'В источник добавлен раздел «{sheet.name}»',
                  after={'sheet': snapshot(sheet)})
        messages.success(request, 'Лист / раздел добавлен. Теперь его можно выбрать в рецептуре или карточке.')
        return redirect('source_detail', pk=pk)
    return form_page(request, form, 'Новый раздел источника', reverse('source_detail', args=[pk]), source.title or source.original_path)


@author_required
def source_cell_create(request, pk, sheet_pk):
    source = get_object_or_404(SourceFile, pk=pk)
    sheet = get_object_or_404(SourceSheet, pk=sheet_pk, file=source)
    form = SourceCellForm(request.POST or None, sheet=sheet)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            cell = form.save()
            audit(request, source, 'source', {}, f'Добавлен фрагмент {sheet.name} · {cell.address}',
                  after={'cell': snapshot(cell)})
        messages.success(request, 'Фрагмент источника сохранён дословно.')
        return redirect('source_detail', pk=pk)
    return form_page(request, form, 'Новый фрагмент источника', reverse('source_detail', args=[pk]),
                     f'{source.title or source.original_path} · {sheet.name}')


@site_required
def metric_list(request):
    metrics = Metric.objects.all()
    q = request.GET.get('q', '').strip()
    if q:
        metrics = metrics.filter(Q(code__icontains=q) | Q(label__icontains=q))
    result = page(request, metrics)
    return render(request, 'lab/catalog/metric_list.html', {'metrics': result, 'page_obj': result, 'q': q})


@author_required
def metric_save(request, pk=None):
    metric = get_object_or_404(Metric, pk=pk) if pk else None
    form = MetricForm(request.POST or None, instance=metric)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            before = snapshot(Metric.objects.select_for_update().get(pk=pk)) if pk else {}
            metric = form.save()
            audit(request, metric, 'metric', before, f'Сохранён показатель {metric.label}')
        messages.success(request, 'Показатель сохранён.')
        return redirect('metric_list')
    return form_page(request, form, 'Редактирование показателя' if pk else 'Новый показатель',
                     reverse('metric_list'), 'Единица и тип показателя используются в условиях и результатах испытаний.')


@site_required
def mixture_list(request):
    mixtures = MixtureType.objects.all()
    q = request.GET.get('q', '').strip()
    if q:
        mixtures = mixtures.filter(Q(code__icontains=q) | Q(title__icontains=q))
    result = page(request, mixtures)
    return render(request, 'lab/catalog/mixture_list.html', {'mixtures': result, 'page_obj': result, 'q': q})


@site_required
def mixture_detail(request, pk):
    mixture = get_object_or_404(MixtureType, pk=pk)
    return render(request, 'lab/catalog/mixture_detail.html', {'mixture': mixture,
                  'recipes': mixture.recipes.all(), 'events': events('mixture_type', pk)})


@author_required
def mixture_save(request, pk=None):
    mixture = get_object_or_404(MixtureType, pk=pk) if pk else None
    form = MixtureTypeForm(request.POST or None, instance=mixture)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            before = snapshot(MixtureType.objects.select_for_update().get(pk=pk)) if pk else {}
            mixture = form.save()
            audit(request, mixture, 'mixture_type', before, f'Сохранён тип смеси {mixture}')
        messages.success(request, 'Тип смеси сохранён.')
        return redirect('mixture_detail', pk=mixture.pk)
    return form_page(request, form, 'Редактирование типа смеси' if pk else 'Новый тип смеси',
                     reverse('mixture_detail', args=[pk]) if pk else reverse('mixture_list'),
                     'Тип объединяет версии рецептур одной продуктовой серии.')
