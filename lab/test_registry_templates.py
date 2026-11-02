"""Registry enhancement hooks and pagination retain server-rendered filters."""
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

from django.core.paginator import Paginator
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase


REGISTRY_FILTERS = (
    ('recipe_list.html', {'status': 'published'}),
    ('test_list.html', {'status': 'active'}),
    ('history.html', {'entity': 'material'}),
    ('catalog/material_list.html', {'category': 'Полиолы'}),
    ('catalog/category_list.html', {}),
    ('catalog/lot_list.html', {'material': '42', 'status': 'quarantine'}),
    ('catalog/source_list.html', {'kind': 'file'}),
    ('catalog/metric_list.html', {}),
    ('catalog/mixture_list.html', {}),
    ('templates/recipe_list.html', {}),
    ('templates/calculation_list.html', {}),
)


class RegistryMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.live_forms, self.panels, self.inputs = [], [], []
        self.query_forms, self.options, self.pagination_links = [], [], []
        self.form, self.select, self.pagination = None, None, False

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == 'form':
            self.form = attrs
            if 'data-live-search' in attrs:
                self.live_forms.append(attrs)
        elif tag == 'section' and 'data-live-results' in attrs:
            self.panels.append(attrs)
        elif tag == 'input':
            self.inputs.append(attrs)
            if attrs.get('name') == 'q':
                self.query_forms.append(self.form)
        elif tag == 'select':
            self.select = attrs.get('name')
        elif tag == 'option' and self.select == 'status':
            self.options.append(attrs)
        elif tag == 'nav' and attrs.get('class') == 'pagination':
            self.pagination = True
        elif tag == 'a' and self.pagination:
            self.pagination_links.append(attrs.get('href', ''))

    def handle_endtag(self, tag):
        if tag == 'form':
            self.form = None
        elif tag == 'select':
            self.select = None
        elif tag == 'nav':
            self.pagination = False


class RegistryTemplateTests(SimpleTestCase):
    def registry(self, name, **filters):
        values = {'q': 'пена & A_Б.В', **filters, 'page': '2'}
        request = RequestFactory().get('/registry/', values)
        page = Paginator(range(60), 20).page(2)
        html = render_to_string(
            f'lab/{name}', {'page_obj': page, **values}, request=request,
        )
        markup = RegistryMarkup()
        markup.feed(html)
        return markup

    def test_all_search_forms_keep_get_fallback_and_have_one_result_panel(self):
        for name, filters in REGISTRY_FILTERS:
            with self.subTest(template=name):
                markup = self.registry(name, **filters)
                self.assertEqual(len(markup.live_forms), 1)
                self.assertEqual(markup.live_forms[0]['method'], 'get')
                self.assertEqual(markup.query_forms, markup.live_forms)
                self.assertEqual(len(markup.panels), 1)

    def test_next_and_previous_links_preserve_every_registry_filter(self):
        for name, filters in REGISTRY_FILTERS:
            with self.subTest(template=name):
                markup = self.registry(name, **filters)
                self.assertEqual(len(markup.pagination_links), 2)
                for url, page in zip(markup.pagination_links, ('1', '3')):
                    self.assertEqual(parse_qs(urlsplit(url).query), {
                        'q': ['пена & A_Б.В'], 'page': [page],
                        **{key: [value] for key, value in filters.items()},
                    })

    def test_lot_search_submission_retains_selected_material(self):
        markup = self.registry('catalog/lot_list.html', material='42')
        fields = [field for field in markup.inputs
                  if field.get('name') == 'material']
        self.assertEqual(fields, [{
            'type': 'hidden', 'name': 'material', 'value': '42',
        }])

    def test_recipe_status_menu_has_real_published_option(self):
        markup = self.registry('recipe_list.html', status='published')
        self.assertEqual([option['value'] for option in markup.options], [
            '', 'published', 'draft', 'archived',
        ])
        selected = [option['value'] for option in markup.options
                    if 'selected' in option]
        self.assertEqual(selected, ['published'])
