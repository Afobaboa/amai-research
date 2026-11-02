from django.db import migrations


def use_recipe_identifiers(apps, schema_editor):
    matches = apps.get_model('lab', 'RecipeMatch')
    cards = apps.get_model('lab', 'TestSession')
    database = schema_editor.connection.alias
    for match in matches.objects.using(database).filter(status='confirmed').select_related('recipe_version').iterator():
        cards.objects.using(database).filter(pk=match.session_id).update(
            identifier=match.recipe_version.version_label,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('lab', '0009_feedback'),
    ]

    operations = [
        migrations.RunPython(use_recipe_identifiers, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='recipematch',
            name='reason',
        ),
    ]
