"""Small initial context and maintained navigation help for the lab assistant."""
from django.urls import Resolver404, resolve

from .assistant_tools import execute_tool

CARD_ENTITIES = {
    'recipe_detail': 'recipes', 'test_detail': 'tests',
    'material_detail': 'materials', 'lot_detail': 'lots',
    'mixture_detail': 'mixtures', 'source_detail': 'sources',
    'recipe_template_detail': 'recipe_templates',
    'calculation_template_detail': 'calculation_templates',
    'calculation_detail': 'calculations',
}

UI_GUIDE = """Краткая справка по интерфейсу KUDO LIMS:
Главная / — обзор. Рецептуры /recipes/ — поиск по номеру, названию,
примечанию и фильтр статуса; «Новая рецептура» /recipes/new/.
Карточка рецептуры /recipes/{id}/ — состав, план заправки и связи;
редактирование /recipes/{id}/edit/, новая версия /recipes/{id}/fork/,
расчёт /recipes/{id}/calculate/.
Испытания /tests/ — поиск по номеру, рецептуре, названию и статусу;
«Новое испытание» /tests/new/. Карточка /tests/{id}/ — условия,
результаты, наблюдения и связи с рецептурами. Из неё доступны
/tests/{id}/edit/, /tests/{id}/results/new/,
/tests/{id}/observations/new/ и /tests/{id}/conditioning/new/.
Справочники: материалы /materials/, партии /lots/, типы смесей /mixtures/,
показатели /metrics/, источники /sources/. Создание — путь реестра + new/.
Карточка материала /materials/{id}/ содержит партии и использование
в рецептурах; «Новая партия» /lots/new/?material={id}.
Шаблоны рецептур /recipe-templates/ и расчётов /calculation-templates/;
создание — new/, карточка — {id}/, новая версия — {id}/fork/.
Сохранённые расчёты /calculations/{id}/. История изменений /history/.
Автор создаёт и редактирует данные через формы; читатель только смотрит.
Переписка помощника сохраняется в БД по выбранному сотруднику. Над чатом
есть список диалогов, ↺ начинает новый. Переключение, обновление страницы
и закрытие вкладки не останавливают серверную генерацию; ■ останавливает её.
Помощник сам лабораторные данные не сохраняет. В ссылках замени {id} известным ID;
если ID неизвестен, дай ссылку на реестр. Для деталей, которых нет в этой
справке или снимке формы, можно читать разрешённые шаблоны и документацию.
"""


def initial_context(page, role):
    context = {'employee_role': role, 'page': '/'}
    if not isinstance(page, str) or not page.startswith('/'):
        return context
    if page.startswith('//') or len(page) >= 500:
        return context
    try:
        match = resolve(page)
    except Resolver404:
        return context
    context.update(page=page, route=match.url_name)
    entity = CARD_ENTITIES.get(match.url_name)
    if entity and 'pk' in match.kwargs:
        context['current_record'] = execute_tool('get_lab_record', {
            'entity': entity, 'id': match.kwargs['pk'], 'section': 'summary',
        })
    return context
