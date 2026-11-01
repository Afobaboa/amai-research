"""Coded values use their owning model's vocabulary without rewriting records."""
from copy import deepcopy

from django.test import SimpleTestCase

from . import models as m
from .assistant_values import (
    SECTION_MODELS, annotate_choice_values, resolve_model_field, section_model,
    technical_choice_labels,
)
from .assistant_tools import ENTITIES
from .catalog_forms import SourceCellForm
from .template_forms import OPERATION_CHOICES
from .ui_text import SOURCE_CELL_KIND_LABELS


class ChoiceValueLabelsTests(SimpleTestCase):
    def test_all_choice_fields_published_by_entities_and_sections_are_covered(self):
        models = {spec[0] for spec in ENTITIES.values()} | set(SECTION_MODELS)
        models.update(model for sections in SECTION_MODELS.values() for model in sections.values())
        count = 0
        for model in models:
            for field in model._meta.concrete_fields:
                for code, label in field.flatchoices:
                    count += 1
                    with self.subTest(model=model.__name__, field=field.name, code=code):
                        payload = {field.name: code}
                        annotate_choice_values(payload, model)
                        self.assertEqual(payload[field.name], code)
                        self.assertEqual(payload[field.name + '_display'], str(label))
                        self.assertEqual(payload['value_labels'][field.name][code], str(label))
        self.assertGreater(count, 20)

    def test_status_uses_the_owning_model_in_root_and_nested_search_rows(self):
        lot = {'status': 'active', 'recipes': {'items': [{'status': 'published'}]}}
        test = {'status': 'active', 'recipe_links': {'items': [{'status': 'candidate'}]}}
        annotate_choice_values(lot, m.MaterialLot)
        annotate_choice_values(test, m.TestSession)
        self.assertEqual(lot['status_display'], 'Доступна')
        self.assertEqual(test['status_display'], 'В работе')
        self.assertEqual(lot['recipes']['items'][0]['status_display'], 'Опубликована')
        self.assertEqual(test['recipe_links']['items'][0]['status_display'], 'Кандидат')
        self.assertEqual(test['value_labels']['status'], {'active': 'В работе'})
        self.assertEqual(test['value_labels']['recipe_links.status'], {'candidate': 'Кандидат'})
        self.assertEqual(lot['status'], 'active')

    def test_result_and_group_choices_include_related_fields(self):
        data = {
            'groups': {'items': [{'quantity_basis': 'mass_percent', 'completeness': 'partial'}]},
            'components': {'items': [{'group__quantity_basis': 'weight_parts'}]},
            'filling_plan': {'items': [{'mass_status': 'calculation_unchecked'}]},
        }
        annotate_choice_values(data, m.RecipeVersion)
        self.assertEqual(data['groups']['items'][0]['quantity_basis_display'], 'Массовые доли, %')
        self.assertEqual(data['components']['items'][0]['group__quantity_basis_display'], 'Массовые части')
        self.assertEqual(data['filling_plan']['items'][0]['mass_status_display'], 'Расчёт не проверен')
        result = {'value_state': 'missing_inputs', 'dependencies': {'items': [{
            'input_result__value_state': 'formula_unchecked',
            'input_result__origin': 'calculated',
        }]}}
        annotate_choice_values(result, m.Result)
        self.assertEqual(result['value_state_display'], 'Нет исходных данных')
        dependency = result['dependencies']['items'][0]
        self.assertEqual(dependency['input_result__value_state_display'], 'Формула не проверена')
        self.assertEqual(dependency['input_result__origin_display'], 'Рассчитан')
        self.assertEqual(result['value_labels']['dependencies.input_result__value_state'],
                         {'formula_unchecked': 'Формула не проверена'})

    def test_same_field_name_does_not_mix_source_and_conditioning_choices(self):
        source = {'kind': 'file'}
        session = {'conditioning': {'items': [{'kind': 'aging', 'target_kind': 'specimen'}]}}
        annotate_choice_values(source, m.SourceFile)
        annotate_choice_values(session, m.TestSession)
        self.assertEqual(source['kind_display'], 'Файл')
        row = session['conditioning']['items'][0]
        self.assertEqual(row['kind_display'], 'Выдержка')
        self.assertEqual(row['target_kind_display'], 'Испытуемый материал')
        self.assertEqual(session['value_labels']['conditioning.kind'], {'aging': 'Выдержка'})

    def test_unknown_codes_and_arbitrary_json_do_not_get_invented_labels(self):
        data = {
            'items': [{'value_state': 'custom_state', 'unknown_field': 'missing_inputs'}],
            'definition': {'status': 'active', 'groups': [{'quantity_basis': 'mass_percent'}]},
            'template_snapshot': {'status': 'active'},
        }
        before = deepcopy(data)
        annotate_choice_values(data, m.Result)
        self.assertEqual(data, before)
        self.assertIsNone(resolve_model_field(m.Result, 'unknown__status'))
        self.assertIsNone(resolve_model_field(m.Result, 'numeric_value__status'))
        self.assertIs(section_model(m.Result, 'dependencies'), m.ResultDependency)

    def test_calculation_snapshots_get_metadata_without_changes_inside_snapshot(self):
        component = {'lot_status': 'quarantine', 'density_origin': 'lot', 'price_origin': 'material'}
        data = {
            'inputs': {
                'template': {'operations': ['mass', 'totalCost']},
                'groups': [{'quantity_basis': 'mass_percent', 'completeness': 'partial',
                            'components': [component]}],
                'filling_plan': [{'mass_status': 'calculation_checked'}],
                'unknown_payload': {'status': 'active'},
            },
            'outputs': {
                'purpose': 'publication',
                'groups': [{'quantity_basis': 'weight_parts', 'rows': [dict(component)]}],
            },
        }
        original = deepcopy(data)
        annotate_choice_values(data, m.CalculationRecord)
        self.assertEqual(data['inputs'], original['inputs'])
        self.assertEqual(data['outputs'], original['outputs'])
        labels = data['value_labels']
        self.assertEqual(labels['inputs.groups.components.lot_status'], {'quarantine': 'Карантин'})
        self.assertEqual(labels['outputs.groups.rows.density_origin'], {'lot': 'Из партии'})
        self.assertEqual(labels['outputs.groups.rows.price_origin'], {'material': 'Из справочника материала'})
        self.assertEqual(labels['outputs.purpose'], {'publication': 'Расчёт при публикации'})
        self.assertEqual(labels['inputs.filling_plan.mass_status'], {'calculation_checked': 'Расчёт проверен'})
        self.assertEqual(labels['inputs.template.operations']['totalCost'], dict(OPERATION_CHOICES)['totalCost'])
        self.assertFalse(any('unknown_payload' in path for path in labels))

    def test_template_definition_known_choices_are_metadata_only(self):
        definition = {'components': [{'quantity_basis': 'weight_parts', 'completeness': 'complete'}],
                      'filling': [{'material': 1}]}
        data = {'definition': deepcopy(definition)}
        annotate_choice_values(data, m.RecipeTemplate)
        self.assertEqual(data['definition'], definition)
        self.assertEqual(data['value_labels']['definition.components.quantity_basis'],
                         {'weight_parts': 'Массовые части'})

    def test_operation_names_and_existing_metadata_are_preserved(self):
        data = {'operations': ['mass', 'totalCost'],
                'value_labels': {'custom': {'code': 'Существующая подпись'}}}
        annotate_choice_values(data, m.CalculationTemplate)
        self.assertEqual(data['operations'], ['mass', 'totalCost'])
        self.assertEqual(data['operations_display'], [dict(OPERATION_CHOICES)[code] for code in data['operations']])
        self.assertEqual(data['value_labels']['custom'], {'code': 'Существующая подпись'})
        unknown = {'operations': ['mass', 'unknown_operation']}
        annotate_choice_values(unknown, m.CalculationTemplate)
        self.assertNotIn('operations_display', unknown)
        self.assertNotIn('unknown_operation', unknown['value_labels']['operations'])

    def test_fallback_only_contains_unambiguous_technical_choice_codes(self):
        labels = technical_choice_labels()
        self.assertEqual(labels['missing_inputs'], 'Нет исходных данных')
        self.assertEqual(labels['formula_unchecked'], 'Формула не проверена')
        self.assertEqual(labels['calculation_checked'], 'Расчёт проверен')
        self.assertNotIn('active', labels)

    def test_current_and_imported_source_cell_types_have_shared_display_names(self):
        sheet = m.SourceSheet(name='Лист')
        for code, label in SOURCE_CELL_KIND_LABELS.items():
            with self.subTest(code=code):
                payload = {'cell_kind': code}
                annotate_choice_values(payload, m.SourceCell)
                self.assertEqual(payload['cell_kind'], code)
                self.assertEqual(payload['cell_kind_display'], label)
                self.assertEqual(payload['value_labels']['cell_kind'], {code: label})
                cell = m.SourceCell(sheet=sheet, address='A1', cell_kind=code)
                choices = dict(SourceCellForm(instance=cell, sheet=sheet)
                               .fields['cell_kind'].widget.choices)
                self.assertEqual(choices[code], label)
        unknown = {'cell_kind': 'custom_kind'}
        annotate_choice_values(unknown, m.SourceCell)
        self.assertEqual(unknown, {'cell_kind': 'custom_kind'})

    def test_cached_excel_formula_type_preserves_its_code_and_formula(self):
        payload = {'cell_kind': 'n', 'formula_text': 'ROUND(A1 / B1, 2)',
                   'cached_value': '10.5'}
        original = deepcopy(payload)
        annotate_choice_values(payload, m.SourceCell)
        self.assertEqual(payload['cell_kind_display'], 'Формула')
        self.assertEqual(payload['value_labels']['cell_kind'], {'n': 'Число'})
        for key, value in original.items():
            self.assertEqual(payload[key], value)
