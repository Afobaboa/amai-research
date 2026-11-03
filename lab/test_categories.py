"""Material category membership, currency display and audited catalogue edits."""
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse

from .categories import sync_material_categories
from .models import (
    ChangeEvent, EmployeeProfile, Material, MaterialCategory, MaterialLot,
)


@override_settings(DEMO_MODE=True)
class MaterialCategoryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = EmployeeProfile.objects.create(
            full_name='Категории: автор', role='author',
        )
        cls.reader = EmployeeProfile.objects.create(
            full_name='Категории: читатель', role='reader',
        )
        cls.category = MaterialCategory.objects.create(
            name='Полиолы', description='Сырьё группы A.',
        )
        cls.material = Material.objects.create(
            name='Полиол QA', category='Полиолы',
        )
        cls.unclassified = Material.objects.create(name='Сырьё без категории')

    def setUp(self):
        self.sign_in(self.author)

    def sign_in(self, employee):
        session = self.client.session
        session['site_authenticated'] = True
        session['employee_id'] = employee.pk
        session.save()

    def test_list_explains_current_categories_and_unclassified_materials(self):
        response = self.client.get(reverse('category_list'))
        self.assertContains(response, 'Сырьё группы A.')
        self.assertContains(response, 'Без категории: 1')
        self.assertEqual(response.context['categories'][0].material_count, 1)
        self.assertContains(response, reverse('category_detail', args=[
            self.category.pk,
        ]))

    def test_category_details_and_membership_filter(self):
        response = self.client.get(reverse('category_detail', args=[
            self.category.pk,
        ]))
        self.assertEqual(list(response.context['materials']), [self.material])
        self.assertContains(response, 'ПАРТИИ')
        self.assertNotContains(response, 'ЦЕНА ЗА КГ')
        response = self.client.get(reverse('material_list'), {
            'category': 'unclassified',
        })
        self.assertEqual(list(response.context['materials']), [self.unclassified])
        response = self.client.get(reverse('material_list'), {
            'category': self.category.name,
        })
        self.assertEqual(list(response.context['materials']), [self.material])

    def test_author_creates_and_renames_with_membership_and_audit(self):
        response = self.client.post(reverse('category_create'), {
            'name': 'Катализаторы', 'description': 'Катализаторы реакции.',
        })
        category = MaterialCategory.objects.get(name='Катализаторы')
        self.assertRedirects(response, reverse('category_detail', args=[
            category.pk,
        ]))
        response = self.client.post(reverse('category_edit', args=[
            self.category.pk,
        ]), {'name': 'Полиэфиры', 'description': 'Описание уточнено.'})
        self.assertEqual(response.status_code, 302)
        self.material.refresh_from_db()
        self.unclassified.refresh_from_db()
        self.assertEqual(self.material.category, 'Полиэфиры')
        self.assertEqual(self.unclassified.category, '')
        event = ChangeEvent.objects.get(
            entity_type='material', entity_id=self.material.pk,
        )
        self.assertEqual(event.before['category'], 'Полиолы')
        self.assertEqual(event.after['category'], 'Полиэфиры')
        self.assertEqual(ChangeEvent.objects.filter(
            entity_type='material_category',
        ).count(), 2)

    def test_duplicate_category_does_not_rename_members(self):
        response = self.client.post(reverse('category_create'), {
            'name': 'полиолы', 'description': 'Повтор.',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Такая категория уже существует')
        self.assertEqual(MaterialCategory.objects.count(), 1)
        self.assertFalse(ChangeEvent.objects.exists())

    def test_material_form_selects_category_without_price_or_density(self):
        response = self.client.get(reverse('material_edit', args=[
            self.material.pk,
        ]))
        self.assertContains(response, '<select name="category"')
        self.assertEqual(set(response.context['form'].fields), {
            'name', 'category', 'notes',
        })
        response = self.client.post(reverse('material_edit', args=[
            self.material.pk,
        ]), {'name': self.material.name, 'category': 'Полиолы',
             'supplier': 'Поставщик QA'})
        self.assertEqual(response.status_code, 302)
        self.material.refresh_from_db()
        self.assertEqual(self.material.category, 'Полиолы')
        self.assertFalse(hasattr(self.material, 'supplier'))

    def test_unknown_category_does_not_create_material(self):
        response = self.client.post(reverse('material_create'), {
            'name': 'Не создаётся', 'category': 'Неизвестная категория',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Material.objects.filter(name='Не создаётся').exists())

    def test_existing_category_values_are_retained_idempotently(self):
        Material.objects.create(name='Архивный материал', category='Из архива')
        sync_material_categories()
        sync_material_categories()
        self.assertEqual(MaterialCategory.objects.filter(
            name='Из архива',
        ).count(), 1)
        self.assertFalse(MaterialCategory.objects.filter(name='').exists())

    def test_lot_currency_display_belongs_to_that_lot(self):
        lot = MaterialLot.objects.create(
            material=self.material, lot_code='QA', price_currency='EUR',
            price_per_kg=Decimal('1.5'), density_g_ml=Decimal('1.05'),
        )
        response = self.client.get(reverse('lot_detail', args=[lot.pk]))
        self.assertContains(response, '€/кг')
        other = MaterialLot.objects.create(
            material=self.material, lot_code='OTHER',
        )
        response = self.client.get(reverse('lot_detail', args=[other.pk]))
        self.assertContains(response, '<td>Не указана</td>', count=2)
        self.assertNotContains(response, '€/кг')
        self.assertNotContains(response, 'Справочник материала')
        lot.price_currency = 'RUB'
        lot.save(update_fields=['price_currency'])
        response = self.client.get(reverse('lot_list'))
        self.assertContains(response, '₽/кг')

    def test_material_detail_shows_each_lot_properties_separately(self):
        first = MaterialLot.objects.create(
            material=self.material, lot_code='QA-ONE',
            price_per_kg=Decimal('1.5'), price_currency='EUR',
            density_g_ml=Decimal('1.05'),
        )
        second = MaterialLot.objects.create(
            material=self.material, lot_code='QA-TWO',
            price_per_kg=Decimal('2.7'), price_currency='EUR',
            density_g_ml=Decimal('1.2'),
        )
        response = self.client.get(reverse('material_detail', args=[
            self.material.pk,
        ]))
        self.assertEqual(set(response.context['lots']), {first, second})
        self.assertContains(response, 'QA-ONE')
        self.assertContains(response, 'QA-TWO')
        self.assertContains(response, '€/кг', count=2)
        self.assertNotContains(response, 'Справочная цена')
        self.assertNotContains(response, 'Справочная плотность')

    def test_reader_can_browse_but_cannot_save_categories(self):
        self.sign_in(self.reader)
        self.assertEqual(self.client.get(reverse('category_list')).status_code, 200)
        for route, args in [
            ('category_create', []), ('category_edit', [self.category.pk]),
        ]:
            response = self.client.post(reverse(route, args=args), {
                'name': 'Запрещено',
            })
            self.assertEqual(response.status_code, 403)
        self.assertFalse(MaterialCategory.objects.filter(name='Запрещено').exists())
