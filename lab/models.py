"""Documentary LIMS core. Physical samples and actual manufacturing stay out of scope."""

from .ui_text import UI_TEXT

from functools import reduce
from operator import or_
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.db.models import F, Q


def exactly_one(*fields):
    return reduce(or_, (
        reduce(lambda q, other: q & Q(**{f"{other}__isnull": other != field}), fields, Q())
        for field in fields
    ))


def at_most_one(*fields):
    return exactly_one(*fields) | reduce(lambda q, field: q & Q(**{f"{field}__isnull": True}), fields, Q())


def number(**kwargs):
    return models.DecimalField(max_digits=22, decimal_places=8, null=True, blank=True, **kwargs)


def source_cell(related_name="+"):
    return models.ForeignKey("SourceCell", on_delete=models.PROTECT, null=True, blank=True, related_name=related_name)


def source_upload_path(instance, filename):
    suffix = Path(filename).suffix.lower()
    suffix = suffix if len(suffix) <= 16 and suffix.lstrip('.').isalnum() else ''
    return f"sources/{uuid4().hex}{suffix}"


def default_calculation_operations():
    """Retained for historical migrations; all operations are always enabled."""
    return ["mass", "cost", "volume", "totalCost", "density"]


class PriceCurrency(models.TextChoices):
    RUB = "RUB", "₽ (RUB)"
    EUR = "EUR", "€ (EUR)"
    USD = "USD", "$ (USD)"


def currency_symbol(currency):
    return {"RUB": "₽", "EUR": "€", "USD": "$"}.get(currency, currency)


class EmployeeProfile(models.Model):
    class Role(models.TextChoices):
        READER = "reader", "Читатель"
        AUTHOR = "author", "Автор"

    full_name = models.CharField("ФИО", max_length=180)
    role = models.CharField("Роль", max_length=20, choices=Role.choices, default=Role.READER)
    is_active = models.BooleanField("Активен", default=True)

    class Meta:
        ordering = ["full_name"]
        verbose_name = "Сотрудник"
        verbose_name_plural = "Сотрудники"
        constraints = [models.CheckConstraint(condition=Q(role__in=["reader", "author"]), name="employee_valid_role")]

    @property
    def can_write(self):
        return self.is_active and self.role == self.Role.AUTHOR

    def __str__(self):
        return self.full_name


class Feedback(models.Model):
    text = models.TextField("Сообщение", max_length=5000)
    employee = models.ForeignKey(
        EmployeeProfile, verbose_name="Сотрудник", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="feedback",
    )
    employee_name = models.CharField("Автор на момент отправки", max_length=180)
    created_at = models.DateTimeField(
        "Дата отправки", auto_now_add=True, db_index=True,
    )

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = "Сообщение обратной связи"
        verbose_name_plural = "Обратная связь"

    def __str__(self):
        return f"{self.employee_name}: {self.text[:80]}"


