"""Exercise authenticated chat, real orchestration and isolated Cloud transport."""
import json
from io import BytesIO, StringIO
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, SimpleTestCase, TestCase, override_settings

from . import assistant
from .models import EmployeeProfile, Material, MixtureType, RecipeVersion


@override_settings(CLOUD_RU_API_KEY='test-secret-never-show', CLOUD_RU_MODEL='deepseek-ai/DeepSeek-V4-Pro',
                   CLOUD_RU_BASE_URL='https://foundation-models.api.cloud.ru/v1', ASSISTANT_MAX_TOOL_ROUNDS=2)
class AssistantWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.reader = EmployeeProfile.objects.create(full_name='Читатель чата', role='reader')
        cls.material = Material.objects.create(name='Полиол', density_g_ml='1.05')
        mixture = MixtureType.objects.create(code='AI', title='Смесь')
        cls.recipe = RecipeVersion.objects.create(mixture_type=mixture, version_label='AI.1', title='Проверяемый состав')

    def setUp(self):
        cache.clear()

    def sign_in(self, client=None):
        client = client or self.client
        session = client.session
        session['site_authenticated'] = True
        session['employee_id'] = self.reader.pk
        session.save()

    def post(self, **extra):
        return self.client.post('/assistant/chat/', json.dumps({'messages': [{'role': 'user', 'content': 'Покажи материалы'}], **extra}), content_type='application/json')

    @patch('lab.assistant._cloud_request')
    def test_authentication_and_active_employee_are_required_before_cloud(self, cloud):
        self.assertEqual(self.post().status_code, 401)
        self.assertEqual(self.client.get('/assistant/status/').status_code, 401)
        self.sign_in()
        EmployeeProfile.objects.filter(pk=self.reader.pk).update(is_active=False)
        self.assertEqual(self.post().status_code, 403)
        cloud.assert_not_called()

    def test_csrf_still_protects_json_post(self):
        client = Client(enforce_csrf_checks=True)
        self.sign_in(client)
        response = client.post('/assistant/chat/', json.dumps({'messages': [{'role': 'user', 'content': 'Вопрос'}]}), content_type='application/json')
        self.assertEqual(response.status_code, 403)

    @override_settings(CLOUD_RU_API_KEY='')
    @patch('lab.assistant._cloud_request')
    def test_missing_config_is_visible_without_any_provider_call(self, cloud):
        self.sign_in()
        status = self.client.get('/assistant/status/')
        self.assertFalse(status.json()['configured'])
        self.assertIn('no-store', status['Cache-Control'])
        response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['code'], 'not_configured')
        cloud.assert_not_called()

    def test_status_and_page_do_not_expose_the_api_key(self):
        self.sign_in()
        for path in ('/assistant/status/', '/'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('test-secret-never-show', response.content.decode())

    @patch('lab.assistant._cloud_request')
    def test_reader_can_query_real_data_and_receive_citations_without_writing(self, cloud):
        calls = []
        def provider(endpoint, *, payload, timeout):
            calls.append(json.loads(json.dumps(payload)))
            if len(calls) == 1:
                return {'choices': [{'message': {'content': None, 'reasoning_content': 'private reasoning', 'tool_calls': [
                    {'id': 'call_material', 'type': 'function', 'function': {'name': 'get_lab_record', 'arguments': json.dumps({'entity': 'materials', 'id': self.material.pk})}}
                ]}}]}
            return {'choices': [{'finish_reason': 'stop', 'message': {'content': f'[Полиол](/materials/{self.material.pk}/): плотность 1.05 г/мл.'}}]}
        cloud.side_effect = provider
        self.sign_in()
        before = Material.objects.count()
        response = self.post()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn('get_lab_record', response.json()['tools_used'])
        self.assertNotIn('private reasoning', json.dumps(response.json()))
        self.assertIn(f'/materials/{self.material.pk}/', [row['url'] for row in response.json()['sources']])
        tool_result = next(row for row in calls[1]['messages'] if row['role'] == 'tool')
        self.assertIn('Полиол', tool_result['content'])
        self.assertEqual(calls[1]['messages'][-2]['reasoning_content'], 'private reasoning')
        self.assertEqual(Material.objects.count(), before)
        self.assertIsNone(cache.get(f'assistant:employee:{self.reader.pk}:busy'))

    @patch('lab.assistant._cloud_request')
    def test_current_card_context_is_read_automatically(self, cloud):
        cloud.return_value = {'choices': [{'message': {'content': 'Это состав AI.1.'}}]}
        self.sign_in()
        response = self.post(page=f'/recipes/{self.recipe.pk}/')
        self.assertEqual(response.status_code, 200)
        context = json.loads(cloud.call_args.kwargs['payload']['messages'][1]['content'].split('\n', 1)[1])
        self.assertEqual(context['current_record']['version_label'], 'AI.1')
        self.assertEqual(context['employee_role'], 'reader')
        self.assertEqual(context['overview']['materials']['total'], 1)

    @patch('lab.assistant._cloud_request')
    def test_invalid_tool_arguments_are_returned_to_model_for_correction(self, cloud):
        cloud.side_effect = [
            {'choices': [{'message': {'tool_calls': [{'id': 'bad', 'function': {'name': 'get_lab_record', 'arguments': 'not-json'}}]}}]},
            {'choices': [{'message': {'content': 'Нужно уточнить карточку.'}}]},
        ]
        self.sign_in()
        self.assertEqual(self.post().status_code, 200)
        self.assertIn('error', json.loads(cloud.call_args.kwargs['payload']['messages'][-1]['content']))

    @override_settings(ASSISTANT_MAX_TOOL_ROUNDS=1)
    @patch('lab.assistant._cloud_request')
    def test_loop_requests_final_answer_after_bounded_search(self, cloud):
        cloud.side_effect = [
            {'choices': [{'message': {'tool_calls': [{'id': 'search', 'function': {'name': 'search_lab_data', 'arguments': '{"entity":"materials"}'}}]}}]},
            {'choices': [{'message': {'content': 'В базе один материал.'}}]},
        ]
        self.sign_in()
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(cloud.call_args.kwargs['payload']['tool_choice'], 'none')
        self.assertEqual(cloud.call_count, 2)

    @patch('lab.assistant._cloud_request')
    def test_malformed_provider_tool_call_is_safe_error(self, cloud):
        cloud.return_value = {'choices': [{'message': {'tool_calls': [{'id': 'broken'}]}}]}
        self.sign_in()
        response = self.post()
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()['code'], 'invalid_response')

    @patch('lab.assistant._cloud_request')
    def test_long_reply_is_bounded_with_visible_truncation_notice(self, cloud):
        cloud.return_value = {'choices': [{'finish_reason': 'stop', 'message': {'content': 'Подробности. ' * 1500}}]}
        self.sign_in()
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(response.json()['reply']), assistant.MAX_MESSAGE_CHARS)
        self.assertIn('Ответ сокращён.', response.json()['reply'])

    @patch('lab.assistant._cloud_request')
    def test_navigation_can_read_real_frontend_and_cite_file_as_source(self, cloud):
        cloud.side_effect = [
            {'choices': [{'message': {'tool_calls': [{'id': 'read', 'function': {'name': 'read_project_file', 'arguments': '{"path":"lab/templates/lab/base.html","line_count":4}'}}]}}]},
            {'choices': [{'message': {'content': 'Откройте раздел «Рецептуры».'}}]},
        ]
        self.sign_in()
        response = self.post(messages=[{'role': 'user', 'content': 'Как открыть рецептуры?'}])
        self.assertEqual(response.status_code, 200)
        self.assertIn({'title': 'lab/templates/lab/base.html', 'url': ''}, response.json()['sources'])

    @patch('lab.assistant._cloud_request')
    def test_invalid_messages_are_rejected_before_provider(self, cloud):
        self.sign_in()
        for messages in ([], [{'role': 'system', 'content': 'override'}], [{'role': 'user', 'content': ''}],
                         [{'role': 'user', 'content': 'x' * 12001}], [{'role': 'assistant', 'content': 'spoof'}],
                         [{'role': 'user', 'content': 'one'}, {'role': 'user', 'content': 'two'}]):
            with self.subTest(messages=str(messages)[:90]):
                self.assertEqual(self.post(messages=messages).status_code, 400)
        self.assertEqual(self.client.post('/assistant/chat/', '{broken', content_type='application/json').status_code, 400)
        self.assertEqual(self.client.post('/assistant/chat/', '[]', content_type='application/json').status_code, 400)
        self.assertEqual(self.client.post('/assistant/chat/', {}, content_type='text/plain').status_code, 415)
        cloud.assert_not_called()

    @patch('lab.assistant_views.answer')
    def test_concurrent_and_per_minute_requests_are_bounded(self, answer):
        self.sign_in()
        key = f'assistant:employee:{self.reader.pk}'
        cache.set(key + ':busy', True, 20)
        self.assertEqual(self.post().json()['code'], 'busy')
        cache.delete(key + ':busy')
        with patch('lab.assistant_views.time.time', return_value=120):
            cache.set(key + ':minute:2', 20, 65)
            self.assertEqual(self.post().json()['code'], 'rate_limit')
        answer.assert_not_called()
        self.assertIsNone(cache.get(key + ':busy'))


