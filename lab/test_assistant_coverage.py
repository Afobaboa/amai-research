"""Every declared assistant field needs interface vocabulary as the API grows."""
import ast
import inspect
import textwrap

from django.test import SimpleTestCase

from . import assistant_tools, calculations
from .assistant_labels import field_labels, ui_labels
from .models import RecipeTemplate, RecipeVersion


def source_tree(value):
    return ast.parse(textwrap.dedent(inspect.getsource(value)))


def literal_dict_keys(value):
    """Inspect data builders, not arbitrary database JSON or user-defined keys."""
    return {key.value for node in ast.walk(source_tree(value))
            if isinstance(node, ast.Dict) for key in node.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)}


def assigned_dict_keys(value):
    return {node.slice.value for node in ast.walk(source_tree(value))
            if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store)
            and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str)}


class AssistantVocabularyCoverageTests(SimpleTestCase):
    def assert_readable_fields(self, names, model=RecipeVersion):
        # value_labels contains code dictionaries, deliberately excluded from
        # field_labels' recursive walk; its own heading is still interface text.
        names = set(names)
        labels = field_labels({name: [] if name == 'available_sections' else None
                               for name in names - {'value_labels'}}, model)
        if 'value_labels' in names:
            labels['value_labels'] = ui_labels().get('value_labels', '')
        missing = sorted(name for name in names
                         if not labels.get(name) or not any(
                             'А' <= ch <= 'я' or ch in 'Ёё' for ch in labels[name]))
        self.assertEqual(missing, [], f'Add Russian labels for: {missing}')

    def test_every_published_entity_field_has_a_russian_label(self):
        for entity, (model, route, fields, search_fields) in assistant_tools.ENTITIES.items():
            with self.subTest(entity=entity):
                self.assert_readable_fields(fields.split(), model)

    def test_every_declared_related_field_has_a_russian_label(self):
        fields = set()
        for name, value in vars(assistant_tools).items():
            if name.endswith('_FIELDS') and isinstance(value, str):
                fields.update(value.split())
        # Section builders also declare small field lists inline. Discover them
        # from the real calls so a new column cannot bypass this check.
        for node in ast.walk(source_tree(assistant_tools)):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in {'related', '_rows'} and len(node.args) > 1
                    and isinstance(node.args[1], ast.Constant)
                    and isinstance(node.args[1].value, str)):
                fields.update(node.args[1].value.split())
        self.assertTrue(fields)
        self.assert_readable_fields(fields)

    def test_all_detail_sections_have_readable_names(self):
        labels = field_labels({
            'available_sections': list(assistant_tools.DETAIL_SECTIONS),
        }, RecipeVersion)
        missing = set(assistant_tools.DETAIL_SECTIONS) - labels.keys()
        self.assertEqual(missing, set())

    def test_tool_names_parameters_and_service_codes_have_russian_labels(self):
        names = set(assistant_tools.ENTITIES)
        for tool in assistant_tools.TOOLS:
            function = tool['function']
            names.add(function['name'])
            parameters = function['parameters']['properties']
            names.update(parameters)
            for field in ('entity', 'section', 'order'):
                names.update(parameters.get(field, {}).get('enum', ()))
        self.assert_readable_fields(names)

    def test_tool_metadata_and_source_fields_are_covered(self):
        fields = set()
        for name in ('_limit_text', '_finish', '_rows', 'search_lab_data',
                     '_source_cell', '_provenance', 'get_lab_record',
                     'list_project_files', 'read_project_file'):
            fields.update(literal_dict_keys(getattr(assistant_tools, name)))
        self.assert_readable_fields(fields)

    def test_calculation_snapshots_and_recipe_definition_schema_are_covered(self):
        fields = set()
        for name in ('_material_inputs', '_calculate_group', 'calculate_recipe', 'save_calculation'):
            builder = getattr(calculations, name)
            fields.update(literal_dict_keys(builder))
            fields.update(assigned_dict_keys(builder))
        # Allowed keys are the source of truth for versioned template JSON.
        for node in ast.walk(source_tree(RecipeTemplate.clean)):
            if isinstance(node, ast.Set):
                fields.update(item.value for item in node.elts
                              if isinstance(item, ast.Constant) and isinstance(item.value, str))
        self.assert_readable_fields(fields)
