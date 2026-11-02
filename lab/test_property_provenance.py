"""Recipe writes preserve explicitly documented cross-sheet property evidence."""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from .calculations import calculate_recipe
from .models import (
    ChangeEvent, EmployeeProfile, Material, MaterialLot, RecipeVersion,
    SourceCell, SourceFile, SourceSheet,
)


class PropertyProvenanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = EmployeeProfile.objects.create(full_name='Автор', role='author')
        material = Material.objects.create(name='Сырьё')
        cls.lot = MaterialLot.objects.create(
            material=material, lot_code='LOT-01', price_per_kg=100,
            density_g_ml=1, price_currency='EUR')
        source = SourceFile.objects.create(original_path='archive.xlsx')
        cls.target = SourceSheet.objects.create(file=source, name='302.84')
        donor = SourceSheet.objects.create(file=source, name='302.83')
        cls.name_cell = SourceCell.objects.create(
            sheet=cls.target, address='A11', cell_kind='s', raw_value='Сырьё')
        cls.donor_cell = SourceCell.objects.create(
            sheet=donor, address='C12', cell_kind='n', raw_value='64.69')
        cls.note = 'По указанию пользователя перенесено из предыдущего листа.'

    def setUp(self):
        session = self.client.session
        session.update({'site_authenticated': True, 'employee_id': self.author.pk})
        session.save()

    def payload(self, origin='carried_forward', note=None):
        return {
            'version_label': 'PROVENANCE.1', 'status': 'draft',
            'source_sheet': str(self.target.pk), 'batch_mass_g': '1000',
            'components-TOTAL_FORMS': '1', 'components-INITIAL_FORMS': '0',
            'components-0-group_label': 'A',
            'components-0-quantity_basis': 'mass_percent',
            'components-0-completeness': 'complete',
            'components-0-specified_lot': str(self.lot.pk),
            'components-0-quantity_value': '100',
            'components-0-name_cell': str(self.name_cell.pk),
            'filling-TOTAL_FORMS': '0', 'filling-INITIAL_FORMS': '0',
            'properties-TOTAL_FORMS': '1', 'properties-INITIAL_FORMS': '0',
            'properties-0-target': 'component', 'properties-0-group_label': 'A',
            'properties-0-position': '1',
            'properties-0-property_code': 'source_price_per_kg',
            'properties-0-value': '64.69', 'properties-0-unit_code': 'EUR/kg',
            'properties-0-source_cell': str(self.donor_cell.pk),
            'properties-0-origin': origin,
            'properties-0-provenance_note': self.note if note is None else note,
        }

    def test_cross_sheet_recorded_property_is_rejected_atomically(self):
        response = self.client.post(reverse('recipe_create'),
                                    self.payload(origin='recorded'))
        self.assertContains(response, 'относится к другому листу')
        self.assertFalse(RecipeVersion.objects.exists())
        self.assertFalse(ChangeEvent.objects.exists())

    def test_carried_forward_property_requires_an_explanation(self):
        response = self.client.post(reverse('recipe_create'), self.payload(note=''))
        self.assertContains(response, 'Для переноса нужны исходная ячейка и основание')
        self.assertFalse(RecipeVersion.objects.exists())

    def test_explicit_carried_forward_property_keeps_actual_donor(self):
        response = self.client.post(reverse('recipe_create'), self.payload())
        self.assertEqual(response.status_code, 302)
        recipe = RecipeVersion.objects.get()
        prop = recipe.groups.get().components.get().properties.get()
        self.assertEqual(prop.origin, 'carried_forward')
        self.assertEqual(prop.provenance_note, self.note)
        self.assertEqual(prop.source_cell_id, self.donor_cell.pk)
        self.assertEqual(prop.value, Decimal('64.69'))
        row = calculate_recipe(recipe)['groups'][0]['rows'][0]
        self.assertEqual(row['price_source_cell_id'], self.donor_cell.pk)
        self.assertEqual(row['price_source_origin'], 'carried_forward')
        self.assertEqual(Decimal(row['price_per_kg']), Decimal('64.69'))

    def test_carried_forward_cannot_link_a_different_file(self):
        other = SourceFile.objects.create(original_path='unrelated.xlsx')
        sheet = SourceSheet.objects.create(file=other, name='302.83')
        cell = SourceCell.objects.create(
            sheet=sheet, address='C12', cell_kind='n', raw_value='64.69')
        payload = self.payload()
        payload['properties-0-source_cell'] = str(cell.pk)
        response = self.client.post(reverse('recipe_create'), payload)
        self.assertContains(response, 'относится к другому листу')
        self.assertFalse(RecipeVersion.objects.exists())

    def test_fork_form_and_saved_copy_keep_origin_explanation_and_donor(self):
        self.client.post(reverse('recipe_create'), self.payload())
        original = RecipeVersion.objects.get()
        url = reverse('recipe_fork', args=[original.pk])
        page = self.client.get(url)
        prop_form = page.context['property_formset'].forms[0]
        self.assertEqual(prop_form['origin'].value(), 'carried_forward')
        self.assertEqual(prop_form['provenance_note'].value(), self.note)
        payload = self.displayed_payload(page)
        payload['version_label'] = 'PROVENANCE.2'
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        fork = RecipeVersion.objects.get(version_label='PROVENANCE.2')
        prop = fork.groups.get().components.get().properties.get()
        self.assertEqual(prop.origin, 'carried_forward')
        self.assertEqual(prop.provenance_note, self.note)
        self.assertEqual(prop.source_cell_id, self.donor_cell.pk)
        self.assertEqual(fork.source_sheet_id, self.target.pk)
        self.assertNotEqual(fork.pk, original.pk)

    @staticmethod
    def displayed_payload(response):
        forms = [response.context['form']]
        for key in ('component_formset', 'filling_formset', 'property_formset'):
            formset = response.context[key]
            forms.extend([formset.management_form, *formset.forms])
        payload = {}
        for form in forms:
            for field in form:
                value = field.value()
                if isinstance(value, bool):
                    if value:
                        payload[field.html_name] = 'on'
                else:
                    payload[field.html_name] = '' if value is None else str(value)
        return payload
