"""Preserve existing runs and convert recorded conditions into measurements."""
from django.db import migrations

GROUP_SPECS = {'basic_parameters': ('Основные параметры',
                      ['ambient_temperature',
                       'can_mass_after',
                       'can_mass_before',
                       'can_pressure',
                       'dispensed_mass',
                       'recorded_gross_mass',
                       'recorded_net_mass',
                       'relative_humidity',
                       'shakeability']),
 'paper': ('На бумаге',
           ['bead_count_paper',
            'cut_time',
            'density_paper',
            'shrinkage_paper',
            'shrinkage_paper_day_2',
            'shrinkage_paper_day_3',
            'tack_free',
            'yield_paper']),
 'mold': ('В форме', ['density_mold', 'yield_mold']),
 'additional': ('Дополнительные показатели', ['bead_count_other', 'yield_tm1003']),
 'can_checks': ('Проверка баллона',
                ['can_mass_after',
                 'can_mass_before',
                 'can_pressure',
                 'dispensed_mass',
                 'recorded_gross_mass',
                 'recorded_net_mass',
                 'shakeability'])}

METRIC_SPECS = {'ambient_temperature': ('Температура окружающей среды', '°C', 'numeric'),
 'relative_humidity': ('Относительная влажность', '%', 'numeric'),
 'recorded_net_mass': ('Масса нетто по записи', 'г', 'numeric'),
 'can_mass_before': ('Масса баллона до выпуска', 'г', 'numeric'),
 'can_mass_after': ('Масса баллона после выпуска', 'г', 'numeric'),
 'dispensed_mass': ('Масса выпуска', 'г', 'numeric'),
 'tack_free': ('Время отлипа', 'мин', 'numeric'),
 'cut_time': ('Время резки', 'мин', 'numeric'),
 'bead_count_paper': ('Выход в «змейках»', 'шт.', 'numeric'),
 'density_paper': ('Плотность в ядре (на бумаге)', 'кг/м³', 'numeric'),
 'yield_paper': ('Выход пены (расчётный, на бумаге)', 'л', 'numeric'),
 'shrinkage_paper': ('Усадка пены (срок не указан)', '%', 'numeric'),
 'yield_mold': ('Выход пены (расчётный, в форме)', 'л', 'numeric'),
 'bead_count_other': ('Дополнительная запись выхода в «змейках»', None, 'numeric'),
 'can_pressure': ('Давление в баллоне', 'атм', 'numeric'),
 'shakeability': ('Встряхиваемость по шкале 0–5', None, 'text'),
 'shrinkage_paper_day_3': ('Усадка пены, срок измерения: 3 сут', '%', 'numeric'),
 'shrinkage_paper_day_2': ('Усадка пены, срок измерения: 2 сут', '%', 'numeric'),
 'density_mold': ('Плотность в ядре (в форме)', 'кг/м³', 'numeric'),
 'yield_tm1003': ('Выход в куб, ТМ1003', 'л', 'numeric'),
 'recorded_gross_mass': ('Масса брутто по записи', 'г', 'numeric')}


def seed_measurement_groups(registry=None, alias='default'):
    if registry is None:
        from django.apps import apps
        registry = apps
    Group = registry.get_model('lab', 'MeasurementGroup')
    Metric = registry.get_model('lab', 'Metric')
    for code, (name, codes) in GROUP_SPECS.items():
        group, _ = Group.objects.using(alias).get_or_create(
            code=code, defaults={'name': name},
        )
        for metric_code in codes:
            label, unit, kind = METRIC_SPECS[metric_code]
            metric, _ = Metric.objects.using(alias).get_or_create(
                code=metric_code,
                defaults={'label': label, 'unit_code': unit, 'value_kind': kind},
            )
            group.metrics.add(metric)


ALIASES = {
    'Параметры карточки': 'basic_parameters',
    'Основные параметры': 'basic_parameters',
    'Основное испытание': 'basic_parameters',
    'Испытание на бумаге': 'paper', 'На бумаге': 'paper', 'paper': 'paper',
    'Испытание в форме': 'mold', 'В форме': 'mold', 'form': 'mold',
    'can_checks': 'can_checks', 'Проверка баллона': 'can_checks',
    'Дополнительные показатели': 'additional',
}


def convert_runs(apps, alias):
    Group = apps.get_model('lab', 'MeasurementGroup')
    Run = apps.get_model('lab', 'TestRun')
    for run in Run.objects.using(alias).all().iterator():
        code = ALIASES.get(run.scope)
        if code:
            group = Group.objects.using(alias).get(code=code)
        else:
            group, _ = Group.objects.using(alias).get_or_create(
                name=run.scope, defaults={'code': f'legacy-{run.pk}'},
            )
        run.measurement_group_id = group.pk
        run.scope = group.name
        run.save(using=alias, update_fields=['measurement_group', 'scope'])
        group.metrics.add(*run.results.values_list('metric_id', flat=True))


def convert_conditions(apps, alias):
    Group = apps.get_model('lab', 'MeasurementGroup')
    Run = apps.get_model('lab', 'TestRun')
    Result = apps.get_model('lab', 'Result')
    Condition = apps.get_model('lab', 'SessionCondition')
    group = Group.objects.using(alias).get(code='basic_parameters')
    for row in Condition.objects.using(alias).order_by('pk').iterator():
        run = Run.objects.using(alias).filter(
            session_id=row.session_id, measurement_group_id=group.pk,
        ).order_by('pk').first()
        if run is None:
            run = Run.objects.using(alias).create(
                session_id=row.session_id, measurement_group_id=group.pk,
                scope=group.name,
            )
        Result.objects.using(alias).create(
            test_run_id=run.pk, metric_id=row.metric_id,
            numeric_value=row.numeric_value, text_value=row.text_value,
            source_cell_id=row.source_cell_id, origin='recorded',
            value_state='present',
        )
        group.metrics.add(row.metric_id)
    Condition.objects.using(alias).all().delete()


def forward(apps, schema_editor):
    alias = schema_editor.connection.alias
    seed_measurement_groups(apps, alias)
    convert_runs(apps, alias)
    convert_conditions(apps, alias)


class Migration(migrations.Migration):
    dependencies = [('lab', '0013_measurement_groups')]
    # On rollback values remain ordinary results; no measured data is deleted.
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
