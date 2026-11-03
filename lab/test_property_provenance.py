"""Protected archive evidence survives the three-part recipe editor."""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from .calculations import calculate_recipe
from .initial_data import seed_initial
from .models import EmployeeProfile, RecipeVersion, SourceCell, SourceFile, SourceSheet
from .test_recipe_editor import editor_payload


class PropertyProvenanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_initial()
        cls.author = EmployeeProfile.objects.get(role='author')
        cls.original = RecipeVersion.objects.get(
            mixture_type__code='302', version_label='302.84',
        )
        cls.target = cls.original.source_sheet
        cls.donor_cell = SourceCell.objects.get(
            sheet__file=cls.target.file, sheet__name='302.83', address='C12',
        )
        prop = cls.original.groups.get().components.get(
            specified_lot__material__name='TEP-Z',
        ).properties.get(property_code='source_price_per_kg')
        cls.note = prop.provenance_note

    def setUp(self):
        session = self.client.session
        session.update({'site_authenticated': True, 'employee_id': self.author.pk})
        session.save()

    def payload(self):
        page = self.client.get(reverse('recipe_fork', args=[self.original.pk]))
        self.assertEqual(page.status_code, 200)
        data = editor_payload(page)
        data.update(version_label='PROVENANCE.1', status='draft')
        return data

    def save(self, payload):
        response = self.client.post(
            reverse('recipe_fork', args=[self.original.pk]), payload,
        )
        self.assertEqual(response.status_code, 302)
        return RecipeVersion.objects.get(version_label=payload['version_label'])

    def property(self, recipe):
        return recipe.groups.get().components.get(
            specified_lot__material__name='TEP-Z',
        ).properties.get(property_code='source_price_per_kg')

    def assert_donor(self, recipe):
        prop = self.property(recipe)
        self.assertEqual(prop.origin, 'carried_forward')
        self.assertEqual(prop.provenance_note, self.note)
        self.assertEqual(prop.source_cell_id, self.donor_cell.pk)
        self.assertEqual(prop.value, Decimal('64.69'))
        self.assertEqual(recipe.source_sheet_id, self.target.pk)

    def test_forged_recorded_property_cannot_replace_cross_sheet_evidence(self):
        payload = self.payload()
        payload.update({
            'properties-0-target': 'component', 'properties-0-position': '1',
            'properties-0-property_code': 'source_price_per_kg',
            'properties-0-value': '999', 'properties-0-origin': 'recorded',
            'properties-0-source_cell': str(self.donor_cell.pk),
        })
        recipe = self.save(payload)
        self.assert_donor(recipe)
        self.assertFalse(recipe.groups.get().components.filter(
            properties__value=999,
        ).exists())

    def test_submitted_empty_explanation_cannot_erase_carried_forward_origin(self):
        payload = self.payload()
        payload['properties-0-provenance_note'] = ''
        payload['polyol-0-provenance_note'] = ''
        recipe = self.save(payload)
        self.assertTrue(self.note)
        self.assert_donor(recipe)
        original = self.property(self.original)
        self.assertEqual(original.provenance_note, self.note)

    def test_explicit_carried_forward_property_keeps_actual_donor_in_calculation(self):
        recipe = self.save(self.payload())
        self.assert_donor(recipe)
        row = next(row for row in calculate_recipe(recipe)['groups'][0]['rows']
                   if row['name'] == 'TEP-Z')
        self.assertEqual(row['price_source_cell_id'], self.donor_cell.pk)
        self.assertEqual(row['price_source_origin'], 'carried_forward')
        self.assertEqual(Decimal(row['price_per_kg']), Decimal('64.69'))

    def test_submitted_source_from_another_file_cannot_change_protected_donor(self):
        other = SourceFile.objects.create(original_path='unrelated.xlsx')
        sheet = SourceSheet.objects.create(file=other, name='302.83')
        cell = SourceCell.objects.create(
            sheet=sheet, address='C12', cell_kind='n', raw_value='64.69',
        )
        payload = self.payload()
        payload.update({'source_sheet': str(sheet.pk),
                        'properties-0-source_cell': str(cell.pk),
                        'polyol-0-price_source_cell': str(cell.pk)})
        recipe = self.save(payload)
        self.assert_donor(recipe)
        self.assertNotEqual(self.property(recipe).source_cell_id, cell.pk)

    def test_fork_form_and_saved_copy_keep_origin_explanation_and_donor(self):
        url = reverse('recipe_fork', args=[self.original.pk])
        page = self.client.get(url)
        form = next(form for form in page.context['polyol_formset'].forms
                    if form.initial.get('name') == 'TEP-Z')
        row = form.original_rows[form.initial['id']]
        self.assertEqual(row['price_per_kg']['origin'], 'carried_forward')
        self.assertEqual(row['price_per_kg']['provenance_note'], self.note)
        self.assertEqual(row['price_per_kg']['source_sheet'], '302.83')
        self.assertNotIn('source_cell', form.fields)
        payload = editor_payload(page)
        payload.update(version_label='PROVENANCE.2', status='draft')
        recipe = self.save(payload)
        self.assert_donor(recipe)
        self.assertNotEqual(recipe.pk, self.original.pk)
