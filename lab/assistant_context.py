"""Small initial context and maintained navigation help for the lab assistant."""
from django.urls import Resolver404, resolve, reverse

from .assistant_tools import execute_tool
from .ui_text import UI_TEXT, navigation_labels

CARD_ENTITIES = {
    'recipe_detail': 'recipes', 'test_detail': 'tests',
    'material_detail': 'materials', 'lot_detail': 'lots',
    'mixture_detail': 'mixtures', 'source_detail': 'sources',
    'calculation_detail': 'calculations',
    'measurement_group_detail': 'measurement_groups',
}

UI_GUIDE_INTRO = """Краткая справка по интерфейсу AMAI LAB:
Адреса ниже — служебная справка. Пользователю показывай Markdown-ссылки
с русскими подписями, без самих адресов и повторов. Замени {id} известным ID;
если ID неизвестен, дай ссылку на список. Не создавай ссылки с {id}.
В карточке рецептуры кнопка создания эксперимента ведёт на адрес создания
с ?recipe={id} и заранее выбирает рецептуру в форме.
Карточка рецептуры содержит состав, план заправки и связи, а карточка эксперимента —
группы измерений с показателями, наблюдения и связи с рецептурами.
Новая рецептура содержит полиольную смесь, необязательный изоцианат и газы.
Заправка рассчитывается из исходных параметров. Для повторного использования
рецептуры создают новую версию из её карточки; раздела шаблонов рецептур нет.
Группы настраиваются в разделе «Группы измерений». «Основные параметры»
содержат температуру, влажность, давление и другие общие показатели.
Кнопка «Добавить группу измерений» предлагает выбрать группу и заполнить её показатели.
Реестры рецептур и экспериментов поддерживают поиск и фильтр статуса.
Автор создаёт и редактирует данные через формы; читатель только смотрит.
Переписка сохраняется по сотруднику; список диалогов над чатом.
Кнопка «Новый диалог» начинает новый чат. «Остановить ответ» прерывает генерацию.
Переключение и закрытие вкладки не останавливают генерацию.
ИИ-ассистент сам данные не сохраняет. Для деталей можно читать разрешённые
шаблоны и документацию, только если справки и снимка формы недостаточно.
"""


def interface_guide():
    return '\n'.join(
        f"[{item['label']}]({item['path']})" for item in navigation_labels()
    )


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
    navigation = [
        {'label': UI_TEXT['recipe_list'], 'url': reverse('recipe_list')},
        {'label': UI_TEXT['test_list'], 'url': reverse('test_list')},
    ]
    if role == 'author':
        navigation.append({'label': UI_TEXT['test_create'], 'url': reverse('test_create')})
        pk = match.kwargs.get('pk')
        if match.url_name == 'recipe_detail' and pk:
            navigation[-1]['url'] += f'?recipe={pk}'
        elif match.url_name == 'test_detail' and pk:
            navigation.extend({'label': UI_TEXT[route], 'url': reverse(route, args=[pk])}
                              for route in ('result_add', 'observation_add', 'conditioning_add'))
    context['navigation'] = navigation
    entity = CARD_ENTITIES.get(match.url_name)
    if entity and 'pk' in match.kwargs:
        context['current_record'] = execute_tool('get_lab_record', {
            'entity': entity, 'id': match.kwargs['pk'], 'section': 'summary',
        })
    return context
