"""Every initial material has a reviewed, reproducible functional category."""
import json
from collections import Counter

from django.test import SimpleTestCase, TestCase

from .categories import CATEGORY_PATH, seed_material_categories
from .initial_data import DATA_PATH
from .models import ChangeEvent, EmployeeProfile, Material, MaterialCategory


def mapping():
    return json.loads(CATEGORY_PATH.read_text(encoding='utf-8'))


class InitialCategoryEvidenceTests(SimpleTestCase):
    def test_mapping_covers_exact_initial_materials_without_duplicate_members(self):
        data = json.loads(DATA_PATH.read_text(encoding='utf-8'))
        names = set()
        for source in data['files']:
            for sheet in source['sheets']:
                recipe = sheet.get('recipe')
                if not recipe:
                    continue
                for group in recipe['groups']:
                    names.update(row['name'] for row in group['components'])
                names.update(row['material_name'] for row in recipe['filling'])
        names.discard(None)
        rows = mapping()['materials']
        self.assertEqual(len(rows), 72)
        self.assertEqual(len({row['name'] for row in rows}), 72)
        self.assertSetEqual({row['name'] for row in rows}, names)
        self.assertEqual(Counter(row['category'] for row in rows), {
            'Полиолы': 23, 'Аминные катализаторы': 7,
            'Стабилизаторы и регуляторы ячеек': 13,
            'Антипирены и пластификаторы': 5, 'Изоцианаты': 7,
            'Пропелленты и вспенивающие газы': 4,
            'Добавки (тип требует уточнения)': 13,
        })

    def test_every_member_has_matching_excel_provenance_and_valid_references(self):
        data = json.loads(DATA_PATH.read_text(encoding='utf-8'))
        sources = {row['path']: row for row in data['files']}
        categories = mapping()
        for row in categories['materials']:
            with self.subTest(material=row['name']):
                evidence = row['excel_evidence']
                source = sources[evidence['file']]
                self.assertEqual(source['sha256'], evidence['sha256'])
                sheet = next(sheet for sheet in source['sheets']
                             if sheet['name'] == evidence['sheet'])
                self.assertEqual(
                    sheet['cells'][evidence['name_cell']]['raw_value'],
                    evidence['raw_name'],
                )
                for reference in row['references']:
                    self.assertTrue(categories['references'][reference]['url']
                                    .startswith('https://'))
                self.assertTrue(row['reason'])
                if row['basis'] == 'provisional':
                    self.assertEqual(row['category'],
                                     'Добавки (тип требует уточнения)')


class InitialCategorySeedTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = EmployeeProfile.objects.create(
            full_name='Категории: автор начального набора', role='author',
        )
        Material.objects.bulk_create([
            Material(name=row['name']) for row in mapping()['materials']
        ])

    def seed(self):
        return seed_material_categories(self.author)

    def event(self, entity, kind, field, before, after):
        ChangeEvent.objects.create(
            employee=self.author, entity_type=kind, entity_id=entity.pk,
            action='updated', summary='Уточнение сотрудника',
            before={field: before}, after={field: after},
        )

    def test_all_initial_categories_and_members_are_seeded_idempotently(self):
        self.assertEqual(self.seed(), {
            'categories_created': 7, 'materials_classified': 72,
        })
        self.assertFalse(Material.objects.filter(category='').exists())
        self.assertFalse(MaterialCategory.objects.filter(description='').exists())
        self.assertEqual(Material.objects.get(name='Inovol C210').category,
                         'Полиолы')
        event_count = ChangeEvent.objects.count()
        self.assertEqual(self.seed(), {
            'categories_created': 0, 'materials_classified': 0,
        })
        self.assertEqual(ChangeEvent.objects.count(), event_count)

    def test_custom_membership_and_explicit_clearing_are_preserved(self):
        self.seed()
        custom = Material.objects.get(name='Inovol C210')
        custom.category = 'Категория сотрудника'
        custom.save(update_fields=['category'])
        cleared = Material.objects.get(name='Inovol C307')
        before = cleared.category
        cleared.category = ''
        cleared.save(update_fields=['category'])
        self.event(cleared, 'material', 'category', before, '')
        self.seed()
        custom.refresh_from_db()
        cleared.refresh_from_db()
        self.assertEqual(custom.category, 'Категория сотрудника')
        self.assertEqual(cleared.category, '')
        self.assertTrue(MaterialCategory.objects.filter(
            name='Категория сотрудника',
        ).exists())

    def test_user_category_rename_is_respected_for_future_default_members(self):
        self.seed()
        category = MaterialCategory.objects.get(name='Полиолы')
        category.name = 'Уточнённые полиолы'
        category.save(update_fields=['name'])
        Material.objects.filter(category='Полиолы').update(category=category.name)
        self.event(category, 'material_category', 'name', 'Полиолы', category.name)
        Material.objects.filter(name='Inovol C210').delete()
        material = Material.objects.create(name='Inovol C210')
        self.seed()
        material.refresh_from_db()
        self.assertEqual(material.category, category.name)
        self.assertFalse(MaterialCategory.objects.filter(name='Полиолы').exists())

    def test_user_description_and_unrelated_material_are_preserved(self):
        self.seed()
        category = MaterialCategory.objects.get(name='Полиолы')
        before = category.description
        category.description = ''
        category.save(update_fields=['description'])
        self.event(category, 'material_category', 'description', before, '')
        custom = Material.objects.create(name='Новая марка сотрудника')
        self.seed()
        category.refresh_from_db()
        custom.refresh_from_db()
        self.assertEqual(category.description, '')
        self.assertEqual(custom.category, '')
