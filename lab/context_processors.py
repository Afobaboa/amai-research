from django.conf import settings

def workspace(request):
    employee = getattr(request, 'employee', None)
    return {'employee': employee, 'can_write': bool(employee and employee.role == 'author'), 'demo_mode': settings.DEMO_MODE, 'active_page': getattr(request.resolver_match, 'url_name', '') or ''}
