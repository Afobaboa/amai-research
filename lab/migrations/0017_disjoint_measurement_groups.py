"""Remove the default balloon overlap while preserving recorded measurements."""
from django.db import migrations


BALLOON_METRICS = {
    'can_mass_after', 'can_mass_before', 'can_pressure', 'dispensed_mass',
    'recorded_gross_mass', 'recorded_net_mass', 'shakeability',
}


def move_results(apps, alias, metric, owner, others, targets):
    Run = apps.get_model('lab', 'TestRun')
    Result = apps.get_model('lab', 'Result')
    runs = Run.objects.using(alias).filter(
        measurement_group_id__in=others, results__metric_id=metric.pk,
    ).distinct()
    for run in runs.iterator():
        key = (run.pk, owner.pk)
        if key not in targets:
            targets[key] = Run.objects.using(alias).create(
                session_id=run.session_id, measurement_group_id=owner.pk,
                scope=owner.name, method_code=run.method_code,
                scope_cell_id=run.scope_cell_id,
            ).pk
        Result.objects.using(alias).filter(
            test_run_id=run.pk, metric_id=metric.pk,
        ).update(test_run_id=targets[key])


def forward(apps, schema_editor):
    Group = apps.get_model('lab', 'MeasurementGroup')
    alias = schema_editor.connection.alias
    basic = Group.objects.using(alias).filter(code='basic_parameters').first()
    balloon = Group.objects.using(alias).filter(code='can_checks').first()
    if basic is None or balloon is None:
        return
    metrics = list(basic.metrics.using(alias).filter(
        code__in=BALLOON_METRICS, measurement_groups=balloon,
    ))
    targets = {}
    for metric in metrics:
        move_results(apps, alias, metric, balloon, [basic.pk], targets)
        basic.metrics.remove(metric)


class Migration(migrations.Migration):
    dependencies = [('lab', '0016_measurement_missing_reason')]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
