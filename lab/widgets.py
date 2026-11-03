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


class ComponentLotSelect(forms.Select):
    material_id = None

    def __init__(self, attrs=None, choices=()):
        super().__init__({'data-component-lot': True, **(attrs or {})}, choices)

    def create_option(self, name, value, label, selected, index, **kwargs):
        option = super().create_option(
            name, value, label, selected, index, **kwargs,
        )
        if value:
            material_id = str(value.instance.material_id)
            option['attrs']['data-material-id'] = material_id
            if material_id != str(self.material_id):
                option['attrs'].update(hidden=True, disabled=True)
        return option
