"""Technical names stay readable in complete replies and partial streams."""
from copy import deepcopy
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from . import assistant
from .assistant_output import OutputLabels, ReadableOutput, normalize_output


class ReadableOutputTests(SimpleTestCase):
    def test_known_identifiers_change_without_touching_unknown_codes_or_names(self):
        self.assertEqual(
            normalize_output('`specified_lot_id`: null; source_price_per_kg; Inovol C210; '
                             'new_property_code; xsource_price_per_kg; source_price_per_kg_extra'),
            '`Партия`: null; Цена из исходной рецептуры; Inovol C210; '
            'new_property_code; xsource_price_per_kg; source_price_per_kg_extra',
        )

    def test_links_source_paths_units_and_formulas_are_preserved(self):
        text = ('[source_price_per_kg](/materials/source_price_per_kg/?field=specified_lot_id) '
                'https://example.test/source_price_per_kg '
                'https://example.test/?field=(specified_lot_id) '
                '`source_price_per_kg * 100` '
                '```text\nsource_price_per_kg\n``` г/мл; 316Ф-3!B7')
        self.assertEqual(
            normalize_output(text),
            '[Цена из исходной рецептуры](/materials/source_price_per_kg/?field=specified_lot_id) '
            'https://example.test/source_price_per_kg '
            'https://example.test/?field=(specified_lot_id) '
            '`source_price_per_kg * 100` '
            '```text\nЦена из исходной рецептуры\n``` г/мл; 316Ф-3!B7',
        )

    def test_tool_labels_are_used_without_altering_tool_data(self):
        data = {'field_labels': {'component__specified_lot_id': 'Партия компонента'},
                'value_labels': {'metric__code': {'foam_yield': 'Выход пены'}}}
        original = deepcopy(data)
        labels = OutputLabels()
        labels.add_data({'current_record': data})
        self.assertEqual(labels.render('component__specified_lot_id; foam_yield'),
                         'Партия компонента; Выход пены')
        self.assertEqual(data, original)

    def test_every_stream_split_produces_the_same_reply_and_keeps_keys_private(self):
        text = ('Партия: `specified_lot_id: null`; source_price_per_kg; '
                'specified_lot_id: null; unknown_code: null; '
                '[Карточка](/materials/source_price_per_kg/); '
                '`source_price_per_kg * 100`; unknown_code.')
        expected = normalize_output(text)
        for split in range(len(text) + 1):
            with self.subTest(split=split):
                output = ReadableOutput(OutputLabels())
                rendered = output.feed(text[:split]) + output.feed(text[split:])
                rendered += output.feed(final=True)
                self.assertEqual(rendered, expected)
        output = ReadableOutput(OutputLabels())
        deltas = [output.feed(character) for character in 'specified_lot_id']
        self.assertEqual(''.join(deltas), '')
        self.assertEqual(output.feed(final=True), 'Партия')

    def test_missing_relation_is_readable_without_changing_unrelated_json(self):
        self.assertEqual(normalize_output('`specified_lot_id: null`; other_code: null; null'),
                         '`Партия: не указана`; other_code: null; null')

    def test_delimiter_runs_can_continue_in_later_chunks_without_duplicate_output(self):
        for text in ('`specified_lot_id``', '```text\nsource_price_per_kg\n````'):
            for split in range(len(text) + 1):
                with self.subTest(text=text, split=split):
                    output = ReadableOutput(OutputLabels())
                    deltas = [output.feed(text[:split]), output.feed(text[split:])]
                    deltas.append(output.feed(final=True))
                    self.assertEqual(''.join(deltas), normalize_output(text))

    def test_subtraction_modulo_and_function_expressions_are_source_data(self):
        for expression in ('source_price_per_kg - cost_per_kg',
                           'source_price_per_kg % 10', 'ROUND(source_price_per_kg, 2)'):
            with self.subTest(expression=expression):
                code = '`' + expression + '`'
                self.assertEqual(normalize_output(code), code)

    def test_saved_answers_translate_real_unambiguous_enum_codes(self):
        self.assertEqual(
            normalize_output('missing_inputs; formula_unchecked; mass_percent; '
                             'calculation_checked; calculation_unchecked'),
            'Нет исходных данных; Формула не проверена; Массовые доли, %; '
            'Расчёт проверен; Расчёт не проверен',
        )

    def test_simple_field_names_are_translated_only_in_explicit_code(self):
        text = 'status origin completeness; `status; origin; completeness`; ISO_METHOD_42; EN 17333-1'
        self.assertEqual(normalize_output(text),
                         'status origin completeness; `Статус; Происхождение; Полнота`; '
                         'ISO_METHOD_42; EN 17333-1')

    def test_simple_value_codes_require_unambiguous_tool_metadata(self):
        labels = OutputLabels()
        self.assertEqual(labels.render('totalCost; `totalCost`; `active`'),
                         'totalCost; `totalCost`; `active`')
        labels.add_data({'value_labels': {
            'operations': {'totalCost': 'Стоимость смеси, ₽, и цена за кг'},
            'status': {'active': 'В работе'},
        }})
        self.assertEqual(labels.render('totalCost; `totalCost`; active; `active`'),
                         'totalCost; `Стоимость смеси, ₽, и цена за кг`; active; `В работе`')
        labels.add_data({'value_labels': {'lots.status': {'active': 'Доступна'}}})
        self.assertEqual(labels.render('`active`; `status`'), '`active`; `Статус`')

    def test_context_collisions_do_not_choose_the_first_snake_case_value(self):
        labels = OutputLabels()
        labels.add_data({'value_labels': {
            'one.status': {'custom_state': 'Первый смысл'},
            'two.status': {'custom_state': 'Другой смысл'},
        }})
        self.assertEqual(labels.render('custom_state; `custom_state`'),
                         'custom_state; `custom_state`')

    def test_exact_unit_spans_are_readable_and_expressions_stay_intact(self):
        text = '`g/ml`; `mg KOH/g`; `source_currency/kg`; `g/ml * source_price_per_kg`'
        self.assertEqual(normalize_output(text),
                         '`г/мл`; `мг KOH/г`; `валюта не указана / кг`; '
                         '`g/ml * source_price_per_kg`')

    def test_stream_uses_enum_fallback_and_code_only_contextual_values(self):
        labels = OutputLabels()
        labels.add_data({'value_labels': {
            'operations': {'totalCost': 'Стоимость смеси, ₽, и цена за кг'},
        }})
        text = 'missing_inputs; `status`; totalCost; `totalCost`; `g/ml`; ISO_METHOD_42.'
        expected = ('Нет исходных данных; `Статус`; totalCost; '
                    '`Стоимость смеси, ₽, и цена за кг`; `г/мл`; ISO_METHOD_42.')
        self.assertEqual(normalize_output(text, labels), expected)
        for split in range(len(text) + 1):
            with self.subTest(split=split):
                output = ReadableOutput(labels)
                self.assertEqual(output.feed(text[:split]) + output.feed(text[split:])
                                 + output.feed(final=True), expected)