class AssistantRequestLog(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "В работе"
        COMPLETED = "completed", "Завершён"
        FAILED = "failed", "Ошибка"
        INTERRUPTED = "interrupted", "Прерван"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    employee = models.ForeignKey(EmployeeProfile, on_delete=models.SET_NULL, null=True, related_name="assistant_logs")
    employee_name = models.CharField("Сотрудник на момент запроса", max_length=180)
    employee_role = models.CharField("Роль на момент запроса", max_length=20)
    created_at = models.DateTimeField("Начало", auto_now_add=True, db_index=True)
    finished_at = models.DateTimeField("Завершение", null=True, blank=True)
    duration_ms = models.PositiveIntegerField("Длительность, мс", null=True, blank=True)
    status = models.CharField("Статус", max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    page = models.CharField("Страница", max_length=2048, blank=True)
    model = models.CharField("Модель", max_length=255, blank=True)
    messages = models.JSONField("Переданная история", default=list)
    question = models.TextField("Вопрос")
    reply = models.TextField("Ответ или частичный текст", blank=True)
    progress = models.JSONField("Публичные пояснения перед ответом", default=list, blank=True)
    sources = models.JSONField("Источники", default=list, blank=True)
    tools_used = models.JSONField("Инструменты", default=list, blank=True)
    provider_calls = models.JSONField(
        "Обращения к провайдеру", default=list, blank=True,
    )
    tool_calls = models.JSONField(
        "Вызовы инструментов и размер результатов", default=list, blank=True,
    )
    provider_call_count = models.PositiveIntegerField(
        "Число обращений к провайдеру", default=0,
    )
    tool_call_count = models.PositiveIntegerField(
        "Число вызовов инструментов", default=0,
    )
    usage_call_count = models.PositiveIntegerField(
        "Обращения со статистикой токенов", default=0,
    )
    prompt_tokens = models.PositiveBigIntegerField(
        "Входные токены (известная часть)", null=True, blank=True,
    )
    completion_tokens = models.PositiveBigIntegerField(
        "Выходные токены (известная часть)", null=True, blank=True,
    )
    error_code = models.CharField("Код ошибки", max_length=80, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = "Запрос помощнику"
        verbose_name_plural = "Журнал помощника"
        constraints = [models.CheckConstraint(
            condition=Q(status__in=["pending", "completed", "failed", "interrupted"]),
            name="assistant_log_valid_status",
        )]

    def __str__(self):
        return f"{self.employee_name}: {self.question[:80]}"


class SourceFile(models.Model):
    class Kind(models.TextChoices):
        FILE = "file", "Файл"
        LINK = "link", "Ссылка"
        NOTE = "note", "Запись"

    original_path = models.TextField("Исходный путь")
    title = models.CharField("Название", max_length=255, blank=True)
    kind = models.CharField("Тип источника", max_length=20, choices=Kind.choices, default=Kind.FILE)
    url = models.URLField("Ссылка", max_length=1000, blank=True)
    notes = models.TextField("Примечание", blank=True)
    attachment = models.FileField("Файл", upload_to=source_upload_path, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    created_by = models.ForeignKey(EmployeeProfile, on_delete=models.PROTECT, null=True, blank=True, related_name="sources")
    sha256 = models.CharField(max_length=64, blank=True, default="", validators=[RegexValidator(r"^[0-9a-f]{64}$", "Нужен SHA-256 в нижнем регистре.")])
    date_system = models.CharField(max_length=4, choices=[("1900", "1900"), ("1904", "1904")], default="1900")

    class Meta:
        ordering = ["original_path", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(date_system__in=["1900", "1904"]), name="source_valid_date_system"),
            models.CheckConstraint(condition=Q(sha256="") | Q(sha256__regex=r"^[0-9a-f]{64}$"), name="source_valid_sha256"),
            models.CheckConstraint(condition=Q(kind__in=["file", "link", "note"]), name="source_valid_kind"),
        ]

    def __str__(self):
        return self.title or self.original_path


class SourceSheet(models.Model):
    file = models.ForeignKey(SourceFile, on_delete=models.PROTECT, related_name="sheets")
    name = models.CharField(max_length=255)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["file", "name"], name="source_sheet_unique_name")]

    def __str__(self):
        return f"{self.file} · {self.name}"


class SourceCell(models.Model):
    sheet = models.ForeignKey(SourceSheet, on_delete=models.PROTECT, related_name="cells")
    address = models.CharField(max_length=40)
    cell_kind = models.CharField(max_length=40)
    raw_value = models.TextField(null=True, blank=True)
    formula_text = models.TextField(null=True, blank=True)
    cached_value = models.TextField(null=True, blank=True)
    number_format = models.TextField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["sheet", "address"], name="source_cell_unique_address"),
            models.CheckConstraint(condition=Q(formula_text__isnull=True) | Q(raw_value__isnull=True), name="source_cell_formula_or_raw"),
            models.CheckConstraint(condition=Q(formula_text__isnull=False) | Q(cached_value__isnull=True), name="source_cell_cache_requires_formula"),
        ]

    def __str__(self):
        return f"{self.sheet.name}!{self.address}"


class MixtureType(models.Model):
    code = models.CharField("Код", max_length=80, unique=True)
    title = models.CharField("Название", max_length=255, null=True, blank=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} · {self.title}" if self.title else self.code


