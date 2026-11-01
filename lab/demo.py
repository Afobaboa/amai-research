"""Versioned, explicitly illustrative winter fixture helpers. No archive access."""
from datetime import date
from decimal import Decimal

from .calculations import save_calculation
from .models import (CalculationTemplate, ChangeEvent, FillingPlanLine, Material, MaterialLot,
                     MixtureType, RecipeComponent, RecipeGroup, RecipeTemplate, RecipeVersion,
                     SourceFile, TestSession)

RECIPE_NOTE = 'Вымышленная учебная рецептура. Не является импортом или лабораторной рекомендацией.'
MATERIALS = [
    ('Полиол P-101', 'Полиол', 'Лабораторный поставщик А', '1.04', '245'),
    ('Полиол P-202', 'Полиол', 'Лабораторный поставщик Б', '1.06', '268'),
    ('Стабилизатор S-10', 'Стабилизатор', 'Лабораторный поставщик А', '1.02', '485'),
    ('Катализатор C-3', 'Катализатор', 'Лабораторный поставщик В', '0.98', '620'),
    ('Антипирен FR-5', 'Антипирен', 'Лабораторный поставщик Б', '1.29', '172'),
    ('Пластификатор PL-2', 'Пластификатор', 'Лабораторный поставщик А', '1.25', '138'),
    ('Изоцианат MDI', 'Изоцианат', 'Лабораторный поставщик Г', '1.23', '224'),
    ('Пропеллент G-1', 'Пропеллент', 'Лабораторный поставщик Д', '0.67', '78'),
]


def audit(employee, entity, entity_type, summary, before=None, after=None, action='created'):
    ChangeEvent.objects.create(employee=employee, entity_type=entity_type, entity_id=entity.pk,
                               action=action, summary=summary, before=before or {}, after=after or {})


def winter_catalog(author):
    """Add missing fixtures; never overwrite a user-entered density, price or lot."""
    source, source_created = SourceFile.objects.get_or_create(
        kind='note', title='Зимняя пена: учебные данные и допущения',
        defaults={'original_path': 'Учебный зимний набор LIMS', 'sha256': '',
                  'notes': 'Составы, партии, цены, плотности и результаты вымышлены для проверки интерфейса. '
                           'Плотность смеси рассчитывается аддитивно до реакции. Это не импорт Excel и не лабораторная рекомендация.',
                  'created_by': author},
    )
    if source_created:
        audit(author, source, 'source', 'Добавлен источник учебных зимних данных', after={'title': source.title, 'kind': 'note'})
    materials = []
    for name, category, supplier, density, price in MATERIALS:
        material = Material.objects.filter(name=name).order_by('pk').first()
        if material is None:
            material = Material.objects.filter(name='DEMO · ' + name).order_by('pk').first()
        created = material is None
        if created:
            material = Material(name=name)
        before = {'name': material.name, 'density_g_ml': str(material.density_g_ml) if material.density_g_ml is not None else None,
                  'price_per_kg': str(material.price_per_kg) if material.price_per_kg is not None else None}
        changed = created
        if material.name == 'DEMO · ' + name:
            material.name = name
            changed = True
        for field, value in [('category', category), ('supplier', supplier), ('notes', 'Иллюстративные свойства и цены учебного зимнего набора.')]:
            if not getattr(material, field):
                setattr(material, field, value)
                changed = True
        for field, value in [('density_g_ml', density), ('price_per_kg', price)]:
            if getattr(material, field) is None:
                setattr(material, field, Decimal(value))
                changed = True
        if changed:
            material.full_clean()
            material.save()
            audit(author, material, 'material', ('Добавлен материал ' if created else 'Дополнены учебные свойства материала ') + name,
                  before={} if created else before, after={'name': material.name, 'density_g_ml': str(material.density_g_ml),
                  'price_per_kg': str(material.price_per_kg)}, action='created' if created else 'updated')
        materials.append(material)
    lots = []
    for index, code, status, density, price in [(0, 'ЗИМА-2026-001', 'active', '1.035', '247'),
                                              (1, 'ЗИМА-2026-002', 'active', '1.055', '265'),
                                              (2, 'ЗИМА-2026-К01', 'quarantine', None, None)]:
        lot, created = MaterialLot.objects.get_or_create(material=materials[index], lot_code=code, defaults={
            'supplier': materials[index].supplier, 'received_on': date(2026, 10, 20), 'expires_on': date(2027, 10, 20),
            'status': status, 'density_g_ml': Decimal(density) if density else None,
            'price_per_kg': Decimal(price) if price else None, 'source': source,
            'notes': 'Учебная партия. На карантине — ожидается проверка документов.' if status == 'quarantine' else 'Учебная партия для прослеживаемости состава и расчёта.',
        })
        if created:
            audit(author, lot, 'lot', 'Добавлена учебная партия ' + code,
                  after={'lot_code': code, 'material': materials[index].pk, 'status': status})
        lots.append(lot)
    return source, materials, lots


