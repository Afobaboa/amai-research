"""Browser assets refresh by content while retaining Django storage URLs."""
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
from re import escape
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.staticfiles.storage import ManifestStaticFilesStorage
from django.template import Context, Engine, Template
from django.template.loader import render_to_string
from django.test import SimpleTestCase, override_settings

from .templatetags.static_assets import PUBLIC_ASSETS, versioned_static


TAG_MODULE = 'lab.templatetags.static_assets'


class StaticAssetTests(SimpleTestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.asset = Path(directory) / 'app.css'
        self.payload = b'old-content'
        self.asset.write_bytes(self.payload)
        self.finder = self.enterContext(patch(
            f'{TAG_MODULE}.finders.find', return_value=str(self.asset),
        ))

    def test_url_uses_content_hash_and_configured_static_prefix(self):
        with override_settings(STATIC_URL='https://cdn.example/assets/'):
            url = versioned_static('lab/app.css')
        version = sha256(self.payload).hexdigest()[:16]
        self.assertEqual(url, f'https://cdn.example/assets/lab/app.css?v={version}')

    def test_content_change_refreshes_same_size_preserved_mtime_asset(self):
        original_url = versioned_static('lab/app.css')
        metadata = self.asset.stat()
        self.asset.write_bytes(b'new-content')
        os.utime(self.asset, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
        changed_url = versioned_static('lab/app.css')
        self.assertNotEqual(changed_url, original_url)
        self.assertIn(sha256(b'new-content').hexdigest()[:16], changed_url)

    def test_unchanged_content_keeps_url_when_metadata_changes(self):
        original_url = versioned_static('lab/app.css')
        self.asset.write_bytes(self.payload)
        self.assertEqual(versioned_static('lab/app.css'), original_url)

    def test_query_and_fragment_from_storage_are_preserved(self):
        with patch(
            f'{TAG_MODULE}.static',
            return_value='https://cdn.example/app.css?token=public#fragment',
        ):
            url = versioned_static('lab/app.css')
        parts = urlsplit(url)
        self.assertEqual(parts.fragment, 'fragment')
        self.assertEqual(parse_qs(parts.query), {
            'token': ['public'], 'v': [sha256(self.payload).hexdigest()[:16]],
        })

    def test_collected_storage_is_used_when_sources_are_unavailable(self):
        self.finder.return_value = None
        with patch(f'{TAG_MODULE}.staticfiles_storage') as storage:
            storage.open.return_value = BytesIO(b'collected-asset')
            url = versioned_static('lab/app.css')
            storage.open.assert_called_once_with('lab/app.css', 'rb')
        self.assertIn(sha256(b'collected-asset').hexdigest()[:16], url)

    def test_unknown_or_private_paths_are_rejected_before_file_access(self):
        for name in ('../.env', '/etc/passwd', 'lab/.env', 'lab/other.css'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                versioned_static(name)
        self.finder.assert_not_called()

    def test_missing_public_asset_preserves_django_static_behavior(self):
        self.finder.return_value = None
        with patch(f'{TAG_MODULE}.staticfiles_storage') as storage:
            storage.open.side_effect = FileNotFoundError
            self.assertEqual(versioned_static('lab/app.css'), '/static/lab/app.css')

    def test_template_tag_escapes_storage_query_separators(self):
        with patch(f'{TAG_MODULE}.static', return_value='/assets/app.css?public=1'):
            rendered = Template(
                "{% load lab_tags %}{% versioned_static 'lab/app.css' %}",
            ).render(Context())
        self.assertIn('?public=1&amp;v=', rendered)


class StaticManifestTests(SimpleTestCase):
    def manifest_storage(self, directory):
        folder = Path(directory) / 'lab'
        folder.mkdir()
        for name in ('app.css', 'app.abcdef.css'):
            (folder / name).write_bytes(b'collected-production-css')
        storage = ManifestStaticFilesStorage(location=directory)
        storage.hashed_files = {'lab/app.css': 'lab/app.abcdef.css'}
        storage.save_manifest()
        return {
            'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
            'staticfiles': {
                'BACKEND': (
                    'django.contrib.staticfiles.storage.ManifestStaticFilesStorage'
                ),
            },
        }

    def test_production_manifest_filename_and_cdn_url_are_retained(self):
        with TemporaryDirectory() as directory:
            storages = self.manifest_storage(directory)
            with override_settings(
                DEBUG=False, STATIC_ROOT=directory, STORAGES=storages,
                STATIC_URL='https://cdn.example/static/',
            ), patch(f'{TAG_MODULE}.finders.find', return_value=None):
                url = versioned_static('lab/app.css')
        self.assertEqual(
            urlsplit(url)._replace(query='').geturl(),
            'https://cdn.example/static/lab/app.abcdef.css',
        )
        self.assertIn(sha256(b'collected-production-css').hexdigest()[:16], url)


class StaticLayoutTests(SimpleTestCase):
    def test_existing_library_registry_can_render_both_layouts(self):
        engine = Engine(app_dirs=True, libraries={
            'static': 'django.templatetags.static',
            'lab_tags': 'lab.templatetags.lab_tags',
        })
        for name in ('base.html', 'auth_base.html'):
            with self.subTest(template=name):
                html = engine.get_template(f'lab/{name}').render(Context())
                self.assertRegex(
                    html, r'lab/search-select\.css\?v=[0-9a-f]{16}',
                )

    def test_both_layouts_version_styles_and_scripts(self):
        for name in ('base.html', 'auth_base.html'):
            with self.subTest(template=name):
                html = render_to_string(
                    f'lab/{name}', {'employee': {'full_name': 'Автор'}},
                )
                assets = PUBLIC_ASSETS if name == 'base.html' else {
                    path for path in PUBLIC_ASSETS
                    if 'assistant' not in path and 'live-search' not in path
                }
                for asset in assets:
                    self.assertRegex(html, rf'{escape(asset)}\?v=[0-9a-f]{{16}}')
                self.assertLess(
                    html.index('lab/app.css?v='),
                    html.index('lab/search-select.css?v='),
                )