class RecipeVersion(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        PUBLISHED = "published", "Опубликована"
        ARCHIVED = "archived", "В архиве"

    mixture_type = models.ForeignKey(MixtureType, on_delete=models.PROTECT, null=True, blank=True, related_name="recipes")
    version_label = models.CharField("Обозначение версии", max_length=120)
    recorded_on = models.DateField("Дата рецептуры", null=True, blank=True)
    source_sheet = models.ForeignKey(SourceSheet, on_delete=models.PROTECT, null=True, blank=True, related_name="recipes")
    title = models.CharField("Название", max_length=255, blank=True)
    status = models.CharField("Статус", max_length=20, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField("Примечания", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(EmployeeProfile, on_delete=models.PROTECT, null=True, blank=True, related_name="recipes")
    recipe_template = models.ForeignKey("RecipeTemplate", on_delete=models.PROTECT, null=True, blank=True, related_name="recipes")
    batch_mass_g = models.DecimalField("Масса смеси для расчета, г", max_digits=22, decimal_places=8, default=Decimal("1000"), validators=[MinValueValidator(Decimal("0.00000001"))])
    template_snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [models.CheckConstraint(condition=Q(status__in=["draft", "published", "archived"]), name="recipe_valid_status"), models.CheckConstraint(condition=Q(batch_mass_g__gt=0), name="recipe_positive_batch_mass")]

    def __str__(self):
        return self.version_label


class RecipeGroup(models.Model):
    class QuantityBasis(models.TextChoices):
        MASS_PERCENT = "mass_percent", "Массовые доли, %"
        WEIGHT_PARTS = "weight_parts", "Массовые части"
        UNKNOWN = "unknown", "Не установлено"

    class Completeness(models.TextChoices):
        UNKNOWN = "unknown", "Не установлена"
        COMPLETE = "complete", "Полный состав"
        PARTIAL = "partial", "Частичный состав"

    recipe_version = models.ForeignKey(RecipeVersion, on_delete=models.PROTECT, related_name="groups")
    label = models.CharField("Группа", max_length=120)
    quantity_basis = models.CharField(max_length=20, choices=QuantityBasis.choices, default=QuantityBasis.UNKNOWN)
    completeness = models.CharField(max_length=20, choices=Completeness.choices, default=Completeness.UNKNOWN)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(condition=Q(quantity_basis__in=["mass_percent", "weight_parts", "unknown"]), name="recipe_group_valid_basis"),
            models.CheckConstraint(condition=Q(completeness__in=["unknown", "complete", "partial"]), name="recipe_group_valid_completeness"),
        ]

    def __str__(self):
        return f"{self.recipe_version} · {self.label}"


class MaterialCategory(models.Model):
    name = models.CharField("Категория материалов", max_length=100, unique=True)
    description = models.TextField("Описание", blank=True)

    class Meta:
        ordering = ["name", "pk"]
        verbose_name = "Категория материалов"
        verbose_name_plural = "Категории материалов"

    def __str__(self):
        return self.name


class Material(models.Model):
    name = models.CharField("Материал", max_length=255)
    category = models.CharField("Категория", max_length=100, blank=True)
    supplier = models.CharField("Поставщик", max_length=255, blank=True)
    notes = models.TextField("Примечание", blank=True)

    class Meta:
        ordering = ["name", "pk"]

    def __str__(self):
        return self.name


class MaterialLot(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Доступна"
        QUARANTINE = "quarantine", "Карантин"
        EXHAUSTED = "exhausted", "Закрыта"

    material = models.ForeignKey(Material, on_delete=models.PROTECT, related_name="lots")
    lot_code = models.CharField("Партия", max_length=160)
    supplier = models.CharField("Поставщик партии", max_length=255, blank=True)
    received_on = models.DateField("Дата поступления", null=True, blank=True)
    expires_on = models.DateField("Годна до", null=True, blank=True)
    status = models.CharField("Статус", max_length=20, choices=Status.choices, default=Status.ACTIVE)
    density_g_ml = number(verbose_name="Плотность партии, г/мл", validators=[MinValueValidator(Decimal("0.00000001"))])
    price_per_kg = number(verbose_name="Цена партии за кг", validators=[MinValueValidator(0)])
    price_currency = models.CharField(
        "Валюта цены партии", max_length=3,
        choices=PriceCurrency.choices, default="RUB",
    )
    import_snapshot = models.JSONField(default=dict, blank=True, editable=False)
    source = models.ForeignKey(SourceFile, on_delete=models.PROTECT, null=True, blank=True, related_name="lots")
    notes = models.TextField("Примечание", blank=True)

    class Meta:
        ordering = ["material__name", "lot_code", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(status__in=["active", "quarantine", "exhausted"]), name="lot_valid_status"),
            models.CheckConstraint(condition=Q(density_g_ml__isnull=True) | Q(density_g_ml__gt=0), name="lot_positive_density"),
            models.CheckConstraint(condition=Q(price_per_kg__isnull=True) | Q(price_per_kg__gte=0), name="lot_nonnegative_price"),
            models.CheckConstraint(condition=Q(price_currency__in=PriceCurrency.values), name="lot_valid_currency"),
        ]

    @property
    def effective_density_g_ml(self):
        return self.density_g_ml

    @property
    def effective_price_per_kg(self):
        return self.price_per_kg

    @property
    def effective_price_currency(self):
        return self.price_currency

    @property
    def price_unit(self):
        return f"{currency_symbol(self.price_currency)}/кг"

    @property
    def effective_price_unit(self):
        return f"{currency_symbol(self.effective_price_currency)}/кг"

    def __str__(self):
        return f"{self.material} · {self.lot_code}"


class RecipeComponent(models.Model):
    group = models.ForeignKey(RecipeGroup, on_delete=models.PROTECT, related_name="components")
    position = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    unbatched_material = models.ForeignKey(Material, on_delete=models.PROTECT, null=True, blank=True, related_name="recipe_components")
    specified_lot = models.ForeignKey(MaterialLot, on_delete=models.PROTECT, null=True, blank=True, related_name="recipe_components")
    name_cell = source_cell()
    quantity_value = number(validators=[MinValueValidator(0)])
    quantity_cell = source_cell()

    class Meta:
        ordering = ["position", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(position__gt=0), name="recipe_component_positive_position"),
            models.CheckConstraint(condition=Q(quantity_value__gte=0) | Q(quantity_value__isnull=True), name="recipe_component_nonnegative_qty"),
            models.CheckConstraint(condition=at_most_one("unbatched_material", "specified_lot"), name="recipe_component_one_material"),
        ]

    @property
    def material(self):
        return self.specified_lot.material if self.specified_lot_id else self.unbatched_material

    @property
    def display_name(self):
        if self.material:
            return self.material.name
        if self.name_cell:
            return self.name_cell.raw_value or "Не нормализовано"
        return "Материал не указан"

    def __str__(self):
        return self.display_name


class FillingPlanLine(models.Model):
    class MassStatus(models.TextChoices):
        RECORDED = "recorded", "Записана"
        CALCULATION_UNCHECKED = "calculation_unchecked", "Расчёт не проверен"
        CALCULATION_CHECKED = "calculation_checked", "Расчёт проверен"
        UNKNOWN = "unknown", "Не установлена"

    recipe_version = models.ForeignKey(RecipeVersion, on_delete=models.PROTECT, related_name="filling_plan")
    input_group = models.ForeignKey(RecipeGroup, on_delete=models.PROTECT, null=True, blank=True, related_name="filling_lines")
    unbatched_material = models.ForeignKey(Material, on_delete=models.PROTECT, null=True, blank=True, related_name="filling_lines")
    specified_lot = models.ForeignKey(MaterialLot, on_delete=models.PROTECT, null=True, blank=True, related_name="filling_lines")
    label_cell = source_cell()
    mass_g = number(validators=[MinValueValidator(0)])
    mass_cell = source_cell()
    mass_status = models.CharField(max_length=24, choices=MassStatus.choices, default=MassStatus.UNKNOWN)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(condition=at_most_one("input_group", "unbatched_material", "specified_lot"), name="filling_line_one_input"),
            models.CheckConstraint(condition=Q(mass_g__gte=0) | Q(mass_g__isnull=True), name="filling_line_nonnegative_mass"),
            models.CheckConstraint(condition=(Q(mass_status__in=["recorded", "calculation_checked"], mass_g__isnull=False) | Q(mass_status__in=["calculation_unchecked", "unknown"], mass_g__isnull=True)), name="filling_line_mass_state"),
        ]

    def clean(self):
        super().clean()
        if self.input_group_id and self.input_group.recipe_version_id != self.recipe_version_id:
            raise ValidationError({"input_group": "Группа должна принадлежать этой версии рецептуры."})

    @property
    def display_name(self):
        if self.input_group_id:
            return self.input_group.label
        if self.specified_lot_id:
            return str(self.specified_lot)
        if self.unbatched_material_id:
            return self.unbatched_material.name
        if self.label_cell_id:
            return self.label_cell.raw_value or "Не нормализовано"
        return "Компонент плана"

    def __str__(self):
        return self.display_name


