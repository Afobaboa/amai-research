"""UI vocabulary for tool output; technical keys remain usable by the agent."""
from functools import lru_cache

from . import models
from .ui_text import FIELD_LABELS, SECTION_LABELS, UI_TEXT


@lru_cache(maxsize=1)
def ui_labels():
    labels = {}
    for model in vars(models).values():
        if not isinstance(model, type) or not hasattr(model, '_meta'):
            continue
        for field in model._meta.fields:
            label = str(field.verbose_name)
            if any('А' <= character <= 'я' or character in 'Ёё' for character in label):
                labels.setdefault(field.name, label)
                if field.is_relation:
                    labels.setdefault(field.attname, label)
    labels.update(FIELD_LABELS)
    labels.update(SECTION_LABELS)
    labels.update({
        'effective_density_g_ml': UI_TEXT['density_g_ml'],
        'effective_price_per_kg': UI_TEXT['price_per_kg'],
        'lots': UI_TEXT['lot_list'], 'recipes': UI_TEXT['recipe_list'],
    })
    return labels


@lru_cache(maxsize=1)
def form_labels():
    # Use field declarations without instantiating forms or evaluating querysets.
    from . import catalog_forms, forms, template_forms
    result = {}
    for module in (forms, catalog_forms, template_forms):
        for form in vars(module).values():
            model = getattr(getattr(form, '_meta', None), 'model', None)
            if model is not None and hasattr(form, 'base_fields'):
                result.setdefault(model, {}).update({
                    key: str(field.label) for key, field in form.base_fields.items()
                    if field.label and any('А' <= ch <= 'я' or ch in 'Ёё' for ch in str(field.label))
                })
    return result


def field_labels(data, model):
    """Include only vocabulary used in this response, including section names."""
    labels, used = dict(ui_labels()), {}
    for field in model._meta.fields:
        label = str(field.verbose_name)
        if any('А' <= ch <= 'я' or ch in 'Ёё' for ch in label):
            labels[field.name] = label
    labels.update(form_labels().get(model, {}))

    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                label = labels.get(key, labels.get(key.split('__')[-1]))
                if label:
                    used[key] = label
                if key == 'available_sections':
                    used.update((section, labels[section]) for section in item if section in labels)
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(data)
    return used
