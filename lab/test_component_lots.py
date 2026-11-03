"""Component selection scopes lot choices without changing stored references."""
from django.test import TestCase

from .forms import ComponentForm
from .models import Material, MaterialLot


class ComponentLotTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.material = Material.objects.create(name='Компонент с партиями')
        cls.other_material = Material.objects.create(name='Другой компонент')
        cls.lot = MaterialLot.objects.create(
            material=cls.material, lot_code='MATCHING',
        )
        cls.other_lot = MaterialLot.objects.create(
            material=cls.other_material, lot_code='OTHER',
        )

    def payload(self, **changes):
        return {
            'group_label': 'A', 'quantity_basis': 'mass_percent',
            'quantity_value': '100', 'material': str(self.material.pk),
            'specified_lot': str(self.lot.pk), **changes,
        }

    def available_lots(self, form):
        widget = form.fields['specified_lot'].widget
        groups = widget.optgroups('specified_lot', [''])
        return [
            str(option['value']) for _, options, _ in groups for option in options
            if option['value'] and not option['attrs'].get('disabled')
        ]

    def test_only_selected_material_lots_are_available(self):
        form = ComponentForm(initial={'material': self.material.pk})
        self.assertEqual(self.available_lots(form), [str(self.lot.pk)])
        self.assertEqual(self.available_lots(ComponentForm()), [])

    def test_matching_pair_keeps_only_the_lot_reference(self):
        form = ComponentForm(self.payload())
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['specified_lot'], self.lot)
        self.assertIsNone(form.cleaned_data['material'])

    def test_foreign_lot_is_rejected_on_the_server(self):
        form = ComponentForm(self.payload(specified_lot=str(self.other_lot.pk)))
        self.assertFalse(form.is_valid())
        self.assertIn('specified_lot', form.errors)

    def test_material_without_a_lot_remains_valid(self):
        form = ComponentForm(self.payload(specified_lot=''))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['material'], self.material)

    def test_initial_lot_infers_material_without_mutating_template(self):
        initial = {'specified_lot': self.lot.pk}
        form = ComponentForm(initial=initial)
        self.assertEqual(form['material'].value(), self.material.pk)
        self.assertNotIn('material', initial)
        self.assertEqual(self.available_lots(form), [str(self.lot.pk)])

    def test_legacy_lot_only_post_preserves_display_after_validation_error(self):
        payload = self.payload(material='', quantity_value='-1')
        form = ComponentForm(payload)
        self.assertFalse(form.is_valid())
        self.assertEqual(form['material'].value(), str(self.material.pk))
        self.assertEqual(form['specified_lot'].value(), str(self.lot.pk))
        self.assertEqual(payload['material'], '')

    def test_invalid_lot_id_is_a_field_error(self):
        form = ComponentForm(self.payload(material='', specified_lot='invalid'))
        self.assertFalse(form.is_valid())
        self.assertIn('specified_lot', form.errors)