class RecipeProperty(models.Model):
    class Origin(models.TextChoices):
        RECORDED = "recorded", "Из исходной записи"
        CARRIED_FORWARD = "carried_forward", "Перенесено с предыдущего листа"

    recipe_version = models.ForeignKey(RecipeVersion, on_delete=models.PROTECT, null=True, blank=True, related_name="properties")
    group = models.ForeignKey(RecipeGroup, on_delete=models.PROTECT, null=True, blank=True, related_name="properties")
    component = models.ForeignKey(RecipeComponent, on_delete=models.PROTECT, null=True, blank=True, related_name="properties")
    filling_plan_line = models.ForeignKey(FillingPlanLine, on_delete=models.PROTECT, null=True, blank=True, related_name="properties")
    property_code = models.CharField(max_length=100)
    value = models.DecimalField(max_digits=22, decimal_places=8)
    unit_code = models.CharField(max_length=60, null=True, blank=True)
    source_cell = source_cell()
    origin = models.CharField(
        "Происхождение свойства", max_length=24,
        choices=Origin.choices, default=Origin.RECORDED,
    )
    provenance_note = models.TextField("Основание переноса", blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=exactly_one("recipe_version", "group", "component", "filling_plan_line"), name="recipe_property_exactly_one_owner")]

    def __str__(self):
        return self.property_code


