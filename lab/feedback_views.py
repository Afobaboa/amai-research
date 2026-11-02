"""Collect feedback without granting access to submitted messages."""

from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .feedback_forms import FeedbackForm
from .views import site_required


@site_required
@require_http_methods(["GET", "POST"])
def feedback(request):
    form = FeedbackForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        message = form.save(commit=False)
        message.employee = request.employee
        message.employee_name = request.employee.full_name
        message.save()
        messages.success(request, "Спасибо! Обратная связь отправлена.")
        return redirect("dashboard")
    return render(request, "lab/feedback_form.html", {
        "form": form, "title": "Обратная связь",
    })

