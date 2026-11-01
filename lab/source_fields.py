"""Render source choices on demand instead of embedding the whole archive."""
from django import forms

from .models import SourceCell


class SourceCellSelect(forms.Select):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.attrs['data-source-cell'] = ''

    def get_context(self, name, value, attrs):
        ids = [str(item) for item in (value if isinstance(value, list) else [value])
               if item is not None and str(item).isdigit()]
        selected = SourceCell.objects.filter(pk__in=ids).select_related('sheet')
        self.choices = [('', '---------'), *[(row.pk, str(row)) for row in selected]]
        return super().get_context(name, value, attrs)


class SourceCellField(forms.ModelChoiceField):
    widget = SourceCellSelect

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('queryset', SourceCell.objects.select_related('sheet__file'))
        kwargs.setdefault('required', False)
        kwargs.setdefault('help_text', 'Выберите лист источника для выбора ячейки.')
        super().__init__(*args, **kwargs)
