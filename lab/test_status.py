"""Status shortcuts preserve data, publication checks and terminal states."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    ChangeEvent, EmployeeProfile, Material, MaterialLot, Metric, RecipeComponent,
    RecipeGroup, RecipeMatch, RecipeVersion, MeasurementGroup, Result, TestRun, TestSession,
)


class StatusTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = EmployeeProfile.objects.create(full_name='Автор', role='author')
        cls.reader = EmployeeProfile.objects.create(full_name='Читатель', role='reader')
        cls.material = Material.objects.create(name='Материал')
        cls.lot = MaterialLot.objects.create(
            material=cls.material, lot_code='LOT.1', density_g_ml=1,
        )
        cls.recipe = RecipeVersion.objects.create(
            version_label='STATUS.1', title='Исходное название', notes='Примечание',
        )
        cls.group = RecipeGroup.objects.create(
            recipe_version=cls.recipe, label='A', quantity_basis='mass_percent',
            completeness='complete',
        )
        cls.component = RecipeComponent.objects.create(
            group=cls.group, position=1, specified_lot=cls.lot, quantity_value=100,
        )
        cls.card = TestSession.objects.create(
            identifier='STATUS-TEST', title='Карточка', status='active',
            notes='Исходный вывод',
        )

    def setUp(self):
        self.sign_in(self.author)

    def sign_in(self, employee):
        session = self.client.session
        session['site_authenticated'] = True
        session['employee_id'] = employee.pk
        session.save()

    def change(self, kind, instance, status, **extra):
        return self.client.post(reverse(f'{kind}_status', args=[instance.pk]), {
            'status': status, 'current_status': instance.status, **extra,
        })

    def test_get_is_read_only_and_buttons_are_next_to_edit(self):
        for kind, instance in (
            ('recipe', self.recipe), ('test', self.card), ('lot', self.lot),
        ):
            with self.subTest(kind=kind):
                url = reverse(f'{kind}_status', args=[instance.pk])
                self.assertContains(self.client.get(url), 'Новый статус')
                detail = self.client.get(reverse(f'{kind}_detail', args=[instance.pk]))
                self.assertContains(detail, f'href="{url}"')
        self.assertFalse(ChangeEvent.objects.exists())
        self.assertFalse(self.recipe.calculations.exists())

    def test_recipe_publication_preserves_composition_and_saves_snapshot(self):
        response = self.change('recipe', self.recipe, 'published', title='Подмена')
        self.assertRedirects(response, reverse('recipe_detail', args=[self.recipe.pk]))
        self.recipe.refresh_from_db()
        self.assertEqual(self.recipe.status, 'published')
        self.assertEqual(self.recipe.title, 'Исходное название')
        self.assertEqual(self.recipe.notes, 'Примечание')
        self.assertEqual(self.recipe.groups.get().pk, self.group.pk)
        self.assertEqual(self.group.components.get().pk, self.component.pk)
        record = self.recipe.calculations.get()
        self.assertEqual(record.outputs['purpose'], 'publication')
        event = ChangeEvent.objects.get(entity_type='recipe')
        self.assertEqual(event.before['status'], 'draft')
        self.assertEqual(event.after['status'], 'published')
        self.assertEqual(event.employee, self.author)
        self.assertEqual(event.after['calculation_ids'], [record.pk])

    def test_empty_recipe_cannot_be_published(self):
        self.component.delete()
        response = self.change('recipe', self.recipe, 'published')
        self.assertContains(response, 'добавьте хотя бы один компонент')
        self.recipe.refresh_from_db()
        self.assertEqual(self.recipe.status, 'draft')
        self.assertFalse(ChangeEvent.objects.exists())
        self.assertFalse(self.recipe.calculations.exists())

    def test_incomplete_mass_percent_is_checked_before_publication(self):
        self.component.quantity_value = Decimal(90)
        self.component.save()
        response = self.change('recipe', self.recipe, 'published')
        self.assertContains(response, '100 массовых процентов')
        self.recipe.refresh_from_db()
        self.assertEqual(self.recipe.status, 'draft')

    def test_quarantined_exhausted_and_expired_lots_block_publication(self):
        for changes in (
            {'status': 'quarantine'}, {'status': 'exhausted'},
            {
                'status': 'active',
                'expires_on': timezone.localdate() - timedelta(days=1),
            },
        ):
            with self.subTest(changes=changes):
                MaterialLot.objects.filter(pk=self.lot.pk).update(**changes)
                response = self.change('recipe', self.recipe, 'published')
                self.assertContains(response, 'недоступна для нового')
                self.recipe.refresh_from_db()
                self.assertEqual(self.recipe.status, 'draft')
        self.assertFalse(ChangeEvent.objects.exists())

    def test_test_completion_changes_only_status_and_preserves_links(self):
        match = RecipeMatch.objects.create(
            session=self.card, recipe_version=self.recipe,
        )
        metric = Metric.objects.create(code='STATUS_TEMP', label='Температура')
        run = TestRun.objects.create(session=self.card, scope='Основные параметры',
                                    measurement_group=MeasurementGroup.objects.get(code='basic_parameters'))
        condition = Result.objects.create(test_run=run, metric=metric, numeric_value=20)
        response = self.change('test', self.card, 'completed', notes='Подмена')
        self.assertRedirects(response, reverse('test_detail', args=[self.card.pk]))
        self.card.refresh_from_db()
        self.assertEqual(self.card.status, 'completed')
        self.assertEqual(self.card.title, 'Карточка')
        self.assertEqual(self.card.notes, 'Исходный вывод')
        self.assertEqual(self.card.recipe_matches.get().pk, match.pk)
        self.assertEqual(run.results.get().pk, condition.pk)
        event = ChangeEvent.objects.get(entity_type='test')
        self.assertEqual(event.before['status'], 'active')
        self.assertEqual(event.after['status'], 'completed')

    def test_lot_status_preserves_properties_and_records_history(self):
        response = self.change('lot', self.lot, 'quarantine', density_g_ml='99')
        self.assertRedirects(response, reverse('lot_detail', args=[self.lot.pk]))
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.status, 'quarantine')
        self.assertEqual(self.lot.density_g_ml, Decimal(1))
        event = ChangeEvent.objects.get(entity_type='lot')
        self.assertEqual(event.before['status'], 'active')
        self.assertEqual(event.after['status'], 'quarantine')

    def test_terminal_records_and_linked_drafts_cannot_change_status(self):
        for status in ('published', 'archived'):
            RecipeVersion.objects.filter(pk=self.recipe.pk).update(status=status)
            response = self.change('recipe', self.recipe, 'draft')
            self.assertEqual(response.status_code, 403)
        TestSession.objects.filter(pk=self.card.pk).update(status='completed')
        self.assertEqual(self.change('test', self.card, 'active').status_code, 403)
        RecipeVersion.objects.filter(pk=self.recipe.pk).update(status='draft')
        RecipeMatch.objects.create(session=self.card, recipe_version=self.recipe)
        response = self.change('recipe', self.recipe, 'published')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ChangeEvent.objects.exists())

    def test_stale_invalid_or_missing_status_is_rejected(self):
        for data in (
            {'status': 'completed', 'current_status': 'draft'},
            {'status': 'invalid', 'current_status': 'active'},
            {'status': 'active', 'current_status': 'active'},
            {'status': 'completed'}, {},
        ):
            response = self.client.post(
                reverse('test_status', args=[self.card.pk]), data,
            )
            self.assertEqual(response.status_code, 200)
            self.card.refresh_from_db()
            self.assertEqual(self.card.status, 'active')
        self.assertFalse(ChangeEvent.objects.exists())

    def test_readers_cannot_open_or_post_status_forms(self):
        self.sign_in(self.reader)
        for kind, instance in (
            ('recipe', self.recipe), ('test', self.card), ('lot', self.lot),
        ):
            url = reverse(f'{kind}_status', args=[instance.pk])
            self.assertEqual(self.client.get(url).status_code, 403)
            response = self.client.post(url, {'status': 'draft'})
            self.assertEqual(response.status_code, 403)
            detail = self.client.get(reverse(f'{kind}_detail', args=[instance.pk]))
            self.assertNotContains(detail, f'href="{url}"')
        self.assertFalse(ChangeEvent.objects.exists())
