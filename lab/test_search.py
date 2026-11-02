"""Search behavior shared by user registries, administration and assistant."""

from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib import admin
from django.db.backends.postgresql.base import DatabaseWrapper
from django.db.models import Value
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from .assistant_tools import execute_tool
from .models import (
    ChangeEvent, EmployeeProfile, Material,
    MaterialCategory, MaterialLot, Metric, MixtureType, RecipeMatch,
    RecipeTemplate, RecipeVersion, SourceFile, TestSession,
)
from .search import (
    NormalizedText, SEPARATOR_PATTERN, filter_search, normalize_search,
)


class NormalizedSearchTests(TestCase):
    key = 'ПРОБА - А__Б.\u00a0В'
    queries = (
        'оба_а-б_в', 'ОБА.А.Б.В', 'оба-а_б-в', 'оба а б в',
        'оба - а__б..в', '  оба\u00a0а\u202fб\tв  ',
    )

    @classmethod
    def setUpTestData(cls):
        cls.employee = EmployeeProfile.objects.create(full_name=cls.key)
        cls.category = MaterialCategory.objects.create(name=cls.key)
        cls.material = Material.objects.create(name=cls.key)
        cls.lot = MaterialLot.objects.create(
            material=cls.material, lot_code=cls.key,
        )
        cls.mixture = MixtureType.objects.create(code=cls.key, title=cls.key)
        cls.recipe = RecipeVersion.objects.create(
            version_label=cls.key, mixture_type=cls.mixture, status='published',
        )
        cls.card = TestSession.objects.create(identifier=cls.key)
        cls.source = SourceFile.objects.create(title=cls.key)
        cls.metric = Metric.objects.create(code=cls.key, label=cls.key)
        cls.recipe_template = RecipeTemplate.objects.create(name=cls.key)
        cls.event = ChangeEvent.objects.create(
            entity_type='material', entity_id=cls.material.pk,
            action='created', summary=cls.key, employee=cls.employee,
        )

    def setUp(self):
        session = self.client.session
        session['site_authenticated'] = True
        session['employee_id'] = self.employee.pk
        session.save()

    def registry_objects(self):
        return (
            ('material_list', self.material), ('category_list', self.category),
            ('lot_list', self.lot), ('mixture_list', self.mixture),
            ('recipe_list', self.recipe), ('test_list', self.card),
            ('source_list', self.source), ('metric_list', self.metric),
            ('recipe_template_list', self.recipe_template),
            ('history', self.event),
        )

    def test_every_registry_matches_substring_case_and_equivalent_separators(self):
        for route, obj in self.registry_objects():
            for query in self.queries:
                with self.subTest(route=route, query=query):
                    response = self.client.get(reverse(route), {'q': query})
                    self.assertEqual(response.status_code, 200)
                    records = list(response.context['page_obj'])
                    self.assertEqual([row.pk for row in records], [obj.pk])

    def test_unmatched_query_returns_no_records_in_every_registry(self):
        for route, _ in self.registry_objects():
            with self.subTest(route=route):
                response = self.client.get(reverse(route), {'q': 'несуществующий'})
                self.assertEqual(response.context['page_obj'].paginator.count, 0)

    def test_assistant_uses_the_same_search_rules(self):
        entities = ('materials', 'lots', 'mixtures', 'recipes', 'tests',
                    'metrics', 'sources', 'recipe_templates')
        for entity in entities:
            for query in self.queries:
                with self.subTest(entity=entity, query=query):
                    data = execute_tool('search_lab_data', {
                        'entity': entity, 'q': query,
                    })
                    self.assertEqual(data['total'], 1)

    def test_related_recipe_matches_are_searched_once_and_keep_status_filter(self):
        second = RecipeVersion.objects.create(version_label=self.key + '.2')
        for recipe in (self.recipe, second):
            RecipeMatch.objects.create(session=self.card, recipe_version=recipe)
        TestSession.objects.filter(pk=self.card.pk).update(identifier='CARD')
        response = self.client.get(reverse('test_list'), {'q': self.queries[0]})
        self.assertEqual(response.context['page_obj'].paginator.count, 1)
        response = self.client.get(reverse('test_list'), {
            'q': self.queries[0], 'status': 'completed',
        })
        self.assertEqual(response.context['page_obj'].paginator.count, 0)

    def test_search_preserves_values_and_treats_like_patterns_literally(self):
        literal = Material.objects.create(name='Лаб.100% Состав')
        Material.objects.create(name='Лаб.1000 Состав')
        result = filter_search(Material.objects.all(), ('name',), '100%')
        self.assertEqual(list(result), [literal])
        result = filter_search(Material.objects.all(), ('name',), "' OR 1=1")
        self.assertFalse(result.exists())
        self.material.refresh_from_db()
        self.assertEqual(self.material.name, self.key)

    def test_all_recorded_separators_and_whitespace_runs_are_interchangeable(self):
        names = ('A - Б', 'A__Б', 'A.Б', 'A   Б', 'A\tБ',
                 'A\u00a0Б', 'A\u202fБ', 'A\u2007Б', 'A\ufeffБ')
        expected = [Material.objects.create(name=name).pk for name in names]
        queryset = Material.objects.filter(pk__in=expected).order_by('pk')
        for query in ('a б', 'a__б', 'A - Б', 'a.._ -\u00a0б'):
            with self.subTest(query=query):
                result = filter_search(queryset, ('name',), query)
                self.assertEqual(list(result.values_list('pk', flat=True)), expected)
        self.assertEqual(list(queryset.values_list('name', flat=True)), list(names))

    def test_postgresql_compiles_global_separator_normalization(self):
        postgres = DatabaseWrapper({'NAME': 'unused'}, alias='search-postgresql')
        compiler = Material.objects.all().query.get_compiler(connection=postgres)
        sql, parameters = compiler.compile(NormalizedText(Value(self.key)))
        self.assertEqual(sql, 'REGEXP_REPLACE(LOWER(%s), %s, %s, %s)')
        self.assertEqual(parameters, [self.key, SEPARATOR_PATTERN, '.', 'g'])
        for separator in (' ', '\t', '\u00a0', '\u202f', '_', '.', '-'):
            with self.subTest(separator=repr(separator)):
                self.assertIn(separator, parameters[1])
                self.assertEqual(normalize_search(f'A{separator * 2}Б'), 'a.б')

    def test_admin_uses_full_query_with_equivalent_separators_and_outer_quotes(self):
        model_admin = admin.site._registry[Material]
        request = RequestFactory().get('/admin/lab/material/')
        Material.objects.create(name='ПРОБА А ДРУГОЕ Б В')
        queries = (*self.queries, '"оба а б в"', "'оба - а__б..в'")
        for query in queries:
            with self.subTest(query=query):
                result, duplicates = model_admin.get_search_results(
                    request, Material.objects.all(), query,
                )
                self.assertEqual(list(result), [self.material])
                self.assertFalse(duplicates)

    def test_project_file_search_normalizes_paths_and_contents(self):
        with TemporaryDirectory(prefix='lims-search-') as root:
            Path(root, 'README.md').write_text(self.key, encoding='utf-8')
            with override_settings(BASE_DIR=Path(root)):
                result = execute_tool('list_project_files', {
                    'query': self.queries[0],
                })
        self.assertEqual(result['total'], 1)
        self.assertIn(self.key, result['items'][0]['match'])
