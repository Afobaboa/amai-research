from django.db import migrations, models


def retain_existing_categories(apps, schema_editor):
    material = apps.get_model('lab', 'Material')
    category = apps.get_model('lab', 'MaterialCategory')
    names = material.objects.using(schema_editor.connection.alias).order_by().exclude(
        category='',
    ).values_list('category', flat=True).distinct()
    for name in names:
        if name.strip():
            category.objects.using(schema_editor.connection.alias).get_or_create(
                name=name,
            )


class Migration(migrations.Migration):
    dependencies = [('lab', '0005_assistantconversation_assistantjob')]

    operations = [
        migrations.CreateModel(
            name='MaterialCategory',
            fields=[
                ('id', models.BigAutoField(
                    auto_created=True, primary_key=True,
                    serialize=False, verbose_name='ID',
                )),
                ('name', models.CharField(
                    max_length=100, unique=True,
                    verbose_name='Категория материалов',
                )),
                ('description', models.TextField(
                    blank=True, verbose_name='Описание',
                )),
            ],
            options={
                'ordering': ['name', 'pk'],
                'verbose_name': 'Категория материалов',
                'verbose_name_plural': 'Категории материалов',
            },
        ),
        migrations.RunPython(
            retain_existing_categories, migrations.RunPython.noop,
        ),
    ]