def definition(materials, *, improved=False, lots=None):
    quantities = ['35', '31', '2.5', '1', '20', '10.5'] if improved else ['36', '29', '2.5', '1.5', '20', '11']
    components = []
    for index, (material, quantity) in enumerate(zip(materials[:6], quantities)):
        lot = lots[index] if lots and index < 2 else None
        components.append({'group_label': 'A', 'quantity_basis': 'mass_percent', 'completeness': 'complete',
                           'material': None if lot else material.pk, 'specified_lot': lot.pk if lot else None,
                           'quantity_value': quantity})
    return {'components': components, 'filling': [
        {'group_label': 'A', 'material': None, 'specified_lot': None, 'mass_g': '225' if improved else '220'},
        {'group_label': '', 'material': materials[6].pk, 'specified_lot': None, 'mass_g': '320'},
        {'group_label': '', 'material': materials[7].pk, 'specified_lot': None, 'mass_g': '105' if improved else '100'},
    ]}


def winter_templates(author, mixtures, materials):
    calculations = []
    for name, operations, description in [
        ('Масса, плотность и стоимость смеси', ['mass', 'cost', 'volume', 'totalCost', 'density'],
         'Полный плановый расчёт на выбранную массу смеси. Цены — ₽/кг; плотность — аддитивная оценка до реакции.'),
        ('Масса и стоимость смеси', ['mass', 'cost', 'totalCost'], 'Расчёт навесок плана и стоимости без оценки плотности.'),
    ]:
        template, created = CalculationTemplate.objects.get_or_create(name=name, version=1, defaults={
            'operations': operations, 'description': description, 'created_by': author})
        if created:
            audit(author, template, 'calculation_template', 'Создан учебный шаблон расчёта «' + name + '»', after={'name': name, 'version': 1, 'operations': operations})
        calculations.append(template)
    recipe_templates = []
    for improved, mixture in enumerate(mixtures):
        name = 'Зимняя улучшенная пена' if improved else 'Зимняя пена'
        template, created = RecipeTemplate.objects.get_or_create(name=name, version=1, defaults={
            'description': 'Учебная структура рецептуры и плана заправки. Свойства и результаты иллюстративные.',
            'mixture_type': mixture, 'calculation_template': calculations[0], 'batch_mass_g': Decimal('1000'),
            'definition': definition(materials, improved=bool(improved)), 'created_by': author})
        if created:
            audit(author, template, 'recipe_template', 'Создан учебный шаблон рецептуры «' + name + '»', after={'name': name, 'version': 1})
        recipe_templates.append(template)
    return calculations, recipe_templates


