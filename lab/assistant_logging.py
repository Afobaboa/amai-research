"""Persist public chat exchanges, without provider payloads or hidden reasoning."""
import logging
import time

from django.db import DatabaseError
from django.utils import timezone

from .assistant import MAX_MESSAGE_CHARS, configuration_status
from .assistant_usage import UsageJournal
from .models import AssistantRequestLog

logger = logging.getLogger(__name__)


class RequestJournal:
    def __init__(self, employee, messages, page):
        self.usage = UsageJournal()
        self.started = time.monotonic()
        self.finished = False
        self.reply = ''
        self.progress = []
        self.record = AssistantRequestLog.objects.create(
            employee=employee, employee_name=employee.full_name,
            employee_role=employee.role,
            messages=messages, question=messages[-1]['content'], page=page[:2048],
            model=configuration_status()['model'][:255],
        )

    def observe(self, event, data):
        if event == 'delta':
            self.reply = (self.reply + data['text'])[:MAX_MESSAGE_CHARS]
        elif event == 'status' and data.get('reset') and self.reply:
            self.progress = (self.progress + [self.reply])[-8:]
            self.reply = ''
        elif event == 'done':
            self.complete(data)

    def metrics(self, event):
        self.usage.observe(event)
        try:
            AssistantRequestLog.objects.filter(
                pk=self.record.pk, status=AssistantRequestLog.Status.PENDING,
            ).update(**self.usage.fields())
        except DatabaseError:
            logger.error('Could not update assistant usage log.')

    def complete(self, data):
        self.reply = data['reply']
        self.finish(AssistantRequestLog.Status.COMPLETED,
                    sources=data.get('sources', []), tools_used=data.get('tools_used', []),
                    model=data.get('model', self.record.model)[:255])

    def fail(self, code):
        self.finish(AssistantRequestLog.Status.FAILED, error_code=code[:80])

    def interrupt(self):
        self.finish(AssistantRequestLog.Status.INTERRUPTED, error_code='stream_closed')

    def finish(self, status, **fields):
        if self.finished:
            return
        try:
            AssistantRequestLog.objects.filter(
                pk=self.record.pk, status=AssistantRequestLog.Status.PENDING,
            ).update(
                status=status, finished_at=timezone.now(),
                duration_ms=max(0, round(
                    (time.monotonic() - self.started) * 1000,
                )),
                reply=self.reply, progress=self.progress,
                **self.usage.fields(final_status=status), **fields,
            )
            self.finished = True
        except DatabaseError:
            # Keep the pending row as evidence; never expose or log DB/provider details.
            logger.error('Could not finalize assistant request log.')
