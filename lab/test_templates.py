from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import (ChangeEvent, EmployeeProfile, Material, MaterialLot,
                     MixtureType, RecipeComponent, RecipeGroup, RecipeTemplate, RecipeVersion)


@override_settings(DEMO_MODE=True)
class TemplateWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = EmployeeProfile.objects.create(
            full_name='Автор шаблонов', role='author')
        cls.reader = EmployeeProfile.objects.create(
            full_name='Читатель шаблонов', role='reader')
        cls.material = Material.objects.create(name='Материал шаблона')
        cls.lot = MaterialLot.objects.create(
            material=cls.material, lot_code='TEMPLATE-01',
            density_g_ml=Decimal('1.1'), price_per_kg=Decimal('100'))
        cls.mixture = MixtureType.objects.create(
            code='TEMPLATE', title='Зимняя тестовая серия')
        cls.recipe = RecipeVersion.objects.create(
            version_label='TPL.1', title='Исходная рецептура', status='published',
            mixture_type=cls.mixture,
            created_by=cls.author)
        cls.group = RecipeGroup.objects.create(
            recipe_version=cls.recipe, label='A', quantity_basis='mass_percent',
            completeness='complete')
        RecipeComponent.objects.create(
            group=cls.group, position=1, specified_lot=cls.lot,
            quantity_value=Decimal('100'))

    def sign_in(self, employee=None):
        session = self.client.session
        session['site_authenticated'] = True
        session['employee_id'] = (employee or self.author).pk
        session.save()

    def recipe_payload(self, **extra):
        payload = {
            'name': 'Зимний шаблон', 'version': '1',
            'mixture_type': str(self.mixture.pk),
            'batch_mass_g': '1000',
            'description': 'Учебный шаблон',
            'components-TOTAL_FORMS': '1', 'components-INITIAL_FORMS': '0',
            'components-0-group_label': 'A',
            'components-0-quantity_basis': 'mass_percent',
            'components-0-completeness': 'complete', 'components-0-material': '',
            'components-0-specified_lot': str(self.lot.pk),
            'components-0-quantity_value': '100',
            'filling-TOTAL_FORMS': '1', 'filling-INITIAL_FORMS': '0',
            'filling-0-group_label': 'A', 'filling-0-mass_g': '220'}
        payload.update(extra)
        return payload

    def test_author_creates_recipe_template_and_fixed_new_version(self):
        self.sign_in()
        response = self.client.post(reverse('recipe_template_create'), self.recipe_payload())
        template = RecipeTemplate.objects.get(name='Зимний шаблон', version=1)
        self.assertRedirects(response, reverse('recipe_template_detail', args=[template.pk]))
        self.assertEqual(template.definition['components'][0]['quantity_value'], '100')
        self.assertEqual(template.definition['filling'][0]['mass_g'], '220')
        self.assertContains(self.client.get(reverse('recipe_template_detail', args=[template.pk])), '?template=' + str(template.pk))
        response = self.client.post(reverse('recipe_template_fork', args=[template.pk]), self.recipe_payload(
            version='2', **{'components-INITIAL_FORMS': '1', 'filling-INITIAL_FORMS': '1', 'filling-0-mass_g': '230'}))
        second = RecipeTemplate.objects.get(name='Зимний шаблон', version=2)
        self.assertRedirects(response, reverse('recipe_template_detail', args=[second.pk]))
        template.refresh_from_db()
        self.assertEqual(template.definition['filling'][0]['mass_g'], '220')
        self.assertEqual(second.definition['filling'][0]['mass_g'], '230')
        with self.assertRaises(ValidationError):
            template.name = 'Нельзя переписать'
            template.save()

    def test_invalid_template_definition_shows_error_and_is_atomic(self):
        self.sign_in()
        response = self.client.post(reverse('recipe_template_create'), self.recipe_payload(**{'filling-0-group_label': 'B'}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Группа плана заправки должна присутствовать в составе')
        self.assertFalse(RecipeTemplate.objects.filter(name='Зимний шаблон').exists())
        self.assertFalse(ChangeEvent.objects.filter(entity_type='recipe_template').exists())

    def test_saving_existing_recipe_as_template_uses_actual_saved_rows(self):
        self.sign_in()
        response = self.client.get(reverse(
            'recipe_template_from_recipe', args=[self.recipe.pk]))
        self.assertEqual(response.status_code, 200)
        formset = response.context['component_formset']
        self.assertIsNone(formset.initial[0]['material'])
        self.assertEqual(formset.initial[0]['specified_lot'], self.lot.pk)
        self.assertEqual(Decimal(formset.initial[0]['quantity_value']), Decimal('100'))
        self.assertNotIn('calculation_template', response.context['form'].fields)

    def test_reader_views_templates_and_cannot_create_or_fork(self):
        template = RecipeTemplate.objects.create(name='Чтение', definition={'components': [], 'filling': []})
        self.sign_in(self.reader)
        for route in ('recipe_template_list',):
            self.assertEqual(self.client.get(reverse(route)).status_code, 200)
        for route, args in [('recipe_template_create', []), ('recipe_template_fork', [template.pk]),
                            ('recipe_template_from_recipe', [self.recipe.pk])]:
            self.assertEqual(self.client.post(reverse(route, args=args), self.recipe_payload()).status_code, 403)
        self.assertEqual(RecipeTemplate.objects.count(), 1)
        self.assertFalse(ChangeEvent.objects.exists())

    def test_concurrent_template_version_error_is_a_form_error_and_rolls_back(self):
        self.sign_in()
        for model, route, payload in [
            (RecipeTemplate, 'recipe_template_create', self.recipe_payload()),
        ]:
            before = model.objects.count()
            with self.subTest(template=model.__name__), patch.object(model, 'save', side_effect=IntegrityError('concurrent unique version')):
                response = self.client.post(reverse(route), payload)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'Эта версия шаблона уже сохранена другим автором')
            self.assertEqual(model.objects.count(), before)
            self.assertFalse(ChangeEvent.objects.exists())
