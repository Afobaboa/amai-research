"""Author-facing forms for the material and documentary catalogues."""
from django import forms
from .ui_text import UI_TEXT

from .forms import CompactNumberInput
from .models import Material, MaterialLot, Metric, MixtureType, SourceCell, SourceFile, SourceSheet


class MaterialForm(forms.ModelForm):
    class Meta:
        model = Material
        fields = ['name', 'category', 'supplier', 'density_g_ml', 'price_per_kg', 'notes']
        labels = {'name': 'Название материала', 'category': 'Категория', 'supplier': 'Производитель / поставщик',
                  'density_g_ml': UI_TEXT['density_g_ml'], 'price_per_kg': UI_TEXT['price_per_kg'], 'notes': 'Примечание'}
        widgets = {'density_g_ml': CompactNumberInput(), 'price_per_kg': CompactNumberInput(),
                   'notes': forms.Textarea(attrs={'rows': 4})}
        help_texts = {'density_g_ml': 'Справочное значение. Для конкретной партии можно записать собственное.',
                      'price_per_kg': 'Пустое значение — цена неизвестна. Ноль означает явно указанную нулевую цену.'}


class MaterialLotForm(forms.ModelForm):
    class Meta:
        model = MaterialLot
        fields = ['material', 'lot_code', 'supplier', 'received_on', 'expires_on', 'status',
                  'density_g_ml', 'price_per_kg', 'source', 'notes']
        labels = {'material': 'Материал', 'lot_code': 'Номер партии', 'supplier': 'Поставщик партии',
                  'received_on': 'Дата поступления', 'expires_on': 'Годен до', 'status': 'Статус партии',
                  'density_g_ml': 'Плотность партии, г/мл', 'price_per_kg': 'Цена партии, ₽/кг',
                  'source': 'Паспорт / источник', 'notes': 'Примечание'}
        widgets = {'received_on': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
                   'expires_on': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
                   'density_g_ml': CompactNumberInput(), 'price_per_kg': CompactNumberInput(),
                   'notes': forms.Textarea(attrs={'rows': 4})}
        help_texts = {'density_g_ml': 'Если не заполнено, используется справочная плотность материала.',
                      'price_per_kg': 'Если не заполнено, используется справочная цена материала.',
                      'status': 'Карантин и израсходованные партии сохраняются в истории использования.'}

    def clean(self):
        data = super().clean()
        if self.instance.pk:
            original = MaterialLot.objects.get(pk=self.instance.pk)
            if original.recipe_components.exists() or original.filling_lines.exists():
                if data.get('material') and data['material'].pk != original.material_id:
                    self.add_error('material', 'Материал использованной партии менять нельзя. Заведите новую партию.')
                if data.get('lot_code') != original.lot_code:
                    self.add_error('lot_code', 'Номер использованной партии менять нельзя. Заведите новую партию.')
        return data


