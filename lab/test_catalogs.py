"""Catalogue workflows and provenance handling, isolated from archive files."""
import hashlib
from datetime import date
from decimal import Decimal
from pathlib import Path
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .catalog_forms import SourceForm
from .models import (
    ChangeEvent, EmployeeProfile, FillingPlanLine, Material, MaterialCategory,
    MaterialLot, Metric,
    MixtureType, RecipeComponent, RecipeGroup, RecipeMatch, RecipeVersion,
    Result, SourceCell, SourceFile, SourceSheet, TestRun, TestSession,
)


@override_settings(DEMO_MODE=True)
class CatalogWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        MaterialCategory.objects.create(name='Полиол')
        cls.author = EmployeeProfile.objects.create(full_name='Каталоги: автор', role='author')
        cls.reader = EmployeeProfile.objects.create(full_name='Каталоги: читатель', role='reader')
        cls.material = Material.objects.create(name='Тестовый полиол')
        cls.lot = MaterialLot.objects.create(
            material=cls.material, lot_code='LOT-001',
            density_g_ml=Decimal('1.05'), price_per_kg=Decimal('200'),
        )
        cls.source = SourceFile.objects.create(title='Паспорт партии', kind='note', original_path='Паспорт партии',
                                               notes='Плотность записана поставщиком.', created_by=cls.author)
        cls.sheet = SourceSheet.objects.create(file=cls.source, name='Показатели')

    def setUp(self):
        self.media = tempfile.TemporaryDirectory(prefix='lims-catalog-media-')
        self.media_override = override_settings(MEDIA_ROOT=self.media.name)
        self.media_override.enable()
        self.addCleanup(self.media.cleanup)
        self.addCleanup(self.media_override.disable)
        self.sign_in(self.author)

    def sign_in(self, employee):
        session = self.client.session
        session['site_authenticated'] = True
        session['employee_id'] = employee.pk
        session.save()

    def source_payload(self, **extra):
        data = {'title': 'Новый паспорт', 'kind': 'note', 'notes': 'Исходный документ поставщика', 'date_system': '1900'}
        data.update(extra)
        return data

    def material_payload(self, **extra):
        data = {'name': 'Материал QA', 'category': 'Полиол',
                'supplier': 'Поставщик QA', 'notes': 'Тестовая запись'}
        data.update(extra)
        return data

    def lot_payload(self, **extra):
        data = {'material': str(self.material.pk), 'lot_code': 'LOT-002', 'supplier': 'Поставщик QA',
                'received_on': '2026-10-01', 'expires_on': '2027-10-01', 'status': 'active',
                'density_g_ml': '', 'price_per_kg': '0', 'source': str(self.source.pk), 'notes': 'Тестовая партия'}
        data.update(extra)
        return data

    def used_recipe(self):
        mixture = MixtureType.objects.create(code='CATALOG-W', title='Зимняя пена')
        recipe = RecipeVersion.objects.create(mixture_type=mixture, version_label='CATALOG-W.1', status='published')
        group = RecipeGroup.objects.create(recipe_version=recipe, label='A', quantity_basis='mass_percent')
        RecipeComponent.objects.create(group=group, position=1, specified_lot=self.lot, quantity_value=100)
        FillingPlanLine.objects.create(recipe_version=recipe, specified_lot=self.lot, mass_g=20, mass_status='recorded')
        return recipe

    def test_author_creates_and_edits_material_identity_with_audit(self):
        response = self.client.post(reverse('material_create'), self.material_payload())
        material = Material.objects.get(name='Материал QA')
        self.assertRedirects(response, reverse('material_detail', args=[material.pk]))
        self.assertEqual(material.category, 'Полиол')
        response = self.client.post(
            reverse('material_edit', args=[material.pk]),
            self.material_payload(supplier='Новый поставщик'),
        )
        self.assertRedirects(response, reverse('material_detail', args=[material.pk]))
        event = ChangeEvent.objects.filter(entity_type='material', entity_id=material.pk).latest('pk')
        self.assertEqual(event.before['supplier'], 'Поставщик QA')
        self.assertEqual(event.after['supplier'], 'Новый поставщик')

    def test_invalid_material_does_not_mutate_or_audit(self):
        for name in ['', ' ']:
            response = self.client.post(reverse('material_create'),
                                        self.material_payload(name=name))
            self.assertEqual(response.status_code, 200)
        self.assertFalse(Material.objects.filter(name='Материал QA').exists())
        self.assertEqual(ChangeEvent.objects.count(), 0)

    def test_lot_properties_unknown_density_zero_price_and_status_change(self):
        response = self.client.post(reverse('lot_create'), self.lot_payload())
        lot = MaterialLot.objects.get(lot_code='LOT-002')
        self.assertRedirects(response, reverse('lot_detail', args=[lot.pk]))
        self.assertIsNone(lot.effective_density_g_ml)
        self.assertEqual(lot.effective_price_per_kg, Decimal('0'))
        self.client.post(reverse('lot_edit', args=[lot.pk]), self.lot_payload(status='quarantine', density_g_ml='1.07'))
        lot.refresh_from_db()
        self.assertEqual(lot.status, 'quarantine')
        self.assertEqual(lot.effective_density_g_ml, Decimal('1.07'))
        self.assertEqual(ChangeEvent.objects.filter(entity_type='lot', entity_id=lot.pk).count(), 2)

    def test_invalid_lot_properties_do_not_mutate_or_audit(self):
        for density, price in [('0', '1'), ('-1', '1'), ('1', '-1')]:
            response = self.client.post(reverse('lot_create'), self.lot_payload(
                density_g_ml=density, price_per_kg=price,
            ))
            self.assertEqual(response.status_code, 200)
        self.assertFalse(MaterialLot.objects.filter(lot_code='LOT-002').exists())
        self.assertEqual(ChangeEvent.objects.count(), 0)

    def test_used_lot_identity_is_preserved_and_traceability_does_not_duplicate_cards(self):
        recipe = self.used_recipe()
        card = TestSession.objects.create(identifier='LOT-CARD-001')
        RecipeMatch.objects.create(session=card, recipe_version=recipe, status='confirmed')
        response = self.client.get(reverse('lot_detail', args=[self.lot.pk]))
        self.assertEqual(list(response.context['recipes']), [recipe])
        self.assertEqual(list(response.context['cards']), [card])
        response = self.client.post(reverse('lot_edit', args=[self.lot.pk]), self.lot_payload(lot_code='CHANGED'))
        self.assertEqual(response.status_code, 200)
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.lot_code, 'LOT-001')
        self.assertContains(response, 'Номер использованной партии менять нельзя')

    def test_note_and_link_sources_do_not_invent_file_hashes(self):
        response = self.client.post(reverse('source_create'), self.source_payload())
        source = SourceFile.objects.get(title='Новый паспорт')
        self.assertRedirects(response, reverse('source_detail', args=[source.pk]))
        self.assertEqual(source.sha256, '')
        self.assertFalse(source.attachment)
        response = self.client.post(reverse('source_create'), self.source_payload(title='Документ по ссылке', kind='link',
                                                                                 url='https://example.com/protocol.pdf', notes='Протокол'))
        link = SourceFile.objects.get(title='Документ по ссылке')
        self.assertRedirects(response, reverse('source_detail', args=[link.pk]))
        self.assertEqual(link.url, 'https://example.com/protocol.pdf')
        self.assertEqual(link.sha256, '')

    def test_source_upload_has_real_hash_and_protected_unchanged_download(self):
        payload = b'Primary laboratory document\nNot an Excel import.'
        response = self.client.post(reverse('source_create'), self.source_payload(kind='file',
                                        upload=SimpleUploadedFile('passport.txt', payload, content_type='text/plain')))
        source = SourceFile.objects.get(title='Новый паспорт')
        self.assertRedirects(response, reverse('source_detail', args=[source.pk]))
        self.assertEqual(source.sha256, hashlib.sha256(payload).hexdigest())
        self.assertEqual(source.original_path, 'passport.txt')
        self.assertTrue(Path(source.attachment.path).is_relative_to(Path(self.media.name)))
        self.sign_in(self.reader)
        downloaded = self.client.get(reverse('source_download', args=[source.pk]))
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(b''.join(downloaded.streaming_content), payload)
        self.assertIn('attachment;', downloaded['Content-Disposition'])
        downloaded.close()
        self.client.post(reverse('logout'))
        self.assertEqual(self.client.get(reverse('source_download', args=[source.pk])).status_code, 302)

    def test_uploaded_evidence_cannot_be_replaced_in_place(self):
        self.client.post(reverse('source_create'), self.source_payload(kind='file',
                         upload=SimpleUploadedFile('original.txt', b'original')))
        source = SourceFile.objects.get(title='Новый паспорт')
        digest = source.sha256
        response = self.client.post(reverse('source_edit', args=[source.pk]), self.source_payload(kind='file',
                                   upload=SimpleUploadedFile('replacement.txt', b'replacement')))
        self.assertEqual(response.status_code, 200)
        source.refresh_from_db()
        self.assertEqual(source.sha256, digest)
        with source.attachment.open('rb') as handle:
            self.assertEqual(handle.read(), b'original')

    def test_upload_size_and_required_source_content_are_validated(self):
        upload = SimpleUploadedFile('too-large.txt', b'small body with declared oversized length')
        upload.size = 25 * 1024 * 1024 + 1
        form = SourceForm(self.source_payload(kind='file'), {'upload': upload})
        self.assertFalse(form.is_valid())
        self.assertIn('upload', form.errors)
        for data in [self.source_payload(kind='link', url=''), self.source_payload(kind='file'), self.source_payload(notes='')]:
            response = self.client.post(reverse('source_create'), data)
            self.assertEqual(response.status_code, 200)
        self.assertEqual(SourceFile.objects.count(), 1)

    def test_sections_and_cells_preserve_raw_text_and_formula_provenance(self):
        response = self.client.post(reverse('source_sheet_create', args=[self.source.pk]), {'name': 'Плотность'})
        self.assertRedirects(response, reverse('source_detail', args=[self.source.pk]))
        sheet = SourceSheet.objects.get(file=self.source, name='Плотность')
        text = '  Плотность партии\nуказана поставщиком  '
        url = reverse('source_cell_create', args=[self.source.pk, sheet.pk])
        response = self.client.post(url, {'address': 'B12', 'cell_kind': 'text', 'raw_value': text})
        if response.status_code == 200:
            self.assertFalse(response.context['form'].errors)
        self.assertRedirects(response, reverse('source_detail', args=[self.source.pk]))
        self.assertEqual(SourceCell.objects.get(sheet=sheet, address='B12').raw_value, text)
        self.assertRedirects(self.client.post(url, {'address': 'B13', 'cell_kind': 'formula',
                             'formula_text': '=SUM(B1:B12)', 'cached_value': '1.05'}), reverse('source_detail', args=[self.source.pk]))
        cell = SourceCell.objects.get(sheet=sheet, address='B13')
        self.assertEqual(cell.formula_text, '=SUM(B1:B12)')
        self.assertIsNone(cell.raw_value)
        self.assertEqual(cell.cached_value, '1.05')
        self.assertEqual(ChangeEvent.objects.filter(entity_type='source', entity_id=self.source.pk).count(), 3)

    def test_source_cells_reject_ambiguous_values_cross_source_and_duplicates(self):
        url = reverse('source_cell_create', args=[self.source.pk, self.sheet.pk])
        for data in [{'address': 'A1', 'cell_kind': 'formula', 'raw_value': '1', 'formula_text': '=1'},
                     {'address': 'A1', 'cell_kind': 'text', 'cached_value': '1'}]:
            self.assertEqual(self.client.post(url, data).status_code, 200)
        other = SourceFile.objects.create(title='Другой документ', kind='note', original_path='Другой', notes='Запись')
        self.assertEqual(self.client.get(reverse('source_cell_create', args=[other.pk, self.sheet.pk])).status_code, 404)
        SourceCell.objects.create(sheet=self.sheet, address='A1', cell_kind='text', raw_value='исходное')
        response = self.client.post(url, {'address': 'A1', 'cell_kind': 'text', 'raw_value': 'замена'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SourceCell.objects.get(sheet=self.sheet, address='A1').raw_value, 'исходное')

    def test_source_cells_paginate_and_deep_links_select_target_page(self):
        SourceCell.objects.bulk_create([
            SourceCell(sheet=self.sheet, address=f'A{index}', cell_kind='text',
                       raw_value=f'Первичный фрагмент {index}')
            for index in range(1, 152)
        ])
        url = reverse('source_detail', args=[self.source.pk])
        first = self.client.get(url)
        self.assertEqual(first.context['selected_sheet'], self.sheet)
        self.assertEqual(len(first.context['cells_page']), 150)
        self.assertNotContains(first, 'Первичный фрагмент 151')
        last = self.client.get(url, {'sheet': self.sheet.pk, 'page': 2})
        self.assertContains(last, 'Первичный фрагмент 151')
        self.assertEqual(len(last.context['cells_page']), 1)
        target = self.client.get(url, {'sheet': self.sheet.pk, 'cell': 'A151'})
        self.assertEqual(target.context['cells_page'].number, 2)
        self.assertContains(target, 'class="source-cell-target"')

    def test_source_sheet_selection_stays_within_the_selected_source(self):
        other_sheet = SourceSheet.objects.create(file=self.source, name='Другой лист')
        SourceCell.objects.create(
            sheet=other_sheet, address='B12', cell_kind='text',
            raw_value='Значение второго листа',
        )
        url = reverse('source_detail', args=[self.source.pk])
        selected = self.client.get(url, {'sheet': other_sheet.pk, 'cell': 'B12'})
        self.assertEqual(selected.context['selected_sheet'], other_sheet)
        self.assertContains(selected, 'Значение второго листа')
        other_source = SourceFile.objects.create(title='Другой источник')
        foreign_url = reverse('source_detail', args=[other_source.pk])
        self.assertEqual(self.client.get(foreign_url, {'sheet': other_sheet.pk}).status_code, 404)

    def test_reader_can_view_catalogues_but_all_direct_posts_are_denied(self):
        self.sign_in(self.reader)
        for name in ['material_list', 'lot_list', 'source_list', 'metric_list', 'mixture_list']:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        routes = [('material_create', []), ('material_edit', [self.material.pk]), ('lot_create', []),
                  ('lot_edit', [self.lot.pk]), ('source_create', []), ('source_edit', [self.source.pk]),
                  ('source_sheet_create', [self.source.pk]), ('source_cell_create', [self.source.pk, self.sheet.pk]),
                  ('metric_create', []), ('mixture_create', [])]
        for name, args in routes:
            self.assertEqual(self.client.post(reverse(name, args=args), {}).status_code, 403, name)
        self.assertEqual(ChangeEvent.objects.count(), 0)

    def test_used_metric_unit_type_and_code_cannot_reinterpret_old_results(self):
        metric = Metric.objects.create(code='CAT-DENSITY', label='Плотность', unit_code='г/мл', value_kind='numeric')
        card = TestSession.objects.create(identifier='METRIC-CARD')
        run = TestRun.objects.create(session=card, scope='Плотность')
        Result.objects.create(test_run=run, metric=metric, numeric_value=1, origin='recorded', value_state='present')
        response = self.client.post(reverse('metric_edit', args=[metric.pk]), {'code': 'CAT-DENSITY', 'label': 'Плотность',
                                                                              'unit_code': 'кг/м³', 'value_kind': 'numeric'})
        self.assertEqual(response.status_code, 200)
        metric.refresh_from_db()
        self.assertEqual(metric.unit_code, 'г/мл')

    def test_author_can_create_metrics_and_mixture_types(self):
        response = self.client.post(reverse('metric_create'), {'code': 'CAT-YIELD', 'label': 'Выход пены', 'unit_code': 'л', 'value_kind': 'numeric'})
        self.assertRedirects(response, reverse('metric_list'))
        self.assertTrue(Metric.objects.filter(code='CAT-YIELD').exists())
        response = self.client.post(reverse('mixture_create'), {'code': 'CAT-WINTER-PLUS', 'title': 'Зимняя улучшенная пена'})
        mixture = MixtureType.objects.get(code='CAT-WINTER-PLUS')
        self.assertRedirects(response, reverse('mixture_detail', args=[mixture.pk]))

    def test_catalogue_search_and_filter(self):
        self.client.post(reverse('lot_create'), self.lot_payload(status='quarantine'))
        response = self.client.get(reverse('lot_list'), {'status': 'quarantine', 'q': 'LOT-002'})
        self.assertEqual([lot.lot_code for lot in response.context['lots']], ['LOT-002'])
        self.assertContains(self.client.get(reverse('source_list'), {'q': 'Паспорт'}), 'Паспорт партии')
