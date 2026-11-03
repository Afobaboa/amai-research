"""Remove the known retired fixtures, inside the initial-data transaction."""
from django.db import models

from . import models as lab


RECIPE_NOTE = (
    'Вымышленная учебная рецептура. '
    'Не является импортом или лабораторной рекомендацией.'
)
EMPLOYEES = (
    'DEMO · Анна Волкова', 'DEMO · Михаил Соколов',
    'DEMO · Елена Морозова',
)
MATERIAL_NAMES = (
    'Полиол P-101', 'Полиол P-202', 'Стабилизатор S-10',
    'Катализатор C-3', 'Антипирен FR-5', 'Пластификатор PL-2',
    'Изоцианат MDI', 'Пропеллент G-1',
)
RECIPE_LABELS = (
    'DEMO-316.01', 'DEMO-316.02', 'DEMO-316.03', 'DEMO-316.04',
    'DEMO-316.05', 'DEMO-302.04', 'DEMO-302.05', 'DEMO-302.06',
    'DEMO-316.W1', 'DEMO-302.W1',
)


def maintenance_delete(queryset):
    """Explicit maintenance bypass for retired immutable fixture snapshots."""
    return models.QuerySet(queryset.model).filter(pk__in=queryset).delete()


def delete_events(entity_type, ids):
    maintenance_delete(lab.ChangeEvent.objects.filter(
        entity_type=entity_type, entity_id__in=ids,
    ))


def remove_tests():
    cards = lab.TestSession.objects.filter(
        identifier__startswith='DEMO-',
        notes='Демонстрационная карточка. Все значения вымышлены.',
        created_by__full_name__in=EMPLOYEES,
    )
    ids = list(cards.values_list('pk', flat=True))
    runs = lab.TestRun.objects.filter(session__in=ids)
    results = lab.Result.objects.filter(test_run__in=runs)
    lab.ResultDependency.objects.filter(result__in=results).delete()
    results.delete()
    lab.Observation.objects.filter(test_run__in=runs).delete()
    runs.delete()
    lab.ConditioningEvent.objects.filter(session__in=ids).delete()
    lab.RecipeMatch.objects.filter(session__in=ids).delete()
    cards.delete()
    delete_events('test', ids)
    return len(ids)


def remove_recipes():
    recipes = lab.RecipeVersion.objects.filter(
        version_label__in=RECIPE_LABELS, notes=RECIPE_NOTE,
        created_by__full_name__in=EMPLOYEES,
    )
    ids = list(recipes.values_list('pk', flat=True))
    groups = lab.RecipeGroup.objects.filter(recipe_version__in=ids)
    components = lab.RecipeComponent.objects.filter(group__in=groups)
    filling = lab.FillingPlanLine.objects.filter(recipe_version__in=ids)
    lab.RecipeProperty.objects.filter(
        models.Q(recipe_version__in=ids) | models.Q(group__in=groups)
        | models.Q(component__in=components)
        | models.Q(filling_plan_line__in=filling)
    ).delete()
    maintenance_delete(lab.CalculationRecord.objects.filter(
        recipe_version__in=ids,
    ))
    filling.delete()
    components.delete()
    groups.delete()
    recipes.delete()
    delete_events('recipe', ids)
    return len(ids)


def remove_catalog(model, queryset, entity_type):
    """Retain catalog entries referenced by user records."""
    from django.db.models.deletion import ProtectedError
    for item in queryset:
        try:
            maintenance_delete(model.objects.filter(pk=item.pk))
        except ProtectedError:
            continue
        delete_events(entity_type, [item.pk])


def remove_templates():
    for model, entity, names in (
        (lab.RecipeTemplate, 'recipe_template',
         ['Зимняя пена', 'Зимняя улучшенная пена']),
    ):
        remove_catalog(model, model.objects.filter(
            created_by__full_name__in=EMPLOYEES, name__in=names,
        ), entity)


def remove_materials():
    names = (*MATERIAL_NAMES, *('DEMO · ' + n for n in MATERIAL_NAMES))
    lots = lab.MaterialLot.objects.filter(models.Q(
        source__original_path='Учебный зимний набор LIMS',
        lot_code__in=['ЗИМА-2026-001', 'ЗИМА-2026-002', 'ЗИМА-2026-К01'],
    ) | models.Q(lot_code='DEMO-LOT-001', material__name__in=names))
    remove_catalog(lab.MaterialLot, lots, 'lot')
    materials = lab.Material.objects.filter(name__in=names).filter(
        models.Q(notes__contains='учебного зимнего набора')
        | models.Q(name__startswith='DEMO · ')
    )
    remove_catalog(lab.Material, materials, 'material')


def retire_employees(author):
    """Preserve user work and conversations under the requested author profile."""
    profiles = lab.EmployeeProfile.objects.filter(full_name__in=EMPLOYEES)
    ids = list(profiles.values_list('pk', flat=True))
    for model in (lab.SourceFile, lab.RecipeVersion, lab.TestSession,
                  lab.RecipeTemplate,
                  lab.CalculationRecord):
        models.QuerySet(model).filter(created_by__in=ids).update(
            created_by=author,
        )
    models.QuerySet(lab.ChangeEvent).filter(employee__in=ids).update(
        employee=author,
    )
    lab.AssistantRequestLog.objects.filter(employee__in=ids).update(
        employee=author,
    )
    lab.AssistantConversation.objects.filter(employee__in=ids).update(
        employee=author,
    )
    profiles.delete()


def remove_demo(author):
    counts = {'removed_tests': remove_tests(), 'removed_recipes': remove_recipes()}
    remove_templates()
    remove_materials()
    remove_catalog(lab.MixtureType, lab.MixtureType.objects.filter(
        code__in=['DEMO-316', 'DEMO-302'],
    ), 'mixture_type')
    remove_catalog(lab.Metric, lab.Metric.objects.filter(
        code__startswith='DEMO_',
    ), 'metric')
    remove_catalog(lab.SourceFile, lab.SourceFile.objects.filter(
        original_path='Учебный зимний набор LIMS',
    ), 'source')
    retire_employees(author)
    return counts
