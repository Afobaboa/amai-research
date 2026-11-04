"""Export rendered assistant tables as XLSX without model or database writes."""
import json
import re
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

from django.core.exceptions import RequestDataTooBig
from django.http import HttpResponse, JsonResponse, RawPostDataException
from django.views.decorators.http import require_POST

from .assistant_views import assistant_required

MAX_BODY_BYTES = 1024 * 1024
MAX_ROWS = 10000
MAX_COLUMNS = 256
MAX_CELLS = 50000
MAX_CELL_CHARS = 32767
SHEET_NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PACKAGE_REL_NS = 'http://schemas.openxmlformats.org/package/2006/relationships'
CONTENT_NS = 'http://schemas.openxmlformats.org/package/2006/content-types'
XLSX_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
INVALID_XML = re.compile(
    r'[^\x09\x0A\x0D\x20-\uD7FF\uE000-\uFFFD\U00010000-\U0010FFFF]',
)


class ExportError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _validate_rows(rows):
    if not isinstance(rows, list) or not rows:
        raise ExportError('Передайте непустую таблицу.')
    if len(rows) > MAX_ROWS:
        raise ExportError('В таблице слишком много строк.', 413)
    if not isinstance(rows[0], list) or not rows[0]:
        raise ExportError('В таблице должны быть столбцы.')
    width = len(rows[0])
    if width > MAX_COLUMNS or len(rows) * width > MAX_CELLS:
        raise ExportError('Таблица слишком большая для скачивания.', 413)
    for row in rows:
        if not isinstance(row, list) or len(row) != width:
            raise ExportError('Число ячеек во всех строках должно совпадать.')
        for cell in row:
            if not isinstance(cell, str):
                raise ExportError('Значения ячеек должны быть текстом.')
            if len(cell) > MAX_CELL_CHARS:
                raise ExportError('Текст в ячейке слишком длинный для Excel.', 413)
    return rows


def _read_rows(request):
    if request.content_type != 'application/json':
        raise ExportError('Используйте JSON для скачивания таблицы.', 415)
    if int(request.META.get('CONTENT_LENGTH') or 0) > MAX_BODY_BYTES:
        raise ExportError('Таблица слишком большая для скачивания.', 413)
    body = request.body
    if len(body) > MAX_BODY_BYTES:
        raise ExportError('Таблица слишком большая для скачивания.', 413)
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ExportError('Ожидается JSON-объект с таблицей.')
    return _validate_rows(data.get('rows'))


