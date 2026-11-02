"""Shared interface text used by templates and assistant navigation."""
import re
from functools import lru_cache

from django.urls import reverse

UI_TEXT = {
    'density_g_ml': 'Плотность, г/мл',
    'price_per_kg': 'Цена за кг',
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
    'category_list': 'Категории материалов', 'category_create': 'Новая категория',
    'category_detail': 'Карточка категории', 'category_edit': 'Редактировать категорию',
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
    'dependencies': 'Исходные результаты', 'sheets': 'Листы источника',
    'cells': 'Ячейки источника',
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
               'attachment': 'Вложение', 'sha256': 'Контрольная сумма SHA-256', 'original_path': 'Исходное имя',
               'date_system': 'Система дат', 'source_sheet': 'Лист источника', 'source_cell': 'Первичная ячейка',
               'material': 'Материал', 'name': 'Название', 'version': 'Версия', 'description': 'Описание',
               'definition': 'Структура шаблона', 'recipe_template': 'Шаблон рецептуры', 'calculation_template': 'Шаблон расчёта',
               'operations': 'Расчётные операции', 'template_snapshot': 'Зафиксированный шаблон',
               'batch_mass_g': 'Масса каждой группы для расчёта, г', 'calculation_id': 'Номер сохранённого расчёта',
               'calculation_ids': 'Сохранённые расчёты', 'properties': 'Свойства плана', 'value': 'Значение',
               'property_code': 'Свойство', 'unit_code': 'Единица', 'cell': 'Фрагмент источника',
               'address': 'Адрес фрагмента', 'raw_value': 'Исходное значение', 'formula_text': 'Формула',
               'cached_value': 'Кэш формулы', 'number_format': 'Исходный формат', 'sheet': 'Раздел источника',
               'total_cost': 'Стоимость', 'cost_per_kg': 'Цена за кг', 'volume_ml': 'Объём, мл',
               'rows': 'Строки расчёта', 'quantity_total': 'Сумма количеств', 'issues': 'Замечания',
               'cost': 'Стоимость компонента', 'density_origin': 'Происхождение плотности', 'price_origin': 'Происхождение цены'})

