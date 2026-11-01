from django.conf import settings
from .ui_text import UI_TEXT, navigation_labels

def workspace(request):
    employee = getattr(request, 'employee', None)
    return {'employee': employee, 'can_write': bool(employee and employee.role == 'author'), 'demo_mode': settings.DEMO_MODE, 'active_page': getattr(request.resolver_match, 'url_name', '') or '',
            'ui_text': UI_TEXT, 'assistant_navigation': navigation_labels()}
