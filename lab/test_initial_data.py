"""Regression evidence for the real initial dataset and retired fixture cleanup."""
import hashlib
import json
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from . import models as lab
from .initial_cleanup import RECIPE_NOTE
from .initial_data import DATA_PATH, seed_initial
from .forms import ComponentForm


class InitialDataTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_initial()

    def test_requested_people_series_and_complete_archive_counts(self):
        self.assertSetEqual(set(lab.EmployeeProfile.objects.values_list(
            'full_name', 'role',
        )), {('Гущин Даниил', 'author'), ('Иван Иванов', 'reader')})
        self.assertSetEqual(set(lab.MixtureType.objects.values_list(
            'title', flat=True,
        )), {'Зимняя пена', 'Зимняя улучшенная пена', 'Зимняя пена с фреоном'})
        self.assertEqual(lab.RecipeVersion.objects.count(), 135)
        self.assertEqual(lab.TestSession.objects.count(), 167)
        self.assertEqual(lab.SourceFile.objects.count(), 6)
        self.assertEqual(lab.SourceSheet.objects.count(), 302)
        self.assertFalse(lab.MaterialLot.objects.exists())
        self.assertFalse(lab.Material.objects.exclude(
            density_g_ml=None, price_per_kg=None,
        ).exists())

    def test_snapshot_hashes_and_every_raw_cell_survive_loading(self):
        data = json.loads(DATA_PATH.read_text())
        root = Path(__file__).resolve().parents[2]
        for source in data['files']:
            self.assertEqual(hashlib.sha256(
                (root / source['path']).read_bytes(),
            ).hexdigest(), source['sha256'])
            sheets = lab.SourceSheet.objects.filter(
                file__original_path=source['path'],
            ).prefetch_related('cells')
            expected = {sheet['name']: sheet['cells'] for sheet in source['sheets']}
            for sheet in sheets:
                actual = {cell.address: {key: getattr(cell, key) for key in (
                    'cell_kind', 'raw_value', 'formula_text', 'cached_value',
                    'number_format',
                )} for cell in sheet.cells.all()}
                self.assertEqual(actual, expected[sheet.name], sheet.name)

    def test_formula_without_inputs_never_becomes_a_measured_zero(self):
        results = lab.Result.objects.filter(value_state='missing_inputs')
        self.assertTrue(results.exists())
        self.assertFalse(results.exclude(origin='calculated').exists())
        self.assertFalse(results.exclude(numeric_value=None, text_value=None).exists())
        self.assertTrue(lab.Result.objects.filter(value_state='unparsed').exists())
        self.assertEqual(lab.FillingPlanLine.objects.filter(
            mass_status='calculation_checked', mass_cell__formula_text__isnull=False,
        ).count(), 675)

    def test_freon_conflict_retains_mass_without_inventing_material(self):
        recipe = lab.RecipeVersion.objects.get(
            mixture_type__code='316-Ф', version_label='316Ф-1',
        )
        self.assertIn('Противоречие марки фреона', recipe.notes)
        self.assertTrue(recipe.filling_plan.filter(
            unbatched_material=None, input_group=None, mass_g__gt=0,
            label_cell__raw_value='F134a',
        ).exists())
        self.assertFalse(lab.RecipeProperty.objects.exclude(
            source_cell=None,
        ).filter(property_code='source_price_per_kg', unit_code='₽/кг').exists())

    def test_repeated_initialization_preserves_user_work_and_has_no_duplicates(self):
        material = lab.Material.objects.first()
        material.notes = 'Проверено сотрудником'
        material.price_per_kg = Decimal('123')
        material.save()
        before = self.counts()
        call_command('seed_initial', stdout=StringIO())
        self.assertEqual(self.counts(), before)
        material.refresh_from_db()
        self.assertEqual(material.notes, 'Проверено сотрудником')
        self.assertEqual(material.price_per_kg, Decimal('123'))

    def test_cross_run_formula_dependencies_link_existing_recorded_results(self):
        result = lab.Result.objects.get(
            test_run__session__source_sheet__name='316.1',
            test_run__session__source_sheet__file__title__contains='испытания',
            metric__code='yield_paper',
        )
        self.assertSetEqual(set(result.dependencies.values_list(
            'input_result__source_cell__address', flat=True,
        )), {'G6', 'C6', 'E6', 'B25'})

    def test_conflicting_source_fragment_aborts_without_overwriting_it(self):
        cell = lab.SourceCell.objects.first()
        lab.SourceCell.objects.filter(pk=cell.pk).update(raw_value='Исправлено')
        with self.assertRaises(CommandError):
            seed_initial()
        cell.refresh_from_db()
        self.assertEqual(cell.raw_value, 'Исправлено')

    def counts(self):
        return tuple(model.objects.count() for model in (
            lab.RecipeVersion, lab.TestSession, lab.SourceCell, lab.ChangeEvent,
            lab.Material, lab.Result, lab.RecipeProperty, lab.RecipeMatch,
        ))

    @override_settings(SITE_PASSWORD_HASH='unused', DEMO_MODE=False)
    def test_clear_names_provenance_and_reader_permissions_in_interface(self):
        profile = lab.EmployeeProfile.objects.get(full_name='Иван Иванов')
        session = self.client.session
        session.update({'site_authenticated': True, 'employee_id': profile.pk})
        session.save()
        recipe = lab.RecipeVersion.objects.first()
        response = self.client.get(reverse('recipe_detail', args=[recipe.pk]))
        self.assertContains(response, recipe.title)
        self.assertContains(response, 'Гидроксильное число')
        self.assertNotContains(response, 'ДЕМО')
        self.assertEqual(
            self.client.post(reverse('recipe_create'), {}).status_code, 403,
        )


