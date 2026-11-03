"""Group entry, catalogue changes, evidence, permissions and missing values."""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from .assistant_tools import execute_tool
from .models import (
    ChangeEvent, EmployeeProfile, MeasurementGroup, Metric, Observation,
    Result, SourceCell, SourceFile, SourceSheet, TestRun, TestSession,
)


class MeasurementGroupWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = EmployeeProfile.objects.create(full_name='Автор', role='author')
        cls.reader = EmployeeProfile.objects.create(full_name='Читатель', role='reader')
        cls.card = TestSession.objects.create(identifier='GROUP.1', status='active')
        cls.numeric = Metric.objects.create(code='GROUP_NUMBER', label='Число', unit_code='г')
        cls.text = Metric.objects.create(code='GROUP_TEXT', label='Оценка', value_kind='text')
        cls.group = MeasurementGroup.objects.create(code='qa', name='Группа QA')
        cls.group.metrics.add(cls.numeric, cls.text)
        cls.source = SourceFile.objects.create(title='Протокол')
        cls.sheet = SourceSheet.objects.create(file=cls.source, name='Лист')
        cls.cell = SourceCell.objects.create(sheet=cls.sheet, address='B2', raw_value='0')

    def setUp(self):
        self.sign_in(self.author)

    def sign_in(self, employee):
        session = self.client.session
        session.update({'site_authenticated': True, 'employee_id': employee.pk})
        session.save()

    def add(self, **values):
        for metric in self.group.metrics.all():
            name = f'metric_{metric.pk}'
            if values.get(name) in (None, ''):
                values.setdefault(name + '_reason', 'Не было оборудования')
        return self.client.post(reverse('result_add', args=[self.card.pk]), {
            'measurement_group': self.group.pk, **values,
        })

    def make_run(self):
        return TestRun.objects.create(
            session=self.card, measurement_group=self.group, scope=self.group.name,
        )

    def edit_url(self, run):
        return reverse('experiment_group_edit', args=[self.card.pk, run.pk])

    def test_directory_is_initialized_and_available_in_navigation(self):
        response = self.client.get(reverse('measurement_group_list'))
        for name in ('Основные параметры', 'На бумаге', 'В форме', 'Проверка баллона'):
            self.assertContains(response, name)
        self.assertContains(response, 'Группы измерений')
        self.assertNotContains(response, 'Создать новое определение')
        self.assertTrue(MeasurementGroup.objects.get(code='paper').metrics.exists())

    def test_choose_group_shows_its_indicators_and_units(self):
        response = self.client.get(reverse('result_add', args=[self.card.pk]), {
            'measurement_group': self.group.pk,
        })
        self.assertContains(response, f'name="metric_{self.numeric.pk}"')
        self.assertContains(response, 'Число, г')
        self.assertContains(response, f'name="metric_{self.text.pk}"')
        self.assertNotContains(response, 'Название нового определения')

    def test_group_saves_zero_text_and_verbatim_observation_together(self):
        text = '  Ровная поверхность\nВторая строка  '
        response = self.add(**{
            f'metric_{self.numeric.pk}': '0', f'metric_{self.text.pk}': '5-',
            'observation': text,
        })
        self.assertRedirects(response, reverse('test_detail', args=[self.card.pk]))
        run = self.card.runs.get()
        self.assertEqual(run.measurement_group, self.group)
        self.assertEqual(run.results.get(metric=self.numeric).numeric_value, Decimal(0))
        self.assertEqual(run.results.get(metric=self.text).text_value, '5-')
        self.assertEqual(run.observations.get().text_value, text)
        self.assertEqual(ChangeEvent.objects.get().action, 'measurement_group_added')

    def test_invalid_number_does_not_save_partial_group(self):
        response = self.add(**{f'metric_{self.numeric.pk}': 'oops',
                               f'metric_{self.text.pk}': 'Сохранять нельзя'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.card.runs.exists())
        self.assertFalse(ChangeEvent.objects.exists())
        self.assertContains(response, 'Сохранять нельзя')

    def test_missing_values_require_reason_and_can_be_measured_later(self):
        response = self.client.post(reverse('result_add', args=[self.card.pk]), {
            'measurement_group': self.group.pk,
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.card.runs.exists())
        self.assertContains(response, 'Укажите значение или причину')
        self.assertEqual(self.add().status_code, 302)
        run = self.card.runs.get()
        number = run.results.get(metric=self.numeric)
        text = run.results.get(metric=self.text)
        self.assertEqual(number.value_state, 'not_measured')
        self.assertIsNone(number.numeric_value)
        self.assertEqual(number.not_measured_reason, 'Не было оборудования')
        self.assertEqual(self.client.post(self.edit_url(run), {
            f'result_{number.pk}': '0',
            f'result_{text.pk}_reason': text.not_measured_reason,
        }).status_code, 302)
        number.refresh_from_db()
        self.assertEqual(number.numeric_value, Decimal(0))
        self.assertEqual(number.value_state, 'present')
        self.assertEqual(number.not_measured_reason, '')

    def test_whitespace_reason_and_value_with_reason_are_rejected(self):
        for values in ({f'metric_{self.numeric.pk}_reason': '   '}, {
            f'metric_{self.numeric.pk}': '0',
            f'metric_{self.numeric.pk}_reason': 'Не измерено',
        }):
            response = self.add(**values)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(self.card.runs.exists())

    def test_results_outside_group_are_not_accepted(self):
        other = Metric.objects.create(code='NOT_IN_GROUP', label='Другой показатель')
        self.add(**{f'metric_{self.numeric.pk}': '1', f'metric_{other.pk}': '5'})
        self.assertFalse(Result.objects.filter(metric=other).exists())

    def test_catalog_creation_persists_membership_and_history(self):
        response = self.client.post(reverse('measurement_group_create'), {
            'code': 'custom', 'name': 'Своя группа', 'description': 'Два показателя',
            'metrics': [self.numeric.pk, self.text.pk],
        })
        group = MeasurementGroup.objects.get(name='Своя группа')
        self.assertRedirects(response, reverse('measurement_group_detail', args=[group.pk]))
        self.assertSetEqual(set(group.metrics.all()), {self.numeric, self.text})
        event = ChangeEvent.objects.get(entity_type='measurement_group')
        self.assertEqual(set(event.after['metrics']), {self.numeric.pk, self.text.pk})

    def test_catalog_edit_does_not_remove_existing_results(self):
        run = self.make_run()
        recorded = Result.objects.create(test_run=run, metric=self.numeric, numeric_value=1)
        response = self.client.post(reverse('measurement_group_edit', args=[self.group.pk]), {
            'code': 'qa', 'name': 'Переименованная группа', 'metrics': [self.text.pk],
        })
        self.assertEqual(response.status_code, 302)
        response = self.client.get(self.edit_url(run))
        self.assertContains(response, f'name="result_{recorded.pk}"')
        self.assertContains(response, f'name="metric_{self.text.pk}"')
        self.assertEqual(self.client.post(self.edit_url(run), {
            f'result_{recorded.pk}': '2', f'metric_{self.text.pk}': 'Новая оценка',
        }).status_code, 302)
        recorded.refresh_from_db()
        self.assertEqual(recorded.numeric_value, Decimal(2))
        self.assertEqual(run.results.count(), 2)

    def test_imported_and_calculated_values_are_preserved_on_edit(self):
        run = self.make_run()
        sourced = Result.objects.create(
            test_run=run, metric=self.numeric, numeric_value=0, source_cell=self.cell,
        )
        calculated = Result.objects.create(
            test_run=run, metric=self.numeric, numeric_value=2,
            origin='calculated', calculation_code='SUM(B2:B3)',
        )
        response = self.client.post(self.edit_url(run), {
            f'result_{sourced.pk}': '999', f'result_{calculated.pk}': '999',
            f'metric_{self.text.pk}': 'Добавлено',
        })
        self.assertEqual(response.status_code, 302)
        sourced.refresh_from_db()
        calculated.refresh_from_db()
        self.assertEqual(sourced.numeric_value, Decimal(0))
        self.assertEqual(calculated.numeric_value, Decimal(2))
        self.assertEqual(calculated.calculation_code, 'SUM(B2:B3)')
        self.assertEqual(sourced.source_cell_id, self.cell.pk)

    def test_group_code_is_stable_and_initialization_preserves_custom_members(self):
        from .measurement_groups import seed_measurement_groups
        basic = MeasurementGroup.objects.get(code='basic_parameters')
        self.client.post(reverse('measurement_group_edit', args=[basic.pk]), {
            'code': 'replacement', 'name': 'Мои основные параметры',
            'metrics': [self.numeric.pk],
        })
        seed_measurement_groups()
        basic.refresh_from_db()
        self.assertEqual(basic.code, 'basic_parameters')
        self.assertEqual(basic.name, 'Мои основные параметры')
        self.assertEqual(list(basic.metrics.all()), [self.numeric])

    def test_cross_experiment_edit_is_rejected(self):
        other = TestSession.objects.create(identifier='OTHER', status='active')
        run = TestRun.objects.create(session=other, measurement_group=self.group, scope='QA')
        self.assertEqual(self.client.post(self.edit_url(run), {}).status_code, 404)
        self.assertFalse(Result.objects.exists())

    def test_repeated_group_entries_have_separate_results(self):
        self.add(**{f'metric_{self.numeric.pk}': '1'})
        self.add(**{f'metric_{self.numeric.pk}': '2'})
        self.assertEqual(self.card.runs.count(), 2)
        self.assertEqual(list(Result.objects.filter(metric=self.numeric).values_list('numeric_value', flat=True)),
                         [Decimal(1), Decimal(2)])

    def test_reader_and_completed_experiment_reject_mutations(self):
        run = self.make_run()
        self.sign_in(self.reader)
        self.assertEqual(self.client.get(reverse('measurement_group_list')).status_code, 200)
        for url in (reverse('measurement_group_create'),
                    reverse('result_add', args=[self.card.pk]), self.edit_url(run)):
            self.assertEqual(self.client.post(url, {}).status_code, 403)
        self.sign_in(self.author)
        TestSession.objects.filter(pk=self.card.pk).update(status='completed')
        self.assertEqual(self.add().status_code, 403)
        self.assertEqual(self.client.post(self.edit_url(run), {}).status_code, 403)
        self.assertFalse(Result.objects.exists())

    def test_experiment_form_has_no_separate_conditions(self):
        response = self.client.get(reverse('test_create'))
        self.assertContains(response, 'Новый эксперимент')
        self.assertNotContains(response, 'conditions-formset')
        self.assertNotContains(response, 'Общие условия')

    def test_assistant_can_read_group_members_and_basic_parameters(self):
        data = execute_tool('get_lab_record', {
            'entity': 'measurement_groups', 'id': self.group.pk, 'section': 'all',
        })
        self.assertEqual(data['metrics']['total'], 2)
        basic = MeasurementGroup.objects.get(code='basic_parameters')
        run = TestRun.objects.create(session=self.card, scope=basic.name, measurement_group=basic)
        Result.objects.create(test_run=run, metric=self.numeric, numeric_value=-10)
        data = execute_tool('get_lab_record', {
            'entity': 'tests', 'id': self.card.pk, 'section': 'all',
        })
        self.assertEqual(Decimal(data['basic_parameters']['items'][0]['numeric_value']), Decimal(-10))
        self.assertNotIn('conditions', data)

    def test_old_links_preserve_query_and_target_the_new_workflow(self):
        response = self.client.get(f'/tests/{self.card.pk}/results/new/?x=1')
        self.assertEqual(response.status_code, 308)
        self.assertEqual(response['Location'], f'/experiments/{self.card.pk}/measurement-groups/new/?x=1')