class TestSession(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        ACTIVE = "active", "В работе"
        COMPLETED = "completed", "Завершено"

    source_sheet = models.ForeignKey(SourceSheet, on_delete=models.PROTECT, null=True, blank=True, related_name="test_sessions")
    identifier_cell = source_cell()
    recorded_manufactured_on = models.DateField("Дата изготовления по записи", null=True, blank=True)
    recorded_tested_on = models.DateField("Дата испытания", null=True, blank=True)
    identifier = models.CharField("Номер карточки", max_length=120, blank=True)
    title = models.CharField("Название испытания", max_length=255, blank=True)
    status = models.CharField("Статус", max_length=20, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField("Примечания", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(EmployeeProfile, on_delete=models.PROTECT, null=True, blank=True, related_name="test_sessions")

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [models.CheckConstraint(condition=Q(status__in=["draft", "active", "completed"]), name="test_session_valid_status")]

    @property
    def confirmed_recipe(self):
        match = self.recipe_matches.select_related("recipe_version").filter(status="confirmed").first()
        return match.recipe_version if match else None

    def __str__(self):
        return self.identifier or f"КИ-{self.pk}"


class RecipeMatch(models.Model):
    class Status(models.TextChoices):
        CANDIDATE = "candidate", "Кандидат"
        CONFIRMED = "confirmed", "Подтверждена"
        REJECTED = "rejected", "Отклонена"

    session = models.ForeignKey(TestSession, on_delete=models.PROTECT, related_name="recipe_matches")
    recipe_version = models.ForeignKey(RecipeVersion, on_delete=models.PROTECT, related_name="session_matches")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CANDIDATE)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["session", "recipe_version"], name="recipe_match_unique_pair"),
            models.UniqueConstraint(fields=["session"], condition=Q(status="confirmed"), name="recipe_match_one_confirmed"),
            models.CheckConstraint(condition=Q(status__in=["candidate", "confirmed", "rejected"]), name="recipe_match_valid_status"),
        ]

    def __str__(self):
        return f"{self.session} → {self.recipe_version}"


class TestRun(models.Model):
    session = models.ForeignKey(TestSession, on_delete=models.PROTECT, related_name="runs")
    scope = models.CharField("Определение / режим", max_length=255)
    method_code = models.CharField("Метод", max_length=160, null=True, blank=True)
    scope_cell = source_cell()

    class Meta:
        ordering = ["pk"]

    def __str__(self):
        return self.scope


class Metric(models.Model):
    class ValueKind(models.TextChoices):
        NUMERIC = "numeric", "Число"
        TEXT = "text", "Текст"
        ORDINAL = "ordinal", "Порядковое значение"

    code = models.CharField("Код", max_length=100, unique=True)
    label = models.CharField("Показатель", max_length=255)
    unit_code = models.CharField("Единица", max_length=60, null=True, blank=True)
    value_kind = models.CharField(max_length=20, choices=ValueKind.choices, default=ValueKind.NUMERIC)

    class Meta:
        ordering = ["label"]
        constraints = [models.CheckConstraint(condition=Q(value_kind__in=["numeric", "text", "ordinal"]), name="metric_valid_kind")]

    def __str__(self):
        return f"{self.label}, {self.unit_code}" if self.unit_code else self.label