class SourceForm(forms.ModelForm):
    upload = forms.FileField(label='Файл источника', required=False,
                            help_text='До 25 МБ. Файл сохраняется как вложение; его строки не импортируются.')

    class Meta:
        model = SourceFile
        fields = ['title', 'kind', 'url', 'notes', 'date_system']
        labels = {'title': 'Название источника', 'kind': 'Тип источника', 'url': 'Ссылка',
                  'notes': 'Описание / текст записи', 'date_system': 'Система дат Excel'}
        widgets = {'notes': forms.Textarea(attrs={'rows': 5})}
        help_texts = {'url': 'Только адрес документа. Система не скачивает содержимое ссылки.',
                      'date_system': 'Применяется только при последующей работе с Excel; на другие документы не влияет.'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['title'].required = True
        self.fields['date_system'].required = False
        if self.instance.pk and self.instance.attachment:
            self.fields['upload'].help_text = 'Сохранённое вложение не заменяется. Для нового файла создайте новый источник.'

    def clean_upload(self):
        upload = self.cleaned_data.get('upload')
        if upload and upload.size > 25 * 1024 * 1024:
            raise forms.ValidationError('Размер файла превышает 25 МБ.')
        if upload and self.instance.pk and self.instance.attachment:
            raise forms.ValidationError('Для замены первичного файла создайте новый источник. Сохранённый файл остается неизменным.')
        return upload

    def clean(self):
        data = super().clean()
        kind, upload = data.get('kind'), data.get('upload')
        if kind == 'file' and not upload and not self.instance.attachment:
            self.add_error('upload', 'Выберите файл для сохранения.')
        if kind == 'link' and not data.get('url'):
            self.add_error('url', 'Укажите ссылку на документ.')
        if kind == 'note' and not data.get('notes'):
            self.add_error('notes', 'Запишите содержание или описание источника.')
        if kind != 'file' and (upload or self.instance.attachment):
            self.add_error('kind', 'Источнику с вложением нужен тип «Файл». Для ссылки или заметки создайте отдельный источник.')
        data['date_system'] = data.get('date_system') or '1900'
        return data


class SourceSheetForm(forms.ModelForm):
    class Meta:
        model = SourceSheet
        fields = ['name']
        labels = {'name': 'Лист / раздел документа'}

    def __init__(self, *args, source, **kwargs):
        super().__init__(*args, **kwargs)
        self.source = source
        self.instance.file = source

    def clean_name(self):
        name = self.cleaned_data['name']
        if SourceSheet.objects.filter(file=self.source, name=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Такой лист или раздел уже добавлен для этого источника.')
        return name


class SourceCellForm(forms.ModelForm):
    class Meta:
        model = SourceCell
        fields = ['address', 'cell_kind', 'raw_value', 'formula_text', 'cached_value', 'number_format']
        labels = {'address': 'Ячейка / ссылка на фрагмент', 'cell_kind': 'Вид фрагмента',
                  'raw_value': 'Исходное значение дословно', 'formula_text': 'Формула дословно',
                  'cached_value': 'Сохранённый результат формулы', 'number_format': 'Исходный формат'}
        widgets = {'raw_value': forms.Textarea(attrs={'rows': 3}), 'formula_text': forms.Textarea(attrs={'rows': 2}),
                   'cached_value': forms.Textarea(attrs={'rows': 2})}
        help_texts = {'address': 'Например B12, страница 3 или раздел «Плотность».',
                      'cached_value': 'Сохранённый кэш не подтверждает правильность формулы и не становится результатом испытания.'}

    def __init__(self, *args, sheet, **kwargs):
        super().__init__(*args, **kwargs)
        self.sheet = sheet
        self.instance.sheet = sheet
        self.fields['cell_kind'].widget = forms.Select(choices=[('text', 'Текст'), ('numeric', 'Число'),
                                                               ('formula', 'Формула'), ('blank', 'Пусто'), ('other', 'Другое')])
        self.fields['raw_value'].strip = False
        self.fields['formula_text'].strip = False

    def clean_address(self):
        address = self.cleaned_data['address']
        if SourceCell.objects.filter(sheet=self.sheet, address=address).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Этот фрагмент уже записан в выбранном разделе.')
        return address

    def clean(self):
        data = super().clean()
        for key in ('raw_value', 'formula_text', 'cached_value', 'number_format'):
            if data.get(key) == '':
                data[key] = None
        if data.get('formula_text') is not None and data.get('raw_value') is not None:
            raise forms.ValidationError('Укажите исходное значение или формулу; оба поля одновременно не заполняются.')
        if data.get('cached_value') is not None and not data.get('formula_text'):
            self.add_error('cached_value', 'Кэш допустим только вместе с формулой.')
        return data


class MetricForm(forms.ModelForm):
    class Meta:
        model = Metric
        fields = ['code', 'label', 'unit_code', 'value_kind']
        labels = {'code': 'Код показателя', 'label': 'Название показателя', 'unit_code': 'Единица измерения',
                  'value_kind': 'Тип значения'}

    def clean(self):
        data = super().clean()
        if self.instance.pk:
            original = Metric.objects.get(pk=self.instance.pk)
            if original.results.exists() or original.conditions.exists():
                for key in ('code', 'unit_code', 'value_kind'):
                    if data.get(key) != getattr(original, key):
                        self.add_error(key, 'Для использованного показателя код, единицу и тип не меняют. Создайте новый показатель.')
        return data


class MixtureTypeForm(forms.ModelForm):
    class Meta:
        model = MixtureType
        fields = ['code', 'title']
        labels = {'code': 'Код типа смеси', 'title': 'Название типа смеси'}

    def clean_code(self):
        code = self.cleaned_data['code']
        if self.instance.pk:
            original = MixtureType.objects.get(pk=self.instance.pk)
            if original.recipes.exists() and code != original.code:
                raise forms.ValidationError('Код используемого типа смеси не меняется. Создайте новый тип.')
        return code
