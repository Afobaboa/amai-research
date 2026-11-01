"""Exercise authenticated chat, real orchestration and isolated Cloud transport."""
import json
from copy import deepcopy
from io import BytesIO, StringIO
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, SimpleTestCase, TestCase, override_settings

from . import assistant
from .models import AssistantRequestLog, EmployeeProfile, Material, MixtureType, RecipeVersion


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

    def stream_post(self, **extra):
        return self.client.post('/assistant/chat/', json.dumps({'messages': [{'role': 'user', 'content': 'Покажи материалы'}], **extra}),
                                content_type='application/json', HTTP_ACCEPT='text/event-stream')

    def stream_events(self, response):
        frames = b''.join(response.streaming_content).decode().strip().split('\n\n')
        return [(frame.split('\n')[0].removeprefix('event: '), json.loads(frame.split('\n', 1)[1].removeprefix('data: '))) for frame in frames]

    def draft(self):
        return {'title': 'Новая рецептура', 'forms': [{'method': 'post', 'fields': [
            {'name': 'title', 'label': 'Название', 'type': 'text', 'value': 'Пробный состав', 'changed': True},
            {'name': 'components-0-quantity_value', 'label': 'Количество', 'type': 'number', 'value': '0', 'changed': True, 'section': 'Состав рецептуры'},
            {'name': 'notes', 'label': 'Примечание', 'type': 'textarea', 'value': '', 'changed': False},
            {'name': 'components-0-DELETE', 'label': 'Удалить', 'type': 'checkbox', 'value': False, 'changed': False},
            {'name': 'components-0-material', 'label': 'Материал', 'type': 'select-one', 'value': str(self.material.pk), 'display': 'Полиол', 'changed': True},
            {'name': 'outputs', 'label': 'Результаты', 'type': 'select-multiple', 'value': ['mass', 'cost'], 'display': ['Масса', 'Стоимость'], 'changed': False},
        ]}], 'truncated': False}

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
        record = AssistantRequestLog.objects.get()
        self.assertEqual(record.status, 'completed')
        self.assertEqual(record.reply, response.json()['reply'])
        self.assertEqual(record.tools_used, response.json()['tools_used'])
        self.assertNotIn('private reasoning', str(record.messages) + record.reply + str(record.progress))
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
        self.assertNotIn('overview', context)
        self.assertNotIn('components', context['current_record'])
        self.assertIn('components', context['current_record']['available_sections'])

    @patch('lab.assistant._cloud_request')
    def test_current_form_snapshot_reaches_provider_without_changing_stored_record(self, cloud):
        cloud.return_value = {'choices': [{'message': {'content': 'В форме указано нулевое количество.'}}]}
        self.sign_in()
        draft = self.draft()
        # Additional client properties cannot become provider instructions/context.
        draft['instructions'] = 'unwanted top-level value'
        draft['forms'][0]['instructions'] = 'unwanted form value'
        draft['forms'][0]['fields'][0]['instructions'] = 'unwanted field value'
        response = self.post(page=f'/recipes/{self.recipe.pk}/', form_state=draft)
        self.assertEqual(response.status_code, 200, response.content)
        context = json.loads(cloud.call_args.kwargs['payload']['messages'][1]['content'].split('\n', 1)[1])
        self.assertEqual(context['form_state'], self.draft())
        self.assertEqual(context['current_record']['title'], 'Проверяемый состав')
        self.recipe.refresh_from_db()
        self.assertEqual(self.recipe.title, 'Проверяемый состав')

    @patch('lab.assistant._cloud_request')
    def test_form_snapshot_is_fresh_for_each_question_and_optional_on_other_pages(self, cloud):
        cloud.return_value = {'choices': [{'message': {'content': 'Проверьте черновик перед сохранением.'}}]}
        self.sign_in()
        draft = self.draft()
        self.assertEqual(self.post(page='/recipes/new/', form_state=draft).status_code, 200)
        first = json.loads(cloud.call_args.kwargs['payload']['messages'][1]['content'].split('\n', 1)[1])
        draft['forms'][0]['fields'][1]['value'] = '25'
        draft['truncated'] = True
        self.assertEqual(self.post(page='/recipes/new/', form_state=draft).status_code, 200)
        current = json.loads(cloud.call_args.kwargs['payload']['messages'][1]['content'].split('\n', 1)[1])
        self.assertEqual(first['form_state']['forms'][0]['fields'][1]['value'], '0')
        self.assertEqual(current['form_state']['forms'][0]['fields'][1]['value'], '25')
        self.assertTrue(current['form_state']['truncated'])
        for extra in ({}, {'form_state': None}):
            self.assertEqual(self.post(page='/materials/', **extra).status_code, 200)
            context = json.loads(cloud.call_args.kwargs['payload']['messages'][1]['content'].split('\n', 1)[1])
            self.assertNotIn('form_state', context)

    @patch('lab.assistant._cloud_request')
    def test_malformed_sensitive_and_oversized_form_snapshots_fail_before_provider(self, cloud):
        self.sign_in()
        bad_states = [[], 'form', {}, {'title': '', 'forms': [], 'truncated': 'false'}]
        for key, value in (('title', 'x' * 201), ('forms', [{'method': 'post', 'fields': []}] * 4)):
            state = self.draft()
            state[key] = value
            bad_states.append(state)
        for key, value in (('name', ''), ('name', 'x' * 121), ('label', 'x' * 161),
                           ('type', 'password'), ('type', 'hidden'), ('type', 'file'),
                           ('name', 'csrfmiddlewaretoken'), ('name', 'access_token'),
                           ('name', 'apiKey'), ('changed', 1), ('value', 0),
                           ('value', {'nested': 'value'}), ('value', 'x' * 2001),
                           ('value', ['x'] * 21), ('display', 'x' * 161), ('section', 'x' * 161)):
            state = self.draft()
            state['forms'][0]['fields'][0][key] = value
            bad_states.append(state)
        for fields in ([deepcopy(self.draft()['forms'][0]['fields'][0])] * 201,
                       [{'name': f'field_{index}', 'label': 'Поле', 'type': 'text', 'value': 'x' * 2000, 'changed': True} for index in range(13)]):
            state = self.draft()
            state['forms'][0]['fields'] = fields
            bad_states.append(state)
        for state in bad_states:
            with self.subTest(state=str(state)[:100]):
                response = self.post(form_state=state)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertEqual(response.json()['code'], 'invalid_request')
        cloud.assert_not_called()

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

    @patch('lab.assistant._cloud_stream')
    def test_streaming_tool_fragments_progress_and_citations_without_reasoning(self, cloud):
        payloads = []
        def provider(payload, *, timeout):
            payloads.append(deepcopy(payload))
            def chunk(delta, finish=None):
                return {'choices': [{'delta': delta, 'finish_reason': finish}]}
            if len(payloads) == 1:
                yield chunk({'reasoning_content': 'private thought', 'content': 'Сначала проверю. '})
                yield chunk({'tool_calls': [{'index': 0, 'id': 'read_material', 'function': {'name': 'get_lab_', 'arguments': '{"entity":"mater'}}]})
                yield chunk({'tool_calls': [{'index': 0, 'function': {'name': 'record', 'arguments': f'ials","id":{self.material.pk}' + '}'}}]}, 'tool_calls')
            else:
                yield chunk({'reasoning_content': 'more private thought'})
                yield chunk({'content': '[Полиол]'})
                yield chunk({'content': f'(/materials/{self.material.pk}/): 1.05 г/мл.'})
                yield chunk({}, 'stop')
        cloud.side_effect = provider
        self.sign_in()
        response = self.stream_post(form_state=self.draft())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.streaming)
        self.assertIn('text/event-stream', response['Content-Type'])
        self.assertEqual(response['X-Accel-Buffering'], 'no')
        lock = f'assistant:employee:{self.reader.pk}:busy'
        self.assertIsNotNone(cache.get(lock))
        events = self.stream_events(response)
        self.assertEqual(events[0][0], 'status')
        self.assertIn(('status', {'text': 'Смотрю материалы…'}), events)
        self.assertTrue(any(data.get('reset') for event, data in events if event == 'status'))
        deltas = [data['text'] for event, data in events if event == 'delta']
        self.assertIn('[Полиол]', deltas)
        self.assertEqual(events[-1][0], 'done')
        final = events[-1][1]
        self.assertEqual(final['reply'], f'[Полиол](/materials/{self.material.pk}/): 1.05 г/мл.')
        self.assertIn('get_lab_record', final['tools_used'])
        self.assertIn(f'/materials/{self.material.pk}/', [row['url'] for row in final['sources']])
        self.assertNotIn('private thought', json.dumps(events))
        self.assertTrue(all(payload['stream'] for payload in payloads))
        self.assertEqual(payloads[1]['messages'][-2]['reasoning_content'], 'private thought')
        self.assertIn('form_state', payloads[0]['messages'][1]['content'])
        record = AssistantRequestLog.objects.get()
        self.assertEqual(record.status, 'completed')
        self.assertEqual(record.progress, ['Сначала проверю. '])
        self.assertEqual(record.sources, final['sources'])
        self.assertNotIn('private thought', str(record.messages) + record.reply + str(record.progress))
        self.assertIsNone(cache.get(lock))

    @patch('lab.assistant._cloud_stream')
    def test_stream_failure_is_explicit_and_releases_guard(self, cloud):
        def provider(*args, **kwargs):
            yield {'choices': [{'delta': {'content': 'Частичный ответ'}}]}
            raise assistant.AssistantError('Соединение прервано.', 'incomplete_response')
        cloud.side_effect = provider
        self.sign_in()
        events = self.stream_events(self.stream_post())
        self.assertIn(('delta', {'text': 'Частичный ответ'}), events)
        self.assertEqual(events[-1], ('error', {'error': 'Соединение прервано.', 'code': 'incomplete_response'}))
        self.assertFalse(any(event == 'done' for event, _ in events))
        self.assertIsNone(cache.get(f'assistant:employee:{self.reader.pk}:busy'))

    @patch('lab.assistant._cloud_stream')
    def test_closing_stream_closes_provider_and_releases_guard(self, cloud):
        closed = []
        def provider(*args, **kwargs):
            try:
                yield {'choices': [{'delta': {'content': 'Начало'}}]}
                yield {'choices': [{'delta': {}, 'finish_reason': 'stop'}]}
            finally:
                closed.append(True)
        cloud.side_effect = provider
        self.sign_in()
        response = self.stream_post()
        iterator = iter(response.streaming_content)
        while b'event: delta' not in next(iterator):
            pass
        response.close()
        self.assertEqual(closed, [True])
        self.assertIsNone(cache.get(f'assistant:employee:{self.reader.pk}:busy'))
        # A response abandoned before its first event must release its guard too.
        unopened = self.stream_post()
        unopened.close()
        self.assertIsNone(cache.get(f'assistant:employee:{self.reader.pk}:busy'))

    @patch('lab.assistant._cloud_stream')
    def test_streaming_validation_and_missing_configuration_never_contact_provider(self, cloud):
        self.sign_in()
        invalid = self.stream_post(messages=[])
        self.assertEqual(invalid.status_code, 400)
        self.assertFalse(invalid.streaming)
        with override_settings(CLOUD_RU_API_KEY=''):
            events = self.stream_events(self.stream_post())
        self.assertEqual(events[-1][0], 'error')
        self.assertEqual(events[-1][1]['code'], 'not_configured')
        cloud.assert_not_called()


