from decimal import Decimal, InvalidOperation, localcontext
from django import template
from django.urls import reverse
register = template.Library()

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


LABELS = {
    'version_label': 'Версия рецептуры', 'title': 'Название', 'mixture_type': 'Тип смеси',
    'recorded_on': 'Дата рецептуры', 'recorded_manufactured_on': 'Записанная дата изготовления',
    'recorded_tested_on': 'Дата испытания', 'identifier': 'Номер карточки', 'status': 'Статус',
    'notes': 'Примечание', 'created_by': 'Автор', 'groups': 'Группы состава',
    'label': 'Группа', 'quantity_basis': 'База количества', 'completeness': 'Полнота',
    'components': 'Компоненты', 'position': 'Позиция', 'unbatched_material': 'Материал',
    'specified_lot': 'Партия', 'quantity_value': 'Количество', 'filling_plan': 'План заправки', 'input_group': 'Группа состава',
    'mass_g': 'Плановая масса, г', 'mass_status': 'Основание массы', 'conditions': 'Условия',
    'metric': 'Показатель', 'numeric_value': 'Значение', 'text_value': 'Текст',
    'recipe_matches': 'Связи с рецептурой', 'recipe_version': 'Рецептура', 'reason': 'Основание связи',
    'result': 'Результат', 'results_count': 'Число результатов', 'run': 'Определение',
    'scope': 'Название определения', 'method_code': 'Метод', 'origin': 'Происхождение',
    'value_state': 'Состояние значения', 'calculation_code': 'Код расчета',
    'observation': 'Наблюдение', 'sequence_no': 'Порядковый номер', 'conditioning': 'Обработка',
    'target_kind': 'Цель обработки', 'step_no': 'Этап', 'kind': 'Вид обработки',
    'duration_value': 'Длительность', 'duration_unit': 'Единица длительности',
    'temperature_c': 'Температура, °C', 'demo': 'Учебные данные',
}
LABELS.update({'category': 'Категория', 'supplier': 'Поставщик', 'density_g_ml': 'Плотность, г/мл',
               'price_per_kg': 'Цена, ₽/кг', 'lot_code': 'Номер партии', 'received_on': 'Дата поступления',
               'expires_on': 'Годен до', 'source': 'Источник', 'kind': 'Вид записи', 'url': 'Ссылка',
               'attachment': 'Вложение', 'sha256': 'SHA-256', 'original_path': 'Исходное имя',
               'date_system': 'Система дат', 'source_sheet': 'Лист источника', 'source_cell': 'Первичная ячейка',
               'material': 'Материал', 'name': 'Название', 'version': 'Версия', 'description': 'Описание',
               'definition': 'Структура шаблона', 'recipe_template': 'Шаблон рецептуры', 'calculation_template': 'Шаблон расчёта',
               'operations': 'Расчётные операции', 'template_snapshot': 'Зафиксированный шаблон',
               'batch_mass_g': 'Масса каждой группы для расчёта, г', 'calculation_id': 'Номер сохранённого расчёта',
               'calculation_ids': 'Сохранённые расчёты', 'properties': 'Свойства плана', 'value': 'Значение',
               'property_code': 'Свойство', 'unit_code': 'Единица', 'cell': 'Фрагмент источника',
               'address': 'Адрес фрагмента', 'raw_value': 'Исходное значение', 'formula_text': 'Формула',
               'cached_value': 'Кэш формулы', 'number_format': 'Исходный формат', 'sheet': 'Раздел источника',
               'total_cost': 'Стоимость, ₽', 'cost_per_kg': 'Цена за кг', 'volume_ml': 'Объём, мл',
               'rows': 'Строки расчёта', 'quantity_total': 'Сумма количеств', 'issues': 'Замечания',
               'cost': 'Стоимость компонента', 'density_origin': 'Происхождение плотности', 'price_origin': 'Происхождение цены'})
