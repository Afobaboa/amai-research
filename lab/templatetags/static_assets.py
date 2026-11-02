"""Content-versioned URLs for the application's public browser assets."""
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from django import template
from django.contrib.staticfiles import finders
from django.contrib.staticfiles.storage import staticfiles_storage
from django.templatetags.static import static


register = template.Library()
PUBLIC_ASSETS = frozenset({
    'lab/amai-theme.css', 'lab/amai-components.css',
    'lab/app.css', 'lab/app.js',
    'lab/live-search.js',
    'lab/search-select.css', 'lab/search-select.js',
    'lab/assistant.css', 'lab/assistant.js',
})


@lru_cache(maxsize=128)
def _file_version(filename, modified_ns, changed_ns, size):
    # File metadata is part of the cache key, including preserved-mtime edits.
    return sha256(Path(filename).read_bytes()).hexdigest()[:16]


def _asset_version(name):
    filename = finders.find(name)
    if filename:
        path = Path(filename)
        metadata = path.stat()
        return _file_version(
            str(path), metadata.st_mtime_ns, metadata.st_ctime_ns,
            metadata.st_size,
        )
    # Collected-only deployments may not retain the original application files.
    with staticfiles_storage.open(name, 'rb') as asset:
        return sha256(asset.read()).hexdigest()[:16]


@register.simple_tag
def versioned_static(name):
    if name not in PUBLIC_ASSETS:
        raise ValueError('Only public application assets can be versioned.')
    url = static(name)
    try:
        version = _asset_version(name)
    except OSError:
        # Match Django's static tag when a file is missing or unreadable.
        return url
    parts = urlsplit(url)
    query = f'{parts.query}&v={version}' if parts.query else f'v={version}'
    return urlunsplit(parts._replace(query=query))
