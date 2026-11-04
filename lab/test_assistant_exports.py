"""Validate actual XLSX contents and the authenticated table export boundary."""
import json
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from . import assistant_exports as exports
from .models import EmployeeProfile

NS = {'s': exports.SHEET_NS}


class AssistantTableExportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.reader = EmployeeProfile.objects.create(
            full_name='Читатель таблиц', role='reader',
        )

    def setUp(self):
        self.url = reverse('assistant_table_export')
        self.sign_in(self.client)

    def sign_in(self, client):
        session = client.session
        session['site_authenticated'] = True
        session['employee_id'] = self.reader.pk
        session.save()

    def post(self, rows, **kwargs):
        return self.client.post(
            self.url, json.dumps({'rows': rows}),
            content_type='application/json', **kwargs,
        )

    def workbook_xml(self, response):
        self.assertEqual(response.status_code, 200)
        with ZipFile(BytesIO(response.content)) as archive:
            self.assertIsNone(archive.testzip())
            return {name: ET.fromstring(archive.read(name))
                    for name in archive.namelist()}

    def test_download_contains_real_workbook_and_exact_visible_text(self):
        rows = [['Рецептура', 'Значение', 'Комментарий'],
                ['316.1', '0012', 'A|B & <пена>'],
                ['Кириллица 🧪', '', '  текст\nс переносом  ']]
        response = self.post(rows)
        self.assertEqual(response['Content-Type'], exports.XLSX_TYPE)
        self.assertIn('attachment;', response['Content-Disposition'])
        self.assertIn('table.xlsx', response['Content-Disposition'])
        self.assertIn('no-store', response['Cache-Control'])
        parts = self.workbook_xml(response)
        self.assertEqual(len(parts), 6)
        sheet = parts['xl/worksheets/sheet1.xml']
        actual = [[cell.find('s:is/s:t', NS).text or ''
                   for cell in row.findall('s:c', NS)]
                  for row in sheet.findall('s:sheetData/s:row', NS)]
        self.assertEqual(actual, rows)
        self.assertEqual(parts['xl/workbook.xml'].find(
            's:sheets/s:sheet', NS).get('name'), 'Таблица')

    def test_header_filter_freeze_and_column_letters(self):
        parts = self.workbook_xml(self.post([
            [f'Столбец {column}' for column in range(28)], [''] * 28,
        ]))
        sheet = parts['xl/worksheets/sheet1.xml']
        self.assertEqual(sheet.find('s:dimension', NS).get('ref'), 'A1:AB2')
        self.assertEqual(sheet.find('s:autoFilter', NS).get('ref'), 'A1:AB2')
        pane = sheet.find('s:sheetViews/s:sheetView/s:pane', NS)
        self.assertEqual(pane.get('state'), 'frozen')
        self.assertEqual(pane.get('topLeftCell'), 'A2')
        cells = sheet.findall('s:sheetData/s:row/s:c', NS)
        self.assertEqual(cells[27].get('r'), 'AB1')
        self.assertEqual(cells[27].get('s'), '1')
        self.assertEqual(cells[28].get('s'), '0')
        styles = parts['xl/styles.xml']
        self.assertIsNotNone(styles.find('s:fonts/s:font[2]/s:b', NS))
        self.assertTrue(all(node.get('wrapText') == '1'
                            for node in styles.findall(
                                's:cellXfs/s:xf/s:alignment', NS)))

    def test_formula_and_xml_escape_sequences_remain_text(self):
        values = ['=1+1', '+SUM(A1)', '-12', '@A1', '=HYPERLINK("x")',
                  '_x0041_', '_x005F_', 'a\x00b\ud800c\ufffed', 'a\rb']
        parts = self.workbook_xml(self.post([['Значение'],
                                             *[[value] for value in values]]))
        sheet = parts['xl/worksheets/sheet1.xml']
        self.assertEqual(sheet.findall('.//s:f', NS), [])
        cells = sheet.findall('s:sheetData/s:row/s:c', NS)[1:]
        self.assertTrue(all(cell.get('t') == 'inlineStr' for cell in cells))
        text = [cell.find('s:is/s:t', NS).text for cell in cells]
        self.assertEqual(text[:5], values[:5])
        self.assertEqual(text[5:], [
            '_x005F_x0041_', '_x005F_x005F_', 'abcd', 'a_x000D_b',
        ])

    def test_authentication_and_active_employee_are_required(self):
        self.client.logout()
        self.assertEqual(self.post([['A']]).status_code, 401)
        session = self.client.session
        session['site_authenticated'] = True
        session.save()
        self.assertEqual(self.post([['A']]).status_code, 403)
        self.sign_in(self.client)
        EmployeeProfile.objects.filter(pk=self.reader.pk).update(is_active=False)
        self.assertEqual(self.post([['A']]).status_code, 403)

    def test_csrf_is_enforced_and_reader_can_download(self):
        client = Client(enforce_csrf_checks=True)
        self.sign_in(client)
        body = json.dumps({'rows': [['A']]})
        response = client.post(self.url, body, content_type='application/json')
        self.assertEqual(response.status_code, 403)
        token = 'a' * 32
        client.cookies['csrftoken'] = token
        response = client.post(self.url, body, content_type='application/json',
                               HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)

    def test_method_content_type_and_malformed_payloads(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertEqual(self.client.post(self.url, {}).status_code, 415)
        for body in ('{', '[]', 'null', '{}', b'\xff', '[' * 1200):
            with self.subTest(body=repr(body)[:40]):
                response = self.client.post(
                    self.url, body, content_type='application/json',
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn('error', response.json())

    def test_invalid_rows_and_cells_are_rejected(self):
        for rows in (None, {}, [], [[]], ['A'], [['A'], []],
                     [['A'], ['B', 'C']], [['A'], [1]], [['A'], [None]],
                     [['A'], [True]], [['A'], [['nested']]]):
            with self.subTest(rows=rows):
                response = self.post(rows)
                self.assertEqual(response.status_code, 400)
                self.assertIn('error', response.json())

    def test_shape_and_cell_limits_are_enforced(self):
        cases = [
            [['']] * (exports.MAX_ROWS + 1),
            [[''] * (exports.MAX_COLUMNS + 1)],
            [[''] * 51] * 1000,
            [['x' * (exports.MAX_CELL_CHARS + 1)]],
        ]
        for rows in cases:
            with self.subTest(rows=len(rows), columns=len(rows[0])):
                self.assertEqual(self.post(rows).status_code, 413)
        self.assertEqual(self.post([
            ['Значение'], ['x' * exports.MAX_CELL_CHARS],
        ]).status_code, 200)

    def test_body_size_is_checked_before_json_decoding(self):
        body = b'x' * (exports.MAX_BODY_BYTES + 1)
        response = self.client.post(self.url, body,
                                    content_type='application/json')
        self.assertEqual(response.status_code, 413)
        self.assertIn('error', response.json())
        request = RequestFactory().post(
            self.url, body, content_type='application/json',
        )
        request.body  # Cache body before simulating a misleading length header.
        request.META['CONTENT_LENGTH'] = '0'
        with self.assertRaises(exports.ExportError) as caught:
            exports._read_rows(request)
        self.assertEqual(caught.exception.status, 413)

    @override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=16)
    def test_django_body_limit_has_clean_json_error(self):
        response = self.post([['A' * 100]])
        self.assertEqual(response.status_code, 413)
        self.assertIn('error', response.json())
