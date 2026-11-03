from decimal import Decimal, InvalidOperation, localcontext
from django import template
from django.urls import reverse
from ..ui_text import FIELD_LABELS as LABELS
from ..ui_text import PROPERTY_LABELS, UNIT_LABELS
from .static_assets import versioned_static


register = template.Library()
register.simple_tag(versioned_static)


@register.filter
def calculation_currency(row):
    # Saved calculations created before explicit currencies used RUB.
    return row.get('currency_symbol', '₽')


@register.filter
def property_label(value):
    return PROPERTY_LABELS.get(value, value)


@register.filter
def unit_label(value):
    return UNIT_LABELS.get(value, value or '—')

@register.filter
def display_value(value):
    if value is None or value == '':
        return '—'
    if isinstance(value, Decimal):
        return format(value, 'f').rstrip('0').rstrip('.').replace('.', ',') if value % 1 else str(int(value))
    return str(value)


@register.filter
def decimal_display(value, places=4):
    if value is None or value == '':
        return '—'
    try:
        with localcontext() as context:
            context.prec = 60
            number = Decimal(str(value))
            if not number.is_finite():
                return '—'
            return display_value(number.quantize(Decimal(1).scaleb(-int(places))))
    except (InvalidOperation, ValueError, TypeError):
        return display_value(value)

@register.filter
def dict_items(value):
    return value.items() if isinstance(value, dict) else []



VALUES = {'draft': 'Черновик', 'active': 'В работе', 'completed': 'Завершено',
          'published': 'Опубликована', 'archived': 'В архиве', 'confirmed': 'Подтверждена',
          'candidate': 'Кандидат', 'rejected': 'Отклонена', 'mass_percent': 'Массовые доли, %',
          'weight_parts': 'Массовые части', 'unknown': 'Не установлено', 'complete': 'Полный состав',
          'partial': 'Частичный состав', 'recorded': 'Записано автором', 'present': 'Записано',
          'calculated': 'Рассчитано', 'blend': 'Смесь', 'can': 'Баллон', 'specimen': 'Испытуемый материал',
          'aging': 'Выдержка', 'conditioning': 'Кондиционирование'}
# Keep retired codes readable in existing history and calculation snapshots.
VALUES.update({'active': 'Активна / в работе', 'quarantine': 'Карантин', 'exhausted': 'Закрыта / израсходована',
               'file': 'Файл', 'link': 'Ссылка', 'note': 'Запись', 'mass': 'Масса компонентов',
               'cost': 'Стоимость компонентов', 'volume': 'Объём компонентов', 'totalCost': 'Стоимость смеси',
               'density': 'Аддитивная плотность', 'calculation_checked': 'Расчёт проверен',
               'calculation_unchecked': 'Расчёт не проверен', 'lot': 'Из партии',
               'source': 'Из исходной рецептуры', 'material': 'Из справочника материала'})
SKIP_FIELDS = {'id', 'identifier_cell', 'name_cell', 'quantity_cell',
               'mass_cell', 'label_cell', 'scope_cell', 'session', 'test_run', 'group'}


def readable(value, key=''):
    if value is None or value == '':
        return '—'
    if isinstance(value, bool):
        return 'Да' if value else 'Нет'
    if isinstance(value, list):
        return '\n\n'.join(readable(item, 'metric' if key == 'metrics' else '')
                            for item in value) or '—'
    if isinstance(value, dict):
        return '\n'.join(f'{LABELS.get(k, k)}: {readable(v, k)}' for k, v in value.items() if k not in SKIP_FIELDS and v is not None and v != '') or '—'
    if isinstance(value, int) and key:
        from lab import models
        refs = {'mixture_type': models.MixtureType, 'created_by': models.EmployeeProfile,
                'unbatched_material': models.Material, 'specified_lot': models.MaterialLot,
                'metric': models.Metric, 'recipe_version': models.RecipeVersion,
                'input_group': models.RecipeGroup, 'source': models.SourceFile, 'source_sheet': models.SourceSheet,
                'measurement_group': models.MeasurementGroup,
                'source_cell': models.SourceCell, 'material': models.Material, 'recipe_template': models.RecipeTemplate}
        if key in refs:
            record = refs[key].objects.filter(pk=value).first()
            return str(record) if record else f'Запись #{value}'
    if isinstance(value, str):
        if key in {'quantity_value', 'numeric_value', 'mass_g', 'temperature_c', 'duration_value'}:
            try:
                return display_value(Decimal(value))
            except InvalidOperation:
                pass
        return VALUES.get(value, value)
    return display_value(value)


@register.filter
def event_changes(event):
    before, after = event.before or {}, event.after or {}
    changes = []
    for key in dict.fromkeys([*before, *after]):
        if key in SKIP_FIELDS:
            continue
        old, new = readable(before.get(key), key), readable(after.get(key), key)
        if old != new:
            changes.append({'label': LABELS.get(key, key), 'before': old, 'after': new})
    return changes


@register.filter
def action_label(action):
    return {'imported': 'Перенос из Excel',
            'created': 'Создание', 'updated': 'Изменение', 'result_added': 'Результат',
            'measurement_group_added': 'Группа измерений',
            'measurement_group_updated': 'Измерения',
            'observation_added': 'Наблюдение', 'conditioning_added': 'Обработка', 'calculation_saved': 'Расчёт',
            'demo_updated': 'Обновление примеров'}.get(action, 'Запись')


@register.filter
def status_label(value):
    return VALUES.get(value, value)


ENTITY_ROUTES = {'recipe': ('recipe_detail', 'Рецептура'), 'test': ('test_detail', 'Эксперимент'),
                 'material_category': ('category_detail', 'Категория материала'),
                 'source': ('source_detail', 'Источник'), 'material': ('material_detail', 'Материал'),
                 'lot': ('lot_detail', 'Партия'), 'metric': ('metric_list', 'Показатель'),
                 'mixture_type': ('mixture_detail', 'Тип смеси')}


@register.filter
def event_url(event):
    route = ENTITY_ROUTES.get(event.entity_type)
    return reverse(route[0], args=[] if event.entity_type == 'metric' else [event.entity_id]) if route else ''


@register.filter
def event_entity_label(event):
    if event.entity_type == 'recipe_template':
        return f'Архивная запись шаблона #{event.entity_id}'
    if event.entity_type == 'calculation_template':
        return f'Архивный шаблон расчёта #{event.entity_id}'
    return f'{ENTITY_ROUTES.get(event.entity_type, (None, "Запись"))[1]} #{event.entity_id}'