VALUES = {'draft': 'Черновик', 'active': 'В работе', 'completed': 'Завершено',
          'published': 'Опубликована', 'archived': 'В архиве', 'confirmed': 'Подтверждена',
          'candidate': 'Кандидат', 'rejected': 'Отклонена', 'mass_percent': 'Массовые доли, %',
          'weight_parts': 'Массовые части', 'unknown': 'Не установлено', 'complete': 'Полный состав',
          'partial': 'Частичный состав', 'recorded': 'Записано автором', 'present': 'Записано',
          'calculated': 'Рассчитано', 'blend': 'Смесь', 'can': 'Баллон', 'specimen': 'Испытуемый материал',
          'aging': 'Выдержка', 'conditioning': 'Кондиционирование'}
VALUES.update({'active': 'Активна / в работе', 'quarantine': 'Карантин', 'exhausted': 'Закрыта / израсходована',
               'file': 'Файл', 'link': 'Ссылка', 'note': 'Запись', 'mass': 'Масса компонентов',
               'cost': 'Стоимость компонентов', 'volume': 'Объём компонентов', 'totalCost': 'Стоимость смеси',
               'density': 'Аддитивная плотность', 'calculation_checked': 'Расчёт проверен',
               'calculation_unchecked': 'Расчёт не проверен', 'lot': 'Из партии', 'material': 'Из справочника материала'})
SKIP_FIELDS = {'id', 'identifier_cell', 'name_cell', 'quantity_cell',
               'mass_cell', 'label_cell', 'scope_cell', 'session', 'test_run', 'group'}


def readable(value, key=''):
    if value is None or value == '':
        return '—'
    if isinstance(value, bool):
        return 'Да' if value else 'Нет'
    if isinstance(value, list):
        return '\n\n'.join(readable(item) for item in value) or '—'
    if isinstance(value, dict):
        return '\n'.join(f'{LABELS.get(k, k)}: {readable(v, k)}' for k, v in value.items() if k not in SKIP_FIELDS and v is not None and v != '') or '—'
    if isinstance(value, int) and key:
        from lab import models
        refs = {'mixture_type': models.MixtureType, 'created_by': models.EmployeeProfile,
                'unbatched_material': models.Material, 'specified_lot': models.MaterialLot,
                'metric': models.Metric, 'recipe_version': models.RecipeVersion,
                'input_group': models.RecipeGroup, 'source': models.SourceFile, 'source_sheet': models.SourceSheet,
                'source_cell': models.SourceCell, 'material': models.Material, 'recipe_template': models.RecipeTemplate,
                'calculation_template': models.CalculationTemplate}
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
    return {'created': 'Создание', 'updated': 'Изменение', 'result_added': 'Результат',
            'observation_added': 'Наблюдение', 'conditioning_added': 'Обработка', 'calculation_saved': 'Расчёт',
            'demo_updated': 'Обновление примеров'}.get(action, 'Запись')


@register.filter
def status_label(value):
    return VALUES.get(value, value)


ENTITY_ROUTES = {'recipe': ('recipe_detail', 'Рецептура'), 'test': ('test_detail', 'Испытание'),
                 'source': ('source_detail', 'Источник'), 'material': ('material_detail', 'Материал'),
                 'lot': ('lot_detail', 'Партия'), 'metric': ('metric_list', 'Показатель'),
                 'mixture_type': ('mixture_detail', 'Тип смеси'),
                 'recipe_template': ('recipe_template_detail', 'Шаблон рецептуры'),
                 'calculation_template': ('calculation_template_detail', 'Шаблон расчёта')}


@register.filter
def event_url(event):
    route = ENTITY_ROUTES.get(event.entity_type)
    return reverse(route[0], args=[] if event.entity_type == 'metric' else [event.entity_id]) if route else ''


@register.filter
def event_entity_label(event):
    return f'{ENTITY_ROUTES.get(event.entity_type, (None, "Запись"))[1]} #{event.entity_id}'
