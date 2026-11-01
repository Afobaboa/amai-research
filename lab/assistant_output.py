"""Use interface vocabulary in prose while preserving links and calculations."""
import re

from .assistant_labels import ui_labels
from .assistant_values import technical_choice_labels
from .ui_text import PROPERTY_LABELS, UNIT_LABELS

_VOCABULARY = r'(?P<identifier>(?<!\w)[A-Za-z_][A-Za-z0-9_]*(?!\w))(?P<empty>\s*:\s*null(?!\w))?'
_IDENTIFIER = re.compile(_VOCABULARY)
_SPANS = re.compile(
    r'(?P<link>\]\((?:\\[^\n]|[^\\()\n]|\([^()\n]*\))*(?:\)|$))'
    r'|(?P<url>(?:https?://|/)[^\s<>`]+)'
    r'|(?P<code>(?P<ticks>`+)[\s\S]*?(?P=ticks))'
    r'|' + _VOCABULARY
)


class OutputLabels:
    def __init__(self):
        self.field_names = {key: str(value) for key, value in ui_labels().items()}
        for key, value in PROPERTY_LABELS.items():
            self.field_names.setdefault(key, str(value))
        self.value_candidates = {key: {value} for key, value in technical_choice_labels().items()}
        self._refresh()

    def _refresh(self):
        # Contexts may disagree: a material lot and a test use different labels
        # for "active". Never turn a bare code into the first label encountered.
        values = {key: next(iter(labels)) for key, labels in self.value_candidates.items()
                  if len(labels) == 1}
        self.code_names = {**values, **self.field_names}
        self.labels = {key: value for key, value in self.code_names.items() if '_' in key}

    def add_data(self, data):
        """Read vocabulary only; the tool result itself remains untouched."""
        def visit(value):
            if isinstance(value, dict):
                fields = value.get('field_labels', {})
                if isinstance(fields, dict):
                    self.field_names.update({key: label for key, label in fields.items()
                                             if isinstance(key, str) and isinstance(label, str)})
                values = value.get('value_labels', {})
                if isinstance(values, dict):
                    for vocabulary in values.values():
                        if isinstance(vocabulary, dict):
                            for code, label in vocabulary.items():
                                if isinstance(code, str) and isinstance(label, str):
                                    self.value_candidates.setdefault(code, set()).add(label)
                for key, child in value.items():
                    if key not in ('field_labels', 'value_labels'):
                        visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(data)
        self._refresh()

    def render(self, content):
        def identifier(match, vocabulary):
            key = match.group('identifier')
            label = vocabulary.get(key)
            if label is None:
                return match.group()
            empty = match.group('empty')
            if empty:
                missing = 'не указана' if key.split('__')[-1] == 'specified_lot_id' else 'не указано'
                return label + empty[:-4] + missing
            return label

        def replace(match):
            if match.group('identifier'):
                return identifier(match, self.labels)
            if match.group('code'):
                # A lone field/code is vocabulary; an expression is source data.
                ticks = match.group('ticks')
                body = match.group()[len(ticks):-len(ticks)]
                prefix = ''
                if len(ticks) >= 3 and '\n' in body:
                    # The fence's language tag is Markdown syntax, not a field.
                    first_line, newline, body = body.partition('\n')
                    prefix = first_line + newline
                # Units containing a slash or spaces are complete values, not
                # mathematical expressions. Only an exact span is translated.
                stripped = body.strip()
                if stripped in UNIT_LABELS and ('/' in stripped or ' ' in stripped):
                    return ticks + prefix + body.replace(stripped, UNIT_LABELS[stripped], 1) + ticks
                if not re.search(r'[=+*/^%()\-]', body):
                    return ticks + prefix + _IDENTIFIER.sub(
                        lambda token: identifier(token, self.code_names), body,
                    ) + ticks
            return match.group()

        return _SPANS.sub(replace, content)


class ReadableOutput:
    """Keep incomplete identifiers private until their boundary arrives."""
    def __init__(self, labels, limit=None):
        self.labels = labels
        self.raw = ''
        self.sent = 0
        self.emitted = ''
        self.limit = limit

    def feed(self, content='', *, final=False):
        self.raw += content
        end = len(self.raw)
        if not final:
            # A code span may turn out to be a formula: wait for its closing run.
            opened = None
            last_closed = None
            for match in re.finditer(r'`+', self.raw):
                if opened is None:
                    opened = (match.start(), len(match.group()))
                elif len(match.group()) == opened[1]:
                    last_closed = (opened[0], match.end())
                    opened = None
            if opened is not None:
                end = opened[0]
            # More backticks in the next delta can extend a closing run and
            # change which delimiter it matches. Its boundary is not known yet.
            if last_closed is not None and last_closed[1] == len(self.raw):
                end = min(end, last_closed[0])
            suffix = re.search(r'[A-Za-z_][A-Za-z0-9_]*$', self.raw[:end])
            if suffix and any(key.startswith(suffix.group()) for key in self.labels.labels):
                end = suffix.start()
            empty = re.search(
                r'(?<!\w)(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?P<empty>n(?:u(?:l(?:l)?)?)?)$',
                self.raw[:end],
            )
            if empty and empty.group('key') in self.labels.labels:
                end = empty.start('empty')
        rendered = self.labels.render(self.raw[:end])
        if self.limit is not None:
            rendered = rendered[:self.limit]
        # Malformed Markdown must never rewind the cursor or duplicate output.
        # The final reply can replace the preview if its syntax remains unstable.
        if not rendered.startswith(self.emitted):
            return ''
        delta = rendered[self.sent:]
        self.sent = len(rendered)
        self.emitted = rendered
        return delta


def normalize_output(content, labels=None):
    """Readable fallback for previously saved replies, without database writes."""
    return (labels if labels is not None else OutputLabels()).render(content)