class LegacyCleanupTests(TestCase):
    def make_legacy(self):
        employee = lab.EmployeeProfile.objects.create(
            full_name='DEMO · Анна Волкова', role='author',
        )
        mixture = lab.MixtureType.objects.create(code='DEMO-316')
        recipe = lab.RecipeVersion.objects.create(
            version_label='DEMO-316.01', title='Базовый состав',
            notes=RECIPE_NOTE, created_by=employee, mixture_type=mixture,
        )
        group = lab.RecipeGroup.objects.create(recipe_version=recipe, label='A')
        lab.RecipeComponent.objects.create(
            group=group, position=1, quantity_value=100,
        )
        lab.ChangeEvent.objects.create(
            employee=employee, entity_type='recipe', entity_id=recipe.pk,
            action='created', summary='Создана учебная запись',
        )
        return employee, recipe

    def test_legacy_alias_removes_fixtures_preserves_independent_work(self):
        employee, old = self.make_legacy()
        real = lab.RecipeVersion.objects.create(
            version_label='Личный состав', title='Рабочая запись',
            created_by=employee,
        )
        call_command('seed_demo', confirm_demo=True, stdout=StringIO())
        self.assertFalse(lab.RecipeVersion.objects.filter(pk=old.pk).exists())
        self.assertFalse(lab.EmployeeProfile.objects.filter(pk=employee.pk).exists())
        real.refresh_from_db()
        self.assertEqual(real.title, 'Рабочая запись')
        self.assertEqual(real.created_by.full_name, 'Гущин Даниил')
        self.assertFalse(lab.ChangeEvent.objects.filter(
            entity_type='recipe', entity_id=old.pk,
        ).exists())

    def test_user_fork_with_inherited_demo_notes_survives_cleanup(self):
        employee, old = self.make_legacy()
        fork = lab.RecipeVersion.objects.create(
            version_label=old.version_label + '-новая',
            notes=old.notes, created_by=employee, title='Состав сотрудника',
        )
        seed_initial()
        fork.refresh_from_db()
        self.assertEqual(fork.title, 'Состав сотрудника')
        self.assertEqual(fork.created_by.full_name, 'Гущин Даниил')

    def test_old_unattributed_demo_lot_and_material_are_removed(self):
        self.make_legacy()
        material = lab.Material.objects.create(name='DEMO · Полиол P-101')
        lot = lab.MaterialLot.objects.create(
            material=material, lot_code='DEMO-LOT-001',
        )
        seed_initial()
        self.assertFalse(lab.MaterialLot.objects.filter(pk=lot.pk).exists())
        self.assertFalse(lab.Material.objects.filter(pk=material.pk).exists())

    def test_failed_import_rolls_back_cleanup_and_profiles(self):
        employee, old = self.make_legacy()
        with patch('lab.initial_data.ArchiveLoader.load', side_effect=ValueError):
            with self.assertRaises(ValueError):
                seed_initial()
        self.assertTrue(lab.RecipeVersion.objects.filter(pk=old.pk).exists())
        self.assertTrue(lab.EmployeeProfile.objects.filter(pk=employee.pk).exists())
        self.assertFalse(lab.EmployeeProfile.objects.filter(
            full_name='Гущин Даниил',
        ).exists())


class SourceChoiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = lab.EmployeeProfile.objects.create(
            full_name='Автор', role='author',
        )
        source = lab.SourceFile.objects.create(original_path='Книга')
        cls.sheet = lab.SourceSheet.objects.create(file=source, name='Лист')
        other = lab.SourceSheet.objects.create(file=source, name='Другой лист')
        lab.SourceCell.objects.bulk_create([
            lab.SourceCell(sheet=cls.sheet, address=f'A{row}', cell_kind='n')
            for row in range(1, 1101)
        ])
        cls.other_cell = lab.SourceCell.objects.create(
            sheet=other, address='B1', cell_kind='text', raw_value='Другая запись',
        )

    def test_form_embeds_only_selected_cells_and_keeps_validation(self):
        form = ComponentForm(initial={'name_cell': self.other_cell.pk})
        html = str(form['name_cell'])
        self.assertEqual(html.count('<option'), 2)
        self.assertIn('Другой лист!B1', html)
        self.assertEqual(str(ComponentForm()['name_cell']).count('<option'), 1)
        field = form.fields['name_cell']
        self.assertEqual(field.clean(str(self.other_cell.pk)), self.other_cell)

    def test_choice_endpoint_requires_profile_and_bounds_selected_sheet(self):
        url = reverse('source_cell_choices')
        self.assertEqual(self.client.get(url).status_code, 302)
        session = self.client.session
        session.update({'site_authenticated': True, 'employee_id': self.author.pk})
        session.save()
        data = self.client.get(url, {'sheet': self.sheet.pk}).json()['cells']
        self.assertEqual(len(data), 1000)
        self.assertNotIn(self.other_cell.pk, {row['id'] for row in data})
        self.assertEqual(
            self.client.get(url, {'sheet': 'invalid'}).json(), {'cells': []},
        )