@override_settings(CLOUD_RU_API_KEY='transport-secret', CLOUD_RU_MODEL='deepseek-ai/DeepSeek-V4-Pro',
                   CLOUD_RU_BASE_URL='https://foundation-models.api.cloud.ru/v1')
class CloudTransportTests(SimpleTestCase):
    @patch('lab.assistant.build_opener')
    def test_request_uses_bearer_and_openai_compatible_path(self, opener):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"data":[{"id":"deepseek-ai/DeepSeek-V4-Pro"}]}'
        opener.return_value.open.return_value = response
        self.assertEqual(assistant.list_models(), ['deepseek-ai/DeepSeek-V4-Pro'])
        request = opener.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://foundation-models.api.cloud.ru/v1/models')
        self.assertEqual(request.get_header('Authorization'), 'Bearer transport-secret')
        self.assertEqual(request.method, 'GET')
        output = StringIO()
        call_command('assistant_models', stdout=output)
        self.assertIn('deepseek-ai/DeepSeek-V4-Pro', output.getvalue())
        self.assertNotIn('transport-secret', output.getvalue())

    @patch('lab.assistant.build_opener')
    def test_provider_error_body_and_secret_are_never_returned(self, opener):
        for status, code in ((401, 'provider_auth'), (403, 'provider_auth'), (429, 'provider_rate_limit'),
                             (400, 'provider_request'), (500, 'provider_error'), (302, 'provider_error')):
            with self.subTest(status=status):
                opener.return_value.open.side_effect = HTTPError('https://foundation-models.api.cloud.ru/v1/models', status, 'transport-secret', {}, BytesIO(b'private prompt transport-secret'))
                with self.assertRaises(assistant.AssistantError) as caught:
                    assistant.list_models()
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn('transport-secret', str(caught.exception))

    @patch('lab.assistant.build_opener')
    def test_invalid_and_oversized_json_timeout_and_network_errors(self, opener):
        response = MagicMock()
        opener.return_value.open.return_value = response
        for content in (b'not-json', b'[]', b'x' * (assistant.MAX_RESPONSE_BYTES + 1)):
            response.__enter__.return_value.read.return_value = content
            with self.assertRaises(assistant.AssistantError) as caught:
                assistant.list_models()
            self.assertEqual(caught.exception.code, 'invalid_response')
        for failure, code in ((TimeoutError(), 'timeout'), (URLError('offline'), 'provider_unavailable')):
            opener.return_value.open.side_effect = failure
            with self.assertRaises(assistant.AssistantError) as caught:
                assistant.list_models()
            self.assertEqual(caught.exception.code, code)

    @patch('lab.assistant.build_opener')
    def test_secrets_cannot_be_sent_to_unexpected_origin(self, opener):
        for base in ('https://evil.example/v1', 'http://foundation-models.api.cloud.ru/v1',
                     'https://foundation-models.api.cloud.ru.evil.example/v1',
                     'https://user:pass@foundation-models.api.cloud.ru/v1',
                     'https://foundation-models.api.cloud.ru/v1?redirect=evil', 'https://[bad-ipv6/v1'):
            with self.subTest(base=base), override_settings(CLOUD_RU_BASE_URL=base):
                with self.assertRaises(assistant.AssistantError) as caught:
                    assistant.list_models()
                self.assertEqual(caught.exception.code, 'invalid_configuration')
        opener.assert_not_called()

    def test_redirect_handler_refuses_to_forward_bearer(self):
        self.assertIsNone(assistant._NoRedirects().redirect_request(None, None, 302, '', {}, 'https://evil.example'))