@override_settings(
    CLOUD_RU_API_KEY='test-key', CLOUD_RU_MODEL='test-model',
    CLOUD_RU_BASE_URL='https://foundation-models.api.cloud.ru/v1',
)
class AssistantReadableReplyTests(SimpleTestCase):
    @patch('lab.assistant._initial_context', return_value={})
    @patch('lab.assistant._cloud_request')
    def test_json_reply_uses_readable_names(self, cloud, context):
        cloud.return_value = {'choices': [{'message': {
            'content': '`specified_lot_id` не указан; source_price_per_kg.'
        }}]}
        result = assistant.answer([{'role': 'user', 'content': 'Что известно о партии?'}])
        self.assertEqual(result['reply'], '`Партия` не указан; Цена из исходной рецептуры.')
        cloud.assert_called_once()

    @patch('lab.assistant._initial_context', return_value={})
    @patch('lab.assistant.execute_tool')
    @patch('lab.assistant._cloud_stream')
    def test_stream_and_final_reply_share_labels_from_tools(self, cloud, tool, context):
        tool.return_value = {
            'url': '/tests/1/', 'title': 'Испытание',
            'field_labels': {'specified_lot_id': 'Партия'},
            'value_labels': {'metric__code': {'foam_yield': 'Выход пены'}},
        }

        def provider(payload, *, timeout):
            if payload['messages'][-1]['role'] != 'tool':
                yield {'choices': [{'delta': {'tool_calls': [{
                    'index': 0, 'id': 'read', 'function': {
                        'name': 'get_lab_record', 'arguments': '{"entity":"tests","id":1}'
                    },
                }]}, 'finish_reason': 'tool_calls'}]}
            else:
                for fragment in ('spec', 'ified_lot_', 'id; foam_', 'yield. [Испытание]', '(/tests/1/)'):
                    yield {'choices': [{'delta': {'content': fragment}}]}
                yield {'choices': [{'delta': {}, 'finish_reason': 'stop'}]}

        cloud.side_effect = provider
        events = list(assistant.answer_stream([{'role': 'user', 'content': 'Что известно?'}]))
        reply = events[-1][1]['reply']
        self.assertEqual(reply, 'Партия; Выход пены. [Испытание](/tests/1/)')
        self.assertEqual(''.join(data['text'] for event, data in events if event == 'delta'), reply)
        self.assertEqual(events[-1][1]['sources'], [{'url': '/tests/1/', 'title': 'Испытание'}])
