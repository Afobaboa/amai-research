"""Choose a reusable group and enter its indicators together."""
from uuid import uuid4

from django import forms

from .forms import LIMSDecimalField
from .models import MeasurementGroup, Metric
from .source_fields import SourceCellField


class MeasurementGroupForm(forms.ModelForm):
    class Meta:
        model = MeasurementGroup
        fields = ['name', 'description', 'metrics']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
            'metrics': forms.CheckboxSelectMultiple,
        }
        help_texts = {
            'metrics': 'Выберите показатели, которые заполняются вместе.',
        }

    def save(self, commit=True):
        if not self.instance.code:
            self.instance.code = 'custom-' + uuid4().hex
        return super().save(commit=commit)

    def clean_name(self):
        name = self.cleaned_data['name']
        if MeasurementGroup.objects.filter(name__iexact=name).exclude(
            pk=self.instance.pk,
        ).exists():
            raise forms.ValidationError('Группа с таким названием уже существует.')
        return name


class GroupSelectionForm(forms.Form):
    measurement_group = forms.ModelChoiceField(
        label='Группа измерений', queryset=MeasurementGroup.objects.all(),
        empty_label='Выберите группу измерений',
    )


class GroupValuesForm(forms.Form):
    method_code = forms.CharField(
        label='Метод / редакция методики', required=False, max_length=150,
    )
    observation = forms.CharField(
        label='Наблюдение', required=False, strip=False,
        widget=forms.Textarea(attrs={'rows': 3}),
    )
    source_cell = SourceCellField(label='Первичная ячейка')

    def __init__(self, *args, group, card, run=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.card, self.group, self.run = card, group, run
        self.entries = []
        if run:
            self.fields.pop('method_code')
        self._add_values()
        if card.source_sheet_id:
            self.fields['source_cell'].widget.attrs['data-source-sheet'] = str(
                card.source_sheet_id,
            )
        names = [key for entry in self.entries
                 for key in (entry[0], entry[0] + '_reason')]
        self.order_fields(['method_code', *names, 'observation', 'source_cell'])

    def _add_values(self):
        seen = set()
        if self.run:
            for result in self.run.results.select_related('metric'):
                self._add_value(result.metric, result)
                seen.add(result.metric_id)
        for metric in self.group.metrics.exclude(pk__in=seen):
            self._add_value(metric)

    def _add_value(self, metric, result=None):
        name = f'result_{result.pk}' if result else f'metric_{metric.pk}'
        locked = bool(result and (result.source_cell_id or result.origin == 'calculated'))
        label = str(metric)
        initial = result.display_value if result else None
        if locked:
            field = forms.CharField(label=label, required=False, disabled=True)
            field.help_text = 'Запись из источника или расчёта сохранена без изменений.'
        elif metric.value_kind == Metric.ValueKind.NUMERIC:
            field = LIMSDecimalField(label=label, required=False)
        else:
            field = forms.CharField(label=label, required=False)
        field.initial = initial
        field.widget.attrs['data-measurement-value'] = ''
        self.fields[name] = field
        self.fields[name + '_reason'] = forms.CharField(
            label=f'{metric.label}: причина, почему не измерено',
            required=False, disabled=locked,
            initial=result.not_measured_reason if result else '',
            help_text='Заполните, если значение не измерено.',
        )
        self.entries.append((name, metric, result, locked))

    def clean(self):
        data = super().clean()
        for name, metric, result, locked in self.entries:
            if locked or name in self.errors:
                continue
            value = data.get(name)
            reason = data.get(name + '_reason')
            if value in (None, '') and not reason:
                self.add_error(name + '_reason',
                               'Укажите значение или причину, почему оно не измерено.')
            elif value not in (None, '') and reason:
                self.add_error(name + '_reason',
                               'Укажите либо значение, либо причину отсутствия измерения.')
        return data

    def clean_source_cell(self):
        cell = self.cleaned_data.get('source_cell')
        if cell and self.card.source_sheet_id and cell.sheet_id != self.card.source_sheet_id:
            raise forms.ValidationError('Ячейка должна принадлежать листу источника эксперимента.')
        return cell
