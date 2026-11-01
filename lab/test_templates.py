from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.urls import reverse

from .demo import RECIPE_NOTE
from .models import (CalculationRecord, CalculationTemplate, ChangeEvent, EmployeeProfile, Material, MaterialLot,
                     MixtureType, RecipeComponent, RecipeGroup, RecipeTemplate, RecipeVersion, SourceFile, TestSession)


@override_settings(DEMO_MODE=True)
class TemplateWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = EmployeeProfile.objects.create(full_name='Автор шаблонов', role='author')
        cls.reader = EmployeeProfile.objects.create(full_name='Читатель шаблонов', role='reader')
        cls.material = Material.objects.create(name='Материал шаблона', density_g_ml=Decimal('1.1'), price_per_kg=Decimal('100'))
        cls.mixture = MixtureType.objects.create(code='TEMPLATE', title='Зимняя тестовая серия')
        cls.calculation = CalculationTemplate.objects.create(name='Расчёт плотности', operations=['mass', 'density'], created_by=cls.author)
        cls.recipe = RecipeVersion.objects.create(version_label='TPL.1', title='Исходная рецептура', status='published',
                                                  mixture_type=cls.mixture, calculation_template=cls.calculation, created_by=cls.author)
        cls.group = RecipeGroup.objects.create(recipe_version=cls.recipe, label='A', quantity_basis='mass_percent', completeness='complete')
        RecipeComponent.objects.create(group=cls.group, position=1, unbatched_material=cls.material, quantity_value=Decimal('100'))

    def sign_in(self, employee=None):
        session = self.client.session
        session['site_authenticated'] = True
        session['employee_id'] = (employee or self.author).pk
        session.save()

    def recipe_payload(self, **extra):
        payload = {'name': 'Зимний шаблон', 'version': '1', 'mixture_type': str(self.mixture.pk),
                   'calculation_template': str(self.calculation.pk), 'batch_mass_g': '1000', 'description': 'Учебный шаблон',
                   'components-TOTAL_FORMS': '1', 'components-INITIAL_FORMS': '0',
                   'components-0-group_label': 'A', 'components-0-quantity_basis': 'mass_percent',
                   'components-0-completeness': 'complete', 'components-0-material': str(self.material.pk),
                   'components-0-quantity_value': '100',
                   'filling-TOTAL_FORMS': '1', 'filling-INITIAL_FORMS': '0',
                   'filling-0-group_label': 'A', 'filling-0-mass_g': '220'}
        payload.update(extra)
        return payload

    def test_author_creates_and_forks_calculation_template_without_rewriting_old(self):
        self.sign_in()
        response = self.client.post(reverse('calculation_template_create'), {'name': 'Цена состава', 'version': '1',
            'description': 'Стоимость', 'operations': ['mass', 'cost', 'totalCost']})
        created = CalculationTemplate.objects.get(name='Цена состава', version=1)
        self.assertRedirects(response, reverse('calculation_template_detail', args=[created.pk]))
        self.assertContains(self.client.get(reverse('calculation_template_detail', args=[created.pk])), 'Стоимость компонента')
        response = self.client.post(reverse('calculation_template_fork', args=[created.pk]), {'name': created.name,
            'version': '2', 'description': 'Добавлена плотность', 'operations': ['mass', 'density', 'totalCost']})
        fork = CalculationTemplate.objects.get(name=created.name, version=2)
        self.assertRedirects(response, reverse('calculation_template_detail', args=[fork.pk]))
        created.refresh_from_db()
        self.assertEqual(created.operations, ['mass', 'cost', 'totalCost'])
        self.assertEqual(ChangeEvent.objects.filter(entity_type='calculation_template').count(), 2)
        self.assertEqual(self.client.post(reverse('calculation_template_create'), {'name': 'Код', 'version': '1',
            'operations': ['eval(1)']}).status_code, 200)
        self.assertFalse(CalculationTemplate.objects.filter(name='Код').exists())

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
        response = self.client.get(reverse('recipe_template_from_recipe', args=[self.recipe.pk]))
        self.assertEqual(response.status_code, 200)
        formset = response.context['component_formset']
        self.assertEqual(formset.initial[0]['material'], self.material.pk)
        self.assertEqual(Decimal(formset.initial[0]['quantity_value']), Decimal('100'))
        self.assertEqual(response.context['form'].initial['calculation_template'], self.calculation.pk)

    def test_reader_views_templates_and_cannot_create_or_fork(self):
        template = RecipeTemplate.objects.create(name='Чтение', definition={'components': [], 'filling': []})
        self.sign_in(self.reader)
        for route in ('recipe_template_list', 'calculation_template_list'):
            self.assertEqual(self.client.get(reverse(route)).status_code, 200)
        for route, args in [('recipe_template_create', []), ('recipe_template_fork', [template.pk]),
                            ('recipe_template_from_recipe', [self.recipe.pk]), ('calculation_template_create', []),
                            ('calculation_template_fork', [self.calculation.pk])]:
            self.assertEqual(self.client.post(reverse(route, args=args), self.recipe_payload()).status_code, 403)
        self.assertEqual(RecipeTemplate.objects.count(), 1)
        self.assertFalse(ChangeEvent.objects.exists())

    def test_concurrent_template_version_error_is_a_form_error_and_rolls_back(self):
        self.sign_in()
        for model, route, payload in [
            (RecipeTemplate, 'recipe_template_create', self.recipe_payload()),
            (CalculationTemplate, 'calculation_template_create', {'name': 'Параллельный расчёт', 'version': '1', 'operations': ['mass']}),
        ]:
            before = model.objects.count()
            with self.subTest(template=model.__name__), patch.object(model, 'save', side_effect=IntegrityError('concurrent unique version')):
                response = self.client.post(reverse(route), payload)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'Эта версия шаблона уже сохранена другим автором')
            self.assertEqual(model.objects.count(), before)
            self.assertFalse(ChangeEvent.objects.exists())


