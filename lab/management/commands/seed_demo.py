"""Create explicitly synthetic data without opening archive files."""
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from lab.demo import make_recipe, upgrade_winter, winter_catalog, winter_templates

from lab.models import (
    ChangeEvent, ConditioningEvent, EmployeeProfile, FillingPlanLine, Material,
    MaterialLot, Metric, MixtureType, Observation, RecipeComponent, RecipeGroup,
    RecipeMatch, RecipeVersion, Result, SessionCondition, TestRun, TestSession,
)


class Command(BaseCommand):
    help = "Создать вымышленные демонстрационные записи. Excel не читается."

    def add_arguments(self, parser):
        parser.add_argument("--yes", "--confirm-demo", dest="confirm_demo", action="store_true", help="Явно подтвердить создание демонстрационных данных")
        parser.add_argument('--upgrade-winter', action='store_true', help='Дополнить прежний учебный набор зимними шаблонами и новыми версиями, сохранив прежние составы')

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEMO_MODE:
            raise CommandError("Команда разрешена только при DEMO_MODE=true.")
        if not options["confirm_demo"]:
            raise CommandError("Добавьте --confirm-demo для создания вымышленных данных.")
        if MixtureType.objects.filter(code="DEMO-316").exists():
            if options.get('upgrade_winter'):
                author, _ = EmployeeProfile.objects.get_or_create(full_name='DEMO · Анна Волкова', defaults={'role': 'author'})
                upgrade_winter(author)
                self.stdout.write(self.style.SUCCESS('Зимний учебный набор обновлён: шаблоны, партии, свойства и новые версии. Прежние составы и пользовательские записи сохранены.'))
            else:
                self.stdout.write("Демонстрационные данные уже созданы. Существующие записи не изменены. Для обновления учебного набора: --confirm-demo --upgrade-winter.")
            return
        author, _ = EmployeeProfile.objects.get_or_create(full_name="DEMO · Анна Волкова", defaults={"role": "author"})
        colleague, _ = EmployeeProfile.objects.get_or_create(full_name="DEMO · Михаил Соколов", defaults={"role": "author"})
        EmployeeProfile.objects.get_or_create(full_name="DEMO · Елена Морозова", defaults={"role": "reader"})
        mixture = MixtureType.objects.create(code="DEMO-316", title="Зимняя пена")
        second = MixtureType.objects.create(code="DEMO-302", title="Зимняя улучшенная пена")
        source, materials, lots = winter_catalog(author)
        calculations, templates = winter_templates(author, [mixture, second], materials)
        metrics = {}
        for code, label, unit, kind in [
            ("DEMO_TEMP", "Температура испытания", "°C", "numeric"),
            ("DEMO_RH", "Относительная влажность", "%", "numeric"),
            ("DEMO_CAN_TEMP", "Температура баллона", "°C", "numeric"),
            ("DEMO_YIELD", "Выход пены (учебный метод)", "л", "numeric"),
            ("DEMO_TACK", "Время до отлипа", "мин", "numeric"),
            ("DEMO_CUT", "Время до резки", "мин", "numeric"),
            ("DEMO_DENSITY", "Плотность пены", "кг/м³", "numeric"),
            ("DEMO_STRUCTURE", "Структура пены", None, "text"),
        ]:
            metrics[code] = Metric.objects.create(code=code, label=label, unit_code=unit, value_kind=kind)
        recipes = []
        titles = ['Зимняя пена · базовый состав', 'Зимняя пена · контрольный повтор', 'Зимняя пена · холодный режим',
                  'Зимняя улучшенная пена · базовый состав', 'Зимняя улучшенная пена · подбор катализатора', 'Зимняя улучшенная пена · контрольный состав']
        for i, title in enumerate(titles):
            recipe = make_recipe(author if i % 2 == 0 else colleague, f'DEMO-{316 if i < 3 else 302}.{i + 1:02d}', title,
                                 mixture if i < 3 else second, templates[0 if i < 3 else 1],
                                 materials=materials, lots=lots, improved=i >= 3, status='draft' if i == 4 else 'published',
                                 recorded_on=date(2026, 10, 20) + timedelta(days=i))
            recipes.append(recipe)
        subjects = ['Зимняя пена: базовый состав при −10 °C', 'Зимняя пена: контрольный повтор', 'Зимняя пена: время отлипа',
                    'Зимняя пена: холодный режим −18 °C', 'Зимняя пена: оценка структуры', 'Зимняя пена: выход и плотность',
                    'Зимняя улучшенная пена: выход при −10 °C', 'Зимняя улучшенная пена: повторное определение',
                    'Зимняя улучшенная пена: план новой версии', 'Зимняя улучшенная пена: контрольный состав']
        for i, title in enumerate(subjects):
            recipe = recipes[min(i // 2, 5)] if i < 8 else recipes[4 if i == 8 else 5]
            status = "completed" if i < 5 or i == 9 else "active" if i < 8 else "draft"
            card = TestSession.objects.create(identifier=f"DEMO-2026-{i + 1:03d}", title=title, recorded_manufactured_on=date(2026, 10, 23) + timedelta(days=i // 2), recorded_tested_on=date(2026, 10, 24) + timedelta(days=i // 2) if status != "draft" else None, status=status, notes="Демонстрационная карточка. Все значения вымышлены.", created_by=author if i % 2 == 0 else colleague)
            if recipe.status == "published":
                RecipeMatch.objects.create(session=card, recipe_version=recipe, status="confirmed", reason="Учебная связь задана при создании демобазы; не подтверждает фактическое изготовление.")
            for code, value in [("DEMO_TEMP", -18 if i == 3 else -10), ("DEMO_RH", 55), ("DEMO_CAN_TEMP", 20)]:
                SessionCondition.objects.create(session=card, metric=metrics[code], numeric_value=Decimal(value))
            ChangeEvent.objects.create(employee=card.created_by, entity_type="test", entity_id=card.pk, action="created", summary=f"Создана учебная карточка {card.identifier}", after={"identifier": card.identifier, "status": "draft", "demo": True})
            if status != "draft":
                run = TestRun.objects.create(session=card, scope="Основное испытание", method_code="DEMO-FOAM-01 · учебный метод")
                for code, value in [("DEMO_YIELD", Decimal("61.2") + i * Decimal("1.3")), ("DEMO_TACK", Decimal("8.5") - i * Decimal("0.2")), ("DEMO_CUT", Decimal("35") - i), ("DEMO_DENSITY", Decimal("24.2") + i * Decimal("0.3"))]:
                    Result.objects.create(test_run=run, metric=metrics[code], numeric_value=value, origin="recorded", value_state="present")
                Result.objects.create(test_run=run, metric=metrics["DEMO_STRUCTURE"], text_value="Равномерная мелкоячеистая", origin="recorded", value_state="present")
                Observation.objects.create(test_run=run, sequence_no=1, text_value="Учебное наблюдение: равномерный выход. После выдержки видимых дефектов не отмечено." if i % 3 else "Учебное наблюдение: небольшое замедление старта при низкой температуре.")
                ConditioningEvent.objects.create(session=card, target_kind="can", step_no=1, kind="conditioning", duration_value=Decimal("24"), duration_unit="ч", temperature_c=Decimal("20"))
                ChangeEvent.objects.create(employee=card.created_by, entity_type="test", entity_id=card.pk, action="result_added", summary=f"Записаны учебные результаты {card.identifier}", after={"results_count": 5, "demo": True})
            if status == "completed":
                ChangeEvent.objects.create(employee=card.created_by, entity_type="test", entity_id=card.pk, action="updated", summary=f"Завершена учебная карточка {card.identifier}", before={"status": "active"}, after={"status": "completed", "demo": True})
        self.stdout.write(self.style.SUCCESS("Создан зимний учебный набор: 6 рецептур, 10 карточек, 2 шаблона рецептур, 2 шаблона расчётов, партии и источник. Excel не читался."))
