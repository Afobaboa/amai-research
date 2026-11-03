"""Normalize archive materials and retain the provenance of catalog defaults."""
from collections import defaultdict
from decimal import Decimal

from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.core.management.base import CommandError

from . import models as lab


def archive_rows(data):
    for source in data['files']:
        for sheet in source['sheets']:
            recipe = sheet.get('recipe')
            if recipe:
                for group in recipe['groups']:
                    yield from group['components']
                yield from recipe.get('filling', [])


def row_name(row):
    return row.get('name') or row.get('material_name')


def legacy_name(row):
    return ' '.join(str(row.get('name_raw') or row_name(row) or '').split())


def decimal_text(value):
    return None if value is None else str(Decimal(str(value)))


def property_source_cell(row, cells, source):
    if not row.get('source_sheet'):
        return cells.get(row.get('cell'))
    if row.get('source_path', source.original_path) != source.original_path:
        raise CommandError('Цена с предыдущего листа должна быть из той же книги.')
    try:
        return lab.SourceCell.objects.get(
            sheet__file=source, sheet__name=row['source_sheet'],
            address=row['cell'],
        )
    except lab.SourceCell.DoesNotExist:
        raise CommandError('Не найдена исходная цена с предыдущего листа.') from None


def source_bindings(data, kind):
    result = {}
    for source in data['files']:
        for sheet in source['sheets']:
            recipe = sheet.get('recipe')
            if not recipe:
                continue
            rows = recipe.get('filling', []) if kind == 'filling' else [
                row for group in recipe['groups'] for row in group['components']
            ]
            fields = ('label_cell', 'mass_cell') if kind == 'filling' else (
                'name_cell', 'quantity_cell',
            )
            for row in rows:
                key = (source['path'], source['sha256'], sheet['name'],
                       *(row.get(field) for field in fields))
                result[key] = row
    return result


def existing_source_materials(data, kind):
    bindings = source_bindings(data, kind)
    model = lab.FillingPlanLine if kind == 'filling' else lab.RecipeComponent
    prefix = 'recipe_version__' if kind == 'filling' else 'group__recipe_version__'
    prefix += 'source_sheet__'
    fields = ('label_cell__address', 'mass_cell__address') if kind == 'filling' else (
        'name_cell__address', 'quantity_cell__address',
    )
    source_filter = Q(pk__in=[])
    for source in data['files']:
        source_filter |= Q(**{prefix + 'file__original_path': source['path'],
                              prefix + 'file__sha256': source['sha256']})
    rows = model.objects.filter(source_filter).values_list(
        'unbatched_material_id', 'unbatched_material__name',
        prefix + 'file__original_path', prefix + 'file__sha256',
        prefix + 'name', *fields,
    )
    owned, invalid = set(), set()
    for material_id, name, *location in rows:
        expected = bindings.get(tuple(location))
        if material_id and expected and name in {
            row_name(expected), legacy_name(expected),
        }:
            owned.add(material_id)
            if expected.get('invalid_material_label'):
                invalid.add(material_id)
    return owned, invalid


