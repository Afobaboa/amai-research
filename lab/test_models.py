"""Meaningful data invariants independent of the HTML workflows."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from .models import (
    ChangeEvent, ConditioningEvent, EmployeeProfile, FillingPlanLine, Material,
    MaterialLot, Metric, RecipeComponent, RecipeGroup, RecipeMatch,
    RecipeProperty, RecipeVersion, Result, ResultDependency, MeasurementGroup,
    SourceCell, SourceFile, SourceSheet, TestRun, TestSession,
)


class DocumentaryModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.employee = EmployeeProfile.objects.create(full_name="Автор проверки", role="author")
        cls.recipe = RecipeVersion.objects.create(version_label="CHECK.1", created_by=cls.employee)
        cls.other_recipe = RecipeVersion.objects.create(version_label="CHECK.1", created_by=cls.employee)
        cls.group = RecipeGroup.objects.create(recipe_version=cls.recipe, label="A", quantity_basis="mass_percent", completeness="partial")
        cls.material = Material.objects.create(name="Условный материал")
        cls.lot = MaterialLot.objects.create(material=cls.material, lot_code="LOT-1")
        cls.card = TestSession.objects.create(identifier="CHECK-1")
        cls.metric = Metric.objects.create(code="length", label="Длина", unit_code="мм")
        cls.text_metric = Metric.objects.create(code="texture", label="Структура", value_kind="text")
        cls.test_run = TestRun.objects.create(session=cls.card, scope="Проверка модели")

    def assert_rejected(self, operation):
        # Inner savepoint prevents an expected DB violation breaking the outer test.
        with self.assertRaises((IntegrityError, ValidationError)):
            with transaction.atomic():
                operation()

    def test_duplicate_labels_do_not_merge_historical_recipe_records(self):
        self.assertNotEqual(self.recipe.pk, self.other_recipe.pk)
        self.assertEqual(RecipeVersion.objects.filter(version_label="CHECK.1").count(), 2)

    def test_component_requires_at_most_one_material_reference(self):
        self.assert_rejected(lambda: RecipeComponent.objects.create(group=self.group, position=1, unbatched_material=self.material, specified_lot=self.lot))
        row = RecipeComponent.objects.create(group=self.group, position=1, specified_lot=self.lot)
        self.assertEqual(row.material, self.material)

    def test_zero_and_unknown_quantity_remain_distinct(self):
        zero = RecipeComponent.objects.create(group=self.group, position=1, quantity_value=0)
        unknown = RecipeComponent.objects.create(group=self.group, position=2)
        zero.refresh_from_db()
        unknown.refresh_from_db()
        self.assertEqual(zero.quantity_value, Decimal("0"))
        self.assertIsNone(unknown.quantity_value)
        self.assert_rejected(lambda: RecipeComponent.objects.create(group=self.group, position=3, quantity_value=-1))
        for value in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(value=value):
                self.assert_rejected(lambda: RecipeComponent.objects.create(group=self.group, position=3, quantity_value=Decimal(value)))

    def test_unchecked_filling_formula_is_not_promoted_to_mass(self):
        self.assert_rejected(lambda: FillingPlanLine.objects.create(recipe_version=self.recipe, input_group=self.group, mass_g=123, mass_status="calculation_unchecked"))
        unchecked = FillingPlanLine.objects.create(recipe_version=self.recipe, input_group=self.group, mass_status="calculation_unchecked")
        self.assertIsNone(unchecked.mass_g)

    def test_filling_group_belongs_to_recipe_on_validation(self):
        line = FillingPlanLine(recipe_version=self.other_recipe, input_group=self.group)
        with self.assertRaises(ValidationError):
            line.full_clean()

    def test_recipe_property_requires_exactly_one_owner(self):
        self.assert_rejected(lambda: RecipeProperty.objects.create(recipe_version=self.recipe, group=self.group, property_code="NCO", value=10))
        self.assert_rejected(lambda: RecipeProperty.objects.create(property_code="NCO", value=10))
        RecipeProperty.objects.create(group=self.group, property_code="NCO", value=10)

    def test_only_one_confirmed_match_but_multiple_candidates_are_preserved(self):
        RecipeMatch.objects.create(session=self.card, recipe_version=self.recipe, status="confirmed")
        self.assert_rejected(lambda: RecipeMatch.objects.create(session=self.card, recipe_version=self.other_recipe, status="confirmed"))
        RecipeMatch.objects.create(session=self.card, recipe_version=self.other_recipe, status="candidate")
        self.assertEqual(self.card.recipe_matches.count(), 2)
        self.assertEqual(self.card.confirmed_recipe, self.recipe)

    def test_inconsistent_historical_dates_are_retained_for_review(self):
        self.card.recorded_tested_on = "2026-01-01"
        self.card.recorded_manufactured_on = "2026-02-01"
        self.card.full_clean()
        self.card.save()

    def test_result_state_does_not_fabricate_missing_values(self):
        self.assert_rejected(lambda: Result.objects.create(test_run=self.test_run, metric=self.metric, value_state="present"))
        self.assert_rejected(lambda: Result.objects.create(test_run=self.test_run, metric=self.metric, origin="calculated", numeric_value=1))
        unchecked = Result.objects.create(test_run=self.test_run, metric=self.metric, origin="calculated", value_state="formula_unchecked")
        self.assertIsNone(unchecked.numeric_value)
        self.assertIsNone(unchecked.text_value)

    def test_measurement_has_one_value_and_matches_metric_kind(self):
        self.assert_rejected(lambda: Result.objects.create(test_run=self.test_run, metric=self.metric, numeric_value=1, text_value="1"))
        with self.assertRaises(ValidationError):
            Result(test_run=self.test_run, metric=self.text_metric, numeric_value=1).full_clean()
        with self.assertRaises(ValidationError):
            Result(test_run=self.test_run, metric=self.metric, text_value="Не число").full_clean()

    def test_conditioning_duration_and_unit_are_paired(self):
        self.assert_rejected(lambda: ConditioningEvent.objects.create(session=self.card, target_kind="blend", step_no=1, kind="aging", duration_value=1))
        self.assert_rejected(lambda: ConditioningEvent.objects.create(session=self.card, target_kind="blend", step_no=1, kind="aging", duration_unit="месяц"))
        row = ConditioningEvent.objects.create(session=self.card, target_kind="blend", step_no=1, kind="aging", duration_value=1, duration_unit="месяц")
        self.assertEqual(row.duration_unit, "месяц")

    def test_source_cell_never_treats_formula_cache_as_raw_value(self):
        file = SourceFile.objects.create(original_path="TEST.xlsx", sha256="0" * 64)
        sheet = SourceSheet.objects.create(file=file, name="Тест")
        self.assert_rejected(lambda: SourceCell.objects.create(sheet=sheet, address="A1", cell_kind="n", raw_value="2", formula_text="=1+1"))
        self.assert_rejected(lambda: SourceCell.objects.create(sheet=sheet, address="A1", cell_kind="n", cached_value="2"))
        row = SourceCell.objects.create(sheet=sheet, address="A1", cell_kind="n", formula_text="=1+1", cached_value="2")
        self.assertIsNone(row.raw_value)

    def test_result_dependencies_reject_direct_and_indirect_cycles(self):
        one = Result.objects.create(test_run=self.test_run, metric=self.metric, numeric_value=1)
        two = Result.objects.create(test_run=self.test_run, metric=self.metric, numeric_value=2)
        self.assert_rejected(lambda: ResultDependency.objects.create(result=one, input_result=one, role="input"))
        ResultDependency.objects.create(result=one, input_result=two, role="input")
        with self.assertRaises(ValidationError):
            ResultDependency(result=two, input_result=one, role="input").full_clean()

    def test_history_cannot_be_rewritten_or_deleted_through_model_api(self):
        event = ChangeEvent.objects.create(employee=self.employee, entity_type="recipe", entity_id=self.recipe.pk, action="created", summary="Исходная запись")
        event.summary = "Правка истории"
        self.assert_rejected(event.save)
        self.assert_rejected(lambda: ChangeEvent.objects.filter(pk=event.pk).update(summary="Правка"))
        self.assert_rejected(lambda: ChangeEvent.objects.bulk_update([event], ["summary"]))
        self.assert_rejected(event.delete)
        self.assert_rejected(lambda: ChangeEvent.objects.filter(pk=event.pk).delete())
        event.refresh_from_db()
        self.assertEqual(event.summary, "Исходная запись")
