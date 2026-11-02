"""Assistant metadata uses the same vocabulary as laboratory cards."""
from decimal import Decimal
from copy import deepcopy
from unittest.mock import patch

from django.test import TestCase

from .assistant_labels import add_value_labels, field_labels
from .assistant_tools import execute_tool
from .models import (
    FillingPlanLine, Material, MaterialLot, Metric, RecipeComponent, RecipeGroup,
    RecipeProperty, RecipeVersion, Result, ResultDependency, TestRun, TestSession,
)
from .templatetags.lab_tags import property_label, unit_label
from .ui_text import FIELD_LABELS, PROPERTY_LABELS, UNIT_LABELS


class AssistantLabelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.material = Material.objects.create(name='Полиол')
        cls.lot = MaterialLot.objects.create(
            material=cls.material, lot_code='П-01',
            density_g_ml=Decimal('1.05'), price_per_kg=0,
        )
        cls.recipe = RecipeVersion.objects.create(version_label='316Ф-3')
        cls.group = RecipeGroup.objects.create(recipe_version=cls.recipe, label='A')
        cls.unbatched_component = RecipeComponent.objects.create(
            group=cls.group, position=1, unbatched_material=cls.material,
            quantity_value=0,
        )
        cls.batched_component = RecipeComponent.objects.create(
            group=cls.group, position=2, specified_lot=cls.lot,
        )
        FillingPlanLine.objects.create(
            recipe_version=cls.recipe, unbatched_material=cls.material,
        )
        FillingPlanLine.objects.create(recipe_version=cls.recipe, specified_lot=cls.lot)
        cls.price = RecipeProperty.objects.create(
            component=cls.unbatched_component, property_code='source_price_per_kg',
            value=0, unit_code='source_currency/kg',
        )

    def recipe_section(self, section):
        result = execute_tool('get_lab_record', {
            'entity': 'recipes', 'id': self.recipe.pk, 'section': section,
        })
        self.assertNotIn('error', result)
        return result

    def test_foreign_key_labels_cover_components_and_filling_without_changing_values(self):
        for section in ('components', 'filling_plan'):
            with self.subTest(section=section):
                result = self.recipe_section(section)
                self.assertEqual(
                    result['field_labels']['specified_lot_id'], FIELD_LABELS['specified_lot'],
                )
                self.assertEqual(
                    result['field_labels']['unbatched_material_id'],
                    FIELD_LABELS['unbatched_material'],
                )
                unbatched, batched = result[section]['items']
                self.assertIsNone(unbatched['specified_lot_id'])
                self.assertEqual(unbatched['unbatched_material_id'], self.material.pk)
                self.assertEqual(batched['specified_lot_id'], self.lot.pk)
                self.assertIsNone(batched['unbatched_material_id'])
                if section == 'components':
                    self.assertEqual(Decimal(unbatched['quantity_value']), Decimal(0))

    def test_nested_property_codes_and_units_have_readable_labels_and_preserve_zero(self):
        result = self.recipe_section('properties')
        price = result['properties']['items'][0]
        self.assertEqual(price['id'], self.price.pk)
        self.assertEqual(price['component_id'], self.unbatched_component.pk)
        self.assertEqual(price['property_code'], 'source_price_per_kg')
        self.assertEqual(price['property_label'], property_label('source_price_per_kg'))
        self.assertEqual(price['unit_code'], 'source_currency/kg')
        self.assertEqual(price['unit_label'], unit_label('source_currency/kg'))
        self.assertEqual(Decimal(price['value']), Decimal(0))
        self.assertEqual(result['value_labels']['property_code'], {
            'source_price_per_kg': property_label('source_price_per_kg'),
        })
        self.assertEqual(result['value_labels']['unit_code'], {
            'source_currency/kg': unit_label('source_currency/kg'),
        })
        self.assertNotIn('ohv', result['value_labels']['property_code'])

    def test_property_and_unit_renaming_reaches_both_templates_and_assistant(self):
        with patch.dict(PROPERTY_LABELS, {'source_price_per_kg': 'Цена из документа'}), \
                patch.dict(UNIT_LABELS, {'source_currency/kg': 'валюта источника / кг'}):
            result = self.recipe_section('properties')
            row = result['properties']['items'][0]
            self.assertEqual(property_label('source_price_per_kg'), 'Цена из документа')
            self.assertEqual(row['property_label'], 'Цена из документа')
            self.assertEqual(
                result['value_labels']['property_code']['source_price_per_kg'],
                'Цена из документа',
            )
            self.assertEqual(unit_label('source_currency/kg'), 'валюта источника / кг')
            self.assertEqual(row['unit_label'], 'валюта источника / кг')

    def test_custom_property_and_unit_do_not_get_invented_labels(self):
        custom = RecipeProperty.objects.create(
            recipe_version=self.recipe, property_code='custom_lab_property',
            value=Decimal('12.5'), unit_code='custom_lab_unit',
        )
        result = self.recipe_section('properties')
        row = next(row for row in result['properties']['items'] if row['id'] == custom.pk)
        self.assertEqual(row['property_code'], 'custom_lab_property')
        self.assertEqual(row['unit_code'], 'custom_lab_unit')
        self.assertEqual(Decimal(row['value']), Decimal('12.5'))
        self.assertNotIn('property_label', row)
        self.assertNotIn('unit_label', row)
        self.assertNotIn('custom_lab_property', result['value_labels']['property_code'])
        self.assertNotIn('custom_lab_unit', result['value_labels']['unit_code'])

    def test_property_density_label_does_not_replace_lot_field_label(self):
        RecipeProperty.objects.create(
            component=self.unbatched_component, property_code='density_g_ml',
            value=Decimal('1.05'), unit_code='g/ml',
        )
        properties = self.recipe_section('properties')
        density = next(row for row in properties['properties']['items']
                       if row['property_code'] == 'density_g_ml')
        lot = execute_tool('get_lab_record', {
            'entity': 'lots', 'id': self.lot.pk,
        })
        self.assertEqual(density['property_label'], 'Плотность компонента')
        self.assertEqual(lot['field_labels']['density_g_ml'],
                         'Плотность партии, г/мл')
        self.assertEqual(
            properties['value_labels']['property_code']['density_g_ml'],
            'Плотность компонента',
        )

    def test_result_dependencies_include_metric_name_and_unit_with_original_code(self):
        session = TestSession.objects.create(identifier='316Ф-3')
        run = TestRun.objects.create(session=session, scope='На бумаге')
        metric = Metric.objects.create(
            code='custom_input_metric', label='Масса выпущенной пены', unit_code='г',
        )
        source = Result.objects.create(test_run=run, metric=metric, numeric_value=0)
        calculated = Result.objects.create(
            test_run=run, metric=metric, origin='calculated',
            value_state='missing_inputs', calculation_code='source / density',
        )
        ResultDependency.objects.create(result=calculated, input_result=source, role='mass')
        result = execute_tool('get_lab_record', {
            'entity': 'results', 'id': calculated.pk, 'section': 'dependencies',
        })
        dependency = result['dependencies']['items'][0]
        self.assertEqual(dependency['input_result_id'], source.pk)
        self.assertEqual(dependency['input_result__metric__code'], metric.code)
        self.assertEqual(dependency['input_result__metric__label'], metric.label)
        self.assertEqual(dependency['input_result__metric__unit_code'], metric.unit_code)
        self.assertEqual(Decimal(dependency['input_result__numeric_value']), Decimal(0))
        self.assertEqual(result['value_labels']['input_result__metric__code'], {
            metric.code: metric.label,
        })

    def test_integrated_metadata_preserves_documentary_json_and_combines_vocabularies(self):
        definition = {'components': [{'quantity_basis': 'weight_parts'}],
                      'properties': [{'property_code': 'source_price_per_kg',
                                      'unit_code': 'source_currency/kg', 'value': '0'}]}
        from .models import RecipeTemplate
        data = {'definition': deepcopy(definition)}
        add_value_labels(data, RecipeTemplate)
        self.assertEqual(data['definition'], definition)
        self.assertEqual(data['value_labels']['definition.components.quantity_basis'],
                         {'weight_parts': 'Массовые части'})
        self.assertEqual(data['value_labels']['property_code']['source_price_per_kg'],
                         'Цена из исходной рецептуры')

    def test_field_labels_use_related_model_and_section_context_without_queries(self):
        from .catalog_forms import MaterialForm
        from .forms import ConditioningForm
        with self.assertNumQueries(0):
            labels = field_labels({
                'material__name': 'Полиол',
            }, MaterialLot)
            session_labels = field_labels({
                'conditioning': {'items': [{'kind': 'aging', 'kind_display': 'Выдержка'}]},
            }, TestSession)
        self.assertEqual(labels['material__name'], MaterialForm.base_fields['name'].label)
        self.assertEqual(session_labels['kind'], ConditioningForm.base_fields['kind'].label)
        self.assertEqual(session_labels['kind_display'], ConditioningForm.base_fields['kind'].label)
