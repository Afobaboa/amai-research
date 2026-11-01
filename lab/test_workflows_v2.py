"""Cross-feature checks for templates, batch traceability and historical evidence."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    CalculationRecord, CalculationTemplate, ChangeEvent, EmployeeProfile,
    Material, MaterialLot, Metric, RecipeComponent, RecipeGroup, RecipeProperty,
    RecipeTemplate, RecipeVersion, SourceCell, SourceFile, SourceSheet, Result,
    TestRun, TestSession,
)


class ExpandedWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = EmployeeProfile.objects.create(full_name='Автор новых возможностей', role='author')
        cls.reader = EmployeeProfile.objects.create(full_name='Читатель новых возможностей', role='reader')
        cls.material = Material.objects.create(name='Зимний полиол', density_g_ml=1, price_per_kg=100)
        cls.calc_template = CalculationTemplate.objects.create(name='Плотность и цена', created_by=cls.author)
        cls.recipe_template = RecipeTemplate.objects.create(
            name='Зимняя пена', created_by=cls.author, calculation_template=cls.calc_template,
            definition={'components': [{'group_label': 'A', 'quantity_basis': 'mass_percent',
                                       'completeness': 'complete', 'material': cls.material.pk,
                                       'quantity_value': '100'}],
                        'filling': [{'group_label': 'A', 'mass_g': '220'}]},
        )
        cls.source = SourceFile.objects.create(original_path='Первичная запись', title='Первичная запись', kind='note')
        cls.sheet = SourceSheet.objects.create(file=cls.source, name='Зимняя пена')
        cls.other_sheet = SourceSheet.objects.create(file=cls.source, name='Другой режим')
        cls.name_cell = SourceCell.objects.create(sheet=cls.sheet, address='A1', cell_kind='text', raw_value='Зимний полиол')
        cls.qty_cell = SourceCell.objects.create(sheet=cls.sheet, address='B1', cell_kind='numeric', raw_value='100')
        cls.other_cell = SourceCell.objects.create(sheet=cls.other_sheet, address='A1', cell_kind='numeric', raw_value='50')
        cls.metric = Metric.objects.create(code='V2_TEMP', label='Температура', value_kind='numeric', unit_code='°C')

    def setUp(self):
        self.sign_in(self.author)

    def sign_in(self, employee):
        session = self.client.session
        session['site_authenticated'] = True
        session['employee_id'] = employee.pk
        session.save()

    def payload(self, **changes):
        result = {
            'version_label': 'WINTER.1', 'title': 'Зимняя пена', 'status': 'published',
            'calculation_template': str(self.calc_template.pk), 'batch_mass_g': '1000',
            'components-TOTAL_FORMS': '1', 'components-INITIAL_FORMS': '0',
            'components-MIN_NUM_FORMS': '0', 'components-MAX_NUM_FORMS': '100',
            'components-0-group_label': 'A', 'components-0-quantity_basis': 'mass_percent',
            'components-0-completeness': 'complete', 'components-0-material': str(self.material.pk),
            'components-0-quantity_value': '100',
            'filling-TOTAL_FORMS': '1', 'filling-INITIAL_FORMS': '0',
            'filling-MIN_NUM_FORMS': '0', 'filling-MAX_NUM_FORMS': '50',
            'filling-0-group_label': 'A', 'filling-0-mass_g': '220', 'filling-0-mass_status': 'recorded',
        }
        result.update(changes)
        return result

    def assert_saved(self, response, version='WINTER.1'):
        if response.status_code != 302:
            errors = [str(response.context[name].errors) for name in ('form', 'component_formset', 'filling_formset', 'property_formset') if response.context and name in response.context]
            self.fail(f'Expected saved recipe, got HTTP {response.status_code}: {errors}')
        return RecipeVersion.objects.get(version_label=version)

    @staticmethod
    def displayed_payload(response):
        """Submit the values a browser received, including provenance and formsets."""
        data = {}
        forms = [response.context['form']]
        for key in ('component_formset', 'filling_formset', 'property_formset'):
            formset = response.context[key]
            forms.append(formset.management_form)
            forms.extend(formset.forms)
        for form in forms:
            for field in form:
                value = field.value()
                if isinstance(value, bool):
                    if value:
                        data[field.html_name] = 'on'
                elif isinstance(value, (list, tuple)):
                    data[field.html_name] = [str(item) for item in value]
                else:
                    data[field.html_name] = '' if value is None else str(value)
        return data

    def test_recipe_from_template_freezes_definition_and_publication_calculation(self):
        url = reverse('recipe_create') + f'?template={self.recipe_template.pk}'
        page = self.client.get(url)
        self.assertEqual(page.context['component_formset'].forms[0]['quantity_value'].value(), '100')
        payload = self.displayed_payload(page)
        payload.update(version_label='WINTER.1', status='published')
        recipe = self.assert_saved(self.client.post(reverse('recipe_create'), payload))
        self.assertEqual(recipe.recipe_template_id, self.recipe_template.pk)
        self.assertEqual(recipe.template_snapshot['definition'], self.recipe_template.definition)
        self.assertEqual(recipe.template_snapshot['version'], 1)
        record = recipe.calculations.get()
        self.assertEqual(record.inputs['groups'][0]['components'][0]['price_per_kg'], '100')
        self.assertEqual(record.outputs['groups'][0]['total_cost'], '100')
        event = ChangeEvent.objects.get(entity_type='recipe', entity_id=recipe.pk)
        self.assertEqual(event.after['calculation_ids'], [record.pk])

    def test_lot_status_and_expiry_block_publication_but_allow_drafts(self):
        for number, values in enumerate([
            {'status': 'quarantine'}, {'status': 'exhausted'},
            {'status': 'active', 'expires_on': timezone.localdate() - timedelta(days=1)},
        ]):
            with self.subTest(values=values):
                lot = MaterialLot.objects.create(material=self.material, lot_code=f'WINTER-LOT-{number}', **values)
                payload = self.payload(**{'version_label': f'WINTER.BAD.{number}',
                    'components-0-material': '', 'components-0-specified_lot': str(lot.pk)})
                response = self.client.post(reverse('recipe_create'), payload)
                self.assertEqual(response.status_code, 200)
                self.assertFalse(RecipeVersion.objects.filter(version_label=payload['version_label']).exists())
                self.assertFalse(CalculationRecord.objects.exists())
                payload['status'] = 'draft'
                recipe = self.assert_saved(self.client.post(reverse('recipe_create'), payload), payload['version_label'])
                self.assertEqual(recipe.groups.get().components.get().specified_lot_id, lot.pk)

    def test_properties_and_source_cells_survive_edit_and_fork(self):
        payload = self.payload(status='draft', source_sheet=str(self.sheet.pk), **{
            'components-0-name_cell': str(self.name_cell.pk), 'components-0-quantity_cell': str(self.qty_cell.pk),
            'filling-0-label_cell': str(self.name_cell.pk), 'filling-0-mass_cell': str(self.qty_cell.pk),
            'properties-TOTAL_FORMS': '4', 'properties-INITIAL_FORMS': '0',
            'properties-MIN_NUM_FORMS': '0', 'properties-MAX_NUM_FORMS': '100',
        })
        targets = [('recipe', '', ''), ('group', 'A', ''), ('component', 'A', '1'), ('filling', '', '1')]
        for index, (target, label, position) in enumerate(targets):
            payload.update({f'properties-{index}-target': target, f'properties-{index}-group_label': label,
                f'properties-{index}-position': position, f'properties-{index}-property_code': f'PROPERTY_{target}',
                f'properties-{index}-value': str(index), f'properties-{index}-unit_code': 'ед.',
                f'properties-{index}-source_cell': str(self.qty_cell.pk)})
        recipe = self.assert_saved(self.client.post(reverse('recipe_create'), payload))
        self.assertEqual(RecipeProperty.objects.count(), 4)
        page = self.client.get(reverse('recipe_edit', args=[recipe.pk]))
        edit = self.displayed_payload(page)
        edit['title'] = 'Зимняя пена — уточнение'
        self.assert_saved(self.client.post(reverse('recipe_edit', args=[recipe.pk]), edit))
        component = recipe.groups.get().components.get()
        self.assertEqual(component.name_cell_id, self.name_cell.pk)
        self.assertEqual(component.quantity_cell_id, self.qty_cell.pk)
        self.assertEqual(recipe.filling_plan.get().mass_cell_id, self.qty_cell.pk)
        self.assertEqual(RecipeProperty.objects.count(), 4)
        self.assertEqual(set(RecipeProperty.objects.values_list('source_cell_id', flat=True)), {self.qty_cell.pk})
        page = self.client.get(reverse('recipe_fork', args=[recipe.pk]))
        fork_payload = self.displayed_payload(page)
        fork_payload['version_label'] = 'WINTER.2'
        fork = self.assert_saved(self.client.post(reverse('recipe_fork', args=[recipe.pk]), fork_payload), 'WINTER.2')
        self.assertEqual(fork.groups.get().components.get().quantity_cell_id, self.qty_cell.pk)
        self.assertEqual(RecipeProperty.objects.count(), 8)
        copied = RecipeProperty.objects.filter(property_code='PROPERTY_component', component__group__recipe_version=fork).get()
        self.assertEqual(copied.value, Decimal(2))
        self.assertEqual(copied.source_cell_id, self.qty_cell.pk)
        self.assertNotEqual(fork.groups.get().pk, recipe.groups.get().pk)

    def test_wrong_source_sheet_rejects_plan_atomically(self):
        payload = self.payload(source_sheet=str(self.sheet.pk), **{'components-0-quantity_cell': str(self.other_cell.pk)})
        response = self.client.post(reverse('recipe_create'), payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(RecipeVersion.objects.exists())
        self.assertFalse(ChangeEvent.objects.exists())

    def test_published_price_snapshot_remains_default_after_new_calculation(self):
        recipe = self.assert_saved(self.client.post(reverse('recipe_create'), self.payload()))
        original = recipe.calculations.get()
        Material.objects.filter(pk=self.material.pk).update(price_per_kg=900, density_g_ml=2)
        response = self.client.post(reverse('recipe_calculate', args=[recipe.pk]))
        self.assertEqual(response.status_code, 302)
        newer = recipe.calculations.order_by('-pk').first()
        self.assertEqual(newer.outputs['groups'][0]['total_cost'], '900')
        original.refresh_from_db()
        self.assertEqual(original.outputs['groups'][0]['total_cost'], '100')
        page = self.client.get(reverse('recipe_detail', args=[recipe.pk]))
        self.assertEqual(page.context['calculation']['groups'][0]['total_cost'], '100')

    def test_reader_cannot_calculate_or_create_template_versions(self):
        recipe = RecipeVersion.objects.create(version_label='WINTER.READONLY', status='published')
        self.sign_in(self.reader)
        routes = [
            ('recipe_calculate', [recipe.pk]), ('recipe_template_create', []),
            ('recipe_template_fork', [self.recipe_template.pk]),
            ('recipe_template_from_recipe', [recipe.pk]), ('calculation_template_create', []),
            ('calculation_template_fork', [self.calc_template.pk]),
        ]
        for name, args in routes:
            with self.subTest(route=name):
                self.assertEqual(self.client.post(reverse(name, args=args), {}).status_code, 403)
        self.assertFalse(CalculationRecord.objects.exists())
        self.assertEqual(RecipeTemplate.objects.count(), 1)
        self.assertEqual(CalculationTemplate.objects.count(), 1)

    def test_result_and_observation_keep_provenance_and_reject_other_sheet(self):
        card = TestSession.objects.create(identifier='WINTER.TEST.1', status='active', source_sheet=self.sheet)
        run = TestRun.objects.create(session=card, scope='Холодный режим')
        url = reverse('result_add', args=[card.pk])
        payload = {'run': str(run.pk), 'metric': str(self.metric.pk), 'numeric_value': '0',
                   'source_cell': str(self.other_cell.pk)}
        self.assertEqual(self.client.post(url, payload).status_code, 200)
        self.assertFalse(Result.objects.exists())
        payload['source_cell'] = str(self.qty_cell.pk)
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        result = run.results.get()
        self.assertEqual(result.numeric_value, Decimal(0))
        self.assertEqual(result.source_cell_id, self.qty_cell.pk)
        observation_url = reverse('observation_add', args=[card.pk])
        text = '  Наблюдение без изменений\nВторая строка  '
        data = {'run': str(run.pk), 'text_value': text, 'source_cell': str(self.other_cell.pk)}
        self.assertEqual(self.client.post(observation_url, data).status_code, 200)
        self.assertFalse(run.observations.exists())
        data['source_cell'] = str(self.name_cell.pk)
        self.assertEqual(self.client.post(observation_url, data).status_code, 302)
        observation = run.observations.get()
        self.assertEqual(observation.source_cell_id, self.name_cell.pk)
        self.assertEqual(observation.text_value, text)

    def test_condition_provenance_is_validated_and_saved(self):
        payload = {'identifier': 'WINTER.CONDITION.1', 'title': 'Холодный режим', 'status': 'active',
                   'source_sheet': str(self.sheet.pk),
                   'conditions-TOTAL_FORMS': '1', 'conditions-INITIAL_FORMS': '0',
                   'conditions-MIN_NUM_FORMS': '0', 'conditions-MAX_NUM_FORMS': '30',
                   'conditions-0-metric': str(self.metric.pk), 'conditions-0-numeric_value': '-18',
                   'conditions-0-source_cell': str(self.other_cell.pk)}
        url = reverse('test_create')
        self.assertEqual(self.client.post(url, payload).status_code, 200)
        self.assertFalse(TestSession.objects.filter(identifier=payload['identifier']).exists())
        payload['conditions-0-source_cell'] = str(self.qty_cell.pk)
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        card = TestSession.objects.get(identifier=payload['identifier'])
        self.assertEqual(card.conditions.get().source_cell_id, self.qty_cell.pk)
        self.assertEqual(card.conditions.get().numeric_value, Decimal(-18))

    def test_publication_snapshot_distinguishes_unknown_price_from_free_batch(self):
        Material.objects.filter(pk=self.material.pk).update(price_per_kg=None)
        missing = self.assert_saved(self.client.post(reverse('recipe_create'),
            self.payload(version_label='WINTER.MISSING')), 'WINTER.MISSING')
        unknown = missing.calculations.get().outputs['groups'][0]
        self.assertIsNone(unknown['total_cost'])
        self.assertIsNone(unknown['rows'][0]['price_per_kg'])
        lot = MaterialLot.objects.create(material=self.material, lot_code='WINTER.FREE', price_per_kg=0)
        free = self.assert_saved(self.client.post(reverse('recipe_create'), self.payload(
            version_label='WINTER.FREE', **{'components-0-material': '',
                                           'components-0-specified_lot': str(lot.pk)})), 'WINTER.FREE')
        output = free.calculations.get().outputs['groups'][0]
        self.assertEqual(output['total_cost'], '0')
        self.assertEqual(output['cost_per_kg'], '0')
        self.assertEqual(output['rows'][0]['price_per_kg'], '0')

    def test_manual_draft_calculation_is_not_the_published_composition_baseline(self):
        free = Material.objects.create(name='Первоначальный бесплатный компонент', density_g_ml=1, price_per_kg=0)
        draft = self.assert_saved(self.client.post(reverse('recipe_create'), self.payload(
            status='draft', **{'components-0-material': str(free.pk)})))
        self.assertFalse(draft.calculations.exists())
        self.assertEqual(self.client.post(reverse('recipe_calculate', args=[draft.pk])).status_code, 302)
        manual = draft.calculations.get()
        self.assertEqual(manual.outputs['purpose'], 'manual')
        self.assertEqual(manual.outputs['groups'][0]['total_cost'], '0')
        page = self.client.get(reverse('recipe_edit', args=[draft.pk]))
        payload = self.displayed_payload(page)
        payload.update(status='published', **{'components-0-material': str(self.material.pk)})
        self.assert_saved(self.client.post(reverse('recipe_edit', args=[draft.pk]), payload))
        publication = draft.calculations.get(outputs__purpose='publication')
        self.assertEqual(publication.outputs['groups'][0]['total_cost'], '100')
        self.assertEqual(publication.inputs['groups'][0]['components'][0]['material_id'], self.material.pk)
        self.assertNotEqual(publication.pk, manual.pk)
        page = self.client.get(reverse('recipe_detail', args=[draft.pk]))
        self.assertEqual(page.context['calculation']['purpose'], 'publication')
        self.assertEqual(page.context['calculation']['groups'][0]['total_cost'], '100')
        manual.refresh_from_db()
        self.assertEqual(manual.outputs['groups'][0]['total_cost'], '0')

    def test_card_source_cannot_be_changed_away_from_existing_result_evidence(self):
        card = TestSession.objects.create(identifier='WINTER.PROVENANCE', title='Исходная карточка',
                                          status='active', source_sheet=self.sheet)
        run = TestRun.objects.create(session=card, scope='Холодный режим')
        result = Result.objects.create(test_run=run, metric=self.metric, numeric_value=0,
                                       source_cell=self.qty_cell)
        payload = {'identifier': card.identifier, 'title': 'Уточненная карточка', 'status': 'active',
                   'source_sheet': str(self.other_sheet.pk), 'conditions-TOTAL_FORMS': '0',
                   'conditions-INITIAL_FORMS': '0', 'conditions-MIN_NUM_FORMS': '0',
                   'conditions-MAX_NUM_FORMS': '30'}
        url = reverse('test_edit', args=[card.pk])
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertIn('source_sheet', response.context['form'].errors)
        card.refresh_from_db()
        result.refresh_from_db()
        self.assertEqual(card.source_sheet_id, self.sheet.pk)
        self.assertEqual(card.title, 'Исходная карточка')
        self.assertEqual(result.source_cell_id, self.qty_cell.pk)
        self.assertFalse(ChangeEvent.objects.exists())
        payload['source_sheet'] = str(self.sheet.pk)
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        card.refresh_from_db()
        self.assertEqual(card.title, 'Уточненная карточка')
        self.assertEqual(run.results.get().source_cell_id, self.qty_cell.pk)
