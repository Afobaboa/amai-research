"""Seed reviewed functional groups and retain explicit catalogue edits."""
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import CommandError

from .models import ChangeEvent, Material, MaterialCategory


CATEGORY_PATH = Path(settings.BASE_DIR) / 'initial_data/material-categories.json'


def sync_material_categories():
    names = Material.objects.order_by().exclude(category='').values_list(
        'category', flat=True,
    ).distinct()
    for name in names:
        if name.strip():
            MaterialCategory.objects.get_or_create(name=name)


def _user_edits(kind, field):
    events = ChangeEvent.objects.filter(
        entity_type=kind, action__in=['created', 'updated'],
    )
    return {event.entity_id for event in events if field in event.after
            and event.after[field] != event.before.get(field)}


def _import_event(author, entity, kind, before, after):
    ChangeEvent.objects.create(
        employee=author, entity_type=kind, entity_id=entity.pk,
        action='imported', summary='Заполнены категории исходных материалов',
        before=before, after=after,
    )


def _renamed_categories():
    result = {}
    events = ChangeEvent.objects.filter(
        entity_type='material_category', action='updated',
    ).order_by('created_at', 'pk')
    for event in events:
        old, new = event.before.get('name'), event.after.get('name')
        if old and new and old != new:
            category = MaterialCategory.objects.filter(pk=event.entity_id).first()
            if category:
                result[old] = category
    return result


def _seed_categories(rows, author):
    renamed = _renamed_categories()
    edited_descriptions = _user_edits('material_category', 'description')
    targets, created_count = {}, 0
    for row in rows:
        category = renamed.get(row['name'])
        created = False
        if category is None:
            category, created = MaterialCategory.objects.get_or_create(
                name=row['name'], defaults={'description': row['description']},
            )
        if created:
            created_count += 1
            _import_event(author, category, 'material_category', {}, row)
        elif not category.description and category.pk not in edited_descriptions:
            category.description = row['description']
            category.save(update_fields=['description'])
            _import_event(author, category, 'material_category',
                          {'description': ''}, {'description': row['description']})
        targets[row['name']] = category
    return targets, created_count


def seed_material_categories(author=None):
    data = json.loads(CATEGORY_PATH.read_text(encoding='utf-8'))
    if data.get('schema_version') != 1:
        raise CommandError('Неизвестная версия категорий исходных материалов.')
    targets, created_count = _seed_categories(data['categories'], author)
    edited_members = _user_edits('material', 'category')
    classified = 0
    for row in data['materials']:
        category = targets[row['category']]
        materials = Material.objects.filter(name=row['name'], category='')
        for material in materials.exclude(pk__in=edited_members):
            material.category = category.name
            material.save(update_fields=['category'])
            _import_event(author, material, 'material', {'category': ''},
                          {'category': category.name})
            classified += 1
    sync_material_categories()
    return {'categories_created': created_count, 'materials_classified': classified}
