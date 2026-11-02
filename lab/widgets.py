"""Keep server defaults distinct from browser-restored hidden input values."""
from django import forms


class ServerValueMixin:
    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context['widget']['attrs']['data-server-value'] = context['widget']['value'] or ''
        return context


class ServerHiddenInput(ServerValueMixin, forms.HiddenInput):
    pass


class ServerDateInput(ServerValueMixin, forms.DateInput):
    pass
