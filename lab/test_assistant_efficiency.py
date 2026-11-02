"""Context stays selective; provider usage survives errors and stream closure."""
import json
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import resolve

from . import assistant
from .assistant_context import initial_context, interface_guide
from .assistant_tools import execute_tool
from .assistant_labels import ui_labels
from .catalog_forms import MaterialForm
from .context_processors import workspace
from .ui_text import UI_TEXT, navigation_labels
from django.template.loader import render_to_string
from django.test import RequestFactory
from .models import AssistantRequestLog, EmployeeProfile, Material, MaterialLot


class CompactContextTests(TestCase):
    def setUp(self):
        self.material = Material.objects.create(
            name='Полиол', notes='Важное примечание ' * 150,
        )

    @patch('lab.assistant_context.execute_tool', wraps=execute_tool)
    def test_initial_context_reads_only_current_summary(self, tool):
        plain = initial_context('/materials/', 'reader')
        self.assertNotIn('overview', plain)
        tool.assert_not_called()
        card = initial_context(f'/materials/{self.material.pk}/', 'reader')
        tool.assert_called_once_with('get_lab_record', {
            'entity': 'materials', 'id': self.material.pk, 'section': 'summary',
        })
        self.assertNotIn('notes', card['current_record'])
        self.assertNotIn('lots', card['current_record'])
        self.assertIn('lots', card['current_record']['available_sections'])

    def test_summary_is_small_and_full_notes_remain_accessible(self):
        args = {'entity': 'materials', 'id': self.material.pk}
        with self.assertNumQueries(2):
            summary = execute_tool('get_lab_record', args)
        full = execute_tool('get_lab_record', {**args, 'section': 'all'})
        self.assertEqual(full['notes'], self.material.notes)
        self.assertLess(len(json.dumps(summary)), len(json.dumps(full)) / 3)
        with self.assertNumQueries(4):
            lots = execute_tool('get_lab_record', {**args, 'section': 'lots'})
        self.assertNotIn('recipes', lots)
        self.assertNotIn('notes', lots)

    def test_search_matches_omitted_notes_and_preserves_pagination(self):
        for index in range(7):
            Material.objects.create(name=f'Другой-{index}', notes='Важное')
        first = execute_tool('search_lab_data', {
            'entity': 'materials', 'q': 'Важное',
        })
        self.assertEqual(first['total'], 8)
        self.assertEqual(len(first['items']), 5)
        self.assertTrue(first['truncated'])
        self.assertNotIn('notes', first['items'][0])
        tail = execute_tool('search_lab_data', {
            'entity': 'materials', 'q': 'Важное', 'offset': first['next_offset'],
        })
        self.assertEqual(len(tail['items']), 3)
        self.assertIsNone(tail['next_offset'])

    def test_long_search_titles_are_marked_and_full_card_keeps_them(self):
        self.material.name = 'Д' * 300
        self.material.save()
        item = execute_tool('search_lab_data', {
            'entity': 'materials',
        })['items'][0]
        self.assertEqual(len(item['name']), 240)
        self.assertIn('name', item['truncated_fields'])
        full = execute_tool('get_lab_record', {
            'entity': 'materials', 'id': self.material.pk, 'section': 'all',
        })
        self.assertEqual(full['name'], self.material.name)

    def test_navigation_help_links_resolve_and_source_reading_is_optional(self):
        for path in (
            '/recipes/', '/recipes/new/', '/recipes/1/fork/',
            '/recipes/1/calculate/', '/tests/1/results/new/',
            '/tests/1/observations/new/', '/tests/1/conditioning/new/',
            '/lots/new/', '/recipe-templates/', '/calculation-templates/',
            '/calculations/1/', '/history/',
        ):
            self.assertIsNotNone(resolve(path).url_name)
        self.assertIn('Краткая справка по интерфейсу', assistant.SYSTEM_PROMPT)
        self.assertIn('только если справки', assistant.SYSTEM_PROMPT)

    def test_navigation_uses_current_card_and_author_permissions(self):
        context = initial_context('/recipes/8/', 'author')
        link = next(item for item in context['navigation'] if item['label'] == 'Новое испытание')
        self.assertEqual(link['url'], '/tests/new/?recipe=8')
        context = initial_context('/tests/12/', 'author')
        for item in context['navigation']:
            self.assertIsNotNone(resolve(item['url']).url_name)
            self.assertNotIn('{id}', item['url'])
        reader = initial_context('/tests/12/', 'reader')
        self.assertEqual([item['label'] for item in reader['navigation']], ['Рецептуры', 'Испытания'])

    def test_tools_explain_property_names_using_ui_labels(self):
        lot = MaterialLot.objects.create(material=self.material, lot_code='Партия')
        result = execute_tool('get_lab_record', {'entity': 'lots', 'id': lot.pk})
        self.assertEqual(result['field_labels']['effective_density_g_ml'], 'Плотность, г/мл')
        self.assertEqual(result['field_labels']['effective_price_per_kg'], 'Цена за кг')
        self.assertIn('effective_price_per_kg', result['available_sections'])
        result = execute_tool('search_lab_data', {'entity': 'materials'})
        self.assertEqual(result['field_labels']['name'], MaterialForm.base_fields['name'].label)

    def test_renaming_shared_label_updates_interface_and_assistant(self):
        navigation_labels.cache_clear()
        ui_labels.cache_clear()
        try:
            with patch.dict(UI_TEXT, {'test_create': 'Создать опыт', 'density_g_ml': 'Плотность сырья'}):
                request = RequestFactory().get('/')
                request.employee = EmployeeProfile(role='author')
                context = workspace(request)
                html = render_to_string('lab/dashboard.html', context)
                self.assertIn('Создать опыт</a>', html)
                self.assertIn('[Создать опыт](/tests/new/)', interface_guide())
                self.assertIn({'label': 'Создать опыт', 'url': '/tests/new/'},
                              initial_context('/', 'author')['navigation'])
                self.assertIn('Создать опыт', [item['label'] for item in context['assistant_navigation']])
                lot = MaterialLot.objects.create(material=self.material, lot_code='Партия')
                html = render_to_string('lab/catalog/lot_detail.html', {**context, 'lot': lot})
                labels = execute_tool('get_lab_record', {'entity': 'lots', 'id': lot.pk})['field_labels']
                self.assertIn('Плотность сырья</td>', html)
                self.assertEqual(labels['effective_density_g_ml'], UI_TEXT['density_g_ml'])
        finally:
            navigation_labels.cache_clear()
            ui_labels.cache_clear()


