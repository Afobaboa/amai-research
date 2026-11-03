"""Retired lot statuses upgrade without altering properties or saved evidence."""
from decimal import Decimal

from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class LotStatusMigrationTests(TransactionTestCase):
    previous = [('lab', '0017_disjoint_measurement_groups')]
    current = [('lab', '0018_remove_lot_quarantine')]

    def setUp(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        executor.migrate(self.previous)
        self.old = executor.loader.project_state(self.previous).apps

    def test_quarantine_becomes_active_and_other_data_is_preserved(self):
        material = self.old.get_model('lab', 'Material').objects.create(name='Сырьё')
        source = self.old.get_model('lab', 'SourceFile').objects.create(title='Паспорт')
        Lot = self.old.get_model('lab', 'MaterialLot')
        for status in ('active', 'quarantine', 'exhausted'):
            Lot.objects.create(
                material=material, lot_code=status, status=status, source=source,
                density_g_ml=Decimal('1.05'), price_per_kg=Decimal('200'),
                notes='Сохранённое примечание',
            )
        lot = Lot.objects.get(status='quarantine')
        expected = Lot.objects.values().get(pk=lot.pk)
        expected['status'] = 'active'
        recipe = self.old.get_model('lab', 'RecipeVersion').objects.create(version_label='MIGRATION')
        group = self.old.get_model('lab', 'RecipeGroup').objects.create(recipe_version=recipe, label='A')
        component = self.old.get_model('lab', 'RecipeComponent').objects.create(
            group=group, position=1, specified_lot=lot, quantity_value=100,
        )
        snapshot = {'lot_id': lot.pk, 'lot_status': 'quarantine'}
        record = self.old.get_model('lab', 'CalculationRecord').objects.create(
            recipe_version=recipe, inputs=snapshot,
        )
        event = self.old.get_model('lab', 'ChangeEvent').objects.create(
            entity_type='lot', entity_id=lot.pk, action='updated',
            summary='Прежний статус', after={'status': 'quarantine'},
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.current)
        current = executor.loader.project_state(self.current).apps
        Lot = current.get_model('lab', 'MaterialLot')
        self.assertEqual(Lot.objects.values().get(pk=lot.pk), expected)
        self.assertEqual(dict(Lot.objects.values_list('lot_code', 'status')), {
            'active': 'active', 'quarantine': 'active', 'exhausted': 'exhausted',
        })
        self.assertEqual(current.get_model('lab', 'RecipeComponent').objects.get(
            pk=component.pk,
        ).specified_lot_id, lot.pk)
        self.assertEqual(current.get_model('lab', 'CalculationRecord').objects.get(
            pk=record.pk,
        ).inputs, snapshot)
        self.assertEqual(current.get_model('lab', 'ChangeEvent').objects.get(
            pk=event.pk,
        ).after, {'status': 'quarantine'})
        with self.assertRaises(IntegrityError), transaction.atomic():
            Lot.objects.filter(pk=lot.pk).update(status='quarantine')

    def test_supplier_moves_to_lots_without_overwriting_explicit_suppliers(self):
        Material = self.old.get_model('lab', 'Material')
        material = Material.objects.create(name='Сырьё', supplier='Прежний поставщик')
        other = Material.objects.create(name='Другое сырьё')
        Lot = self.old.get_model('lab', 'MaterialLot')
        Lot.objects.create(material=material, lot_code='EMPTY')
        Lot.objects.create(material=material, lot_code='EXPLICIT', supplier='Поставщик партии')
        Lot.objects.create(material=other, lot_code='UNKNOWN')

        target = [('lab', '0019_remove_material_supplier')]
        executor = MigrationExecutor(connection)
        executor.migrate(target)
        current = executor.loader.project_state(target).apps
        Lot = current.get_model('lab', 'MaterialLot')
        self.assertEqual(dict(Lot.objects.values_list('lot_code', 'supplier')), {
            'EMPTY': 'Прежний поставщик', 'EXPLICIT': 'Поставщик партии', 'UNKNOWN': '',
        })
        fields = {field.name for field in current.get_model('lab', 'Material')._meta.fields}
        self.assertNotIn('supplier', fields)