def _xml(root):
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def _column_name(number):
    name = ''
    while number:
        number, remainder = divmod(number - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _cell_text(text):
    text = INVALID_XML.sub('', text)
    # Spreadsheet escape sequences must remain literal text, too.
    text = re.sub(r'_x[0-9A-Fa-f]{4}_', lambda m: '_x005F_' + m[0][1:], text)
    return text.replace('\r', '_x000D_')


def _append_sheet_rows(sheet, rows):
    data = ET.SubElement(sheet, 'sheetData')
    for number, values in enumerate(rows, 1):
        row = ET.SubElement(data, 'row', {'r': str(number)})
        for column, value in enumerate(values, 1):
            cell = ET.SubElement(row, 'c', {
                'r': f'{_column_name(column)}{number}',
                't': 'inlineStr', 's': '1' if number == 1 else '0',
            })
            inline = ET.SubElement(cell, 'is')
            ET.SubElement(inline, 't', {
                '{http://www.w3.org/XML/1998/namespace}space': 'preserve',
            }).text = _cell_text(value)


def _sheet(rows):
    sheet = ET.Element('worksheet', {'xmlns': SHEET_NS})
    last = f'{_column_name(len(rows[0]))}{len(rows)}'
    ET.SubElement(sheet, 'dimension', {'ref': f'A1:{last}'})
    views = ET.SubElement(sheet, 'sheetViews')
    view = ET.SubElement(views, 'sheetView', {'workbookViewId': '0'})
    ET.SubElement(view, 'pane', {
        'ySplit': '1', 'topLeftCell': 'A2',
        'activePane': 'bottomLeft', 'state': 'frozen',
    })
    columns = ET.SubElement(sheet, 'cols')
    for number, values in enumerate(zip(*rows), 1):
        width = min(60, max(12, max(map(len, values)) + 2))
        ET.SubElement(columns, 'col', {
            'min': str(number), 'max': str(number),
            'width': str(width), 'customWidth': '1',
        })
    _append_sheet_rows(sheet, rows)
    ET.SubElement(sheet, 'autoFilter', {'ref': f'A1:{last}'})
    return _xml(sheet)


def _style_formats(styles):
    formats = ET.SubElement(styles, 'cellStyleXfs', {'count': '1'})
    common = {'numFmtId': '0', 'fontId': '0', 'fillId': '0', 'borderId': '0'}
    ET.SubElement(formats, 'xf', common)
    cells = ET.SubElement(styles, 'cellXfs', {'count': '2'})
    for font in ('0', '1'):
        cell = ET.SubElement(cells, 'xf', {
            **common, 'fontId': font, 'xfId': '0',
            'applyAlignment': '1', 'applyFont': '1',
        })
        ET.SubElement(cell, 'alignment', {'vertical': 'top', 'wrapText': '1'})
    names = ET.SubElement(styles, 'cellStyles', {'count': '1'})
    ET.SubElement(names, 'cellStyle', {
        'name': 'Normal', 'xfId': '0', 'builtinId': '0',
    })


def _styles():
    styles = ET.Element('styleSheet', {'xmlns': SHEET_NS})
    fonts = ET.SubElement(styles, 'fonts', {'count': '2'})
    for bold in (False, True):
        font = ET.SubElement(fonts, 'font')
        ET.SubElement(font, 'sz', {'val': '11'})
        ET.SubElement(font, 'name', {'val': 'Calibri'})
        if bold:
            ET.SubElement(font, 'b')
    fills = ET.SubElement(styles, 'fills', {'count': '2'})
    for pattern in ('none', 'gray125'):
        fill = ET.SubElement(fills, 'fill')
        ET.SubElement(fill, 'patternFill', {'patternType': pattern})
    borders = ET.SubElement(styles, 'borders', {'count': '1'})
    border = ET.SubElement(borders, 'border')
    for side in ('left', 'right', 'top', 'bottom', 'diagonal'):
        ET.SubElement(border, side)
    _style_formats(styles)
    return _xml(styles)


def _workbook():
    workbook = ET.Element('workbook', {'xmlns': SHEET_NS, 'xmlns:r': REL_NS})
    sheets = ET.SubElement(workbook, 'sheets')
    ET.SubElement(sheets, 'sheet', {
        'name': 'Таблица', 'sheetId': '1', 'r:id': 'rId1',
    })
    return _xml(workbook)


def _relationships(entries):
    root = ET.Element('Relationships', {'xmlns': PACKAGE_REL_NS})
    for number, (kind, target) in enumerate(entries, 1):
        ET.SubElement(root, 'Relationship', {
            'Id': f'rId{number}', 'Type': f'{REL_NS}/{kind}', 'Target': target,
        })
    return _xml(root)


def _content_types():
    root = ET.Element('Types', {'xmlns': CONTENT_NS})
    for extension, content in (
        ('rels', 'application/vnd.openxmlformats-package.relationships+xml'),
        ('xml', 'application/xml'),
    ):
        ET.SubElement(root, 'Default', {
            'Extension': extension, 'ContentType': content,
        })
    for path, kind in (
        ('workbook.xml', 'sheet.main'), ('styles.xml', 'styles'),
        ('worksheets/sheet1.xml', 'worksheet'),
    ):
        ET.SubElement(root, 'Override', {
            'PartName': '/xl/' + path,
            'ContentType': (
                'application/vnd.openxmlformats-officedocument.'
                f'spreadsheetml.{kind}+xml'
            ),
        })
    return _xml(root)


def table_workbook(rows):
    parts = {
        '[Content_Types].xml': _content_types(),
        '_rels/.rels': _relationships([('officeDocument', 'xl/workbook.xml')]),
        'xl/workbook.xml': _workbook(),
        'xl/_rels/workbook.xml.rels': _relationships([
            ('worksheet', 'worksheets/sheet1.xml'), ('styles', 'styles.xml'),
        ]),
        'xl/worksheets/sheet1.xml': _sheet(rows),
        'xl/styles.xml': _styles(),
    }
    buffer = BytesIO()
    with ZipFile(buffer, 'w', compression=ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return buffer.getvalue()


@assistant_required
@require_POST
def table_export(request):
    try:
        rows = _read_rows(request)
    except ExportError as error:
        return JsonResponse({'error': str(error)}, status=error.status)
    except RequestDataTooBig:
        return JsonResponse({'error': 'Таблица слишком большая.'}, status=413)
    except (ValueError, UnicodeError, RawPostDataException, RecursionError):
        return JsonResponse({'error': 'Не удалось прочитать таблицу.'}, status=400)
    response = HttpResponse(table_workbook(rows), content_type=XLSX_TYPE)
    response['Content-Disposition'] = 'attachment; filename="table.xlsx"'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
