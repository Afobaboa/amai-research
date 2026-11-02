"""Shared substring search for registries, selectors and assistant tools."""

import re

from django.db.models import F, Func, Q, TextField, Value
from django.db.models.functions import Lower


# Keep Python/JavaScript whitespace equivalent without relying on DB locales.
# NBSP and narrow NBSP frequently occur in labels copied from Excel.
SEARCH_WHITESPACE = (
    '\t\n\v\f\r \u001c\u001d\u001e\u001f\u0085\u00a0\u1680'
    '\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007'
    '\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff'
)
SEPARATOR_PATTERN = f'[{SEARCH_WHITESPACE}._-]+'
SEPARATORS = re.compile(SEPARATOR_PATTERN)


def normalize_search(value):
    """Keep punctuation literal while treating identifier separators alike."""
    return SEPARATORS.sub('.', value.lower()) if isinstance(value, str) else value


class NormalizedText(Func):
    function = 'lims_search_normalize'
    output_field = TextField()

    def as_sqlite(self, compiler, connection, **extra_context):
        # SQLite's built-in LOWER/LIKE only ignore case for ASCII characters.
        # Register on the execution connection, including after reconnection.
        connection.ensure_connection()
        connection.connection.create_function(
            self.function, 1, normalize_search, deterministic=True,
        )
        return super().as_sql(compiler, connection, **extra_context)

    def as_sql(self, compiler, connection, **extra_context):
        expression = Func(
            Lower(self.source_expressions[0]), Value(SEPARATOR_PATTERN),
            Value('.'), Value('g'), function='REGEXP_REPLACE',
            output_field=TextField(),
        )
        return compiler.compile(expression)


def filter_search(queryset, fields, query):
    """Match any field, preserving SQL pagination and literal LIKE characters."""
    query = normalize_search(query.strip())
    if not query:
        return queryset
    aliases, condition = {}, Q()
    for index, field in enumerate(fields):
        alias = f'_lims_search_{index}'
        aliases[alias] = NormalizedText(F(field))
        condition |= Q(**{f'{alias}__contains': query})
    return queryset.alias(**aliases).filter(condition).distinct()