def make_recipe(author, label, title, mixture, template, *, materials, lots, improved=False, status='published', recorded_on=None):
    recipe = RecipeVersion.objects.create(mixture_type=mixture, version_label=label, title=title, status=status,
        recorded_on=recorded_on or date(2026, 11, 1), notes=RECIPE_NOTE, created_by=author, recipe_template=template,
        calculation_template=template.calculation_template, batch_mass_g=template.batch_mass_g,
        template_snapshot={'id': template.pk, 'name': template.name, 'version': template.version, 'definition': template.definition})
    data = definition(materials, improved=improved, lots=lots)
    group = RecipeGroup.objects.create(recipe_version=recipe, label='A', quantity_basis='mass_percent', completeness='complete')
    for position, row in enumerate(data['components'], 1):
        RecipeComponent.objects.create(group=group, position=position, unbatched_material_id=row['material'],
            specified_lot_id=row['specified_lot'], quantity_value=Decimal(row['quantity_value']))
    for row in data['filling']:
        FillingPlanLine.objects.create(recipe_version=recipe, input_group=group if row['group_label'] else None,
            unbatched_material_id=row['material'], specified_lot_id=row['specified_lot'], mass_g=Decimal(row['mass_g']), mass_status='recorded')
    audit(author, recipe, 'recipe', 'Создан зимний учебный план ' + label,
          after={'version_label': label, 'title': title, 'recipe_template': template.pk, 'calculation_template': template.calculation_template_id})
    calculation = save_calculation(recipe, author, purpose='publication' if status == 'published' else 'manual')
    audit(author, recipe, 'recipe', 'Сохранён учебный расчёт ' + label,
          after={'calculation_id': calculation.pk, 'batch_mass_g': str(recipe.batch_mass_g)}, action='calculated')
    return recipe


def upgrade_winter(author):
    mixtures = []
    old_titles = [
        {'Зимняя монтажная пена · учебная серия', 'Зимняя пена'},
        {'Всесезонная пена · учебная серия', 'Зимняя улучшенная пена'},
    ]
    for index, code in enumerate(('DEMO-316', 'DEMO-302')):
        mixture, created = MixtureType.objects.get_or_create(code=code, defaults={'title': 'Зимняя улучшенная пена' if index else 'Зимняя пена'})
        title = 'Зимняя улучшенная пена' if index else 'Зимняя пена'
        if not created and mixture.title in old_titles[index] and mixture.title != title:
            before = {'title': mixture.title}
            mixture.title = title
            mixture.save(update_fields=['title'])
            audit(author, mixture, 'mixture_type', 'Обновлено название учебной зимней серии', before=before,
                  after={'title': title}, action='updated')
        mixtures.append(mixture)
    source, materials, lots = winter_catalog(author)
    calculations, templates = winter_templates(author, mixtures, materials)
    # Only exact known legacy fixture metadata is relabelled; composition and links stay untouched.
    old_recipe_titles = ['Базовый состав', 'Меньше катализатора', 'Замена полиола', 'Баланс пропеллента', 'Ускоренное отверждение', 'Контрольный состав']
    for index, old_title in enumerate(old_recipe_titles):
        old_label = f'DEMO-{316 if index < 5 else 302}.{index + 1:02d}'
        recipe = RecipeVersion.objects.filter(version_label=old_label, title=old_title, notes=RECIPE_NOTE,
                                             created_by__full_name__startswith='DEMO · ').order_by('pk').first()
        if recipe:
            title = ('Зимняя улучшенная пена' if index == 5 else 'Зимняя пена') + ' · ' + old_title.lower()
            recipe.title = title
            recipe.save(update_fields=['title'])
            audit(author, recipe, 'recipe', 'Уточнено название учебного зимнего плана ' + old_label,
                  before={'title': old_title}, after={'title': title}, action='updated')
    card = TestSession.objects.filter(
        identifier='DEMO-2026-010', title='Контроль всесезонной пены', notes='Демонстрационная карточка. Все значения вымышлены.',
        created_by__full_name__startswith='DEMO · ').first()
    if card:
        card.title = 'Контроль улучшенной зимней пены'
        card.save(update_fields=['title'])
        audit(author, card, 'test', 'Уточнено название учебного зимнего испытания',
              before={'title': 'Контроль всесезонной пены'}, after={'title': card.title}, action='updated')
    for index, mixture in enumerate(mixtures):
        label = f'DEMO-{302 if index else 316}.W1'
        if not RecipeVersion.objects.filter(version_label=label).exists():
            make_recipe(author, label, 'Зимняя улучшенная пена' if index else 'Зимняя пена', mixture, templates[index],
                        materials=materials, lots=lots, improved=bool(index))
    return source