class ArchiveMaterials:
    def __init__(self, data, author):
        self.author = author
        self.materials = {}
        self.lots = {}
        self.candidates = defaultdict(dict)
        self.aliases = defaultdict(set)
        for row in archive_rows(data):
            name = row_name(row)
            if name:
                self.aliases[name].add(legacy_name(row))
        self.edits = self.catalog_edits()
        self.source_owned, self.invalid_labels = set(), set()
        for kind in ('component', 'filling'):
            owned, invalid = existing_source_materials(data, kind)
            self.source_owned.update(owned)
            self.invalid_labels.update(invalid)

    def catalog_edits(self):
        edited = defaultdict(set)
        events = lab.ChangeEvent.objects.filter(
            entity_type__in=['material', 'lot'], action__in=['created', 'updated'],
        )
        for event in events:
            for field, value in event.after.items():
                if value != event.before.get(field):
                    edited[(event.entity_type, event.entity_id)].add(field)
        return edited

    def material(self, name):
        if not name:
            return None
        if name not in self.materials:
            material = lab.Material.objects.filter(name=name).first()
            if material is None:
                material = lab.Material.objects.filter(
                    name__in=self.aliases[name], pk__in=self.source_owned,
                ).order_by('pk').first()
            if material is None:
                material = lab.Material.objects.create(name=name)
            elif material.name != name and (
                'name' not in self.edits[('material', material.pk)]
            ):
                before = {'name': material.name}
                material.name = name
                material.save(update_fields=['name'])
                self.audit(material, 'material', before, {'name': name})
            elif material.name != name:
                material = lab.Material.objects.create(name=name)
            self.materials[name] = material
        return self.materials[name]

    def binding(self, row, source):
        material = self.material(row_name(row))
        code = row.get('lot_code')
        if not material or not code:
            return {'unbatched_material': material, 'specified_lot': None}
        key = material.pk, code
        if key not in self.lots:
            self.lots[key], _ = lab.MaterialLot.objects.get_or_create(
                material=material, lot_code=code,
                defaults={'source': source, 'notes': row.get('lot_notes', '')},
            )
        return {'unbatched_material': None, 'specified_lot': self.lots[key]}

    def observe(self, row, binding, recipe, cells):
        lot = binding['specified_lot']
        if lot is None:
            return
        rank = (str(recipe.recorded_on or ''), recipe.source_sheet.file.original_path,
                recipe.source_sheet.name)
        for prop in row.get('properties', []):
            field = {'density_g_ml': 'density_g_ml',
                     'source_price_per_kg': 'price_per_kg'}.get(prop['code'])
            if field and (Decimal(prop['value']) > 0 or field == 'price_per_kg'):
                cell = property_source_cell(prop, cells, recipe.source_sheet.file)
                candidate = {'value': prop['value'], 'source_cell_id': cell.pk,
                             'recorded_on': str(recipe.recorded_on or ''),
                             'rank': list(rank),
                             'origin': prop.get('origin', 'recorded'),
                             'provenance_note': prop.get('provenance_note', '')}
                self.offer(lot, field, rank, candidate)

    def offer(self, target, field, rank, candidate):
        key = 'lot', target.pk
        previous = self.candidates[key].get(field)
        if previous is None or rank > previous[0]:
            self.candidates[key][field] = rank, target, candidate

    def finish(self):
        for name, material in self.materials.items():
            self.merge_aliases(name, material)
        for key, fields in self.candidates.items():
            self.apply_defaults(key, fields)
        self.remove_invalid_labels()

    def remove_invalid_labels(self):
        invalid = lab.Material.objects.filter(pk__in=self.invalid_labels)
        for material in invalid:
            if self.edits[('material', material.pk)] or any(
                getattr(material, field) not in (None, '') for field in
                ('category', 'notes')
            ):
                continue
            if not self.referenced(material) and not material.lots.exists():
                self.audit(material, 'material', {'name': material.name},
                           {'invalid_material_label': True})
                material.delete()

    def apply_defaults(self, key, fields):
        target = next(iter(fields.values()))[1]
        target.refresh_from_db()
        snapshot = dict(target.import_snapshot)
        before, after = {}, {}
        for field, (rank, _, candidate) in fields.items():
            if self.can_update(key, target, field, snapshot, rank):
                before[field] = decimal_text(getattr(target, field))
                setattr(target, field, Decimal(candidate['value']))
                after[field] = candidate['value']
                if field == 'price_per_kg':
                    target.price_currency = 'EUR'
                    after['price_currency'] = 'EUR'
                snapshot[field] = candidate
        if snapshot != target.import_snapshot:
            target.import_snapshot = snapshot
            target.save()
            if any(before[field] is None or Decimal(before[field]) !=
                   Decimal(after[field]) for field in before):
                self.audit(target, key[0], before, after)

    def can_update(self, key, target, field, snapshot, rank):
        edited = self.edits[key]
        if field in edited or (field == 'price_per_kg' and (
            'price_currency' in edited
        )):
            return False
        previous = snapshot.get(field)
        current = getattr(target, field)
        if previous is None:
            return current is None
        if rank < self.snapshot_rank(previous):
            return False
        if field == 'price_per_kg' and target.price_currency != 'EUR':
            return False
        return current is not None and current == Decimal(previous['value'])

    def snapshot_rank(self, snapshot):
        if 'rank' in snapshot:
            return tuple(snapshot['rank'])
        cell = lab.SourceCell.objects.filter(
            pk=snapshot.get('source_cell_id'),
        ).select_related('sheet__file').first()
        return (snapshot.get('recorded_on', ''),
                cell.sheet.file.original_path if cell else '',
                cell.sheet.name if cell else '')

    def merge_aliases(self, name, target):
        aliases = lab.Material.objects.filter(
            name__in=self.aliases[name], pk__in=self.source_owned,
        )
        for alias in aliases.exclude(pk=target.pk):
            if self.referenced(alias) or alias.lots.exists():
                continue
            self.retain_details(alias, target)
            try:
                alias.delete()
            except ProtectedError:
                continue

    def referenced(self, alias):
        if alias.recipe_components.exists() or alias.filling_lines.exists():
            return True
        for template in lab.RecipeTemplate.objects.all():
            for rows in template.definition.values():
                if isinstance(rows, list) and any(
                    row.get('material') == alias.pk for row in rows
                    if isinstance(row, dict)
                ):
                    return True
        return False

    def retain_details(self, alias, target):
        before, after = {}, {}
        for field in ('category', 'notes'):
            old, new = getattr(target, field), getattr(alias, field)
            if new not in (None, '') and old in (None, ''):
                before[field], after[field] = str(old or ''), str(new)
                setattr(target, field, new)
                if field in self.edits[('material', alias.pk)]:
                    self.edits[('material', target.pk)].add(field)
        if after:
            target.save()
            self.audit(target, 'material', before, after)
        self.audit(target, 'material', {'merged_id': alias.pk, 'name': alias.name},
                   {'name': target.name, 'preserved_catalog': {
                       field: str(getattr(alias, field))
                       if getattr(alias, field) is not None else '' for field in
                       ('category', 'notes')
                   }})

    def audit(self, target, kind, before, after):
        lab.ChangeEvent.objects.create(
            employee=self.author, entity_type=kind, entity_id=target.pk,
            action='imported', summary='Уточнены данные из исходного Excel',
            before=before, after=after,
        )
