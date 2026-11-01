"""Shared interface text used by templates and assistant navigation."""
import re
from functools import lru_cache

from django.urls import reverse

UI_TEXT = {
    'density_g_ml': 'Плотность, г/мл',
    'price_per_kg': 'Цена, ₽/кг',
    'dashboard': 'Обзор лаборатории',
    'recipe_list': 'Рецептуры', 'recipe_create': 'Новая рецептура',
    'recipe_detail': 'Карточка рецептуры', 'recipe_edit': 'Редактировать рецептуру',
    'recipe_fork': 'Новая версия', 'recipe_calculate': 'Расчёт рецептуры',
    'test_list': 'Испытания', 'test_create': 'Новое испытание',
    'test_detail': 'Карточка испытания', 'test_edit': 'Редактировать испытание',
    'result_add': 'Добавить результат', 'observation_add': 'Добавить наблюдение',
    'conditioning_add': 'Записать обработку',
    'material_list': 'Материалы', 'material_create': 'Новый материал',
    'material_detail': 'Карточка материала', 'material_edit': 'Редактировать материал',
    'lot_list': 'Партии сырья', 'lot_create': 'Новая партия',
    'lot_detail': 'Карточка партии', 'lot_edit': 'Редактировать партию',
    'mixture_list': 'Типы смесей', 'mixture_create': 'Новый тип смеси',
    'mixture_detail': 'Карточка типа смеси', 'mixture_edit': 'Редактировать тип смеси',
    'metric_list': 'Показатели', 'metric_create': 'Новый показатель',
    'metric_edit': 'Редактировать показатель',
    'source_list': 'Источники', 'source_create': 'Новый источник',
    'source_detail': 'Карточка источника', 'source_edit': 'Редактировать источник',
    'recipe_template_list': 'Шаблоны рецептур', 'recipe_template_create': 'Новый шаблон рецептуры',
    'recipe_template_detail': 'Шаблон рецептуры', 'recipe_template_fork': 'Новая версия шаблона',
    'calculation_template_list': 'Шаблоны расчётов', 'calculation_template_create': 'Новый шаблон расчёта',
    'calculation_template_detail': 'Шаблон расчёта', 'calculation_template_fork': 'Новая версия шаблона',
    'calculation_detail': 'Сохранённый расчёт', 'history': 'История изменений',
}
NAVIGATION_ROUTES = tuple(key for key in UI_TEXT if key not in ('density_g_ml', 'price_per_kg'))
SECTION_LABELS = {
    'groups': 'Состав', 'components': 'Компоненты', 'filling_plan': 'План заправки',
    'properties': 'Свойства рецептуры', 'test_links': 'Связанные испытания',
    'recipe_links': 'Связанные рецептуры', 'calculations': 'Сохранённые расчёты',
    'conditions': 'Условия испытания', 'conditioning': 'Кондиционирование',
    'runs': 'Серии измерений', 'results': 'Результаты измерений',
    'observations': 'Наблюдения', 'source': 'Первичный источник',
    'dependencies': 'Исходные результаты',
}
UI_TEXT.update(SECTION_LABELS)


FIELD_LABELS = {
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
FIELD_LABELS.update({'category': 'Категория', 'supplier': 'Поставщик', 'density_g_ml': UI_TEXT['density_g_ml'],
               'price_per_kg': UI_TEXT['price_per_kg'], 'lot_code': 'Номер партии', 'received_on': 'Дата поступления',
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

@lru_cache(maxsize=1)
def navigation_labels():
    """Use URLconf as the sole source of paths; no parallel JS route table."""
    items = []
    for route in NAVIGATION_ROUTES:
        label = UI_TEXT[route]
        needs_id = route.endswith(('_detail', '_edit', '_fork', '_calculate')) or route in (
            'result_add', 'observation_add', 'conditioning_add',
        )
        path = reverse(route, args=[123456789] if needs_id else [])
        items.append({'label': label, 'path': path.replace('123456789', '{id}'),
                      'pattern': '^' + re.escape(path).replace('123456789', r'(?:\d+|\{id\})') + '$'})
    return items
