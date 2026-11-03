"""UI vocabulary for tool output; technical keys remain usable by the agent."""
from functools import lru_cache

from . import models
from .assistant_values import annotate_choice_values, resolve_model_field, section_model
from .ui_text import ASSISTANT_LABELS, FIELD_LABELS, PROPERTY_LABELS, SECTION_LABELS, UNIT_LABELS, UI_TEXT


def _model_classes():
    return [model for model in vars(models).values()
            if isinstance(model, type) and hasattr(model, '_meta')]


def _relation_labels(labels, model_classes):
    # Django values() uses attname (e.g. specified_lot_id), while forms and
    # FIELD_LABELS use the relation's name (specified_lot).
    for model in model_classes:
        for field in model._meta.fields:
            if field.is_relation and field.name in labels:
                labels[field.attname] = labels[field.name]


@lru_cache(maxsize=1)
def ui_labels():
    labels = {}
    for model in _model_classes():
        for field in model._meta.fields:
            label = str(field.verbose_name)
            if any('А' <= character <= 'я' or character in 'Ёё' for character in label):
                labels.setdefault(field.name, label)
                if field.is_relation:
                    labels.setdefault(field.attname, label)
    labels.update(FIELD_LABELS)
    labels.update(SECTION_LABELS)
    labels.update(ASSISTANT_LABELS)
    labels.update({
        'effective_density_g_ml': UI_TEXT['density_g_ml'],
        'effective_price_per_kg': UI_TEXT['price_per_kg'],
        'lots': UI_TEXT['lot_list'], 'recipes': UI_TEXT['recipe_list'],
    })
    _relation_labels(labels, _model_classes())
    return labels


@lru_cache(maxsize=1)
def form_labels():
    # Use field declarations without instantiating forms or evaluating querysets.
    from . import catalog_forms, forms, template_forms, measurement_forms
    result = {}
    for module in (forms, catalog_forms, template_forms, measurement_forms):
        for form in vars(module).values():
            model = getattr(getattr(form, '_meta', None), 'model', None)
            if model is not None and hasattr(form, 'base_fields'):
                result.setdefault(model, {}).update({
                    key: str(field.label) for key, field in form.base_fields.items()
                    if field.label and any('А' <= ch <= 'я' or ch in 'Ёё' for ch in str(field.label))
                })
    return result


def field_label(key, model=None):
    """Resolve related field names using their owner, without database reads."""
    labels = ui_labels()
    if key.endswith('_display'):
        key = key.removesuffix('_display')
    if '__' in key and key in FIELD_LABELS:
        return FIELD_LABELS[key]
    field = resolve_model_field(model, key)
    if field is not None:
        label = form_labels().get(field.model, {}).get(field.name)
        if label:
            return label
        label = str(getattr(field, 'verbose_name', ''))
        if any('А' <= ch <= 'я' or ch in 'Ёё' for ch in label):
            return label
        if field.name in labels:
            return labels[field.name]
    return labels.get(key, labels.get(key.split('__')[-1]))


def field_labels(data, model):
    """Include only vocabulary used in this response, including section names."""
    labels, used = ui_labels(), {}

    def visit(value, current_model):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ('field_labels', 'value_labels'):
                    continue
                label = field_label(key, current_model)
                if label:
                    used[key] = label
                if key == 'available_sections':
                    used.update((section, labels[section]) for section in item if section in labels)
                nested_model = section_model(current_model, key)
                if nested_model is None:
                    documents = (
                        'definition', 'inputs', 'outputs', 'template_snapshot',
                        'formulation', 'formulation_calculation',
                    )
                    nested_model = None if key in documents else current_model
                visit(item, nested_model)
        elif isinstance(value, list):
            for item in value:
                visit(item, current_model)

    visit(data, model)
    return used


def add_value_labels(data, model):
    """Put human names beside coded values without altering the source data."""
    annotate_choice_values(data, model)
    used = {}

    def remember(key, code, label):
        used.setdefault(key, {})[code] = label

    def visit(value, annotate=True):
        if isinstance(value, dict):
            for key, item in list(value.items()):
                if key in ('field_labels', 'value_labels'):
                    continue
                if isinstance(item, str):
                    if key == 'entity' and item in ASSISTANT_LABELS:
                        label = ASSISTANT_LABELS[item]
                        if annotate:
                            value['entity_display'] = label
                        remember(key, item, label)
                    elif key == 'property_code' and item in PROPERTY_LABELS:
                        label = PROPERTY_LABELS[item]
                        if annotate:
                            value['property_label'] = label
                        remember(key, item, label)
                    elif (key == 'unit_code' or key.endswith('__unit_code')) and item in UNIT_LABELS:
                        label = UNIT_LABELS[item]
                        if annotate:
                            value[key.removesuffix('unit_code') + 'unit_label'] = label
                        remember(key, item, label)
                    elif key.endswith('__code') or (key == 'code' and model is models.Metric):
                        label = value.get(key.removesuffix('code') + 'label')
                        if isinstance(label, str) and label:
                            remember(key, item, label)
                visit(item, annotate and key not in ('definition', 'inputs', 'outputs', 'template_snapshot'))
        elif isinstance(value, list):
            for item in value:
                visit(item, annotate)

    visit(data)
    if used:
        vocabulary = data.setdefault('value_labels', {})
        for key, labels in used.items():
            vocabulary.setdefault(key, {}).update(labels)