@override_settings(DEMO_MODE=True)
class WinterSeedTests(TestCase):
    def test_new_winter_seed_is_complete_and_idempotent(self):
        call_command('seed_demo', confirm_demo=True, stdout=StringIO())
        self.assertEqual(RecipeVersion.objects.count(), 6)
        self.assertEqual(TestSession.objects.count(), 10)
        self.assertSetEqual(set(MixtureType.objects.values_list('title', flat=True)), {'Зимняя пена', 'Зимняя улучшенная пена'})
        self.assertEqual(RecipeTemplate.objects.count(), 2)
        self.assertEqual(CalculationTemplate.objects.count(), 2)
        self.assertEqual(CalculationRecord.objects.count(), 6)
        self.assertTrue(MaterialLot.objects.filter(status='quarantine').exists())
        self.assertTrue(RecipeComponent.objects.filter(specified_lot__isnull=False).exists())
        self.assertEqual(SourceFile.objects.get().sha256, '')
        before = ChangeEvent.objects.count()
        call_command('seed_demo', confirm_demo=True, stdout=StringIO())
        self.assertEqual(ChangeEvent.objects.count(), before)
        self.assertEqual(RecipeVersion.objects.count(), 6)

    def test_upgrade_preserves_old_composition_user_changes_and_history(self):
        author = EmployeeProfile.objects.create(full_name='DEMO · Анна Волкова', role='author')
        mixture = MixtureType.objects.create(code='DEMO-316', title='Зимняя монтажная пена · учебная серия')
        MixtureType.objects.create(code='DEMO-302', title='Всесезонная пена · учебная серия')
        material = Material.objects.create(name='DEMO · Полиол P-101', density_g_ml=Decimal('1.234'), price_per_kg=Decimal('0'))
        old = RecipeVersion.objects.create(mixture_type=mixture, version_label='DEMO-316.01', title='Базовый состав',
                                          status='published', notes=RECIPE_NOTE, created_by=author)
        group = RecipeGroup.objects.create(recipe_version=old, label='A', quantity_basis='mass_percent', completeness='complete')
        component = RecipeComponent.objects.create(group=group, position=1, unbatched_material=material, quantity_value=Decimal('99.5'))
        edited = RecipeVersion.objects.create(mixture_type=mixture, version_label='DEMO-316.02', title='Моя доработка',
                                             notes=RECIPE_NOTE, status='published', created_by=author)
        event = ChangeEvent.objects.create(employee=author, entity_type='recipe', entity_id=old.pk, action='created',
                                          summary='Старая история', after={'title': old.title, 'quantity': '99.5'})
        call_command('seed_demo', confirm_demo=True, upgrade_winter=True, stdout=StringIO())
        component.refresh_from_db()
        material.refresh_from_db()
        edited.refresh_from_db()
        event.refresh_from_db()
        self.assertEqual(component.quantity_value, Decimal('99.5'))
        self.assertEqual(component.unbatched_material_id, material.pk)
        self.assertEqual(material.density_g_ml, Decimal('1.234'))
        self.assertEqual(material.price_per_kg, Decimal('0'))
        self.assertEqual(edited.title, 'Моя доработка')
        self.assertEqual(event.after, {'title': 'Базовый состав', 'quantity': '99.5'})
        self.assertEqual(RecipeVersion.objects.filter(version_label__endswith='.W1').count(), 2)
        self.assertEqual(CalculationRecord.objects.count(), 2)
        counts = (RecipeVersion.objects.count(), ChangeEvent.objects.count(), SourceFile.objects.count(), MaterialLot.objects.count())
        call_command('seed_demo', confirm_demo=True, upgrade_winter=True, stdout=StringIO())
        self.assertEqual(counts, (RecipeVersion.objects.count(), ChangeEvent.objects.count(), SourceFile.objects.count(), MaterialLot.objects.count()))