@override_settings(
    CLOUD_RU_API_KEY='private-api-key', CLOUD_RU_MODEL='test-model',
    CLOUD_RU_BASE_URL='https://foundation-models.api.cloud.ru/v1',
)
class AssistantUsageTests(TestCase):
    def setUp(self):
        cache.clear()
        employee = EmployeeProfile.objects.create(full_name='Читатель')
        session = self.client.session
        session.update({'site_authenticated': True, 'employee_id': employee.pk})
        session.save()

    def post(self, stream=False):
        return self.client.post(
            '/assistant/chat/',
            json.dumps({'messages': [{'role': 'user', 'content': 'Вопрос'}]}),
            content_type='application/json',
            HTTP_ACCEPT='text/event-stream' if stream else 'application/json',
        )

    @staticmethod
    def usage(prompt=10, completion=3):
        return {'prompt_tokens': prompt, 'completion_tokens': completion,
                'total_tokens': prompt + completion,
                'completion_tokens_details': {'reasoning_tokens': 2},
                'prompt_tokens_details': {'cached_tokens': 4},
                'private': 'private-api-key'}

    @patch('lab.assistant._cloud_request')
    def test_multiple_model_and_repeated_tool_calls_are_counted(self, cloud):
        tool_calls = [{
            'id': str(i), 'function': {'name': 'search_lab_data',
                                     'arguments': '{"entity":"materials"}'},
        } for i in range(2)]
        cloud.side_effect = [
            {'choices': [{'message': {'tool_calls': tool_calls}}],
             'usage': self.usage()},
            {'choices': [{'message': {'content': 'Ответ'}}],
             'usage': self.usage(20, 7)},
        ]
        response = self.post()
        self.assertEqual(response.status_code, 200)
        record = AssistantRequestLog.objects.get()
        self.assertEqual(record.provider_call_count, 2)
        self.assertEqual(record.tool_call_count, 2)
        self.assertEqual(record.tools_used, ['search_lab_data'])
        self.assertEqual(record.usage_call_count, 2)
        self.assertEqual((record.prompt_tokens, record.completion_tokens), (30, 10))
        self.assertGreater(record.provider_calls[1]['message_chars'],
                           record.provider_calls[0]['message_chars'])
        self.assertNotIn('private-api-key', json.dumps(record.provider_calls))
        self.assertNotIn('provider_calls', response.json())

    @patch('lab.assistant._cloud_stream')
    def test_stream_usage_frames_replace_cumulative_values(self, cloud):
        cloud.return_value = (chunk for chunk in [
            {'choices': [], 'usage': self.usage(10, 1)},
            {'choices': [{'delta': {'content': 'Ответ'}}]},
            {'choices': [{'delta': {}, 'finish_reason': 'stop'}]},
            {'choices': [], 'usage': self.usage(10, 3)},
        ])
        response = self.post(stream=True)
        body = b''.join(response.streaming_content)
        record = AssistantRequestLog.objects.get()
        self.assertEqual(record.status, 'completed')
        self.assertEqual((record.prompt_tokens, record.completion_tokens), (10, 3))
        self.assertEqual(record.usage_call_count, 1)
        self.assertEqual(record.provider_calls[0]['status'], 'completed')
        self.assertNotIn(b'usage', body)
        self.assertEqual(cloud.call_args.args[0]['stream_options'],
                         {'include_usage': True})

    @patch('lab.assistant._cloud_request')
    def test_missing_usage_is_unknown_rather_than_zero(self, cloud):
        cloud.return_value = {'choices': [{'message': {'content': 'Ответ'}}]}
        self.assertEqual(self.post().status_code, 200)
        record = AssistantRequestLog.objects.get()
        self.assertEqual(record.provider_call_count, 1)
        self.assertEqual(record.usage_call_count, 0)
        self.assertIsNone(record.prompt_tokens)
        self.assertIsNone(record.completion_tokens)

    @patch('lab.assistant._cloud_request')
    def test_failed_provider_attempt_is_retained(self, cloud):
        cloud.side_effect = assistant.AssistantError('Таймаут', 'timeout', 504)
        self.assertEqual(self.post().status_code, 504)
        record = AssistantRequestLog.objects.get()
        self.assertEqual(record.provider_call_count, 1)
        self.assertEqual(record.provider_calls[0]['status'], 'failed')
        self.assertIsNone(record.completion_tokens)

    @patch('lab.assistant._cloud_stream')
    def test_interrupted_stream_keeps_attempt_and_closes_provider(self, cloud):
        closed = []

        def chunks(*args, **kwargs):
            try:
                yield {'choices': [{'delta': {'content': 'Начало'}}]}
                yield {'choices': [{'delta': {}, 'finish_reason': 'stop'}]}
            finally:
                closed.append(True)
        cloud.side_effect = chunks
        response = self.post(stream=True)
        for frame in response.streaming_content:
            if b'event: delta' in frame:
                break
        response.close()
        record = AssistantRequestLog.objects.get()
        self.assertEqual(record.status, 'interrupted')
        self.assertEqual(record.provider_call_count, 1)
        self.assertEqual(record.provider_calls[0]['status'], 'interrupted')
        self.assertEqual(closed, [True])
