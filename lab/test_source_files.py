"""Initial source downloads work without the external laboratory archive."""
import hashlib
import json
from pathlib import Path
import tempfile

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from scripts.check_initial_sources import check_sources
from .models import EmployeeProfile, SourceFile
from .source_files import bundled_source_path


class InitialSourceDownloadTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        initial = Path(settings.BASE_DIR) / 'initial_data'
        cls.manifest = json.loads(
            (initial / 'sources/manifest.json').read_text('utf-8'),
        )
        cls.sources = [SourceFile.objects.create(**row) for row in cls.manifest]
        cls.author = EmployeeProfile.objects.create(
            full_name='Источники: автор', role='author',
        )
        cls.reader = EmployeeProfile.objects.create(
            full_name='Источники: читатель', role='reader',
        )

    def sign_in(self, employee):
        session = self.client.session
        session['site_authenticated'] = True
        session['employee_id'] = employee.pk
        session.save()

    def download(self, source):
        return self.client.get(reverse('source_download', args=[source.pk]))

    def test_distribution_contains_all_six_original_sources(self):
        self.assertEqual(check_sources(), 6)

    def test_author_and_reader_can_download_exact_original_bytes(self):
        for employee in (self.author, self.reader):
            self.sign_in(employee)
            for source in self.sources:
                with self.subTest(employee=employee.role, source=source.pk):
                    response = self.download(source)
                    self.assertEqual(response.status_code, 200)
                    payload = b''.join(response.streaming_content)
                    self.assertEqual(hashlib.sha256(payload).hexdigest(),
                                     source.sha256)
                    self.assertIn('attachment;', response['Content-Disposition'])
                    self.assertEqual(response['X-Content-Type-Options'],
                                     'nosniff')
                    response.close()

    def test_download_links_are_visible_in_source_list_and_card(self):
        self.sign_in(self.reader)
        source = self.sources[0]
        target = reverse('source_download', args=[source.pk])
        self.assertContains(self.client.get(reverse('source_list')), target)
        self.assertContains(self.client.get(reverse('source_detail',
                                                   args=[source.pk])), target)

    def test_login_and_active_employee_are_required(self):
        self.assertEqual(self.download(self.sources[0]).status_code, 302)
        self.sign_in(self.reader)
        self.reader.is_active = False
        self.reader.save()
        response = self.download(self.sources[0])
        self.assertRedirects(response, reverse('choose_employee'))

    def test_original_path_and_hash_must_both_be_allowlisted(self):
        self.sign_in(self.reader)
        source = self.sources[0]
        for path in ('/etc/passwd', '../.env', '/app/.env',
                     self.sources[1].original_path):
            with self.subTest(path=path):
                source.original_path = path
                source.save()
                self.assertIsNone(bundled_source_path(source))
                self.assertEqual(self.download(source).status_code, 404)

    def prepare_isolated_bundle(self, directory, payload):
        root = Path(directory)
        folder = root / 'initial_data/sources'
        folder.mkdir(parents=True)
        source = self.sources[0]
        (folder / 'manifest.json').write_text(json.dumps([self.manifest[0]]))
        path = folder / (source.sha256 + '.xlsx')
        if payload is not None:
            path.write_bytes(payload)
        return root, path

    def test_missing_or_corrupted_bundle_never_serves_other_content(self):
        self.sign_in(self.reader)
        for payload in (None, b'changed source'):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as tmp:
                root, _ = self.prepare_isolated_bundle(tmp, payload)
                with override_settings(BASE_DIR=root):
                    self.assertEqual(self.download(self.sources[0]).status_code,
                                     404)

    def test_symlink_outside_bundle_is_not_served(self):
        self.sign_in(self.reader)
        with tempfile.TemporaryDirectory() as tmp:
            root, path = self.prepare_isolated_bundle(tmp, None)
            outside = root / 'outside.xlsx'
            outside.write_bytes(b'private document')
            path.symlink_to(outside)
            with override_settings(BASE_DIR=root):
                self.assertIsNone(bundled_source_path(self.sources[0]))
                self.assertEqual(self.download(self.sources[0]).status_code, 404)
