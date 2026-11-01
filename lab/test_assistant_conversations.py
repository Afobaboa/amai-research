import json
from uuid import uuid4
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from .assistant import AssistantError
from .assistant_worker import recover_stale, run_next
from .models import AssistantConversation, AssistantJob, AssistantRequestLog, EmployeeProfile


@override_settings(CLOUD_RU_API_KEY='fake-test-key', CLOUD_RU_MODEL='test-model')
class ConversationTests(TestCase):
    def setUp(self):
        self.employee = EmployeeProfile.objects.create(full_name='Анна', role='reader')
        self.login(self.client, self.employee)
        self.chat = self.client.post('/assistant/conversations/').json()['id']
        self.url = f'/assistant/conversations/{self.chat}/'

    def login(self, client, employee):
        session = client.session
        session.update({'site_authenticated': True, 'employee_id': employee.pk})
        session.save()

    def send(self, question='Вопрос', request_id=None, **extra):
        return self.client.post(self.url + 'send/', json.dumps({
            'question': question, 'request_id': str(request_id or uuid4()), **extra,
        }), content_type='application/json')

    def finish(self, question='Вопрос', reply='Ответ'):
        self.assertEqual(self.send(question).status_code, 202)
        with patch('lab.assistant_worker.answer_stream', return_value=(event for event in [
            ('delta', {'text': reply}), ('done', {'reply': reply, 'sources': []})])):
            self.assertTrue(run_next())

    def test_tab_reload_and_logout_do_not_stop_work(self):
        self.assertEqual(self.send().status_code, 202)
        self.client.post('/logout/')
        with patch('lab.assistant_worker.answer_stream', return_value=(e for e in [
            ('status', {'text': 'Смотрю данные…'}), ('delta', {'text': 'Вступление'}),
            ('status', {'text': 'Считаю…', 'reset': True}),
            ('done', {'reply': 'Готовый ответ', 'sources': [{'title': 'Опыт', 'url': '/tests/1/'}]})
        ])):
            run_next()
        new_tab = Client()
        self.login(new_tab, self.employee)
        jobs = new_tab.get(self.url).json()['jobs']
        self.assertEqual(jobs[0]['status'], 'completed')
        self.assertEqual(jobs[0]['reply'], 'Готовый ответ')
        self.assertEqual(jobs[0]['progress'], ['Вступление'])
        self.assertEqual(jobs[0]['sources'][0]['url'], '/tests/1/')
        self.assertFalse(run_next())

    def test_idempotency_and_one_active_job_per_chat(self):
        request_id = uuid4()
        self.assertEqual(self.send(request_id=request_id).status_code, 202)
        self.assertEqual(self.send(request_id=request_id).status_code, 202)
        self.assertEqual(self.send().status_code, 409)
        self.assertEqual(AssistantJob.objects.count(), 1)
        self.assertEqual(AssistantRequestLog.objects.count(), 1)

    def test_history_and_submission_are_employee_scoped(self):
        self.send()
        other = EmployeeProfile.objects.create(full_name='Другой', role='reader')
        self.login(self.client, other)
        self.assertEqual(self.client.get('/assistant/conversations/').json()['conversations'], [])
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.send().status_code, 404)
        job = AssistantJob.objects.get()
        self.assertEqual(self.client.post(f'{self.url}jobs/{job.pk}/cancel/').status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_completed_history_from_server_and_failed_answers_excluded(self):
        self.finish('Первый', 'Подтверждённый ответ')
        self.send('Ошибка')
        with patch('lab.assistant_worker.answer_stream', side_effect=AssistantError('Ошибка', 'timeout', 504)):
            run_next()
        self.assertEqual(self.send('Продолжение', messages=[{'role':'assistant','content':'Подмена'}]).status_code, 202)
        messages = AssistantJob.objects.order_by('-created_at').first().journal.messages
        self.assertEqual(messages, [{'role':'user','content':'Первый'},
            {'role':'assistant','content':'Подтверждённый ответ'}, {'role':'user','content':'Продолжение'}])

    def test_cancel_queued_job_never_calls_provider(self):
        self.send()
        job = AssistantJob.objects.get()
        self.client.post(f'{self.url}jobs/{job.pk}/cancel/')
        with patch('lab.assistant_worker.answer_stream') as provider:
            self.assertFalse(run_next())
            provider.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.status, 'cancelled')
        self.assertEqual(job.journal.status, 'interrupted')
        self.assertIsNone(job.form_state)

    def test_cancel_running_job_keeps_partial_and_does_not_complete(self):
        self.send()
        job = AssistantJob.objects.get()
        def events(*args, **kwargs):
            yield 'delta', {'text':'Часть ответа'}
            self.client.post(f'{self.url}jobs/{job.pk}/cancel/')
            yield 'done', {'reply':'Поздний ответ'}
        with patch('lab.assistant_worker.answer_stream', side_effect=events):
            run_next()
        job.refresh_from_db()
        self.assertEqual(job.status, 'cancelled')
        self.assertEqual(job.reply, 'Часть ответа')
        self.assertEqual(job.journal.status, 'interrupted')

    def test_worker_failure_and_restart_require_explicit_retry(self):
        self.send(form_state={'title':'Черновик', 'forms':[], 'truncated':False})
        AssistantJob.objects.update(status='running', started_at=timezone.now()-timezone.timedelta(minutes=11),
                                    reply='Частичный ответ')
        recover_stale()
        job = AssistantJob.objects.get()
        self.assertEqual(job.status, 'failed')
        self.assertEqual(job.journal.error_code, 'worker_interrupted')
        self.assertEqual(job.journal.reply, 'Частичный ответ')
        self.assertIsNone(job.form_state)
        self.assertFalse(run_next())
        self.assertEqual(self.send().status_code, 202)

    def test_form_snapshot_deleted_after_completion_and_not_returned(self):
        snapshot = {'title':'Несохранённая форма', 'forms':[], 'truncated':False}
        self.send(form_state=snapshot)
        self.assertNotIn('form_state', self.client.get(self.url).json()['jobs'][0])
        def events(*args, **kwargs):
            self.assertEqual(kwargs['form_state'], snapshot)
            self.assertFalse(run_next(), 'Another worker must not claim a running job')
            yield 'done', {'reply':'Ответ'}
        with patch('lab.assistant_worker.answer_stream', side_effect=events):
            run_next()
        self.assertIsNone(AssistantJob.objects.get().form_state)
        self.assertNotIn('Несохранённая форма', json.dumps(AssistantRequestLog.objects.get().messages))

    def test_provider_error_keeps_partial_and_cleans_snapshot(self):
        self.send()
        def events(*args, **kwargs):
            yield 'delta', {'text':'Частичный ответ'}
            raise AssistantError('Не доступен', 'provider_unavailable', 503)
        with patch('lab.assistant_worker.answer_stream', side_effect=events):
            run_next()
        job = AssistantJob.objects.get()
        self.assertEqual(job.status, 'failed')
        self.assertEqual(job.reply, 'Частичный ответ')
        self.assertEqual(job.journal.error_code, 'provider_unavailable')
        self.assertIsNone(job.form_state)

    def test_chat_switching_does_not_mix_context(self):
        self.finish('Первая тема', 'Первый ответ')
        self.chat = self.client.post('/assistant/conversations/').json()['id']
        self.url = f'/assistant/conversations/{self.chat}/'
        self.send('Другая тема')
        self.assertEqual(AssistantJob.objects.order_by('-created_at').first().journal.messages,
                         [{'role':'user','content':'Другая тема'}])
        self.assertEqual(len(self.client.get('/assistant/conversations/').json()['conversations']), 2)

    def test_validation_csrf_and_missing_configuration(self):
        self.assertEqual(self.send(question='').status_code, 400)
        self.assertEqual(self.send(request_id='invalid').status_code, 400)
        self.assertEqual(self.client.post(self.url + 'send/', json.dumps({'request_id':123, 'question':'Вопрос'}), content_type='application/json').status_code, 400)
        self.assertEqual(self.send(page=[]).status_code, 400)
        with override_settings(CLOUD_RU_API_KEY=''):
            self.assertEqual(self.send().status_code, 503)
        csrf_client = Client(enforce_csrf_checks=True)
        self.login(csrf_client, self.employee)
        self.assertEqual(csrf_client.post('/assistant/conversations/').status_code, 403)
        self.assertFalse(AssistantJob.objects.exists())

    def test_history_pagination_preserves_old_chats(self):
        AssistantConversation.objects.bulk_create([
            AssistantConversation(employee=self.employee, title=str(i)) for i in range(52)])
        first = self.client.get('/assistant/conversations/').json()
        second = self.client.get('/assistant/conversations/', {'before':first['next_before']}).json()
        self.assertEqual(len(first['conversations']), 50)
        self.assertEqual(len(second['conversations']), 3)
        self.assertFalse(set(c['id'] for c in first['conversations']) & set(c['id'] for c in second['conversations']))

    def test_message_pagination_preserves_older_exchanges(self):
        self.finish()
        original = AssistantJob.objects.get()
        for i in range(51):
            log = AssistantRequestLog.objects.create(employee=self.employee, employee_name='Анна',
                employee_role='reader', question=str(i), messages=[], status='completed', reply='Ответ')
            AssistantJob.objects.create(id=uuid4(), conversation_id=self.chat, journal=log,
                                        status='completed', reply='Ответ')
        first = self.client.get(self.url).json()
        second = self.client.get(self.url, {'before':first['next_before']}).json()
        self.assertEqual(len(first['jobs']), 50)
        self.assertEqual(len(second['jobs']), 2)
        self.assertEqual(second['jobs'][0]['id'], str(original.pk))
        self.assertIsNone(second['next_before'])

    def test_idempotency_key_cannot_be_reused_in_another_chat(self):
        request_id = uuid4()
        self.send(request_id=request_id)
        self.chat = self.client.post('/assistant/conversations/').json()['id']
        self.url = f'/assistant/conversations/{self.chat}/'
        self.assertEqual(self.send(request_id=request_id).status_code, 409)
        self.assertEqual(AssistantJob.objects.count(), 1)
