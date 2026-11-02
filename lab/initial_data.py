"""Load the reviewed winter archive snapshot without opening original Excel."""
import json
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path

from django.conf import settings
from django.core.management.base import CommandError
from django.db import transaction

from . import models as lab
from .initial_cleanup import remove_demo
from .initial_materials import ArchiveMaterials, property_source_cell, row_name
from .categories import seed_material_categories, sync_material_categories


DATA_PATH = Path(settings.BASE_DIR) / 'initial_data' / 'winter.json'
PRECISION = Decimal('0.00000001')


def number(value):
    if value is None:
        return None
    return Decimal(str(value)).quantize(PRECISION, rounding=ROUND_HALF_EVEN)


def event(author, entity, kind, title):
    lab.ChangeEvent.objects.create(
        employee=author, entity_type=kind, entity_id=entity.pk,
        action='imported', summary=title[:255],
        after={'source': 'winter-archive-v1'},
    )


def employees():
    author = None
    for name, role in [('Гущин Даниил', 'author'), ('Иван Иванов', 'reader')]:
        profile, _ = lab.EmployeeProfile.objects.update_or_create(
            full_name=name, defaults={'role': role, 'is_active': True},
        )
        if role == 'author':
            author = profile
    return author


class ArchiveLoader:
    def __init__(self, author):
        self.author = author
        self.mixtures = {}
        self.materials = {}
        self.recipes = {}
        self.pending_matches = []
        self.counts = {'recipes': 0, 'tests': 0, 'sources': 0}

    def load(self, data):
        self.catalog = ArchiveMaterials(data, self.author)
        for mixture in data['mixtures']:
            self.mixtures[mixture['code']], _ = lab.MixtureType.objects.get_or_create(
                code=mixture['code'], defaults={'title': mixture['title']},
            )
        for source in data['files']:
            self.load_file(source)
        self.link_recipes()
        self.catalog.finish()
        seed_material_categories(self.author)
        sync_material_categories()
        return self.counts

    def load_file(self, data):
        source, created = lab.SourceFile.objects.get_or_create(
            original_path=data['path'], sha256=data['sha256'],
            defaults={
                'title': data['title'], 'kind': 'file',
                'date_system': data.get('date_system', '1900'),
                'notes': data.get('notes', ''), 'created_by': self.author,
            },
        )
        if created:
            self.counts['sources'] += 1
            event(self.author, source, 'source', 'Перенесён источник: ' + source.title)
        self.current_source = source
        for item in data['sheets']:
            self.load_sheet(source, item)

    def load_sheet(self, source, data):
        sheet, _ = lab.SourceSheet.objects.get_or_create(
            file=source, name=data['name'],
        )
        existing = {cell.address: cell for cell in sheet.cells.all()}
        self.validate_cells(sheet, existing, data['cells'])
        new = [lab.SourceCell(sheet=sheet, address=address, **cell)
               for address, cell in data['cells'].items() if address not in existing]
        lab.SourceCell.objects.bulk_create(new)
        cells = {cell.address: cell for cell in sheet.cells.all()}
        if data.get('recipe'):
            self.load_recipe(sheet, cells, data['recipe'])
        if data.get('test'):
            self.load_test(sheet, cells, data['test'])

    def validate_cells(self, sheet, existing, expected):
        for address, row in expected.items():
            current = existing.get(address)
            if current and any(getattr(current, key) != value
                               for key, value in row.items()):
                raise CommandError(
                    f'Исходный фрагмент изменён: {sheet.name}!{address}. '
                    'Сверьте запись с Excel; начальные данные не обновлены.'
                )

    def material(self, name):
        return self.catalog.material(name)

    def load_recipe(self, sheet, cells, data):
        recipe = lab.RecipeVersion.objects.filter(
            source_sheet=sheet, version_label=data['version_label'],
        ).order_by('pk').first()
        if recipe is None:
            recipe = lab.RecipeVersion.objects.create(
                source_sheet=sheet, mixture_type=self.mixtures[data['mixture_code']],
                version_label=data['version_label'], title=data['title'],
                recorded_on=data.get('recorded_on'), status='published',
                notes=data.get('notes', ''), created_by=self.author,
                batch_mass_g=number(data.get('batch_mass_g') or '1000'),
            )
            self.recipe_rows(recipe, cells, data)
            event(self.author, recipe, 'recipe', 'Перенесена рецептура ' + recipe.title)
            self.counts['recipes'] += 1
        else:
            self.upgrade_recipe(recipe, cells, data)
        self.recipes[(sheet.file.original_path, sheet.name)] = recipe

    def upgrade_recipe(self, recipe, cells, data):
        for group in data['groups']:
            for row in group['components']:
                component = lab.RecipeComponent.objects.filter(
                    group__recipe_version=recipe,
                    name_cell=cells.get(row.get('name_cell')),
                    quantity_cell=cells.get(row.get('quantity_cell')),
                ).first()
                if component:
                    self.upgrade_row(component, 'component', cells, row, recipe)
        for row in data.get('filling', []):
            line = recipe.filling_plan.filter(
                label_cell=cells.get(row.get('label_cell')),
                mass_cell=cells.get(row.get('mass_cell')),
            ).first()
            if line:
                self.upgrade_row(line, 'filling_plan_line', cells, row, recipe)
        self.upgrade_notes(recipe, data)

    def upgrade_row(self, owner, field, cells, data, recipe):
        name = row_name(data)
        current = owner.specified_lot.material if owner.specified_lot_id else (
            owner.unbatched_material)
        raw = ' '.join(str(data.get('name_raw') or '').split())
        expected = {name, raw}
        if current is None and name:
            return
        if field == 'filling_plan_line' and owner.input_group_id and name:
            return
        if current and current.name not in expected:
            return
        if owner.specified_lot_id and (
            owner.specified_lot.lot_code != data.get('lot_code')
        ):
            return
        binding = self.catalog.binding(data, recipe.source_sheet.file)
        if owner.unbatched_material_id != getattr(
            binding['unbatched_material'], 'pk', None,
        ) or owner.specified_lot_id != getattr(binding['specified_lot'], 'pk', None):
            for key, value in binding.items():
                setattr(owner, key, value)
            owner.save(update_fields=list(binding))
        self.catalog.observe(data, binding, recipe, cells)
        self.properties(field, owner, cells, data.get('properties', []))

    def upgrade_notes(self, recipe, data):
        old = ('Исторические цены сохранены в свойствах компонентов без '
               'назначения валюты: в исходном столбце указано только Price.')
        replacement = next((note for note in data.get('notes', '').split('\n\n')
                            if 'EUR' in note or 'евро' in note), '')
        notes = recipe.notes.replace(old, replacement)
        for paragraph in data.get('notes', '').split('\n\n'):
            correction = ('вместо марки изоцианата' in paragraph or
                          paragraph.lstrip().startswith(
                              'По указанию пользователя отсутствующая цена'))
            if correction and paragraph not in notes:
                notes += '\n\n' + paragraph
        if notes != recipe.notes:
            recipe.notes = notes
            recipe.save(update_fields=['notes'])

    def recipe_rows(self, recipe, cells, data):
        groups = {}
        for row in data['groups']:
            group = lab.RecipeGroup.objects.create(
                recipe_version=recipe, label=row['label'],
                quantity_basis=row['basis'], completeness=row['completeness'],
            )
            groups[row['label']] = group
            for position, component in enumerate(row['components'], 1):
                self.component(group, position, cells, component)
            self.properties('group', group, cells, row.get('properties', []))
        for row in data.get('filling', []):
            self.filling(recipe, groups, cells, row)
        self.properties('recipe_version', recipe, cells, data.get('properties', []))

    def component(self, group, position, cells, data):
        recipe = group.recipe_version
        binding = self.catalog.binding(data, recipe.source_sheet.file)
        component = lab.RecipeComponent.objects.create(
            group=group, position=position,
            **binding,
            name_cell=cells.get(data.get('name_cell')),
            quantity_value=number(data.get('quantity')),
            quantity_cell=cells.get(data.get('quantity_cell')),
        )
        self.properties('component', component, cells, data.get('properties', []))
        self.catalog.observe(data, binding, recipe, cells)

    def filling(self, recipe, groups, cells, data):
        binding = self.catalog.binding(data, recipe.source_sheet.file)
        line = lab.FillingPlanLine.objects.create(
            recipe_version=recipe, input_group=groups.get(data.get('group_label')),
            **binding,
            label_cell=cells.get(data.get('label_cell')),
            mass_g=number(data.get('mass_g')), mass_status=data['mass_status'],
            mass_cell=cells.get(data.get('mass_cell')),
        )
        self.properties('filling_plan_line', line, cells, data.get('properties', []))
        self.catalog.observe(data, binding, recipe, cells)

    def properties(self, owner_field, owner, cells, rows):
        for row in rows:
            prop, created = lab.RecipeProperty.objects.get_or_create(
                **{owner_field: owner}, property_code=row['code'],
                source_cell=property_source_cell(row, cells, self.current_source),
                defaults={
                    'value': number(row['value']), 'unit_code': row.get('unit'),
                    'origin': row.get('origin', 'recorded'),
                    'provenance_note': row.get('provenance_note', ''),
                },
            )
            if not created and prop.value == number(row['value']) and (
                prop.unit_code == 'source_currency/kg' and row.get('unit') == 'EUR/kg'
            ):
                prop.unit_code = row['unit']
                prop.save(update_fields=['unit_code'])

    def load_test(self, sheet, cells, data):
        if lab.TestSession.objects.filter(source_sheet=sheet).exists():
            return
        card = lab.TestSession.objects.create(
            source_sheet=sheet, identifier=data['identifier'],
            identifier_cell=cells.get(data.get('identifier_cell')),
            title=data['title'], status='completed', created_by=self.author,
            recorded_manufactured_on=data.get('recorded_manufactured_on'),
            recorded_tested_on=data.get('recorded_tested_on'),
            notes=data.get('notes', ''),
        )
        self.conditions(card, cells, data.get('conditions', []))
        results = {}
        rows = []
        for row in data.get('runs', []):
            results.update(self.run(card, cells, row))
            rows.extend(row.get('results', []))
        self.dependencies(results, rows)
        for row in data.get('matches', []):
            self.pending_matches.append((card, row))
        event(self.author, card, 'test', 'Перенесена карточка ' + card.title)
        self.counts['tests'] += 1

    def metric(self, code, title, unit, kind):
        metric, _ = lab.Metric.objects.get_or_create(
            code=code, defaults={
                'label': title, 'unit_code': unit, 'value_kind': kind,
            },
        )
        if metric.value_kind != kind or metric.unit_code != unit:
            raise CommandError('Конфликт справочника показателей: ' + code)
        return metric

    def conditions(self, card, cells, rows):
        for row in rows:
            kind = 'numeric' if row.get('value_numeric') is not None else 'text'
            metric = self.metric(row['code'], row['title'], row.get('unit'), kind)
            lab.SessionCondition.objects.create(
                session=card, metric=metric,
                numeric_value=number(row.get('value_numeric')),
                text_value=row.get('value_text'),
                source_cell=cells.get(row.get('cell')),
            )

    def run(self, card, cells, data):
        run = lab.TestRun.objects.create(
            session=card, scope=data['scope'], method_code=data.get('method_code'),
            scope_cell=cells.get(data.get('scope_cell')),
        )
        results = {}
        for row in data.get('results', []):
            result = self.result(run, cells, row)
            results[row.get('source_cell')] = result
        for position, row in enumerate(data.get('observations', []), 1):
            lab.Observation.objects.create(
                test_run=run, sequence_no=position, text_value=row['text'],
                source_cell=cells.get(row.get('source_cell')),
            )
        return results

    def result(self, run, cells, row):
        kind = 'text' if row.get('value_text') is not None else 'numeric'
        # Unparsed text still belongs to the original numeric indicator.
        if row['value_state'] == 'unparsed':
            kind = 'numeric'
        metric = self.metric(
            row['metric_code'], row['metric_title'], row.get('unit'), kind,
        )
        return lab.Result.objects.create(
            test_run=run, metric=metric,
            numeric_value=number(row.get('value_numeric')),
            text_value=row.get('value_text'), origin=row['origin'],
            value_state=row['value_state'],
            source_cell=cells.get(row.get('source_cell')),
            calculation_code=(row.get('formula_text')
                              if row['origin'] == 'calculated' else None),
        )

    def dependencies(self, results, rows):
        for row in rows:
            result = results.get(row.get('source_cell'))
            for address in row.get('dependencies', []):
                if address in results and results[address] != result:
                    lab.ResultDependency.objects.get_or_create(
                        result=result, role=address,
                        defaults={'input_result': results[address]},
                    )

    def link_recipes(self):
        for card, row in self.pending_matches:
            recipe = self.recipes.get((row['path'], row['sheet']))
            if recipe:
                lab.RecipeMatch.objects.get_or_create(
                    session=card, recipe_version=recipe,
                    defaults={'status': row.get('status', 'candidate')},
                )


@transaction.atomic
def seed_initial():
    data = json.loads(DATA_PATH.read_text(encoding='utf-8'))
    if data.get('schema_version') not in (1, 2):
        raise CommandError('Неизвестная версия набора начальных данных.')
    author = employees()
    counts = remove_demo(author)
    counts.update(ArchiveLoader(author).load(data))
    return counts