@override_settings(CLOUD_RU_API_KEY='transport-secret', CLOUD_RU_MODEL='deepseek-ai/DeepSeek-V4-Pro',
                   CLOUD_RU_BASE_URL='https://foundation-models.api.cloud.ru/v1')
class CloudTransportTests(SimpleTestCase):
    def test_activity_names_the_actual_search_with_bounded_text(self):
        self.assertEqual(assistant._tool_activity('search_lab_data', {'entity': 'tests', 'q': '  зимняя\n пена '}),
                         'Ищу эксперименты: «зимняя пена»…')
        self.assertEqual(assistant._tool_activity('search_lab_data', {'entity': 'results'}),
                         'Изучаю результаты и статистику…')
        self.assertLess(len(assistant._tool_activity('search_lab_data', {'entity': 'tests', 'q': 'x' * 5000})), 120)

    @patch('lab.assistant.build_opener')
    def test_sse_transport_yields_before_provider_finishes_and_closes(self, opener):
        chunks = [
            {'choices': [{'delta': {'content': 'Привет'}}]},
            {'choices': [{'delta': {'content': '!'}, 'finish_reason': 'stop'}]},
        ]
        wire = ': keepalive\r\n\r\n' + ''.join('data: ' + json.dumps(chunk, ensure_ascii=False) + '\r\n\r\n' for chunk in chunks) + 'data: [DONE]\r\n\r\n'
        body = BytesIO(wire.encode())
        response = opener.return_value.open.return_value
        response.__enter__.return_value = body
        stream = assistant._cloud_stream({'stream': True}, timeout=20)
        self.assertEqual(next(stream), {'choices': []})
        self.assertEqual(next(stream), chunks[0])
        self.assertLess(body.tell(), len(wire.encode()))
        request = opener.return_value.open.call_args.args[0]
        self.assertEqual(request.get_header('Accept'), 'text/event-stream')
        self.assertEqual(request.get_header('Authorization'), 'Bearer transport-secret')
        stream.close()
        response.__exit__.assert_called_once()

    @patch('lab.assistant.build_opener')
    def test_sse_transport_rejects_missing_terminal_invalid_and_oversized_frames(self, opener):
        for wire, code in ((b'data: {"choices":[]}\n\n', 'incomplete_response'),
                           (b'data: broken\n\n', 'invalid_response'),
                           (b'data: {"error":"transport-secret"}\n\n', 'invalid_response'),
                           (b'data: []\n\n', 'invalid_response'),
                           (b'data: ' + b'x' * assistant.MAX_RESPONSE_BYTES, 'invalid_response')):
            with self.subTest(code=code, size=len(wire)):
                opener.return_value.open.return_value.__enter__.return_value = BytesIO(wire)
                with self.assertRaises(assistant.AssistantError) as caught:
                    list(assistant._cloud_stream({'stream': True}, timeout=20))
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn('transport-secret', str(caught.exception))

    @patch('lab.assistant.build_opener')
    def test_sse_transport_sanitizes_auth_timeout_and_network_errors(self, opener):
        for failure, code in ((HTTPError('https://foundation-models.api.cloud.ru/v1/chat/completions', 401, 'transport-secret', {}, BytesIO(b'private prompt')), 'provider_auth'),
                              (TimeoutError(), 'timeout'), (URLError('transport-secret'), 'provider_unavailable')):
            opener.return_value.open.side_effect = failure
            with self.assertRaises(assistant.AssistantError) as caught:
                list(assistant._cloud_stream({'stream': True}, timeout=20))
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn('transport-secret', str(caught.exception))

    @patch('lab.assistant._cloud_stream')
    def test_hidden_reasoning_emits_only_heartbeat_and_closes_on_disconnect(self, cloud):
        closed = []
        def provider(*args, **kwargs):
            try:
                yield {'choices': [{'delta': {'reasoning_content': 'never reveal these thoughts'}}]}
                yield {'choices': []}
            finally:
                closed.append(True)
        cloud.side_effect = provider
        with patch('lab.assistant.time.monotonic', side_effect=[0, 2]):
            events = assistant._stream_completion({'stream': True}, timeout=20)
            self.assertEqual(next(events), ('ping', {}))
            events.close()
        self.assertEqual(closed, [True])

    @patch('lab.assistant._cloud_stream')
    def test_stream_completion_requires_finish_and_valid_tool_fragment(self, cloud):
        for chunks in ([{'choices': [{'delta': {'content': 'unfinished'}}]}],
                       [{'choices': [{'delta': {'tool_calls': [{'index': 8}]}, 'finish_reason': 'tool_calls'}]}],
                       [{'choices': [{'delta': {'tool_calls': [{'index': 0, 'function': 'invalid'}]}}]}]):
            cloud.return_value = (chunk for chunk in chunks)
            with self.assertRaises(assistant.AssistantError):
                list(assistant._stream_completion({'stream': True}, timeout=20))

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
