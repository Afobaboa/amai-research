"""Serve uploaded documents and the checked-in initial Excel sources."""
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re

from django.conf import settings
from django.http import FileResponse, Http404


def bundled_source_path(source):
    if source.kind != 'file' or not re.fullmatch(r'[0-9a-f]{64}', source.sha256):
        return None
    root = Path(settings.BASE_DIR) / 'initial_data' / 'sources'
    try:
        manifest = json.loads((root / 'manifest.json').read_text('utf-8'))
    except (OSError, ValueError):
        return None
    allowed = any(row['original_path'] == source.original_path
                  and row['sha256'] == source.sha256 for row in manifest)
    if not allowed:
        return None
    path = (root / (source.sha256 + '.xlsx')).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        return None
    return path


def download_available(source):
    return bool(source.attachment or bundled_source_path(source))


def _open_document(source):
    if source.attachment:
        try:
            return source.attachment.open('rb')
        except FileNotFoundError:
            raise Http404('Файл источника не найден.') from None
    path = bundled_source_path(source)
    if path is None:
        raise Http404('Вложение отсутствует.')
    try:
        handle = path.open('rb')
    except OSError:
        raise Http404('Файл источника не найден.') from None
    if hashlib.file_digest(handle, 'sha256').hexdigest() != source.sha256:
        handle.close()
        raise Http404('Контрольная сумма файла источника не совпадает.')
    handle.seek(0)
    return handle


def download_response(source):
    response = FileResponse(
        _open_document(source), as_attachment=True,
        filename=PureWindowsPath(source.original_path).name,
    )
    response['X-Content-Type-Options'] = 'nosniff'
    return response
