"""Authenticated JSON/SSE endpoints; provider credentials stay on the server."""
import json
import logging
import time
from functools import wraps
from uuid import uuid4

from django.core.cache import cache
from django.http import JsonResponse, RawPostDataException, StreamingHttpResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from .assistant import AssistantError, answer, answer_stream, configuration_status, validate_form_state, validate_messages
from .assistant_logging import RequestJournal
from .models import EmployeeProfile

logger = logging.getLogger(__name__)
MAX_BODY_BYTES = 256000


class _EventStream:
    """Release the request guard even if the response is closed before iteration."""
    def __init__(self, events, release, journal):
        self.release = release
        self.journal = journal
        self.events = self.frames(events)

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.events)

    def frames(self, events):
        def frame(event, data):
            return f'event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'
        try:
            for event, data in events:
                self.journal.observe(event, data)
                yield frame(event, data)
        except AssistantError as error:
            self.journal.fail(error.code)
            yield frame('error', {'error': str(error), 'code': error.code})
        except Exception:
            self.journal.fail('internal_error')
            logger.error('Assistant stream failed with an internal error.')
            yield frame('error', {'error': 'Не удалось подготовить ответ. Повторите запрос позже.', 'code': 'internal_error'})
        finally:
            try:
                events.close()
            finally:
                self.journal.interrupt()
                self.release()

    def close(self):
        try:
            self.events.close()
        finally:
            # Also handles close() before frames() has ever been iterated.
            self.journal.interrupt()
            self.release()


def assistant_required(view):
    @wraps(view)
    @never_cache
    def wrapped(request, *args, **kwargs):
        if not request.session.get('site_authenticated'):
            return JsonResponse({'error': 'Войдите в систему, чтобы пользоваться помощником.', 'code': 'authentication_required'}, status=401)
        request.employee = EmployeeProfile.objects.filter(pk=request.session.get('employee_id'), is_active=True).first()
        if request.employee is None:
            return JsonResponse({'error': 'Выберите действующего сотрудника.', 'code': 'employee_required'}, status=403)
        return view(request, *args, **kwargs)
    return wrapped


@assistant_required
@require_GET
def status(request):
    return JsonResponse(configuration_status())


def _chat_payload(request):
    length = int(request.META.get('CONTENT_LENGTH') or 0)
    if length > MAX_BODY_BYTES or len(request.body) > MAX_BODY_BYTES:
        raise AssistantError('Сообщение слишком большое.', 'invalid_request', 413)
    payload = json.loads(request.body)
    if not isinstance(payload, dict):
        raise AssistantError('Ожидается JSON-объект.', 'invalid_request', 400)
    messages = validate_messages(payload.get('messages'))
    page = payload.get('page', '/')
    if not isinstance(page, str):
        raise AssistantError('Некорректный адрес страницы.', 'invalid_request', 400)
    form_state = validate_form_state(payload.get('form_state'))
    return messages, page, form_state


def _chat_lock(employee_key):
    # This is a per-process guard, not a global spending quota.
    lock_key = employee_key + ':busy'
    lock_token = uuid4().hex
    if not cache.add(lock_key, lock_token, timeout=200):
        raise AssistantError(
            'Предыдущий ответ ещё готовится. Дождитесь его завершения.',
            'busy', 429,
        )

    def release():
        if cache.get(lock_key) == lock_token:
            cache.delete(lock_key)
    return release


def _chat_response(request, payload, release, journal):
    messages, page, form_state = payload
    kwargs = {'page': page, 'role': request.employee.role,
              'form_state': form_state, 'on_metrics': journal.metrics}
    if ('text/event-stream' in request.headers.get('Accept', '')
            and request.accepts('text/event-stream')):
        events = answer_stream(messages, **kwargs)
        response = StreamingHttpResponse(
            _EventStream(events, release, journal),
            content_type='text/event-stream; charset=utf-8',
        )
        response['X-Accel-Buffering'] = 'no'
        response['Cache-Control'] = 'no-cache, no-store, no-transform'
        return response
    result = answer(messages, **kwargs)
    journal.complete(result)
    return JsonResponse(result)


def _guarded_chat(request, payload, employee_key, release):
    journal = None
    response = None
    try:
        rate_key = employee_key + ':minute:' + str(int(time.time() // 60))
        if not cache.add(rate_key, 1, timeout=65) and cache.incr(rate_key) > 20:
            raise AssistantError(
                'Слишком много запросов. Повторите через минуту.',
                'rate_limit', 429,
            )
        messages, page, _ = payload
        journal = RequestJournal(request.employee, messages, page)
        response = _chat_response(request, payload, release, journal)
        return response
    except AssistantError as error:
        if journal is not None:
            journal.fail(error.code)
        raise
    except Exception:
        if journal is not None:
            journal.fail('internal_error')
        logger.error('Assistant request failed with an internal error.')
        return JsonResponse({
            'error': 'Не удалось подготовить ответ. Повторите запрос позже.',
            'code': 'internal_error',
        }, status=500)
    finally:
        if response is None or not response.streaming:
            release()


@assistant_required
@require_POST
def chat(request):
    if request.content_type != 'application/json':
        return JsonResponse({
            'error': 'Используйте JSON для сообщений.',
            'code': 'invalid_request',
        }, status=415)
    try:
        payload = _chat_payload(request)
        employee_key = f'assistant:employee:{request.employee.pk}'
        release = _chat_lock(employee_key)
        return _guarded_chat(request, payload, employee_key, release)
    except (ValueError, UnicodeError, RawPostDataException):
        return JsonResponse({
            'error': 'Не удалось прочитать JSON сообщения.',
            'code': 'invalid_request',
        }, status=400)
    except AssistantError as error:
        return JsonResponse(
            {'error': str(error), 'code': error.code}, status=error.status,
        )
