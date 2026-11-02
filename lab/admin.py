"""Technical directories are editable; laboratory records use audited application forms."""

from django.contrib import admin

from . import models


@admin.register(models.EmployeeProfile)
class EmployeeProfileAdmin(admin.ModelAdmin):
    list_display = ["full_name", "role", "is_active"]
    list_filter = ["role", "is_active"]
    search_fields = ["full_name"]


@admin.register(models.MixtureType)
class MixtureTypeAdmin(admin.ModelAdmin):
    list_display = ["code", "title"]
    search_fields = ["code", "title"]


@admin.register(models.Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ["name", "category", "supplier", "density_g_ml", "price_per_kg"]
    search_fields = ["name"]

    def has_delete_permission(self, request, obj=None):
        # Fixed recipe templates refer to directory identities inside JSON.
        return False


@admin.register(models.MaterialLot)
class MaterialLotAdmin(admin.ModelAdmin):
    list_display = ["material", "lot_code", "status", "supplier", "received_on", "expires_on"]
    list_filter = ["status"]
    search_fields = ["material__name", "lot_code"]

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(models.Metric)
class MetricAdmin(admin.ModelAdmin):
    list_display = ["code", "label", "unit_code", "value_kind"]
    list_filter = ["value_kind"]
    search_fields = ["code", "label"]


class ReadOnlyRecordAdmin(admin.ModelAdmin):
    """Avoid a second unaudited editing path for immutable or sourced records."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(models.AssistantRequestLog)
class AssistantRequestLogAdmin(ReadOnlyRecordAdmin):
    readonly_fields = [field.name for field in models.AssistantRequestLog._meta.fields]
    list_display = ["created_at", "employee_name", "question_preview", "status", "model", "duration_ms",
                    "provider_call_count", "tool_call_count",
                    "prompt_tokens", "completion_tokens", "usage_call_count"]
    list_filter = ["status", "employee", "model"]
    search_fields = ["employee_name", "question", "reply"]
    date_hierarchy = "created_at"
    list_select_related = ["employee"]

    @admin.display(description="Вопрос")
    def question_preview(self, obj):
        return obj.question[:100]


@admin.register(models.RecipeVersion)
class RecipeVersionAdmin(ReadOnlyRecordAdmin):
    list_display = ["version_label", "title", "mixture_type", "status", "created_by", "created_at"]
    list_filter = ["status", "mixture_type"]
    search_fields = ["version_label", "title"]


@admin.register(models.TestSession)
class TestSessionAdmin(ReadOnlyRecordAdmin):
    list_display = ["identifier", "title", "status", "recorded_tested_on", "created_by"]
    list_filter = ["status"]
    search_fields = ["identifier", "title"]


@admin.register(models.ChangeEvent)
class ChangeEventAdmin(ReadOnlyRecordAdmin):
    list_display = ["created_at", "employee", "entity_type", "entity_id", "action", "summary"]
    list_filter = ["entity_type", "action"]
    search_fields = ["summary", "employee__full_name"]


for model in [
    models.SourceFile, models.SourceSheet, models.SourceCell, models.RecipeGroup,
    models.RecipeComponent, models.FillingPlanLine, models.RecipeProperty,
    models.RecipeMatch, models.TestRun, models.SessionCondition, models.Result,
    models.ResultDependency, models.Observation, models.ConditioningEvent,
    models.CalculationTemplate, models.RecipeTemplate, models.CalculationRecord,
]:
    admin.site.register(model, ReadOnlyRecordAdmin)

admin.site.site_header = "AMAI LAB · Администрирование"
admin.site.site_title = "AMAI LAB"
admin.site.index_title = "Справочники и технический просмотр"
