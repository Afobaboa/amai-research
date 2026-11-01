"""Authenticated JSON endpoints; the browser never talks to Cloud.ru directly."""
import json
import logging
import time
from functools import wraps

from django.core.cache import cache
from django.http import JsonResponse, RawPostDataException
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from .assistant import AssistantError, answer, configuration_status, validate_messages
from .models import EmployeeProfile

logger = logging.getLogger(__name__)
MAX_BODY_BYTES = 256000


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


@assistant_required
@require_POST
def chat(request):
    if request.content_type != 'application/json':
        return JsonResponse({'error': 'Используйте JSON для сообщений.', 'code': 'invalid_request'}, status=415)
    try:
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
    except (ValueError, UnicodeError, RawPostDataException):
        return JsonResponse({'error': 'Не удалось прочитать JSON сообщения.', 'code': 'invalid_request'}, status=400)
    except AssistantError as error:
        return JsonResponse({'error': str(error), 'code': error.code}, status=error.status)
    # In-memory cache is deliberately only a per-process guard, not a global quota.
    employee_key = f'assistant:employee:{request.employee.pk}'
    lock_key = employee_key + ':busy'
    if not cache.add(lock_key, True, timeout=200):
        return JsonResponse({'error': 'Предыдущий ответ ещё готовится. Дождитесь его завершения.', 'code': 'busy'}, status=429)
    try:
        rate_key = employee_key + ':minute:' + str(int(time.time() // 60))
        if not cache.add(rate_key, 1, timeout=65) and cache.incr(rate_key) > 20:
            raise AssistantError('Слишком много запросов. Повторите через минуту.', 'rate_limit', 429)
        result = answer(messages, page=page, role=request.employee.role)
        return JsonResponse(result)
    except AssistantError as error:
        return JsonResponse({'error': str(error), 'code': error.code}, status=error.status)
    except Exception:
        # Do not log exception text: database/provider errors can contain user data.
        logger.error('Assistant request failed with an internal error.')
        return JsonResponse({'error': 'Не удалось подготовить ответ. Повторите запрос позже.', 'code': 'internal_error'}, status=500)
    finally:
        cache.delete(lock_key)
