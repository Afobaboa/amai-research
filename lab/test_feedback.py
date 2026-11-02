"""Feedback persists for every active profile and is reviewed in Django admin."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse
from django.utils.html import escape

from .models import EmployeeProfile, Feedback


class FeedbackTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = EmployeeProfile.objects.create(
            full_name='Автор обратной связи', role='author',
        )
        cls.reader = EmployeeProfile.objects.create(
            full_name='Читатель обратной связи', role='reader',
        )

    def sign_in(self, employee, client=None):
        client = client or self.client
        session = client.session
        session.update({
            'site_authenticated': True, 'employee_id': employee.pk,
        })
        session.save()

    def test_header_links_to_feedback_and_get_does_not_save(self):
        self.sign_in(self.reader)
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, f'href="{reverse("feedback")}"')
        self.assertContains(response, 'Обратная связь')
        response = self.client.get(reverse('feedback'))
        self.assertContains(response, 'name="text"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertFalse(Feedback.objects.exists())

    def test_reader_and_author_can_submit_with_server_assigned_identity(self):
        for employee in (self.reader, self.author):
            with self.subTest(role=employee.role):
                self.sign_in(employee)
                response = self.client.post(reverse('feedback'), {
                    'text': '  Предлагаю улучшить поиск.\nСпасибо!  ',
                    'employee': self.author.pk,
                    'employee_name': 'Подменённое имя',
                }, follow=True)
                self.assertRedirects(response, reverse('dashboard'))
                self.assertContains(
                    response, 'Спасибо! Обратная связь отправлена.',
                )
                record = Feedback.objects.first()
                self.assertEqual(record.employee, employee)
                self.assertEqual(record.employee_name, employee.full_name)
                self.assertEqual(
                    record.text, 'Предлагаю улучшить поиск.\nСпасибо!',
                )
                self.assertIsNotNone(record.created_at)
        self.assertEqual(Feedback.objects.count(), 2)

    def test_blank_and_too_long_text_are_rejected_without_losing_input(self):
        self.sign_in(self.reader)
        for text in ('', ' \t\n ', 'Я' * 5001):
            with self.subTest(length=len(text)):
                response = self.client.post(reverse('feedback'), {'text': text})
                self.assertEqual(response.status_code, 200)
                self.assertIn('text', response.context['form'].errors)
                self.assertEqual(response.context['form'].data['text'], text)
        self.assertFalse(Feedback.objects.exists())

    def test_valid_maximum_length_text_is_saved(self):
        self.sign_in(self.reader)
        text = 'Я' * 5000
        response = self.client.post(reverse('feedback'), {'text': text})
        self.assertRedirects(response, reverse('dashboard'))
        self.assertEqual(Feedback.objects.get().text, text)

    def test_site_login_is_required_for_get_and_post(self):
        url = reverse('feedback')
        expected = reverse('login') + '?next=%2Ffeedback%2F'
        for method in ('get', 'post'):
            data = {'text': 'Сообщение'} if method == 'post' else {}
            response = getattr(self.client, method)(url, data)
            self.assertRedirects(response, expected)
        self.assertFalse(Feedback.objects.exists())

    def test_active_profile_is_required_for_get_and_post(self):
        self.sign_in(self.reader)
        self.reader.is_active = False
        self.reader.save(update_fields=['is_active'])
        for method in ('get', 'post'):
            response = getattr(self.client, method)(
                reverse('feedback'), {'text': 'Сообщение'},
            )
            self.assertRedirects(response, reverse('choose_employee'))
        self.assertFalse(Feedback.objects.exists())

    def test_submission_requires_csrf_and_form_provides_working_token(self):
        client = Client(enforce_csrf_checks=True)
        self.sign_in(self.reader, client)
        url = reverse('feedback')
        response = client.post(url, {'text': 'Без токена'})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Feedback.objects.exists())
        self.assertEqual(client.get(url).status_code, 200)
        token = client.cookies['csrftoken'].value
        response = client.post(url, {
            'text': 'С токеном', 'csrfmiddlewaretoken': token,
        })
        self.assertRedirects(response, reverse('dashboard'))
        self.assertEqual(Feedback.objects.get().text, 'С токеном')

    def test_feedback_survives_employee_deletion_with_original_name(self):
        self.sign_in(self.reader)
        self.client.post(reverse('feedback'), {'text': 'Сохранить сообщение'})
        self.reader.delete()
        record = Feedback.objects.get()
        self.assertIsNone(record.employee)
        self.assertEqual(record.employee_name, 'Читатель обратной связи')
        self.assertEqual(record.text, 'Сохранить сообщение')

    def test_feedback_admin_requires_separate_admin_login(self):
        self.sign_in(self.author)
        url = reverse('admin:lab_feedback_changelist')
        response = self.client.get(url)
        self.assertRedirects(response, reverse('admin:login') + '?next=' + url)

    def test_staff_with_view_permission_can_read_escaped_feedback(self):
        text = '<script>alert("feedback")</script>\nВторая строка'
        record = Feedback.objects.create(
            text=text, employee=self.reader, employee_name=self.reader.full_name,
        )
        user = get_user_model().objects.create_user(
            username='feedback-reviewer', is_staff=True,
        )
        user.user_permissions.add(Permission.objects.get(
            content_type__app_label='lab', codename='view_feedback',
        ))
        self.client.force_login(user)
        response = self.client.get(reverse('admin:lab_feedback_changelist'))
        self.assertContains(response, self.reader.full_name)
        detail = reverse('admin:lab_feedback_change', args=[record.pk])
        response = self.client.get(detail)
        self.assertContains(response, escape(text.splitlines()[0]))
        self.assertContains(response, 'Вторая строка')
        self.assertNotContains(response, '<script>alert("feedback")</script>')
        self.assertEqual(self.client.post(detail, {'text': 'Подмена'}).status_code, 403)
        record.refresh_from_db()
        self.assertEqual(record.text, text)

    def test_staff_without_feedback_permission_cannot_read_messages(self):
        record = Feedback.objects.create(
            text='Закрытое сообщение', employee_name=self.reader.full_name,
        )
        user = get_user_model().objects.create_user(
            username='unprivileged-staff', is_staff=True,
        )
        self.client.force_login(user)
        for url in (
            reverse('admin:lab_feedback_changelist'),
            reverse('admin:lab_feedback_change', args=[record.pk]),
        ):
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_admin_search_finds_text_and_original_employee_name(self):
        record = Feedback.objects.create(
            text='Нужна выгрузка таблицы', employee_name='Петров Тестовый',
        )
        Feedback.objects.create(text='Другое сообщение', employee_name='Иванов')
        user = get_user_model().objects.create_superuser(username='feedback-admin')
        self.client.force_login(user)
        url = reverse('admin:lab_feedback_changelist')
        for query in ('выгрузка', 'Петров'):
            response = self.client.get(url, {'q': query})
            self.assertEqual(
                list(response.context['cl'].result_list), [record],
            )

    def test_superuser_cannot_replace_submitted_feedback(self):
        record = Feedback.objects.create(
            text='Оригинальный текст', employee_name=self.reader.full_name,
        )
        user = get_user_model().objects.create_superuser(username='feedback-admin')
        self.client.force_login(user)
        detail = reverse('admin:lab_feedback_change', args=[record.pk])
        self.client.post(detail, {'text': 'Подмена', 'employee_name': 'Подмена'})
        record.refresh_from_db()
        self.assertEqual(record.text, 'Оригинальный текст')
        self.assertEqual(record.employee_name, self.reader.full_name)
        self.assertEqual(
            self.client.get(reverse('admin:lab_feedback_add')).status_code, 403,
        )
