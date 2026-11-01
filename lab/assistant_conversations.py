"""Durable conversations and a DB queue; HTTP requests never run the model."""
import json
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .assistant import AssistantError, configuration_status, validate_form_state, validate_messages
from .assistant_logging import RequestJournal
from .assistant_views import MAX_BODY_BYTES, assistant_required
from .models import AssistantConversation, AssistantJob


def payload(request):
    if request.content_type != 'application/json':
        raise AssistantError('Используйте JSON.', 'invalid_request', 415)
    if int(request.META.get('CONTENT_LENGTH') or 0) > MAX_BODY_BYTES or len(request.body) > MAX_BODY_BYTES:
        raise AssistantError('Сообщение слишком большое.', 'invalid_request', 413)
    data = json.loads(request.body)
    if not isinstance(data, dict):
        raise ValueError
    return data


def summary(chat):
    active = getattr(chat, 'has_active_job', None)
    if active is None:
        active = chat.jobs.filter(status__in=['queued', 'running']).exists()
    return {'id': chat.pk, 'title': chat.title, 'updated_at': chat.updated_at.isoformat(),
            'active': active}


@assistant_required
@require_http_methods(['GET', 'POST'])
def conversations(request):
    if request.method == 'POST':
        chat = AssistantConversation.objects.create(employee=request.employee)
        return JsonResponse(summary(chat), status=201)
    try:
        before = int(request.GET.get('before', 0))
    except ValueError:
        return JsonResponse({'error': 'Некорректная страница.'}, status=400)
    chats = AssistantConversation.objects.filter(employee=request.employee).annotate(
        has_active_job=Exists(AssistantJob.objects.filter(
            conversation_id=OuterRef('pk'), status__in=['queued', 'running'])),
    ).order_by('-pk')
    if before:
        chats = chats.filter(pk__lt=before)
    rows = list(chats[:51])
    return JsonResponse({'conversations': [summary(chat) for chat in rows[:50]],
                         'next_before': rows[49].pk if len(rows) > 50 else None})


@assistant_required
@require_GET
def conversation(request, pk):
    chat = get_object_or_404(AssistantConversation, pk=pk, employee=request.employee)
    jobs_query = chat.jobs.select_related('journal')
    before = request.GET.get('before')
    if before:
        try:
            cursor = get_object_or_404(chat.jobs, pk=UUID(before))
        except (ValueError, TypeError):
            return JsonResponse({'error': 'Некорректная страница.'}, status=400)
        jobs_query = jobs_query.filter(Q(created_at__lt=cursor.created_at) |
            Q(created_at=cursor.created_at, pk__lt=cursor.pk))
    rows = list(jobs_query.order_by('-created_at', '-pk')[:51])
    jobs = rows[:50][::-1]
    return JsonResponse({**summary(chat), 'jobs': [{
        'id': str(job.pk), 'status': job.status, 'question': job.journal.question,
        'reply': job.reply, 'progress': job.progress, 'activity': job.activity,
        'sources': job.journal.sources, 'error_code': job.journal.error_code,
    } for job in jobs], 'next_before': str(jobs[0].pk) if len(rows) > 50 else None})


@assistant_required
@require_POST
def submit(request, pk):
    chat = get_object_or_404(AssistantConversation, pk=pk, employee=request.employee)
    try:
        data = payload(request)
        if not isinstance(data.get('request_id'), str):
            raise ValueError
        job_id = UUID(data['request_id'])
        # Check ownership before acknowledging even an idempotent repeat.
        existing = AssistantJob.objects.filter(pk=job_id).first()
        if existing:
            if existing.conversation_id != chat.pk:
                raise AssistantError('Идентификатор запроса занят.', 'conflict', 409)
            return JsonResponse({'id': str(existing.pk)}, status=202)
        question = validate_messages([{'role': 'user', 'content': data.get('question')}])[0]
        page = data.get('page', '/')
        if not isinstance(page, str) or len(page) > 2048:
            raise ValueError
        form_state = validate_form_state(data.get('form_state'))
        if not configuration_status()['configured']:
            raise AssistantError('Подключение к Cloud.ru не настроено.', 'not_configured', 503)
        with transaction.atomic():
            # Serialize submissions by an employee, including across web processes.
            type(request.employee).objects.select_for_update().get(pk=request.employee.pk)
            if AssistantJob.objects.filter(conversation__employee=request.employee,
                                           status__in=['queued', 'running']).count() >= 3:
                raise AssistantError('Дождитесь завершения предыдущих запросов.', 'busy', 429)
            if chat.jobs.filter(status__in=['queued', 'running']).exists():
                raise AssistantError('В этом диалоге уже готовится ответ.', 'busy', 409)
            if AssistantJob.objects.filter(conversation__employee=request.employee,
                    created_at__gte=timezone.now() - timezone.timedelta(minutes=1)).count() >= 20:
                raise AssistantError('Повторите через минуту.', 'rate_limit', 429)
            messages = []
            completed = list(chat.jobs.filter(status='completed').select_related('journal')
                             .order_by('-created_at', '-pk')[:7])[::-1]
            for job in completed:
                messages.extend([{'role': 'user', 'content': job.journal.question},
                                 {'role': 'assistant', 'content': job.reply}])
            messages.append(question)
            while sum(len(m['content']) for m in messages) > 48000:
                messages = messages[2:]
            journal = RequestJournal(request.employee, validate_messages(messages), page)
            AssistantJob.objects.create(id=job_id, conversation=chat, journal=journal.record,
                                        form_state=form_state)
            AssistantConversation.objects.filter(pk=chat.pk).update(
                title=question['content'][:100] if not chat.jobs.exclude(pk=job_id).exists() else chat.title,
                updated_at=timezone.now())
        return JsonResponse({'id': str(job_id)}, status=202)
    except AssistantError as error:
        return JsonResponse({'error': str(error), 'code': error.code}, status=error.status)
    except (ValueError, TypeError, KeyError):
        return JsonResponse({'error': 'Некорректное сообщение.'}, status=400)
    except IntegrityError:
        # Concurrent retries either find the same durable task or lose the active-chat constraint.
        if AssistantJob.objects.filter(pk=job_id, conversation=chat).exists():
            return JsonResponse({'id': str(job_id)}, status=202)
        return JsonResponse({'error': 'В этом диалоге уже готовится ответ.'}, status=409)


@assistant_required
@require_POST
def cancel(request, pk, job_id):
    chat = get_object_or_404(AssistantConversation, pk=pk, employee=request.employee)
    with transaction.atomic():
        job = get_object_or_404(chat.jobs.select_for_update(), pk=job_id)
        if job.status in ('queued', 'running'):
            job.status = 'cancelled'
            job.form_state = None
            job.finished_at = timezone.now()
            job.save(update_fields=['status', 'form_state', 'finished_at'])
            type(job.journal).objects.filter(pk=job.journal_id, status='pending').update(
                status='interrupted', error_code='cancelled', reply=job.reply,
                progress=job.progress, finished_at=job.finished_at)
    return JsonResponse({'status': job.status})
