from decimal import Decimal

from django import forms
from django.forms import formset_factory

from .source_fields import SourceCellField
from .widgets import ServerDateInput, ServerHiddenInput

from .models import (
    ConditioningEvent, EmployeeProfile, FillingPlanLine, Material, MaterialLot,
    Metric, MixtureType, RecipeVersion, TestSession, TestRun, SourceCell, SourceSheet,
    RecipeGroup, RecipeProperty, RecipeTemplate,
)


class CompactNumberInput(forms.NumberInput):
    """Render exact persisted decimals without insignificant zeros or exponents."""

    def format_value(self, value):
        if isinstance(value, Decimal) and value.is_finite():
            value = format(value, "f")
            if "." in value:
                value = value.rstrip("0").rstrip(".")
            if value == "-0":
                value = "0"
        return super().format_value(value)


class LIMSDecimalField(forms.DecimalField):
    widget = CompactNumberInput

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_digits", 22)
        kwargs.setdefault("decimal_places", 8)
        super().__init__(*args, **kwargs)


class PasswordForm(forms.Form):
    password = forms.CharField(label="Пароль лаборатории", widget=forms.PasswordInput(attrs={"autofocus": True}))


class EmployeeForm(forms.Form):
    employee = forms.ModelChoiceField(label="Выберите сотрудника", queryset=EmployeeProfile.objects.none(), empty_label="Выберите ФИО")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["employee"].queryset = EmployeeProfile.objects.filter(is_active=True).order_by("full_name")


