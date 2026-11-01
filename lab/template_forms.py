"""Forms for fixed recipe and calculation template versions."""
from django import forms
from django.forms import formset_factory

from .forms import CompactNumberInput, ComponentForm, FillingForm
from .models import CalculationTemplate, RecipeTemplate


OPERATION_CHOICES = [
    ('mass', 'Масса компонентов, г'),
    ('cost', 'Стоимость компонентов, ₽'),
    ('volume', 'Аддитивный объём компонентов, мл'),
    ('totalCost', 'Стоимость смеси, ₽, и цена за кг'),
    ('density', 'Расчётная плотность смеси, г/мл'),
]
OPERATION_DESCRIPTIONS = {
    'mass': ('Масса компонента', 'Масса смеси × массовая доля ÷ 100; для массовых частей — нормирование по сумме частей.', 'г'),
    'cost': ('Стоимость компонента', 'Масса компонента ÷ 1 000 × цена материала или партии за кг.', '₽'),
    'volume': ('Аддитивный объём', 'Масса компонента ÷ плотность материала или партии.', 'мл'),
    'totalCost': ('Стоимость смеси и цена за кг', 'Сумма стоимостей компонентов; цена за кг = стоимость × 1 000 ÷ масса смеси.', '₽; ₽/кг'),
    'density': ('Расчётная плотность смеси', 'Масса смеси ÷ сумма аддитивных объёмов компонентов.', 'г/мл'),
}


class TemplateComponentForm(ComponentForm):
    name_cell = None
    quantity_cell = None


class TemplateFillingForm(FillingForm):
    mass_status = None
    label_cell = None
    mass_cell = None


TemplateComponentFormSet = formset_factory(TemplateComponentForm, extra=2, max_num=100, validate_max=True, can_delete=True)
TemplateFillingFormSet = formset_factory(TemplateFillingForm, extra=1, max_num=50, validate_max=True, can_delete=True)


class CalculationTemplateForm(forms.ModelForm):
    operations = forms.MultipleChoiceField(
        label='Что рассчитывать', choices=OPERATION_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        help_text='Расчёты выполняются проверенными операциями. Произвольный код и формулы не исполняются.',
    )

    class Meta:
        model = CalculationTemplate
        fields = ['name', 'version', 'description', 'operations']
        labels = {'name': 'Название шаблона', 'version': 'Версия', 'description': 'Описание'}
        widgets = {'description': forms.Textarea(attrs={'rows': 3})}


class RecipeTemplateForm(forms.ModelForm):
    class Meta:
        model = RecipeTemplate
        fields = ['name', 'version', 'description', 'mixture_type', 'calculation_template', 'batch_mass_g']
        labels = {'name': 'Название шаблона', 'version': 'Версия', 'description': 'Описание',
                  'mixture_type': 'Тип смеси', 'calculation_template': 'Шаблон расчёта',
                  'batch_mass_g': 'Масса смеси для расчёта, г'}
        widgets = {'description': forms.Textarea(attrs={'rows': 3}), 'batch_mass_g': CompactNumberInput}