class SessionCondition(models.Model):
    session = models.ForeignKey(TestSession, on_delete=models.PROTECT, related_name="conditions")
    metric = models.ForeignKey(Metric, on_delete=models.PROTECT, related_name="conditions")
    numeric_value = number()
    text_value = models.TextField(null=True, blank=True)
    source_cell = source_cell()

    class Meta:
        ordering = ["pk"]
        constraints = [models.CheckConstraint(condition=exactly_one("numeric_value", "text_value"), name="session_condition_one_value")]

    def clean(self):
        super().clean()
        if self.metric_id and self.numeric_value is not None and self.metric.value_kind != Metric.ValueKind.NUMERIC:
            raise ValidationError({"numeric_value": "Тип значения не соответствует показателю."})
        if self.metric_id and self.text_value is not None and self.metric.value_kind == Metric.ValueKind.NUMERIC:
            raise ValidationError({"text_value": "Для числового показателя укажите число."})

    @property
    def display_value(self):
        return self.numeric_value if self.numeric_value is not None else self.text_value


class Result(models.Model):
    class Origin(models.TextChoices):
        RECORDED = "recorded", "Записан"
        CALCULATED = "calculated", "Рассчитан"

    class ValueState(models.TextChoices):
        PRESENT = "present", "Записано"
        UNPARSED = "unparsed", "Не разобрано"
        MISSING_INPUTS = "missing_inputs", "Нет исходных данных"
        FORMULA_UNCHECKED = "formula_unchecked", "Формула не проверена"

    test_run = models.ForeignKey(TestRun, on_delete=models.PROTECT, related_name="results")
    metric = models.ForeignKey(Metric, on_delete=models.PROTECT, related_name="results")
    numeric_value = number()
    text_value = models.TextField(null=True, blank=True)
    origin = models.CharField(max_length=20, choices=Origin.choices, default=Origin.RECORDED)
    value_state = models.CharField(max_length=24, choices=ValueState.choices, default=ValueState.PRESENT)
    source_cell = source_cell()
    calculation_code = models.CharField(max_length=160, null=True, blank=True)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(condition=Q(origin__in=["recorded", "calculated"]), name="result_valid_origin"),
            models.CheckConstraint(condition=(Q(value_state="present") & exactly_one("numeric_value", "text_value")) | Q(value_state="unparsed", numeric_value__isnull=True, text_value__isnull=False) | Q(value_state__in=["missing_inputs", "formula_unchecked"], numeric_value__isnull=True, text_value__isnull=True), name="result_value_matches_state"),
            models.CheckConstraint(condition=Q(calculation_code__isnull=True) | Q(origin="calculated"), name="result_formula_only_calculated"),
            models.CheckConstraint(condition=~Q(value_state__in=["missing_inputs", "formula_unchecked"]) | Q(origin="calculated"), name="result_missing_only_calculated"),
            models.CheckConstraint(condition=~Q(origin="calculated", value_state="present") | Q(calculation_code__isnull=False), name="result_calculated_has_formula"),
        ]

    def clean(self):
        super().clean()
        if self.metric_id and self.value_state == self.ValueState.PRESENT:
            if self.numeric_value is not None and self.metric.value_kind != Metric.ValueKind.NUMERIC:
                raise ValidationError({"numeric_value": "Тип значения не соответствует показателю."})
            if self.text_value is not None and self.metric.value_kind == Metric.ValueKind.NUMERIC:
                raise ValidationError({"text_value": "Для числового показателя укажите число."})

    @property
    def display_value(self):
        return self.numeric_value if self.numeric_value is not None else self.text_value

    def __str__(self):
        return f"{self.metric.label}: {self.display_value}"


class ResultDependency(models.Model):
    result = models.ForeignKey(Result, on_delete=models.PROTECT, related_name="dependencies")
    input_result = models.ForeignKey(Result, on_delete=models.PROTECT, related_name="used_by")
    role = models.CharField(max_length=100)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["result", "role"], name="result_dependency_unique_role"),
            models.CheckConstraint(condition=~Q(result=F("input_result")), name="result_dependency_no_self_reference"),
        ]

    def clean(self):
        super().clean()
        if not self.result_id or not self.input_result_id:
            return
        pending = [self.input_result_id]
        seen = set()
        while pending:
            candidate = pending.pop()
            if candidate == self.result_id:
                raise ValidationError("Зависимости результатов не должны образовывать цикл.")
            if candidate not in seen:
                seen.add(candidate)
                pending.extend(ResultDependency.objects.filter(result_id=candidate).exclude(pk=self.pk).values_list("input_result_id", flat=True))


class Observation(models.Model):
    test_run = models.ForeignKey(TestRun, on_delete=models.PROTECT, related_name="observations")
    sequence_no = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    text_value = models.TextField("Наблюдение")
    source_cell = source_cell()

    class Meta:
        ordering = ["sequence_no", "pk"]
        constraints = [models.CheckConstraint(condition=Q(sequence_no__gt=0), name="observation_positive_sequence")]

    def __str__(self):
        return self.text_value[:100]


