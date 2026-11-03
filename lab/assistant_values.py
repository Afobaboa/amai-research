"""Context-aware display names for the assistant's coded laboratory values."""
from functools import lru_cache

from django.core.exceptions import FieldDoesNotExist

from . import models as m
from .ui_text import SOURCE_CELL_KIND_LABELS


# A nested collection has its own model: e.g. a recipe link's status must not
# inherit the surrounding test's status vocabulary.
SECTION_MODELS = {
    m.RecipeVersion: {
        'groups': m.RecipeGroup, 'components': m.RecipeComponent,
        'filling_plan': m.FillingPlanLine, 'properties': m.RecipeProperty,
        'test_links': m.RecipeMatch, 'calculations': m.CalculationRecord,
    },
    m.TestSession: {
        'recipe_links': m.RecipeMatch, 'basic_parameters': m.Result,
        'conditioning': m.ConditioningEvent, 'runs': m.TestRun,
        'results': m.Result, 'observations': m.Observation,
    },
    m.Result: {
        'source': m.SourceCell, 'dependencies': m.ResultDependency,
        'basic_parameters': m.Result, 'recipe_links': m.RecipeMatch,
    },
    m.Material: {'lots': m.MaterialLot, 'recipes': m.RecipeVersion},
    m.MaterialLot: {'recipes': m.RecipeVersion},
    m.MixtureType: {'recipes': m.RecipeVersion},
    m.SourceFile: {'sheets': m.SourceSheet, 'cells': m.SourceCell},
    m.SourceCell: {'source': m.SourceCell},
    m.Metric: {'results': m.Result},
    m.MeasurementGroup: {'metrics': m.Metric},
}


def section_model(model, key):
    return SECTION_MODELS.get(model, {}).get(key)


def resolve_model_field(model, key):
    """Resolve an explicit ORM values() key using metadata, never the database."""
    if not isinstance(key, str):
        return None
    parts = key.split('__')
    for index, part in enumerate(parts):
        try:
            field = model._meta.get_field(part)
        except (FieldDoesNotExist, AttributeError):
            return None
        if index == len(parts) - 1:
            return field
        if not field.is_relation or field.related_model is None:
            return None
        model = field.related_model


@lru_cache(maxsize=1)
def technical_choice_labels():
    """Safe fallback for unambiguous technical enum codes in saved answers."""
    candidates = {}
    for model in vars(m).values():
        if not isinstance(model, type) or not hasattr(model, '_meta'):
            continue
        for field in model._meta.fields:
            for code, label in field.flatchoices:
                if isinstance(code, str) and '_' in code:
                    candidates.setdefault(code, set()).add(str(label))
    return {code: next(iter(labels)) for code, labels in candidates.items()
            if len(labels) == 1}


def annotate_choice_values(data, model):
    """Add display siblings and contextual vocabulary, preserving source values.

    Unknown keys and codes receive no invented caption. Documentary JSON fields
    are not traversed as ORM records; their known calculation structure is read
    only to describe values in top-level metadata.
    """
    used = {}

    def remember(path, code, label):
        used.setdefault(path, {})[code] = str(label)

    def rows(value, key):
        items = value.get(key)
        return items if isinstance(items, list) else []

    def choices(value, current_model, path, *, annotate):
        for key, item in list(value.items()):
            if not isinstance(item, str):
                continue
            field = resolve_model_field(current_model, key)
            if field is None:
                continue
            if field.model is m.SourceCell and field.name == 'cell_kind':
                label = SOURCE_CELL_KIND_LABELS.get(item)
            else:
                label = dict(field.flatchoices).get(item)
            if label is None:
                continue
            if annotate:
                display = label
                if (field.model is m.SourceCell and field.name == 'cell_kind'
                        and value.get(key.removesuffix('cell_kind') + 'formula_text')
                        and item != 'comment'):
                    display = SOURCE_CELL_KIND_LABELS['formula']
                value[key + '_display'] = str(display)
            remember('.'.join(filter(None, (path, key))), item, label)

    def operation_names(value, path, *, annotate):
        if not isinstance(value, dict) or not isinstance(value.get('operations'), list):
            return
        from .calculations import OPERATIONS
        vocabulary = {code: operation['label'] for code, operation in OPERATIONS.items()}
        codes = value['operations']
        for code in codes:
            if isinstance(code, str) and code in vocabulary:
                remember('.'.join(filter(None, (path, 'operations'))), code, vocabulary[code])
        if annotate and all(isinstance(code, str) and code in vocabulary for code in codes):
            value['operations_display'] = [vocabulary[code] for code in codes]

    def calculation_snapshot(value, path):
        if not isinstance(value, dict):
            return
        operation_names(value.get('template'), path + '.template', annotate=False)
        purpose = value.get('purpose')
        purpose_labels = {'manual': 'Сохранённый расчёт', 'publication': 'Расчёт при публикации'}
        if isinstance(purpose, str) and purpose in purpose_labels:
            remember(path + '.purpose', purpose, purpose_labels[purpose])

        def component_names(component, component_path):
            if not isinstance(component, dict):
                return
            lot_status = component.get('lot_status')
            if isinstance(lot_status, str):
                # Historical calculation snapshots retain retired status codes.
                labels = {'quarantine': 'Карантин', **dict(m.MaterialLot.Status.choices)}
                label = labels.get(lot_status)
                if label:
                    remember(component_path + '.lot_status', lot_status, label)
            from .templatetags.lab_tags import VALUES
            for key in ('density_origin', 'price_origin'):
                code = component.get(key)
                if isinstance(code, str) and code in ('lot', 'material', 'source'):
                    remember(component_path + '.' + key, code, VALUES[code])

        for group in rows(value, 'groups'):
            if isinstance(group, dict):
                choices(group, m.RecipeGroup, path + '.groups', annotate=False)
                for collection in ('components', 'rows'):
                    for component in rows(group, collection):
                        component_names(component, path + '.groups.' + collection)
        for line in rows(value, 'filling_plan'):
            if isinstance(line, dict):
                choices(line, m.FillingPlanLine, path + '.filling_plan', annotate=False)

    def visit(value, current_model, path=''):
        if not isinstance(value, dict):
            return
        choices(value, current_model, path, annotate=True)
        if current_model is m.CalculationRecord:
            for key in ('inputs', 'outputs'):
                calculation_snapshot(value.get(key), '.'.join(filter(None, (path, key))))
        if current_model is m.RecipeTemplate:
            definition = value.get('definition')
            if isinstance(definition, dict) and isinstance(definition.get('components'), list):
                for component in definition['components']:
                    if isinstance(component, dict):
                        choices(component, m.RecipeGroup,
                                '.'.join(filter(None, (path, 'definition.components'))),
                                annotate=False)
        # Collections retain the current model; their names are structural JSON.
        for row in rows(value, 'items'):
            visit(row, current_model, path)
        for key, child in list(value.items()):
            nested_model = section_model(current_model, key)
            if nested_model is not None:
                visit(child, nested_model, '.'.join(filter(None, (path, key))))

    visit(data, model)
    if used:
        vocabulary = data.setdefault('value_labels', {})
        for path, labels in used.items():
            vocabulary.setdefault(path, {}).update(labels)
