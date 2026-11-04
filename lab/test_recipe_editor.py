"""Server integration for the three-part editor and calculated filling plan."""

from copy import deepcopy
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from .calculations import calculate_recipe
from .initial_data import seed_initial
from .models import EmployeeProfile, Material, MaterialLot, RecipeVersion


def form_values(form):
    values = {}
    for name, field in form.fields.items():
        value = form[name].value()
        if isinstance(value, bool):
            if value:
                values[form.add_prefix(name)] = 'on'
        elif value is not None:
            values[form.add_prefix(name)] = str(value)
        else:
            values[form.add_prefix(name)] = ''
    return values


def editor_payload(response):
    context = response.context
    data = {'editor_version': '1'}
    for field, name in [('recipe_id', 'recipe'), ('source_id', 'source'),
                        ('template_id', 'selected_template')]:
        data[field] = str(context[name].pk) if context.get(name) else ''
    for key in ('form', 'settings_form', 'isocyanate_form'):
        data.update(form_values(context[key]))
    for key in ('polyol_formset', 'gas_formset'):
        formset = context[key]
        data.update(form_values(formset.management_form))
        for form in formset:
            data.update(form_values(form))
    return data


def simple_recipe_payload(material, lot=None, **changes):
    data = {
        'editor_version': '1', 'polyol-TOTAL_FORMS': '1',
        'polyol-INITIAL_FORMS': '0', 'gases-TOTAL_FORMS': '0',
        'gases-INITIAL_FORMS': '0',
        'version_label': 'TEST.2', 'status': 'published',
        'settings-iso_ratio_mode': 'parts', 'settings-batch_mass_g': '1000',
        'settings-polyol_parts': '100', 'settings-gas_mass_pct': '0',
        'settings-fill_volume_ml': '220',
        'settings-isocyanate_absence_reason': 'Изоцианат не используется',
        'polyol-0-material': str(material.pk),
        'polyol-0-specified_lot': str(lot.pk) if lot else '',
        'polyol-0-lot_reason': '' if lot else 'Партия не установлена',
        'polyol-0-quantity': '100',
        'polyol-0-price_currency': lot.price_currency if lot else 'EUR',
    }
    for name, default in [('density_g_ml', '1'), ('price_per_kg', '100')]:
        value = getattr(lot, name) if lot else default
        data['polyol-0-' + name] = '' if value is None else str(value)
        data['polyol-0-' + name + '_reason'] = 'Не указано' if value is None else ''
    data.pop('settings-isocyanate_present', None)
    data.pop('polyol-0-ohv_applicable', None)
    data.update(changes)
    return data


def remove_form_row(data, prefix, removed):
    total = int(data[prefix + '-TOTAL_FORMS'])
    rows = [{key[len(f'{prefix}-{index}-'):]: value for key, value in data.items()
             if key.startswith(f'{prefix}-{index}-')} for index in range(total)]
    for key in list(data):
        if any(key.startswith(f'{prefix}-{index}-') for index in range(total)):
            del data[key]
    del rows[removed]
    for index, row in enumerate(rows):
        data.update({f'{prefix}-{index}-{key}': value for key, value in row.items()})
    initial = int(data[prefix + '-INITIAL_FORMS'])
    data[prefix + '-INITIAL_FORMS'] = str(initial - (removed < initial))
    data[prefix + '-TOTAL_FORMS'] = str(len(rows))


class RecipeEditorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_initial()
        cls.source = RecipeVersion.objects.get(
            version_label='316.18', mixture_type__code='316',
        )
        cls.author = EmployeeProfile.objects.get(role='author')
        cls.reader = EmployeeProfile.objects.get(role='reader')
        cls.new_material = Material.objects.create(name='Новый полиол для проверки')
        cls.new_lot = MaterialLot.objects.create(
            material=cls.new_material, lot_code='NEW', density_g_ml=4,
            price_per_kg=100, price_currency='EUR',
        )

    def setUp(self):
        self.login(self.author)

    def login(self, employee):
        session = self.client.session
        session.update({'site_authenticated': True, 'employee_id': employee.pk})
        session.save()

    def fork_form(self):
        response = self.client.get(reverse('recipe_fork', args=[self.source.pk]))
        self.assertEqual(response.status_code, 200)
        return response, editor_payload(response)

    def preview(self, data):
        response = self.client.post(reverse('recipe_preview'), data)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def save_fork(self, data):
        response = self.client.post(
            reverse('recipe_fork', args=[self.source.pk]), data,
        )
        if response.status_code != 302:
            context = response.context
            errors = {key: str(context[key].errors) for key in (
                'form', 'settings_form', 'isocyanate_form', 'polyol_formset',
                'gas_formset',
            )}
            self.fail(str(errors))
        return RecipeVersion.objects.get(pk=int(response.url.rstrip('/').split('/')[-1]))

    def test_new_editor_has_three_sections_and_no_legacy_group_controls(self):
        response = self.client.get(reverse('recipe_create'))
        self.assertEqual(response.status_code, 200)
        for text in ('Состав полиольной смеси', 'Изоцианат', 'Состав газов'):
            self.assertContains(response, text)
        for text in ('Свойства плана', 'Полнота группы', 'База количества'):
            self.assertNotContains(response, text)
        self.assertNotIn('batch_mass_g', response.context['form'].fields)
        self.assertNotIn('functionality',
                         response.context['polyol_formset'].forms[0].fields)
        self.assertNotIn('functionality', response.context['polyol_formset'].empty_form.fields)
        self.assertNotContains(response, 'Функциональность')
        self.assertNotContains(response, 'Начать с шаблона')

    def test_fork_prefills_real_excel_inputs_and_calculated_results(self):
        response, _ = self.fork_form()
        settings = response.context['settings_form']
        self.assertEqual(Decimal(settings['batch_mass_g'].value()), Decimal(350))
        self.assertEqual(Decimal(settings['isocyanate_parts'].value()), Decimal(145))
        self.assertEqual(Decimal(settings['fill_volume_ml'].value()), Decimal(800))
        result = response.context['calculation']['filling']
        self.assertEqual(Decimal(result['polyol']['mass_g']['value']), Decimal('260.4'))
        self.assertEqual(Decimal(result['isocyanate']['mass_g']['value']), Decimal('377.2'))

    def test_detail_reuses_worksheet_with_disabled_saved_inputs(self):
        response = self.client.get(reverse('recipe_detail', args=[self.source.pk]))
        self.assertTemplateUsed(response, 'lab/recipe_editor.html')
        self.assertTrue(response.context['readonly'])
        for key in ('form', 'settings_form', 'isocyanate_form'):
            self.assertTrue(all(field.disabled for field in
                                response.context[key].fields.values()))
        for key, rows in (('polyol_formset', 'polyol'), ('gas_formset', 'gases')):
            formset = response.context[key]
            self.assertEqual(len(formset), len(self.source.formulation[rows]))
            self.assertTrue(all(field.disabled for form in formset
                                for field in form.fields.values()))
        self.assertNotContains(response, 'data-add-form=')
        self.assertNotContains(response, 'data-remove-form')
        self.assertNotContains(response, 'data-preview-url=')
        self.assertContains(response, reverse('recipe_fork', args=[self.source.pk]))
        self.assertNotContains(response, reverse('recipe_edit', args=[self.source.pk]))

    def test_reader_can_view_worksheet_without_write_actions(self):
        self.login(self.reader)
        response = self.client.get(reverse('recipe_detail', args=[self.source.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-recipe-readonly')
        for route in ('recipe_fork', 'recipe_edit', 'recipe_calculate'):
            self.assertNotContains(response, reverse(route, args=[self.source.pk]))
        self.assertContains(response, 'Связанные эксперименты')
        self.assertContains(response, 'История')

    def test_draft_detail_offers_editing_and_preserves_absent_isocyanate(self):
        response = self.client.post(reverse('recipe_create'),
                                    simple_recipe_payload(self.new_material,
                                                          status='draft'))
        recipe = RecipeVersion.objects.get(version_label='TEST.2')
        self.assertRedirects(response, reverse('recipe_detail', args=[recipe.pk]))
        detail = self.client.get(response.url)
        self.assertContains(detail, reverse('recipe_edit', args=[recipe.pk]))
        self.assertFalse(detail.context['settings_form']['isocyanate_present'].value())
        self.assertContains(detail, 'Изоцианат не используется')

    def test_archived_detail_keeps_archived_status_in_metadata(self):
        RecipeVersion.objects.filter(pk=self.source.pk).update(status='archived')
        response = self.client.get(reverse('recipe_detail', args=[self.source.pk]))
        field = response.context['form'].fields['status']
        self.assertEqual(list(field.choices), [('archived', 'В архиве')])

    def test_live_preview_reproduces_the_screenshot_filling_plan(self):
        _, data = self.fork_form()
        result = self.preview(data)
        self.assertTrue(result['valid'], result['errors'])
        filling = result['calculation']['filling']
        self.assertEqual(Decimal(filling['polyol']['mass_g']['value']), Decimal('260.4'))
        self.assertEqual(Decimal(filling['isocyanate']['mass_g']['value']), Decimal('377.2'))
        self.assertEqual([Decimal(row['mass_g']['value']) for row in filling['gases']],
                         [Decimal('71.2'), Decimal(0), Decimal('82.1')])
        self.assertEqual(Decimal(filling['mass_g']['value']), Decimal('790.9'))
        self.assertEqual(Decimal(filling['volume_ml']['value']), Decimal(800))

    def test_tampered_derived_post_values_are_ignored_on_preview_and_save(self):
        _, data = self.fork_form()
        data.update({'filling-polyol-mass_g': '99999',
                     'polyol-0-mass_g': '99999', 'filling.mass_g': '99999',
                     'settings-iso_index': '99999'})
        result = self.preview(data)
        self.assertTrue(result['valid'], result['errors'])
        self.assertEqual(Decimal(result['calculation']['filling']['mass_g']['value']),
                         Decimal('790.9'))
        index = result['calculation']['isocyanate']['iso_index']['value']
        self.assertNotEqual(Decimal(index), Decimal(99999))
        recipe = self.save_fork(data)
        self.assertEqual(list(recipe.filling_plan.values_list('mass_g', flat=True)),
                         [Decimal('260.4'), Decimal('377.2'), Decimal('71.2'),
                          Decimal(0), Decimal('82.1')])

    def test_missing_input_requires_reason_and_propagates_calculation_reason(self):
        _, data = self.fork_form()
        data['settings-fill_volume_ml'] = ''
        data['settings-fill_volume_ml_reason'] = ''
        result = self.preview(data)
        self.assertFalse(result['valid'])
        self.assertIn('fill_volume_ml_reason', result['errors']['settings'])
        data['settings-fill_volume_ml_reason'] = 'Объём пока не определён'
        result = self.preview(data)
        self.assertTrue(result['valid'], result['errors'])
        mass = result['calculation']['filling']['polyol']['mass_g']
        self.assertIsNone(mass['value'])
        self.assertTrue(mass['reason'])
        recipe = self.save_fork(data)
        self.assertEqual(recipe.formulation['settings']['fill_volume_ml']['reason'],
                         'Объём пока не определён')
        self.assertFalse(recipe.filling_plan.exclude(mass_g=None).exclude(
            mass_g=0,
        ).exists())

    def test_zero_gas_contribution_is_known_and_needs_no_missing_reason(self):
        _, data = self.fork_form()
        self.assertEqual(Decimal(data['gases-1-quantity']), Decimal(0))
        self.assertEqual(data['gases-1-quantity_reason'], '')
        result = self.preview(data)
        self.assertTrue(result['valid'], result['errors'])
        mass = result['calculation']['filling']['gases'][1]['mass_g']
        self.assertEqual(Decimal(mass['value']), Decimal(0))
        self.assertFalse(mass.get('reason'))

    def test_mismatched_material_and_lot_are_rejected(self):
        _, data = self.fork_form()
        data['polyol-0-specified_lot'] = str(self.new_lot.pk)
        data['polyol-0-lot_reason'] = ''
        result = self.preview(data)
        self.assertFalse(result['valid'])
        self.assertIn('specified_lot', result['errors']['polyol'][0])
        before = RecipeVersion.objects.count()
        response = self.client.post(reverse('recipe_fork', args=[self.source.pk]), data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(RecipeVersion.objects.count(), before)

    def test_replacing_archive_material_uses_new_lot_properties(self):
        _, data = self.fork_form()
        data.update({'polyol-0-material': str(self.new_material.pk),
                     'polyol-0-specified_lot': str(self.new_lot.pk),
                     'polyol-0-material_reason': '', 'polyol-0-lot_reason': ''})
        recipe = self.save_fork(data)
        row = recipe.formulation['polyol'][0]
        self.assertEqual(row['material_id'], self.new_material.pk)
        self.assertEqual(row['lot_id'], self.new_lot.pk)
        self.assertEqual(Decimal(row['density_g_ml']['value']), Decimal(4))
        self.assertEqual(Decimal(row['price_per_kg']['value']), Decimal(100))
        self.assertNotIn('source_cell', row['density_g_ml'])
        self.assertNotIn('source_cell', row['price_per_kg'])

    def test_published_snapshot_survives_later_catalog_changes(self):
        _, data = self.fork_form()
        data.update({'status': 'published',
                     'polyol-0-material': str(self.new_material.pk),
                     'polyol-0-specified_lot': str(self.new_lot.pk),
                     'polyol-0-material_reason': '', 'polyol-0-lot_reason': ''})
        recipe = self.save_fork(data)
        record = recipe.calculations.get(outputs__purpose='publication')
        expected = deepcopy(record.outputs)
        MaterialLot.objects.filter(pk=self.new_lot.pk).update(
            density_g_ml=9, price_per_kg=9999,
        )
        Material.objects.filter(pk=self.new_material.pk).update(name='Новое имя')
        response = self.client.get(reverse('recipe_detail', args=[recipe.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['result'], expected['formulation'])
        self.assertEqual(response.context['saved_calculation'].pk, record.pk)
        row = response.context['polyol_formset'].forms[0]
        self.assertEqual(Decimal(row['density_g_ml'].value()), Decimal(4))
        self.assertEqual(Decimal(row['price_per_kg'].value()), Decimal(100))
        self.assertEqual(list(row.fields['material'].choices),
                         [(self.new_material.pk, self.new_material.name)])
        record.refresh_from_db()
        self.assertEqual(record.outputs, expected)

    def test_reader_cannot_post_preview_or_create_recipe(self):
        _, data = self.fork_form()
        self.login(self.reader)
        for route in ('recipe_preview', 'recipe_create'):
            response = self.client.post(reverse(route), data)
            self.assertEqual(response.status_code, 403)

    def test_published_recipe_cannot_be_edited(self):
        response = self.client.get(reverse('recipe_edit', args=[self.source.pk]))
        self.assertEqual(response.status_code, 403)

    def test_fork_keeps_source_plan_and_calculation_unchanged(self):
        original = deepcopy(self.source.formulation)
        original_result = calculate_recipe(self.source)
        _, data = self.fork_form()
        data['settings-batch_mass_g'] = '700'
        recipe = self.save_fork(data)
        self.assertEqual(recipe.batch_mass_g, Decimal(700))
        self.source.refresh_from_db()
        self.assertEqual(self.source.formulation, original)
        self.assertEqual(calculate_recipe(self.source), original_result)

    def test_removed_initial_rows_do_not_turn_blank_extra_rows_into_components(self):
        _, data = self.fork_form()
        draft = self.save_fork(data)
        url = reverse('recipe_edit', args=[draft.pk])
        data = editor_payload(self.client.get(url))
        remove_form_row(data, 'polyol', 0)
        remove_form_row(data, 'gases', 1)
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        draft.refresh_from_db()
        self.assertEqual(len(draft.formulation['polyol']), 6)
        self.assertEqual(len(draft.formulation['gases']), 2)
        result = calculate_recipe(draft)['formulation']['filling']
        self.assertEqual(Decimal(result['mass_g']['value']), Decimal('790.9'))
        self.assertEqual([row['name'] for row in draft.formulation['polyol']],
                         [row['name'] for row in self.source.formulation['polyol'][1:]])
