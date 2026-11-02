"""Forms for fixed recipe and calculation template versions."""
from django import forms
from django.forms import formset_factory

from .forms import CompactNumberInput, ComponentForm, FillingForm
from .models import RecipeTemplate


class TemplateComponentForm(ComponentForm):
    name_cell = None
    quantity_cell = None


class TemplateFillingForm(FillingForm):
    mass_status = None
    label_cell = None
    mass_cell = None


TemplateComponentFormSet = formset_factory(TemplateComponentForm, extra=1, max_num=100, validate_max=True, can_delete=True)
TemplateFillingFormSet = formset_factory(TemplateFillingForm, extra=1, max_num=50, validate_max=True, can_delete=True)


class RecipeTemplateForm(forms.ModelForm):
    class Meta:
        model = RecipeTemplate
        fields = ['name', 'version', 'description', 'mixture_type', 'batch_mass_g']
        labels = {'name': 'Название шаблона', 'version': 'Версия', 'description': 'Описание',
                  'mixture_type': 'Тип смеси',
                  'batch_mass_g': 'Масса смеси для расчёта, г'}
        widgets = {'description': forms.Textarea(attrs={'rows': 3}), 'batch_mass_g': CompactNumberInput}
