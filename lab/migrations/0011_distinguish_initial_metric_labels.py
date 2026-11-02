from django.db import migrations


RENAMES = [
    ('density_paper', 'Плотность в ядре', 'Плотность в ядре (на бумаге)'),
    ('density_mold', 'Плотность в ядре', 'Плотность в ядре (в форме)'),
    ('yield_paper', 'Выход пены (расчётный)',
     'Выход пены (расчётный, на бумаге)'),
    ('yield_mold', 'Выход пены (расчётный)',
     'Выход пены (расчётный, в форме)'),
    ('shrinkage_paper_day_3', 'Усадка пены, срок измерения: 3 сутки',
     'Усадка пены, срок измерения: 3 сут'),
]


def rename_initial_metrics(apps, schema_editor):
    metrics = apps.get_model('lab', 'Metric')
    database = schema_editor.connection.alias
    for code, previous, label in RENAMES:
        metrics.objects.using(database).filter(
            code=code, label=previous,
        ).update(label=label)


class Migration(migrations.Migration):
    dependencies = [('lab', '0010_simplify_test_card_recipe_link')]

    operations = [
        migrations.RunPython(rename_initial_metrics, migrations.RunPython.noop),
    ]