class RecipeForm(forms.ModelForm):
    batch_mass_g = LIMSDecimalField(label="Масса каждой группы для расчёта, г", required=False, min_value=Decimal('0.00000001'), initial=1000)

    class Meta:
        model = RecipeVersion
        fields = ["mixture_type", "version_label", "title", "recorded_on", "status", "recipe_template", "batch_mass_g", "source_sheet", "notes"]
        labels = {"mixture_type": "Тип смеси", "version_label": "Обозначение версии", "title": "Название", "recorded_on": "Дата рецептуры", "status": "Статус", "notes": "Примечание"}
        widgets = {"recipe_template": ServerHiddenInput, "source_sheet": ServerHiddenInput, "recorded_on": ServerDateInput(format="%Y-%m-%d", attrs={"type": "date"}), "notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["status"].choices = [("draft", "Черновик"), ("published", "Опубликована")]
        self.fields['source_sheet'].label = 'Лист первичного источника'
        self.fields['source_sheet'].queryset = SourceSheet.objects.select_related('file')

    def clean_batch_mass_g(self):
        value = self.cleaned_data.get('batch_mass_g')
        return value if value is not None else self.instance.batch_mass_g or Decimal('1000')


class ComponentForm(forms.Form):
    group_label = forms.CharField(label="Группа", max_length=100, initial="A", required=False)
    quantity_basis = forms.ChoiceField(label="База количества", choices=[("mass_percent", "% масс."), ("weight_parts", "масс. ч."), ("unknown", "Не определена")], initial="mass_percent", required=False)
    completeness = forms.ChoiceField(label='Полнота группы', choices=RecipeGroup.Completeness.choices, initial='unknown', required=False)
    material = forms.ModelChoiceField(label="Компонент", queryset=Material.objects.all(), required=False)
    specified_lot = forms.ModelChoiceField(label="Партия (если известна)", queryset=MaterialLot.objects.select_related("material"), required=False)
    quantity_value = LIMSDecimalField(label="Количество", min_value=Decimal("0"), required=False)
    name_cell = SourceCellField(label='Ячейка наименования')
    quantity_cell = SourceCellField(label='Ячейка количества')

    def clean(self):
        data = super().clean()
        material, lot, quantity = data.get("material"), data.get("specified_lot"), data.get("quantity_value")
        # Default group/basis alone do not turn an unused row into a component.
        if not material and not lot and quantity is None and not data.get('name_cell') and not data.get('quantity_cell'):
            data["unused"] = True
            return data
        if material and lot:
            raise forms.ValidationError("Выберите либо материал, либо конкретную партию.")
        if not material and not lot and not data.get('name_cell'):
            raise forms.ValidationError("Укажите материал или партию для количества.")
        if not data.get("group_label"):
            self.add_error("group_label", "Укажите группу состава.")
        if not data.get("quantity_basis"):
            self.add_error("quantity_basis", "Укажите базу количества.")
        data['completeness'] = data.get('completeness') or 'unknown'
        return data


class FillingForm(forms.Form):
    group_label = forms.CharField(label="Группа смеси", max_length=100, required=False)
    material = forms.ModelChoiceField(label="Материал заправки", queryset=Material.objects.all(), required=False)
    specified_lot = forms.ModelChoiceField(label="Партия", queryset=MaterialLot.objects.select_related("material"), required=False)
    mass_g = LIMSDecimalField(label="Плановая масса, г", min_value=Decimal("0"), required=False)
    mass_status = forms.ChoiceField(label='Основание массы', choices=FillingPlanLine.MassStatus.choices, required=False)
    label_cell = SourceCellField(label='Ячейка наименования')
    mass_cell = SourceCellField(label='Ячейка массы')

    def clean(self):
        data = super().clean()
        targets = [data.get("group_label"), data.get("material"), data.get("specified_lot")]
        if not any(targets) and data.get("mass_g") is None and not data.get('label_cell') and not data.get('mass_cell'):
            data["unused"] = True
            return data
        if sum(bool(value) for value in targets) > 1 or (not any(targets) and not data.get('label_cell')):
            raise forms.ValidationError("Укажите одну группу, материал или партию для строки заправки.")
        data['mass_status'] = data.get('mass_status') or ('recorded' if data.get('mass_g') is not None else 'unknown')
        if data['mass_status'] in ('recorded', 'calculation_checked') and data.get('mass_g') is None:
            self.add_error('mass_g', 'Для этого основания укажите массу.')
        if data['mass_status'] in ('unknown', 'calculation_unchecked') and data.get('mass_g') is not None:
            self.add_error('mass_g', 'Непроверенная или неизвестная масса остаётся пустой; кэш хранится в ячейке источника.')
        return data


ComponentFormSet = formset_factory(ComponentForm, extra=1, max_num=100, validate_max=True, can_delete=True)
FillingFormSet = formset_factory(FillingForm, extra=1, max_num=50, validate_max=True, can_delete=True)


class RecipePropertyForm(forms.Form):
    target = forms.ChoiceField(label='К чему относится', choices=[('recipe', 'Вся рецептура'), ('group', 'Группа'), ('component', 'Компонент группы'), ('filling', 'Строка заправки')], required=False, initial='recipe')
    group_label = forms.CharField(label='Название группы', required=False, max_length=100)
    position = forms.IntegerField(label='№ компонента / строки заправки', min_value=1, required=False)
    property_code = forms.CharField(label='Свойство', required=False, max_length=100, help_text='Например OHV, NCO или плотность')
    value = LIMSDecimalField(label='Значение', required=False)
    unit_code = forms.CharField(label='Единица', required=False, max_length=60)
    source_cell = SourceCellField(label='Первичная ячейка')
    origin = forms.ChoiceField(
        label='Происхождение свойства', choices=RecipeProperty.Origin.choices,
        required=False, initial='recorded', widget=ServerHiddenInput,
    )
    provenance_note = forms.CharField(
        label='Основание переноса', required=False,
        widget=ServerHiddenInput,
    )

    def clean(self):
        data = super().clean()
        if not data.get('property_code') and data.get('value') is None and not data.get('group_label') and not data.get('position') and not data.get('source_cell') and not data.get('unit_code'):
            data['unused'] = True
            return data
        if not data.get('property_code'):
            self.add_error('property_code', 'Укажите свойство.')
        if data.get('value') is None:
            self.add_error('value', 'Укажите числовое значение.')
        if not data.get('target'):
            self.add_error('target', 'Выберите объект свойства.')
        if data.get('target') in ('group', 'component') and not data.get('group_label'):
            self.add_error('group_label', 'Укажите группу состава.')
        if data.get('target') in ('component', 'filling') and not data.get('position'):
            self.add_error('position', 'Укажите номер строки.')
        if data.get('origin') == 'carried_forward' and (
            not data.get('source_cell') or not data.get('provenance_note')
        ):
            self.add_error('provenance_note',
                           'Для переноса нужны исходная ячейка и основание.')
        return data


RecipePropertyFormSet = formset_factory(RecipePropertyForm, extra=1, max_num=100, validate_max=True, can_delete=True)


class TestSessionForm(forms.ModelForm):
    recipe = forms.ModelChoiceField(label="Связанная рецептура", queryset=RecipeVersion.objects.select_related("mixture_type").filter(status="published"), required=False, empty_label="Связь пока не установлена", help_text="Номер карточки совпадает с номером выбранной рецептуры.")

    class Meta:
        model = TestSession
        fields = ["recipe", "title", "recorded_manufactured_on", "recorded_tested_on", "status", "notes"]
        labels = {"title": "Название испытания", "recorded_manufactured_on": "Записанная дата изготовления", "recorded_tested_on": "Дата испытания", "status": "Статус", "notes": "Примечание"}
        widgets = {"recorded_manufactured_on": ServerDateInput(format="%Y-%m-%d", attrs={"type": "date"}), "recorded_tested_on": ServerDateInput(format="%Y-%m-%d", attrs={"type": "date"}), "notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.fields["recipe"].required = True
            self.fields["recipe"].empty_label = "Выберите рецептуру"

    def save(self, commit=True):
        card = super().save(commit=False)
        recipe = self.cleaned_data.get("recipe")
        if recipe:
            card.identifier = recipe.version_label
        if commit:
            card.save()
        return card


class ConditionForm(forms.Form):
    metric = forms.ModelChoiceField(label="Условие", queryset=Metric.objects.all(), required=False)
    numeric_value = LIMSDecimalField(label="Число", required=False)
    text_value = forms.CharField(label="Текст", required=False)
    source_cell = SourceCellField(label='Первичная ячейка')

    def clean(self):
        data = super().clean()
        metric = data.get("metric")
        numeric, text = data.get("numeric_value"), data.get("text_value")
        if not metric and numeric is None and not text and not data.get('source_cell'):
            data["unused"] = True
            return data
        if not metric:
            self.add_error("metric", "Выберите показатель.")
        elif metric.value_kind == "numeric":
            if numeric is None or text:
                raise forms.ValidationError("Для числового показателя заполните только число.")
        elif numeric is not None or not text:
            raise forms.ValidationError("Для текстового показателя заполните только текст.")
        return data


ConditionFormSet = formset_factory(ConditionForm, extra=1, max_num=30, validate_max=True, can_delete=True)


class RunFields(forms.Form):
    run = forms.ModelChoiceField(label="Определение", queryset=TestRun.objects.none(), required=False, empty_label="Создать новое определение")
    scope = forms.CharField(label="Название нового определения", required=False, initial="Основное испытание", max_length=250)
    method_code = forms.CharField(label="Метод / редакция методики", required=False, max_length=150)
    source_cell = SourceCellField(label='Первичная ячейка')

    def __init__(self, *args, card, **kwargs):
        super().__init__(*args, **kwargs)
        self.card = card
        self.fields["run"].queryset = card.runs.all()
        if card.source_sheet_id:
            self.fields["source_cell"].widget.attrs[
                "data-source-sheet"
            ] = str(card.source_sheet_id)

    def clean(self):
        data = super().clean()
        if not data.get("run") and not data.get("scope"):
            self.add_error("scope", "Укажите название нового определения.")
        cell = data.get('source_cell')
        if cell and self.card.source_sheet_id and cell.sheet_id != self.card.source_sheet_id:
            self.add_error('source_cell', 'Ячейка должна принадлежать листу источника карточки.')
        return data


class ResultForm(RunFields):
    metric = forms.ModelChoiceField(label="Показатель", queryset=Metric.objects.all())
    numeric_value = LIMSDecimalField(label="Числовой результат", required=False)
    text_value = forms.CharField(label="Текстовый результат", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def clean(self):
        data = super().clean()
        metric = data.get("metric")
        if metric:
            if metric.value_kind == "numeric" and (data.get("numeric_value") is None or data.get("text_value")):
                raise forms.ValidationError("Для числового показателя укажите только числовой результат. Единица берётся из справочника показателей.")
            if metric.value_kind != "numeric" and (data.get("numeric_value") is not None or not data.get("text_value")):
                raise forms.ValidationError("Для этого показателя укажите только текстовый результат.")
        return data


class ObservationForm(RunFields):
    text_value = forms.CharField(label="Наблюдение дословно", strip=False, widget=forms.Textarea(attrs={"rows": 6}))


class ConditioningForm(forms.ModelForm):
    class Meta:
        model = ConditioningEvent
        fields = ["target_kind", "kind", "duration_value", "duration_unit", "temperature_c", "source_cell"]
        widgets = {"duration_value": CompactNumberInput, "temperature_c": CompactNumberInput, "source_cell": ServerHiddenInput}
        labels = {"target_kind": "Цель обработки", "kind": "Тип обработки", "duration_value": "Длительность", "duration_unit": "Единица длительности", "temperature_c": "Температура, °C"}

    def clean(self):
        data = super().clean()
        if (data.get("duration_value") is None) != (not data.get("duration_unit")):
            raise forms.ValidationError("Длительность и её единицу указывают вместе.")
        return data
