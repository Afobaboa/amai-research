from decimal import Decimal
from io import StringIO

from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import (
    ChangeEvent, EmployeeProfile, Material, Metric, MixtureType, RecipeComponent,
    RecipeGroup, RecipeMatch, RecipeVersion, Result, SessionCondition,
    TestRun, TestSession,
)


@override_settings(SITE_PASSWORD_HASH=make_password("test-lims"), DEMO_MODE=True)
class WorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = EmployeeProfile.objects.create(full_name="Автор", role="author")
        cls.reader = EmployeeProfile.objects.create(full_name="Читатель", role="reader")
        cls.mixture = MixtureType.objects.create(code="TEST", title="Тестовая смесь")
        cls.material = Material.objects.create(name="Тестовый материал")
        cls.metric = Metric.objects.create(code="TEMP", label="Температура", unit_code="°C", value_kind="numeric")
        cls.text_metric = Metric.objects.create(code="NOTE", label="Описание", value_kind="text")
        cls.recipe = RecipeVersion.objects.create(mixture_type=cls.mixture, version_label="TEST.1", status="published", created_by=cls.author)
        group = RecipeGroup.objects.create(recipe_version=cls.recipe, label="A", quantity_basis="mass_percent", completeness="complete")
        RecipeComponent.objects.create(group=group, position=1, unbatched_material=cls.material, quantity_value=Decimal("100"))
        cls.card = TestSession.objects.create(identifier="TEST-001", title="Контроль", status="active", created_by=cls.author)
        RecipeMatch.objects.create(session=cls.card, recipe_version=cls.recipe, status="confirmed")
        cls.test_run = TestRun.objects.create(session=cls.card, scope="Основное испытание")

    def sign_in(self, employee=None):
        session = self.client.session
        session["site_authenticated"] = True
        session["employee_id"] = (employee or self.author).pk
        session.save()

    def recipe_payload(self, **extra):
        payload = {
            "mixture_type": str(self.mixture.pk), "version_label": "TEST.2", "title": "Новый состав", "status": "published", "notes": "",
            "components-TOTAL_FORMS": "1", "components-INITIAL_FORMS": "0", "components-MIN_NUM_FORMS": "0", "components-MAX_NUM_FORMS": "100",
            "components-0-group_label": "A", "components-0-quantity_basis": "mass_percent", "components-0-material": str(self.material.pk), "components-0-quantity_value": "100",
            "filling-TOTAL_FORMS": "1", "filling-INITIAL_FORMS": "0", "filling-MIN_NUM_FORMS": "0", "filling-MAX_NUM_FORMS": "50",
            "filling-0-group_label": "A", "filling-0-mass_g": "220",
        }
        payload.update(extra)
        return payload

    def card_payload(self, **extra):
        payload = {"title": "Новое испытание", "status": "active", "notes": "Наблюдение", "recipe": str(self.recipe.pk), "conditions-TOTAL_FORMS": "1", "conditions-INITIAL_FORMS": "0", "conditions-MIN_NUM_FORMS": "0", "conditions-MAX_NUM_FORMS": "30", "conditions-0-metric": str(self.metric.pk), "conditions-0-numeric_value": "-10"}
        payload.update(extra)
        return payload

    def test_password_and_employee_flow(self):
        self.assertRedirects(self.client.get("/"), "/login/?next=%2F")
        wrong = self.client.post(reverse("login"), {"password": "bad"})
        self.assertEqual(wrong.status_code, 200)
        self.assertNotIn("site_authenticated", self.client.session)
        self.assertRedirects(self.client.post(reverse("login"), {"password": "test-lims"}), reverse("choose_employee"))
        self.assertRedirects(self.client.post(reverse("choose_employee"), {"employee": self.reader.pk}), "/")
        self.assertEqual(self.client.session["employee_id"], self.reader.pk)
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)
        self.client.post(reverse("logout"))
        self.assertNotIn("site_authenticated", self.client.session)

    def test_reader_cannot_mutate_by_direct_post(self):
        self.sign_in(self.reader)
        routes = [("recipe_create", []), ("recipe_fork", [self.recipe.pk]), ("test_create", []), ("test_edit", [self.card.pk]), ("result_add", [self.card.pk]), ("observation_add", [self.card.pk]), ("conditioning_add", [self.card.pk])]
        for name, args in routes:
            with self.subTest(route=name):
                self.assertEqual(self.client.post(reverse(name, args=args), self.recipe_payload()).status_code, 403)
        self.assertEqual(RecipeVersion.objects.count(), 1)
        self.assertEqual(TestSession.objects.count(), 1)
        self.assertEqual(ChangeEvent.objects.count(), 0)

    def test_role_is_rechecked_from_database(self):
        self.sign_in()
        EmployeeProfile.objects.filter(pk=self.author.pk).update(role="reader")
        self.assertEqual(self.client.post(reverse("recipe_create"), self.recipe_payload()).status_code, 403)
        EmployeeProfile.objects.filter(pk=self.author.pk).update(is_active=False)
        self.assertRedirects(self.client.get("/"), reverse("choose_employee"))

    def test_create_and_fork_recipe_preserves_old_version(self):
        self.sign_in()
        response = self.client.post(reverse("recipe_fork", args=[self.recipe.pk]), self.recipe_payload(**{"components-INITIAL_FORMS": "1"}))
        created = RecipeVersion.objects.get(version_label="TEST.2")
        self.assertRedirects(response, reverse("recipe_detail", args=[created.pk]))
        self.assertEqual(created.groups.get().components.get().quantity_value, Decimal("100"))
        self.assertEqual(created.filling_plan.get().mass_g, Decimal("220"))
        self.recipe.refresh_from_db()
        self.assertEqual(self.recipe.version_label, "TEST.1")
        self.assertEqual(self.recipe.groups.get().components.get().quantity_value, Decimal("100"))
        audit = ChangeEvent.objects.get(entity_id=created.pk, entity_type="recipe")
        self.assertEqual(audit.employee_id, self.author.pk)
        self.assertIn("groups", audit.after)
        self.assertEqual(self.client.post(reverse("recipe_edit", args=[self.recipe.pk]), self.recipe_payload()).status_code, 403)

    def test_invalid_recipe_is_atomic(self):
        self.sign_in()
        response = self.client.post(reverse("recipe_create"), self.recipe_payload(**{"components-0-quantity_value": "-1"}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(RecipeVersion.objects.count(), 1)
        self.assertEqual(ChangeEvent.objects.count(), 0)
        response = self.client.post(reverse("recipe_create"), self.recipe_payload(**{"filling-0-group_label": "UNKNOWN"}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(RecipeVersion.objects.count(), 1)

    def test_draft_edit_publish_and_draft_link_rejection(self):
        self.sign_in()
        draft = RecipeVersion.objects.create(mixture_type=self.mixture, version_label="DRAFT.1", status="draft", created_by=self.author)
        response = self.client.post(reverse("test_create"), self.card_payload(recipe=str(draft.pk)))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(TestSession.objects.count(), 1)
        response = self.client.post(reverse("recipe_edit", args=[draft.pk]), self.recipe_payload(version_label="DRAFT.1", status="published"))
        self.assertRedirects(response, reverse("recipe_detail", args=[draft.pk]))
        draft.refresh_from_db()
        self.assertEqual(draft.status, "published")
        self.assertEqual(RecipeVersion.objects.count(), 2)
        event = ChangeEvent.objects.get(entity_type="recipe", entity_id=draft.pk)
        self.assertEqual(event.before["status"], "draft")
        self.assertEqual(event.after["status"], "published")
        self.assertEqual(self.client.post(reverse("recipe_edit", args=[draft.pk]), self.recipe_payload()).status_code, 403)

    def test_fork_and_edit_accept_unchanged_database_decimal_precision(self):
        self.sign_in()
        group = self.recipe.groups.get()
        first = group.components.get()
        first.quantity_value = Decimal("0")
        first.save()
        RecipeComponent.objects.create(group=group, position=2, unbatched_material=self.material, quantity_value=Decimal("100"))
        from .models import FillingPlanLine
        FillingPlanLine.objects.create(recipe_version=self.recipe, input_group=group, mass_g=Decimal("0"), mass_status="recorded")
        fork_url = reverse("recipe_fork", args=[self.recipe.pk])
        page = self.client.get(fork_url)
        component_forms = page.context["component_formset"].forms
        self.assertIn('value="0"', component_forms[0]["quantity_value"].as_widget())
        self.assertIn('value="100"', component_forms[1]["quantity_value"].as_widget())
        self.assertIn('value="0"', page.context["filling_formset"].forms[0]["mass_g"].as_widget())
        # Existing eight-place database values must remain valid even if submitted verbatim.
        payload = self.recipe_payload(status="draft", **{
            "components-TOTAL_FORMS": "2", "components-INITIAL_FORMS": "2",
            "components-0-quantity_value": "0E-8",
            "components-1-group_label": "A", "components-1-quantity_basis": "mass_percent",
            "components-1-material": str(self.material.pk), "components-1-quantity_value": "100.00000000",
            "filling-INITIAL_FORMS": "1", "filling-0-mass_g": "0E-8",
        })
        response = self.client.post(fork_url, payload)
        fork = RecipeVersion.objects.get(version_label="TEST.2")
        self.assertRedirects(response, reverse("recipe_detail", args=[fork.pk]))
        self.assertEqual(list(fork.groups.get().components.values_list("quantity_value", flat=True)), [Decimal("0"), Decimal("100")])
        self.assertEqual(fork.filling_plan.get().mass_g, Decimal("0"))
        response = self.client.post(reverse("recipe_edit", args=[fork.pk]), {**payload, "title": "Изменение названия", "status": "published"})
        self.assertRedirects(response, reverse("recipe_detail", args=[fork.pk]))
        fork.refresh_from_db()
        self.assertEqual(fork.title, "Изменение названия")
        self.assertEqual(fork.status, "published")
        self.assertEqual(fork.groups.get().components.count(), 2)
        self.assertEqual(fork.filling_plan.get().mass_g, Decimal("0"))

    def test_card_form_starts_with_recipe_and_omits_removed_fields(self):
        self.sign_in()
        response = self.client.get(reverse("test_create"), {"recipe": self.recipe.pk})
        form = response.context["form"]
        self.assertEqual(next(iter(form.fields)), "recipe")
        self.assertTrue(form.fields["recipe"].required)
        self.assertEqual(form.initial["recipe"], str(self.recipe.pk))
        for name in ("identifier", "source_sheet", "match_reason"):
            self.assertNotIn(name, form.fields)
            self.assertNotContains(response, f'id="id_{name}"')

    def test_new_card_requires_published_recipe(self):
        self.sign_in()
        before = TestSession.objects.count()
        response = self.client.post(reverse("test_create"), self.card_payload(recipe=""))
        self.assertIn("recipe", response.context["form"].errors)
        self.assertEqual(TestSession.objects.count(), before)
        self.assertFalse(ChangeEvent.objects.exists())

    def test_multiple_cards_can_use_recipe_number_and_ignore_submitted_number(self):
        self.sign_in()
        for _ in range(2):
            response = self.client.post(reverse("test_create"), self.card_payload(identifier="FORGED"))
            self.assertEqual(response.status_code, 302)
        self.assertEqual(TestSession.objects.filter(identifier=self.recipe.version_label).count(), 2)
        self.assertFalse(TestSession.objects.filter(identifier="FORGED").exists())

    def test_relinking_card_changes_number_and_preserves_old_link_status(self):
        self.sign_in()
        other = RecipeVersion.objects.create(mixture_type=self.mixture, version_label="OTHER.1", status="published")
        response = self.client.post(reverse("test_edit", args=[self.card.pk]), self.card_payload(recipe=other.pk))
        self.assertEqual(response.status_code, 302)
        self.card.refresh_from_db()
        self.assertEqual(self.card.identifier, other.version_label)
        self.assertEqual(self.card.recipe_matches.get(recipe_version=self.recipe).status, "rejected")
        self.assertEqual(self.card.recipe_matches.get(status="confirmed").recipe_version, other)
        self.assertNotContains(self.client.get(reverse("test_detail", args=[self.card.pk])), "Основание")
        self.assertNotContains(self.client.get(reverse("recipe_detail", args=[other.pk])), "ОСНОВАНИЕ")

    def test_existing_unlinked_card_can_be_edited_without_recipe(self):
        self.sign_in()
        card = TestSession.objects.create(identifier="ARCHIVE", status="active")
        response = self.client.post(reverse("test_edit", args=[card.pk]), self.card_payload(recipe=""))
        self.assertEqual(response.status_code, 302)
        card.refresh_from_db()
        self.assertEqual(card.identifier, "ARCHIVE")
        self.assertFalse(card.recipe_matches.exists())

    def test_create_and_edit_card_produces_before_after_history(self):
        self.sign_in()
        response = self.client.post(reverse("test_create"), self.card_payload())
        card = TestSession.objects.get(identifier=self.recipe.version_label)
        self.assertRedirects(response, reverse("test_detail", args=[card.pk]))
        self.assertEqual(card.conditions.get().numeric_value, Decimal("-10"))
        self.assertEqual(card.recipe_matches.get(status="confirmed").recipe_version_id, self.recipe.pk)
        self.client.post(reverse("test_edit", args=[card.pk]), self.card_payload(title="Исправленное название", **{"conditions-0-numeric_value": "-18"}))
        card.refresh_from_db()
        self.assertEqual(card.title, "Исправленное название")
        event = ChangeEvent.objects.filter(entity_type="test", entity_id=card.pk).latest("pk")
        self.assertEqual(event.before["title"], "Новое испытание")
        self.assertEqual(event.after["title"], "Исправленное название")
        self.assertEqual(event.before["conditions"][0]["numeric_value"], "-10.00000000")
        self.assertEqual(card.conditions.get().numeric_value, Decimal("-18"))

    def test_record_result_and_reject_wrong_type_and_cross_card_run(self):
        self.sign_in()
        url = reverse("result_add", args=[self.card.pk])
        valid = {"run": self.test_run.pk, "metric": self.metric.pk, "numeric_value": "0", "scope": ""}
        self.assertRedirects(self.client.post(url, valid), reverse("test_detail", args=[self.card.pk]))
        self.assertEqual(Result.objects.get().numeric_value, Decimal("0"))
        invalid = {"run": self.test_run.pk, "metric": self.metric.pk, "text_value": "not a number"}
        self.assertEqual(self.client.post(url, invalid).status_code, 200)
        other = TestSession.objects.create(identifier="OTHER")
        other_run = TestRun.objects.create(session=other, scope="Другой")
        self.assertEqual(self.client.post(url, {**valid, "run": other_run.pk}).status_code, 200)
        self.assertEqual(Result.objects.count(), 1)

    def test_observation_preserves_text_and_completed_card_is_readonly(self):
        self.sign_in()
        text = "  Первая строка\nВторая строка  "
        self.assertRedirects(self.client.post(reverse("observation_add", args=[self.card.pk]), {"run": self.test_run.pk, "text_value": text}), reverse("test_detail", args=[self.card.pk]))
        self.assertEqual(self.test_run.observations.get().text_value, text)
        TestSession.objects.filter(pk=self.card.pk).update(status="completed")
        for route in ["test_edit", "result_add", "observation_add", "conditioning_add"]:
            self.assertEqual(self.client.post(reverse(route, args=[self.card.pk]), {}).status_code, 403)

    def test_no_results_inferred_for_unknown_or_blank_values(self):
        self.sign_in()
        response = self.client.post(reverse("result_add", args=[self.card.pk]), {"run": self.test_run.pk, "metric": self.metric.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Result.objects.count(), 0)

    def test_csrf_is_required_for_mutation(self):
        csrf_client = Client(enforce_csrf_checks=True)
        session = csrf_client.session
        session["site_authenticated"] = True
        session["employee_id"] = self.author.pk
        session.save()
        self.assertEqual(csrf_client.post(reverse("recipe_create"), self.recipe_payload()).status_code, 403)
        self.assertEqual(RecipeVersion.objects.count(), 1)
        csrf_client.get(reverse("recipe_create"))
        csrf_token = csrf_client.cookies["csrftoken"].value
        self.assertEqual(csrf_client.post(reverse("recipe_create"), self.recipe_payload(), HTTP_X_CSRFTOKEN=csrf_token).status_code, 302)
        self.assertEqual(RecipeVersion.objects.count(), 2)

    def test_audit_events_are_append_only(self):
        event = ChangeEvent.objects.create(employee=self.author, entity_type="test", entity_id=self.card.pk, action="created", summary="Создание")
        with self.assertRaises(ValidationError):
            ChangeEvent.objects.filter(pk=event.pk).update(summary="Изменено")
        with self.assertRaises(ValidationError):
            event.delete()

    def test_search_and_history_links(self):
        self.sign_in(self.reader)
        self.assertContains(self.client.get(reverse("recipe_list"), {"q": "TEST.1"}), "TEST.1")
        self.assertContains(self.client.get(reverse("test_list"), {"q": "TEST-001"}), "TEST-001")
        self.assertEqual(self.client.get(reverse("history")).status_code, 200)
        self.assertEqual(self.client.get(reverse("sources")).status_code, 200)
