"""Retired recipe-template routes preserve historical documents and evidence."""

from copy import deepcopy
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import NoReverseMatch, reverse

from .assistant_tools import ENTITIES
from .models import (
    ChangeEvent, EmployeeProfile, Material, MaterialLot, MixtureType,
    RecipeComponent, RecipeGroup, RecipeTemplate, RecipeVersion,
)
from .test_recipe_editor import simple_recipe_payload


@override_settings(DEMO_MODE=True)
class TemplateRetirementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = EmployeeProfile.objects.create(full_name='Автор', role='author')
        cls.reader = EmployeeProfile.objects.create(full_name='Читатель', role='reader')
        cls.material = Material.objects.create(name='Материал прежнего шаблона')
        cls.lot = MaterialLot.objects.create(
            material=cls.material, lot_code='TEMPLATE-01',
            density_g_ml=Decimal('1.1'), price_per_kg=Decimal('100'),
        )
        cls.mixture = MixtureType.objects.create(code='TEMPLATE', title='Серия')
        cls.template = RecipeTemplate.objects.create(
            name='Исторический шаблон', definition=cls.definition(),
            created_by=cls.author,
        )
        cls.recipe = RecipeVersion.objects.create(
            version_label='TPL.1', title='Исходная рецептура', status='published',
            mixture_type=cls.mixture, created_by=cls.author,
            recipe_template=cls.template, template_snapshot={'version': 1},
        )
        group = RecipeGroup.objects.create(
            recipe_version=cls.recipe, label='A', quantity_basis='mass_percent',
            completeness='complete',
        )
        RecipeComponent.objects.create(
            group=group, position=1, specified_lot=cls.lot, quantity_value=100,
        )

    @classmethod
    def definition(cls):
        return {'components': [{'group_label': 'A', 'quantity_basis': 'mass_percent',
                                'completeness': 'complete',
                                'specified_lot': cls.lot.pk, 'quantity_value': '100'}],
                'filling': [{'group_label': 'A', 'mass_g': '220'}]}

    def sign_in(self, employee=None):
        session = self.client.session
        session.update({'site_authenticated': True,
                        'employee_id': (employee or self.author).pk})
        session.save()

    def retired_paths(self):
        return ['/recipe-templates/', '/recipe-templates/new/',
                f'/recipe-templates/{self.template.pk}/',
                f'/recipe-templates/{self.template.pk}/fork/',
                f'/recipe-templates/from-recipe/{self.recipe.pk}/']

    def test_retired_routes_are_unavailable_for_authors_and_readers(self):
        for employee in (self.author, self.reader):
            self.sign_in(employee)
            for path in self.retired_paths():
                with self.subTest(employee=employee.pk, path=path):
                    self.assertEqual(self.client.get(path).status_code, 404)
                    self.assertEqual(self.client.post(path, {'name': 'Подмена'}).status_code,
                                     404)
        self.assertEqual(RecipeTemplate.objects.count(), 1)
        self.assertFalse(ChangeEvent.objects.exists())

    def test_retired_routes_have_no_public_names_or_assistant_registry_entry(self):
        self.assertNotIn('recipe_templates', ENTITIES)
        for name in ('recipe_template_list', 'recipe_template_create',
                     'recipe_template_detail', 'recipe_template_fork',
                     'recipe_template_from_recipe'):
            with self.subTest(route=name), self.assertRaises(NoReverseMatch):
                reverse(name)

    def test_recipe_pages_have_fork_action_without_template_navigation_or_actions(self):
        self.sign_in()
        for path in (reverse('recipe_list'), reverse('recipe_create'),
                     reverse('recipe_detail', args=[self.recipe.pk])):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            for text in ('/recipe-templates/', 'Сохранить как шаблон',
                         'Начать с шаблона', 'Шаблоны рецептур'):
                self.assertNotContains(response, text)
        self.assertContains(response, reverse('recipe_fork', args=[self.recipe.pk]))

    def test_query_and_post_cannot_select_a_retired_template_for_new_recipe(self):
        self.sign_in()
        page = self.client.get(reverse('recipe_create'), {'template': self.template.pk})
        self.assertEqual(page.status_code, 200)
        self.assertIsNone(page.context['polyol_formset'].forms[0]['quantity'].value())
        payload = simple_recipe_payload(self.material, self.lot)
        payload.update(template_id=str(self.template.pk),
                       recipe_template=str(self.template.pk))
        response = self.client.post(reverse('recipe_create'), payload)
        self.assertEqual(response.status_code, 302)
        created = RecipeVersion.objects.get(version_label='TEST.2')
        self.assertIsNone(created.recipe_template_id)
        self.assertEqual(created.template_snapshot, {})

    def test_legacy_documents_remain_immutable_and_versioned(self):
        original = deepcopy(self.template.definition)
        second = deepcopy(original)
        second['filling'][0]['mass_g'] = '230'
        version = RecipeTemplate.objects.create(
            name=self.template.name, version=2, definition=second,
        )
        self.template.refresh_from_db()
        self.assertEqual(self.template.definition, original)
        self.assertEqual(version.definition['filling'][0]['mass_g'], '230')
        with self.assertRaises(ValidationError):
            self.template.name = 'Подмена истории'
            self.template.save()
        self.recipe.refresh_from_db()
        self.assertEqual(self.recipe.recipe_template_id, self.template.pk)
        self.assertEqual(self.recipe.template_snapshot, {'version': 1})

    def test_invalid_legacy_definition_is_rejected_without_changing_saved_documents(self):
        invalid = self.definition()
        invalid['filling'][0]['group_label'] = 'UNKNOWN'
        with self.assertRaises(ValidationError):
            RecipeTemplate.objects.create(name='Некорректная запись', definition=invalid)
        with self.assertRaises(ValidationError):
            RecipeTemplate.objects.create(name=self.template.name, version=1,
                                          definition=self.definition())
        self.assertEqual(RecipeTemplate.objects.count(), 1)
        self.assertFalse(ChangeEvent.objects.exists())

    def test_historical_template_events_remain_readable_without_retired_links(self):
        ChangeEvent.objects.create(
            employee=self.author, entity_type='recipe_template',
            entity_id=self.template.pk, action='created',
            summary='Сохранён исторический шаблон',
        )
        self.sign_in()
        page = self.client.get(reverse('history'))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Сохранён исторический шаблон')
        self.assertNotContains(page, '/recipe-templates/')
