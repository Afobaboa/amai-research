"""Chat journals survive provider errors, stream closure, and UI session cleanup."""
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from .assistant import AssistantError
from .models import AssistantRequestLog, EmployeeProfile


@override_settings(CLOUD_RU_API_KEY='secret-not-for-logs', CLOUD_RU_MODEL='test-model',
                   CLOUD_RU_BASE_URL='https://foundation-models.api.cloud.ru/v1')
class AssistantLoggingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.employee = EmployeeProfile.objects.create(full_name='Тестовый сотрудник', role='reader')
        session = self.client.session
        session.update({'site_authenticated': True, 'employee_id': self.employee.pk})
        session.save()
        self.messages = [
            {'role': 'user', 'content': 'Первый вопрос'},
            {'role': 'assistant', 'content': 'Предыдущий ответ'},
            {'role': 'user', 'content': 'Следующий вопрос', 'reasoning_content': 'untrusted extra'},
        ]
        self.result = {'reply': 'Итоговый ответ', 'model': 'test-model',
                       'sources': [{'title': 'Материал', 'url': '/materials/1/'}],
                       'tools_used': ['get_lab_record']}

    def post(self, *, stream=False, **extra):
        return self.client.post(
            '/assistant/chat/', json.dumps({'messages': self.messages, 'page': '/materials/', **extra}),
            content_type='application/json', HTTP_ACCEPT='text/event-stream' if stream else 'application/json',
        )

    def assert_finished(self, status):
        record = AssistantRequestLog.objects.get()
        self.assertEqual(record.status, status)
        self.assertIsNotNone(record.finished_at)
        self.assertGreaterEqual(record.finished_at, record.created_at)
        self.assertGreaterEqual(record.duration_ms, 0)
        self.assertIsNone(cache.get(f'assistant:employee:{self.employee.pk}:busy'))
        return record

    @patch('lab.assistant_views.answer')
    def test_json_exchange_persists_without_form_snapshots_or_extra_fields(self, answer):
        answer.return_value = self.result
        response = self.post(form_state={'title': 'Unsaved private form', 'forms': [], 'truncated': False})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), self.result)
        record = self.assert_finished('completed')
        self.assertEqual(record.employee, self.employee)
        self.assertEqual(record.employee_name, self.employee.full_name)
        self.assertEqual(record.employee_role, 'reader')
        self.assertEqual(record.page, '/materials/')
        self.assertEqual(record.question, 'Следующий вопрос')
        self.assertEqual(record.messages, [{k: m[k] for k in ('role', 'content')} for m in self.messages])
        for field in ('reply', 'sources', 'tools_used', 'model'):
            self.assertEqual(getattr(record, field), self.result[field])
        serialized = json.dumps(record.messages) + json.dumps(record.progress)
        for excluded in ('untrusted extra', 'Unsaved private form', 'secret-not-for-logs'):
            self.assertNotIn(excluded, serialized)
        self.client.post('/logout/')
        self.assertTrue(AssistantRequestLog.objects.filter(pk=record.pk).exists())
        self.employee.delete()
        record.refresh_from_db()
        self.assertIsNone(record.employee)
        self.assertEqual(record.employee_name, 'Тестовый сотрудник')

    @override_settings(CLOUD_RU_API_KEY='')
    def test_configuration_failure_is_recorded(self):
        self.assertEqual(self.post().status_code, 503)
        record = self.assert_finished('failed')
        self.assertEqual(record.error_code, 'not_configured')
        self.assertEqual(record.reply, '')

    @patch('lab.assistant_views.answer')
    def test_unexpected_exception_records_only_generic_code(self, answer):
        answer.side_effect = RuntimeError('provider credentials or private data')
        with self.assertLogs('lab.assistant_views', level='ERROR') as logs:
            response = self.post()
        self.assertEqual(response.status_code, 500)
        record = self.assert_finished('failed')
        self.assertEqual(record.error_code, 'internal_error')
        self.assertNotIn('provider credentials', json.dumps(record.messages) + record.reply + str(logs.output))

    @patch('lab.assistant_views.answer_stream')
    def test_stream_success_keeps_public_preamble_and_final_metadata(self, answer_stream):
        def events():
            yield 'delta', {'text': 'Проверяю данные.'}
            yield 'status', {'text': 'Ищу', 'reset': True}
            yield 'delta', {'text': 'Итоговый '}
            yield 'delta', {'text': 'ответ'}
            yield 'done', self.result
        answer_stream.return_value = events()
        response = self.post(stream=True)
        self.assertEqual(AssistantRequestLog.objects.get().status, 'pending')
        self.assertIn(b'event: done', b''.join(response.streaming_content))
        response.close()
        record = self.assert_finished('completed')
        self.assertEqual(record.reply, self.result['reply'])
        self.assertEqual(record.progress, ['Проверяю данные.'])
        self.assertEqual(record.sources, self.result['sources'])
        self.assertEqual(record.tools_used, self.result['tools_used'])

    @patch('lab.assistant_views.answer_stream')
    def test_stream_failure_preserves_partial_answer(self, answer_stream):
        def events():
            yield 'delta', {'text': 'Частичный ответ'}
            raise AssistantError('Ответ прервался.', 'incomplete_response')
        answer_stream.return_value = events()
        response = self.post(stream=True)
        self.assertIn(b'event: error', b''.join(response.streaming_content))
        record = self.assert_finished('failed')
        self.assertEqual(record.error_code, 'incomplete_response')
        self.assertEqual(record.reply, 'Частичный ответ')

    @patch('lab.assistant_views.answer_stream')
    def test_disconnected_and_never_started_streams_are_interrupted(self, answer_stream):
        for started in (False, True):
            with self.subTest(started=started):
                AssistantRequestLog.objects.all().delete()
                closed = []
                def events():
                    try:
                        yield 'delta', {'text': 'Начало ответа'}
                        yield 'done', self.result
                    finally:
                        closed.append(True)
                answer_stream.return_value = events()
                response = self.post(stream=True)
                if started:
                    next(iter(response.streaming_content))
                response.close()
                record = self.assert_finished('interrupted')
                self.assertEqual(record.error_code, 'stream_closed')
                self.assertEqual(record.reply, 'Начало ответа' if started else '')
                self.assertEqual(closed, [True] if started else [])

    @patch('lab.assistant_views.answer')
    def test_rejected_requests_do_not_create_journals(self, answer):
        self.assertEqual(self.post(messages=[]).status_code, 400)
        cache.set(f'assistant:employee:{self.employee.pk}:busy', True)
        self.assertEqual(self.post().status_code, 429)
        session = self.client.session
        session.flush()
        self.assertEqual(self.post().status_code, 401)
        self.assertFalse(AssistantRequestLog.objects.exists())
        answer.assert_not_called()

    @patch('lab.assistant_views.answer')
    def test_admin_can_view_journal_but_cannot_edit_or_delete(self, answer):
        answer.return_value = self.result
        self.post()
        record = AssistantRequestLog.objects.get()
        detail = reverse('admin:lab_assistantrequestlog_change', args=[record.pk])
        self.assertEqual(self.client.get(detail).status_code, 302)
        user = get_user_model().objects.create_superuser(username='log-admin', password='test-password')
        self.client.force_login(user)
        response = self.client.get(reverse('admin:lab_assistantrequestlog_changelist'))
        self.assertContains(response, 'Следующий вопрос')
        self.assertContains(self.client.get(detail), 'Итоговый ответ')
        self.assertEqual(self.client.post(detail, {'reply': 'Изменено'}).status_code, 403)
        self.assertEqual(self.client.post(reverse('admin:lab_assistantrequestlog_delete', args=[record.pk])).status_code, 403)
        record.refresh_from_db()
        self.assertEqual(record.reply, 'Итоговый ответ')