# Property codes and units are values, separate from names of model fields.
# Templates and assistant tools use the same vocabulary.
PROPERTY_LABELS = {
    'ohv': 'Гидроксильное число',
    'density_g_ml': 'Плотность компонента',
    'source_price_per_kg': 'Цена из исходной рецептуры',
    'isocyanate_nco_pct': 'Содержание NCO в изоцианате',
    'can_volume_ml': 'Объём баллона',
    'polyol_ratio': 'Массовые части смеси A',
    'isocyanate_ratio': 'Массовые части изоцианата',
    'gas_mix_mass_pct': 'Массовая доля газовой смеси',
}
UNIT_LABELS = {
    'mg KOH/g': 'мг KOH/г', 'g/ml': 'г/мл', 'ml': 'мл',
    'EUR/kg': '€/кг', 'RUB/kg': '₽/кг', 'USD/kg': '$/кг',
    'source_currency/kg': 'валюта не указана / кг',
    'weight_parts': 'масс. ч.',
}
FIELD_LABELS.update({
    'price_currency': 'Валюта цены', 'currency': 'Валюта расчёта',
    'price_unit': 'Единица цены', 'currency_symbol': 'Обозначение валюты',
    'effective_price_currency': 'Применяемая валюта цены',
    'price_source_cell_id': 'Ячейка исходной цены',
    'density_source_cell_id': 'Ячейка исходной плотности',
    'provenance_note': 'Основание переноса',
    'price_provenance_note': 'Основание переноса цены',
    'price_source_origin': 'Происхождение цены в рецептуре',
})
SOURCE_CELL_KIND_LABELS = {
    'text': 'Текст', 'numeric': 'Число', 'formula': 'Формула',
    'blank': 'Пусто', 'other': 'Другое',
    # Imported Excel types retain their original codes as documentary data.
    'n': 'Число', 's': 'Текст', 'inlineStr': 'Текст', 'str': 'Текст',
    'b': 'Логическое значение', 'd': 'Дата', 'e': 'Ошибка Excel',
    'comment': 'Комментарий Excel',
}
FIELD_LABELS.update({
    'property_label': 'Свойство', 'unit_label': 'Единица',
    'metric__label': 'Показатель',
    'input_result__metric__label': 'Исходный показатель',
})
FIELD_LABELS.update({
    # Relation names also label their Django *_id attributes in tool output.
    'id': 'Номер записи', 'group': 'Группа состава', 'component': 'Компонент',
    'filling_plan_line': 'Строка плана заправки', 'input_result': 'Исходный результат',
    'test_run': 'Определение', 'session': 'Испытание', 'template': 'Шаблон расчёта',
    'file': 'Файл источника', 'name_cell': 'Ячейка названия компонента',
    'quantity_cell': 'Ячейка количества', 'label_cell': 'Ячейка названия строки заправки',
    'mass_cell': 'Ячейка массы', 'scope_cell': 'Ячейка названия определения',
    'identifier_cell': 'Ячейка номера карточки',
    'source_title': 'Название источника', 'cell_kind': 'Тип ячейки',
    'value_kind': 'Вид значения', 'group_label': 'Группа состава',
    # Saved calculation snapshots contain explicit identifiers as JSON keys,
    # rather than actual model relations.
    'recipe_id': 'Рецептура', 'lot_id': 'Партия', 'lot_status': 'Статус партии',
    'filling': 'План заправки', 'valid': 'Расчёт выполнен',
    'disclaimer': 'Ограничения расчёта', 'purpose': 'Назначение расчёта',
    'formula': 'Формула расчёта', 'unit': 'Единица',
    # Service metadata explains incomplete selections without exposing API names.
    'entity': 'Реестр', 'total': 'Всего записей', 'offset': 'Начало выборки',
    'items': 'Записи', 'truncated': 'Выборка сокращена',
    'next_offset': 'Продолжение выборки', 'output_truncated': 'Ответ сокращён',
    'truncated_fields': 'Сокращённые поля', 'available_sections': 'Доступные разделы',
    'detail_hint': 'Как получить подробности', 'note': 'Пояснение', 'error': 'Ошибка',
    'field_labels': 'Названия полей', 'value_labels': 'Названия значений',
    'path': 'Путь файла', 'lines': 'Строки файла', 'match': 'Найденный фрагмент',
    'total_lines': 'Всего строк', 'start_line': 'Начальная строка',
    'next_line': 'Следующая строка', 'next_column': 'Следующий столбец',
    'column': 'Столбец', 'number': 'Номер строки', 'text': 'Текст',
})

# Assistant protocol vocabulary does not define website navigation routes.
ASSISTANT_LABELS = {
    'materials': UI_TEXT['material_list'], 'lots': UI_TEXT['lot_list'],
    'mixtures': UI_TEXT['mixture_list'], 'recipes': UI_TEXT['recipe_list'],
    'tests': UI_TEXT['test_list'], 'results': SECTION_LABELS['results'],
    'metrics': UI_TEXT['metric_list'], 'sources': UI_TEXT['source_list'],
    'source_cells': 'Ячейки источников',
    'recipe_templates': UI_TEXT['recipe_template_list'],
    'calculation_templates': UI_TEXT['calculation_template_list'],
    'calculations': SECTION_LABELS['calculations'],
    'summary': 'Краткая сводка', 'all': 'Все сведения карточки',
    'search_lab_data': 'Поиск лабораторных данных',
    'get_lab_record': 'Чтение карточки',
    'list_project_files': 'Поиск справочных файлов',
    'read_project_file': 'Чтение справочного файла',
    'q': 'Поисковый запрос', 'query': 'Поисковый запрос',
    'section': 'Раздел карточки', 'limit': 'Размер выборки',
    'metric_code': 'Показатель', 'match_status': 'Статус связи с рецептурой',
    'min_value': 'Минимальное значение', 'max_value': 'Максимальное значение',
    'order': 'Порядок сортировки', 'start_column': 'Начальный столбец',
    'line_count': 'Число строк',
    'id_asc': 'По номеру записи: по возрастанию',
    'id_desc': 'По номеру записи: по убыванию',
    'value_asc': 'По значению: по возрастанию',
    'value_desc': 'По значению: по убыванию',
}

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
