"""Default measurement groups shared by experiments and the directory."""
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
        group, created = Group.objects.using(alias).get_or_create(
            code=code, defaults={'name': name},
        )
        if not created:
            continue
        for metric_code in codes:
            label, unit, kind = METRIC_SPECS[metric_code]
            metric, _ = Metric.objects.using(alias).get_or_create(
                code=metric_code,
                defaults={'label': label, 'unit_code': unit, 'value_kind': kind},
            )
            group.metrics.add(metric)
