"""Cross-feature checks for recipe versions, lots and historical evidence."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    CalculationRecord, ChangeEvent, EmployeeProfile,
    Material, MaterialLot, Metric, RecipeComponent, RecipeGroup, RecipeProperty,
    RecipeTemplate, RecipeVersion, SourceCell, SourceFile, SourceSheet, Result,
    TestRun, TestSession,
)

from .test_recipe_editor import editor_payload, simple_recipe_payload


class ExpandedWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = EmployeeProfile.objects.create(
            full_name='Автор новых возможностей', role='author')
        cls.reader = EmployeeProfile.objects.create(
            full_name='Читатель новых возможностей', role='reader')
        cls.material = Material.objects.create(name='Зимний полиол')
        cls.lot = MaterialLot.objects.create(
            material=cls.material, lot_code='WINTER-01',
            density_g_ml=1, price_per_kg=100,
        )
        cls.template_fixture()
        cls.source_fixture()

    @classmethod
    def template_fixture(cls):
        cls.recipe_template = RecipeTemplate.objects.create(
            name='Зимняя пена', created_by=cls.author,
            definition={'components': [{'group_label': 'A',
                                       'quantity_basis': 'mass_percent',
                                       'completeness': 'complete',
                                       'specified_lot': cls.lot.pk,
                                       'quantity_value': '100'}],
                        'filling': [{'group_label': 'A', 'mass_g': '220'}]},
        )

    @classmethod
    def source_fixture(cls):
        cls.source = SourceFile.objects.create(
            original_path='Первичная запись', title='Первичная запись', kind='note')
        cls.sheet = SourceSheet.objects.create(file=cls.source, name='Зимняя пена')
        cls.other_sheet = SourceSheet.objects.create(
            file=cls.source, name='Другой режим')
        cls.name_cell = SourceCell.objects.create(
            sheet=cls.sheet, address='A1', cell_kind='text', raw_value='Зимний полиол')
        cls.qty_cell = SourceCell.objects.create(
            sheet=cls.sheet, address='B1', cell_kind='numeric', raw_value='100')
        cls.other_cell = SourceCell.objects.create(
            sheet=cls.other_sheet, address='A1', cell_kind='numeric', raw_value='50')
        cls.metric = Metric.objects.create(
            code='V2_TEMP', label='Температура', value_kind='numeric', unit_code='°C')

    def setUp(self):
        self.sign_in(self.author)

    def sign_in(self, employee):
        session = self.client.session
        session['site_authenticated'] = True
        session['employee_id'] = employee.pk
        session.save()

    def payload(self, **changes):
        lot_id = changes.get('polyol-0-specified_lot', self.lot.pk)
        lot = MaterialLot.objects.get(pk=lot_id) if lot_id else None
        material = lot.material if lot else self.material
        data = simple_recipe_payload(material, lot)
        data.update(version_label='WINTER.1', title='Зимняя пена')
        data.update(changes)
        return data

    def assert_saved(self, response, version='WINTER.1'):
        if response.status_code != 302:
            errors = [str(response.context[name].errors) for name in ('form', 'settings_form', 'polyol_formset', 'gas_formset', 'isocyanate_form') if response.context and name in response.context]
            self.fail(f'Expected saved recipe, got HTTP {response.status_code}: {errors}')
        return RecipeVersion.objects.get(version_label=version)

    @staticmethod
    def displayed_payload(response):
        return editor_payload(response)

    def test_recipe_fork_freezes_own_inputs_and_publication_calculation(self):
        source = self.assert_saved(self.client.post(reverse('recipe_create'),
                                   self.payload(version_label='WINTER.BASE')),
                                   'WINTER.BASE')
        page = self.client.get(reverse('recipe_fork', args=[source.pk]))
        self.assertEqual(page.context['polyol_formset'].forms[0]['quantity'].value(),
                         '100')
        payload = self.displayed_payload(page)
        payload.update(version_label='WINTER.1', status='published', **{
            'settings-batch_mass_g': '700',
        })
        recipe = self.assert_saved(self.client.post(
            reverse('recipe_fork', args=[source.pk]), payload))
        self.assertIsNone(recipe.recipe_template_id)
        self.assertEqual(recipe.template_snapshot, {})
        record = recipe.calculations.get()
        self.assertEqual(Decimal(record.inputs['groups'][0]['components'][0][
            'price_per_kg']), Decimal('100'))
        self.assertEqual(record.outputs['groups'][0]['total_cost'], '70')
        source.refresh_from_db()
        self.assertEqual(source.batch_mass_g, Decimal(1000))
        self.assertEqual(source.calculations.get().outputs['groups'][0]['total_cost'],
                         '100')
        event = ChangeEvent.objects.get(entity_type='recipe', entity_id=recipe.pk)
        self.assertEqual(event.after['calculation_ids'], [record.pk])

    def test_lot_status_and_expiry_block_publication_but_allow_drafts(self):
        for number, values in enumerate([
            {'status': 'exhausted'},
            {'status': 'active', 'expires_on': timezone.localdate() - timedelta(days=1)},
        ]):
            with self.subTest(values=values):
                lot = MaterialLot.objects.create(material=self.material, lot_code=f'WINTER-LOT-{number}', **values)
                payload = self.payload(**{'version_label': f'WINTER.BAD.{number}',
                    'polyol-0-material': '', 'polyol-0-specified_lot': str(lot.pk)})
                response = self.client.post(reverse('recipe_create'), payload)
                self.assertEqual(response.status_code, 200)
                self.assertFalse(RecipeVersion.objects.filter(version_label=payload['version_label']).exists())
                self.assertFalse(CalculationRecord.objects.exists())
                payload['status'] = 'draft'
                recipe = self.assert_saved(self.client.post(reverse('recipe_create'), payload), payload['version_label'])
                self.assertEqual(recipe.groups.get().components.get().specified_lot_id, lot.pk)

    def test_recipe_saves_selected_component_with_its_lot(self):
        payload = self.payload(**{'polyol-0-material': str(self.material.pk)})
        recipe = self.assert_saved(
            self.client.post(reverse('recipe_create'), payload),
        )
        component = recipe.groups.get().components.get()
        self.assertEqual(component.specified_lot_id, self.lot.pk)
        self.assertIsNone(component.unbatched_material_id)
        self.assertEqual(component.material, self.material)

    def test_recipe_rejects_a_lot_from_another_component(self):
        other = Material.objects.create(name='Чужой компонент')
        payload = self.payload(**{'polyol-0-material': str(other.pk)})
        response = self.client.post(reverse('recipe_create'), payload)
        self.assertEqual(response.status_code, 200)
        self.assertIn('specified_lot',
                      response.context['polyol_formset'].forms[0].errors)
        self.assertFalse(RecipeVersion.objects.exists())
        self.assertFalse(ChangeEvent.objects.exists())

    def test_properties_and_source_cells_survive_edit_and_fork(self):
        from .initial_data import seed_initial
        seed_initial()
        source = RecipeVersion.objects.get(version_label='316.26',
                                           mixture_type__code='316')
        original = deepcopy(source.formulation)
        page = self.client.get(reverse('recipe_fork', args=[source.pk]))
        payload = self.displayed_payload(page)
        payload.update(version_label='WINTER.1', status='draft')
        recipe = self.assert_saved(self.client.post(
            reverse('recipe_fork', args=[source.pk]), payload))
        self.assertEqual(recipe.source_sheet_id, source.source_sheet_id)
        self.check_component_provenance(recipe, source)
        page = self.client.get(reverse('recipe_edit', args=[recipe.pk]))
        payload = self.displayed_payload(page)
        payload['title'] = 'Зимняя пена — уточнение'
        self.assert_saved(self.client.post(
            reverse('recipe_edit', args=[recipe.pk]), payload))
        self.check_component_provenance(recipe, source)
        source.refresh_from_db()
        self.assertEqual(source.formulation, original)

    def check_component_provenance(self, recipe, source):
        component = recipe.groups.get().components.first()
        original = source.groups.get().components.first()
        self.assertEqual(component.name_cell_id, original.name_cell_id)
        self.assertEqual(component.quantity_cell_id, original.quantity_cell_id)
        for code in ('ohv', 'density_g_ml', 'source_price_per_kg'):
            copied = component.properties.get(property_code=code)
            expected = original.properties.get(property_code=code)
            self.assertEqual(copied.source_cell_id, expected.source_cell_id)
            self.assertEqual(copied.value, expected.value)
            self.assertNotEqual(copied.pk, expected.pk)

    def test_submitted_source_fields_cannot_forge_manual_recipe_evidence(self):
        payload = self.payload(source_sheet=str(self.sheet.pk), **{
            'polyol-0-quantity_cell': str(self.other_cell.pk),
            'properties-0-source_cell': str(self.other_cell.pk),
            'properties-0-value': '999',
        })
        recipe = self.assert_saved(self.client.post(reverse('recipe_create'), payload))
        self.assertIsNone(recipe.source_sheet_id)
        component = recipe.groups.get().components.get()
        self.assertIsNone(component.quantity_cell_id)
        self.assertFalse(component.properties.exclude(source_cell=None).exists())
        self.assertFalse(component.properties.filter(value=999).exists())

    def test_published_price_snapshot_remains_default_after_new_calculation(self):
        recipe = self.assert_saved(self.client.post(
            reverse('recipe_create'), self.payload()))
        original = recipe.calculations.get()
        MaterialLot.objects.filter(pk=self.lot.pk).update(
            price_per_kg=900, density_g_ml=2)
        response = self.client.post(reverse('recipe_calculate', args=[recipe.pk]))
        self.assertEqual(response.status_code, 302)
        newer = recipe.calculations.order_by('-pk').first()
        self.assertEqual(newer.outputs['groups'][0]['total_cost'], '100')
        self.assertNotEqual(newer.pk, original.pk)
        original.refresh_from_db()
        self.assertEqual(original.outputs['groups'][0]['total_cost'], '100')
        page = self.client.get(reverse('recipe_detail', args=[recipe.pk]))
        self.assertEqual(page.context['saved_calculation'].outputs['groups'][0]['total_cost'], '100')

    def test_reader_cannot_calculate_and_retired_template_routes_cannot_mutate(self):
        recipe = RecipeVersion.objects.create(version_label='WINTER.READONLY',
                                               status='published')
        self.sign_in(self.reader)
        self.assertEqual(self.client.post(reverse('recipe_calculate',
                                                  args=[recipe.pk]), {}).status_code,
                         403)
        for path in ('/recipe-templates/new/',
                     f'/recipe-templates/{self.recipe_template.pk}/fork/',
                     f'/recipe-templates/from-recipe/{recipe.pk}/'):
            self.assertEqual(self.client.post(path, {}).status_code, 404)
        self.assertFalse(CalculationRecord.objects.exists())
        self.assertEqual(RecipeTemplate.objects.count(), 1)

    def test_group_and_observation_keep_provenance_and_reject_other_sheet(self):
        from .models import MeasurementGroup
        card = TestSession.objects.create(identifier='WINTER.TEST.1', status='active', source_sheet=self.sheet)
        group = MeasurementGroup.objects.create(code='cold', name='Холодный режим')
        group.metrics.add(self.metric)
        url = reverse('result_add', args=[card.pk])
        payload = {'measurement_group': group.pk, f'metric_{self.metric.pk}': '0',
                   'source_cell': str(self.other_cell.pk)}
        self.assertEqual(self.client.post(url, payload).status_code, 200)
        self.assertFalse(Result.objects.exists())
        payload['source_cell'] = str(self.qty_cell.pk)
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        run = card.runs.get()
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

    def test_basic_parameter_provenance_is_validated_and_saved(self):
        from .models import MeasurementGroup
        card = TestSession.objects.create(identifier='WINTER.CONDITION.1', status='active', source_sheet=self.sheet)
        group = MeasurementGroup.objects.get(code='basic_parameters')
        group.metrics.set([self.metric])
        payload = {'measurement_group': group.pk, f'metric_{self.metric.pk}': '-18',
                   'source_cell': str(self.other_cell.pk)}
        url = reverse('result_add', args=[card.pk])
        self.assertEqual(self.client.post(url, payload).status_code, 200)
        self.assertFalse(card.runs.exists())
        payload['source_cell'] = str(self.qty_cell.pk)
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        result = Result.objects.get(test_run__session=card, metric=self.metric)
        self.assertEqual(result.source_cell_id, self.qty_cell.pk)
        self.assertEqual(result.numeric_value, Decimal(-18))

    def test_conditioning_returns_to_processing_section_and_rejects_other_sheet(self):
        card = TestSession.objects.create(
            identifier='WINTER.PROCESSING', status='active',
            source_sheet=self.sheet,
        )
        url = reverse('conditioning_add', args=[card.pk])
        payload = {
            'target_kind': 'can', 'kind': 'conditioning',
            'duration_value': '0', 'duration_unit': 'ч', 'temperature_c': '0',
            'source_cell': self.other_cell.pk,
        }
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertIn('source_cell', response.context['form'].errors)
        self.assertFalse(card.conditioning_events.exists())
        payload['source_cell'] = self.qty_cell.pk
        response = self.client.post(url, payload)
        self.assertRedirects(
            response, reverse('test_detail', args=[card.pk])
            + '#experiment-conditioning',
        )
        event = card.conditioning_events.get()
        self.assertEqual(event.source_cell_id, self.qty_cell.pk)
        self.assertEqual(event.duration_value, Decimal(0))

    def test_publication_snapshot_distinguishes_unknown_price_from_free_batch(self):
        MaterialLot.objects.filter(pk=self.lot.pk).update(price_per_kg=None)
        missing = self.assert_saved(self.client.post(reverse('recipe_create'),
            self.payload(version_label='WINTER.MISSING')), 'WINTER.MISSING')
        unknown = missing.calculations.get().outputs['groups'][0]
        self.assertIsNone(unknown['total_cost'])
        self.assertIsNone(unknown['rows'][0]['price_per_kg'])
        lot = MaterialLot.objects.create(
            material=self.material, lot_code='WINTER.FREE', price_per_kg=0)
        payload = self.payload(version_label='WINTER.FREE', **{
            'polyol-0-material': '', 'polyol-0-specified_lot': str(lot.pk)})
        free = self.assert_saved(self.client.post(
            reverse('recipe_create'), payload), 'WINTER.FREE')
        output = free.calculations.get().outputs['groups'][0]
        self.assertEqual(output['total_cost'], '0')
        self.assertEqual(output['cost_per_kg'], '0')
        self.assertEqual(Decimal(output['rows'][0]['price_per_kg']), Decimal('0'))

    def test_manual_draft_calculation_is_not_the_published_composition_baseline(self):
        free = Material.objects.create(name='Первоначальный бесплатный компонент')
        free_lot = MaterialLot.objects.create(
            material=free, lot_code='FREE', density_g_ml=1, price_per_kg=0)
        payload = self.payload(status='draft', **{
            'polyol-0-specified_lot': str(free_lot.pk)})
        draft = self.assert_saved(self.client.post(reverse('recipe_create'), payload))
        self.assertFalse(draft.calculations.exists())
        response = self.client.post(reverse('recipe_calculate', args=[draft.pk]))
        self.assertEqual(response.status_code, 302)
        manual = draft.calculations.get()
        self.assertEqual(manual.outputs['purpose'], 'manual')
        self.assertEqual(manual.outputs['groups'][0]['total_cost'], '0')
        page = self.client.get(reverse('recipe_edit', args=[draft.pk]))
        payload = self.displayed_payload(page)
        payload.update(status='published', **{
            'polyol-0-material': '',
            'polyol-0-specified_lot': str(self.lot.pk)})
        self.assert_saved(self.client.post(
            reverse('recipe_edit', args=[draft.pk]), payload))
        self.check_publication_baseline(draft, manual)

    def check_publication_baseline(self, draft, manual):
        publication = draft.calculations.get(outputs__purpose='publication')
        self.assertEqual(publication.outputs['groups'][0]['total_cost'], '100')
        self.assertEqual(publication.inputs['groups'][0]['components'][0]['material_id'],
                         self.material.pk)
        self.assertNotEqual(publication.pk, manual.pk)
        page = self.client.get(reverse('recipe_detail', args=[draft.pk]))
        self.assertEqual(page.context['saved_calculation'].outputs['purpose'], 'publication')
        self.assertEqual(page.context['saved_calculation'].outputs['groups'][0]['total_cost'], '100')
        manual.refresh_from_db()
        self.assertEqual(manual.outputs['groups'][0]['total_cost'], '0')

    def test_card_edit_ignores_submitted_source_and_preserves_existing_evidence(self):
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
        self.assertEqual(response.status_code, 302)
        card.refresh_from_db()
        result.refresh_from_db()
        self.assertEqual(card.source_sheet_id, self.sheet.pk)
        self.assertEqual(card.title, 'Уточненная карточка')
        self.assertEqual(result.source_cell_id, self.qty_cell.pk)
        payload['source_sheet'] = str(self.sheet.pk)
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        card.refresh_from_db()
        self.assertEqual(card.title, 'Уточненная карточка')
        self.assertEqual(run.results.get().source_cell_id, self.qty_cell.pk)
