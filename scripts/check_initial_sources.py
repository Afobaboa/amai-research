#!/usr/bin/env python3
"""Verify that the standalone LIMS distribution includes its initial Excel."""
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def check_categories(initial, data):
    categories = json.loads((initial / 'material-categories.json').read_text('utf-8'))
    names = {row['name'] for row in categories['categories']}
    mapped = categories['materials']
    materials = set()
    for file in data['files']:
        for sheet in file['sheets']:
            recipe = sheet.get('recipe', {})
            for group in recipe.get('groups', []):
                materials.update(row['name'] for row in group['components'])
            materials.update(row['material_name'] for row in
                             recipe.get('filling', []) if row.get('material_name'))
    if (categories.get('schema_version') != 1 or
        {row['name'] for row in mapped} != materials or
        len(mapped) != len(materials) or
        any(row['category'] not in names for row in mapped)):
        raise ValueError('Material categories do not match the initial dataset.')


def check_sources(root=ROOT):
    initial = root / 'initial_data'
    data = json.loads((initial / 'winter.json').read_text('utf-8'))
    check_categories(initial, data)
    manifest = json.loads((initial / 'sources/manifest.json').read_text('utf-8'))
    expected = {(row['path'], row['sha256']) for row in data['files']}
    actual = {(row['original_path'], row['sha256']) for row in manifest}
    if actual != expected or len(manifest) != len(expected):
        raise ValueError('Source manifest does not match the initial dataset.')
    for original_path, digest in sorted(expected):
        path = initial / 'sources' / (digest + '.xlsx')
        if not path.resolve().is_relative_to((initial / 'sources').resolve()):
            raise ValueError('Bundled source escapes its directory.')
        with path.open('rb') as handle:
            if hashlib.file_digest(handle, 'sha256').hexdigest() != digest:
                raise ValueError('Source checksum mismatch: ' + original_path)
    return len(expected)


if __name__ == '__main__':
    print(f'Initial Excel sources verified: {check_sources()}')
