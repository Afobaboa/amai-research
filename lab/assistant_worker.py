"""At-most-once provider execution of durable jobs, independent of browser connections."""
import time

from django.db import transaction
from django.utils import timezone

from .assistant import AssistantError, answer_stream
from .assistant_logging import RequestJournal
from .models import AssistantConversation, AssistantJob, AssistantRequestLog


def recover_stale():
    # Never automatically repeat a paid request after a process crash.
    stale = AssistantJob.objects.filter(status='running',
        started_at__lt=timezone.now() - timezone.timedelta(minutes=10))
    for pk in stale.values_list('pk', flat=True):
        with transaction.atomic():
            job = AssistantJob.objects.select_for_update().get(pk=pk)
            if job.status != 'running':
                continue
            AssistantRequestLog.objects.filter(pk=job.journal_id, status='pending').update(
                status='interrupted', error_code='worker_interrupted', finished_at=timezone.now(),
                reply=job.reply, progress=job.progress)
            job.status, job.form_state, job.finished_at = 'failed', None, timezone.now()
            job.save(update_fields=['status', 'form_state', 'finished_at'])


def run_next():
    pk = AssistantJob.objects.filter(status='queued').order_by('created_at', 'pk').values_list('pk', flat=True).first()
    if pk is None:
        return False
    # Conditional update claims the task across multiple worker processes.
    if not AssistantJob.objects.filter(pk=pk, status='queued').update(status='running', started_at=timezone.now()):
        return True
    job = AssistantJob.objects.select_related('journal', 'conversation__employee').get(pk=pk)
    journal = RequestJournal(job.conversation.employee, job.journal.messages,
                             job.journal.page, record=job.journal)
    events = None
    last_save = 0
    try:
        events = answer_stream(job.journal.messages, page=job.journal.page,
            role=job.journal.employee_role, form_state=job.form_state, on_metrics=journal.metrics)
        for event, data in events:
            if not AssistantJob.objects.filter(pk=pk, status='running').exists():
                return True
            if event == 'done':
                with transaction.atomic():
                    current = AssistantJob.objects.select_for_update().get(pk=pk)
                    if current.status != 'running':
                        return True
                    journal.observe(event, data)
                    if not journal.finished:
                        raise RuntimeError('Journal was not saved')
                    AssistantJob.objects.filter(pk=pk).update(status='completed',
                        reply=journal.reply, progress=journal.progress, activity='Ответ готов',
                        form_state=None, finished_at=timezone.now())
                    AssistantConversation.objects.filter(pk=job.conversation_id).update(updated_at=timezone.now())
                return True
            journal.observe(event, data)
            if event == 'status':
                job.activity = str(data.get('text', 'Думаю…'))[:240]
            elif event == 'delta':
                job.activity = 'Пишу ответ…'
            now = time.monotonic()
            if now - last_save >= 0.4 or event == 'status':
                AssistantJob.objects.filter(pk=pk, status='running').update(
                    reply=journal.reply, progress=journal.progress, activity=job.activity)
                last_save = now
        raise AssistantError('Поток завершился без ответа.', 'incomplete', 502)
    except Exception as error:
        with transaction.atomic():
            current = AssistantJob.objects.select_for_update().get(pk=pk)
            if current.status == 'running':
                journal.fail(error.code if isinstance(error, AssistantError) else 'internal_error')
                AssistantJob.objects.filter(pk=pk).update(status='failed', reply=journal.reply,
                    progress=journal.progress, form_state=None, finished_at=timezone.now())
    finally:
        if events is not None:
            events.close()
    return True