class ConditioningEvent(models.Model):
    session = models.ForeignKey(TestSession, on_delete=models.PROTECT, related_name="conditioning_events")
    target_kind = models.CharField(max_length=20, choices=[("blend", "Смесь"), ("can", "Баллон"), ("specimen", "Испытуемый материал")])
    step_no = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    kind = models.CharField(max_length=20, choices=[("aging", "Выдержка"), ("conditioning", "Кондиционирование")])
    duration_value = number(validators=[MinValueValidator(0)])
    duration_unit = models.CharField(max_length=60, null=True, blank=True)
    temperature_c = number()
    source_cell = source_cell()

    class Meta:
        ordering = ["step_no", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(step_no__gt=0), name="conditioning_positive_step"),
            models.CheckConstraint(condition=Q(target_kind__in=["blend", "can", "specimen"]), name="conditioning_valid_target"),
            models.CheckConstraint(condition=Q(kind__in=["aging", "conditioning"]), name="conditioning_valid_kind"),
            models.CheckConstraint(condition=Q(duration_value__gte=0) | Q(duration_value__isnull=True), name="conditioning_nonnegative_duration"),
            models.CheckConstraint(condition=Q(duration_value__isnull=True, duration_unit__isnull=True) | Q(duration_value__isnull=False, duration_unit__isnull=False), name="conditioning_duration_with_unit"),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} · {self.session}"


class ImmutableEventQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Историю изменений нельзя редактировать.")

    def delete(self):
        raise ValidationError("Историю изменений нельзя удалять.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError("Историю изменений нельзя редактировать.")


class ChangeEvent(models.Model):
    """Append-only application audit trail; selected employee is attribution, not identity proof."""

    employee = models.ForeignKey(EmployeeProfile, on_delete=models.PROTECT, null=True, blank=True, related_name="changes")
    entity_type = models.CharField(max_length=40)
    entity_id = models.PositiveBigIntegerField()
    action = models.CharField(max_length=40)
    summary = models.CharField(max_length=255)
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    objects = ImmutableEventQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["entity_type", "entity_id"], name="change_event_entity_idx")]
        verbose_name = "Событие истории"
        verbose_name_plural = "История изменений"

    def save(self, *args, **kwargs):
        if self.pk is not None and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Историю изменений нельзя редактировать.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Историю изменений нельзя удалять.")

    def __str__(self):
        return self.summary


class ImmutableDocumentQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Сохраненную версию нельзя изменять. Создайте новую версию.")

    def delete(self):
        raise ValidationError("Сохраненную версию нельзя удалять.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError("Сохраненную версию нельзя изменять.")


class ImmutableDocument(models.Model):
    objects = ImmutableDocumentQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk is not None and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Сохраненную версию нельзя изменять. Создайте новую версию.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Сохраненную версию нельзя удалять.")


def _definition_decimal(value, label):
    if value is None or value == "":
        return
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValidationError(f"{label}: ожидается неотрицательное число или пропуск.")
    try:
        decimal = Decimal(str(value))
        if not decimal.is_finite() or decimal < 0:
            raise InvalidOperation
        # The template must be representable by the application form and DB.
        models.DecimalField(max_digits=22, decimal_places=8).clean(decimal, None)
    except (InvalidOperation, ValidationError):
        raise ValidationError(f"{label}: число должно быть конечным, неотрицательным, не более 8 знаков после запятой.")


class RecipeTemplate(ImmutableDocument):
    name = models.CharField("Название", max_length=255)
    version = models.PositiveIntegerField("Версия", default=1, validators=[MinValueValidator(1)])
    description = models.TextField("Описание", blank=True)
    mixture_type = models.ForeignKey(MixtureType, on_delete=models.PROTECT, null=True, blank=True, related_name="recipe_templates")
    batch_mass_g = models.DecimalField("Расчетная масса смеси, г", max_digits=22, decimal_places=8, default=Decimal("1000"), validators=[MinValueValidator(Decimal("0.00000001"))])
    definition = models.JSONField("Структура рецептуры", default=dict, blank=True)
    created_by = models.ForeignKey(EmployeeProfile, on_delete=models.PROTECT, null=True, blank=True, related_name="recipe_templates")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "-version", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["name", "version"], name="recipe_template_unique_version"),
            models.CheckConstraint(condition=Q(version__gt=0), name="recipe_template_positive_version"),
            models.CheckConstraint(condition=Q(batch_mass_g__gt=0), name="recipe_template_positive_batch"),
        ]

    def clean(self):
        super().clean()
        data = self.definition
        if not isinstance(data, dict) or set(data) - {"components", "filling"}:
            raise ValidationError({"definition": "Структура должна содержать только components и filling."})
        groups = {}
        for key in ("components", "filling"):
            rows = data.get(key, [])
            if not isinstance(rows, list) or len(rows) > (100 if key == "components" else 50):
                raise ValidationError({"definition": "Некорректное число строк шаблона."})
            allowed = {"group_label", "material", "specified_lot", "quantity_value", "quantity_basis", "completeness"} if key == "components" else {"group_label", "material", "specified_lot", "mass_g"}
            for row in rows:
                if not isinstance(row, dict) or set(row) - allowed:
                    raise ValidationError({"definition": "Некорректные поля строки шаблона."})
                label = row.get("group_label", "")
                if not isinstance(label, str) or len(label) > 100:
                    raise ValidationError({"definition": "Некорректное название группы."})
                material, lot = row.get("material"), row.get("specified_lot")
                for model, value in ((Material, material), (MaterialLot, lot)):
                    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1 or not model.objects.filter(pk=value).exists()):
                        raise ValidationError({"definition": "Материал или партия шаблона не найдены."})
                if material is not None and lot is not None:
                    raise ValidationError({"definition": "В строке выбирают материал либо конкретную партию."})
                try:
                    _definition_decimal(row.get("quantity_value" if key == "components" else "mass_g"), "Количество")
                except ValidationError as error:
                    raise ValidationError({"definition": error.messages})
                if key == "components":
                    basis = row.get("quantity_basis", "unknown")
                    completeness = row.get("completeness", "unknown")
                    if not label or basis not in RecipeGroup.QuantityBasis.values or completeness not in RecipeGroup.Completeness.values:
                        raise ValidationError({"definition": "Для компонента нужны группа и база количества."})
                    group_spec = (basis, completeness)
                    if label in groups and groups[label] != group_spec:
                        raise ValidationError({"definition": "У одной группы должны быть одна база количества и одна полнота."})
                    groups[label] = group_spec
                elif sum(bool(item) for item in (label, material, lot)) > 1:
                    raise ValidationError({"definition": "Строка заправки относится к одной группе, материалу или партии."})
        if any(row.get("group_label") and row["group_label"] not in groups for row in data.get("filling", [])):
            raise ValidationError({"definition": "Группа плана заправки должна присутствовать в составе."})

    def __str__(self):
        return f"{self.name} · версия {self.version}"


class CalculationRecord(ImmutableDocument):
    recipe_version = models.ForeignKey(RecipeVersion, on_delete=models.PROTECT, related_name="calculations")
    group = models.ForeignKey(RecipeGroup, on_delete=models.PROTECT, null=True, blank=True, related_name="calculations")
    inputs = models.JSONField("Исходные данные", default=dict)
    outputs = models.JSONField("Результаты", default=dict)
    created_by = models.ForeignKey(EmployeeProfile, on_delete=models.PROTECT, null=True, blank=True, related_name="calculations")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def clean(self):
        super().clean()
        if self.group_id and self.group.recipe_version_id != self.recipe_version_id:
            raise ValidationError({"group": "Группа должна принадлежать рассчитываемой рецептуре."})

    def __str__(self):
        return f"Расчет {self.recipe_version} · {self.created_at}"


class AssistantConversation(models.Model):
    employee = models.ForeignKey(EmployeeProfile, on_delete=models.CASCADE)
    title = models.CharField(max_length=100, default='Новый диалог')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-updated_at', '-pk']


class AssistantJob(models.Model):
    # A browser-supplied UUID makes a repeated submission idempotent.
    id = models.UUIDField(primary_key=True, editable=False)
    conversation = models.ForeignKey(AssistantConversation, on_delete=models.CASCADE, related_name='jobs')
    journal = models.OneToOneField(AssistantRequestLog, on_delete=models.PROTECT)
    status = models.CharField(max_length=16, default='queued', db_index=True)
    form_state = models.JSONField(null=True, blank=True)
    reply = models.TextField(blank=True)
    progress = models.JSONField(default=list)
    activity = models.CharField(max_length=240, default='В очереди…')
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)

    class Meta:
        ordering = ['created_at', 'pk']
        constraints = [models.UniqueConstraint(
            fields=['conversation'], condition=Q(status__in=['queued', 'running']),
            name='assistant_one_active_job_per_chat',
        )]
