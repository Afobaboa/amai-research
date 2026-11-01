"""Persist only numeric provider usage and request sizes, never raw payloads."""
from copy import deepcopy


USAGE_FIELDS = ('prompt_tokens', 'completion_tokens', 'total_tokens',
                'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens')
DETAIL_FIELDS = {
    'prompt_tokens_details': ('cached_tokens',),
    'completion_tokens_details': ('reasoning_tokens',),
}


def clean_usage(value):
    if not isinstance(value, dict):
        return {}
    result = {key: value[key] for key in USAGE_FIELDS
              if type(value.get(key)) is int and value[key] >= 0}
    for group, fields in DETAIL_FIELDS.items():
        details = value.get(group)
        if isinstance(details, dict):
            kept = {key: details[key] for key in fields
                    if type(details.get(key)) is int and details[key] >= 0}
            if kept:
                result[group] = kept
    return result


class UsageJournal:
    def __init__(self):
        self.calls = []
        self.tools = []

    def observe(self, event):
        kind = event['kind']
        if kind == 'provider_start':
            self.calls.append({
                'status': 'started', 'usage': {},
                'message_chars': event['message_chars'],
                'request_chars': event['request_chars'],
            })
        elif kind == 'provider_usage' and self.calls:
            self.calls[-1]['usage'].update(clean_usage(event.get('usage')))
        elif kind == 'provider_end' and self.calls:
            self.calls[-1]['status'] = 'completed'
        elif kind == 'tool':
            self.tools.append({'name': event['name'],
                               'result_chars': event['result_chars']})

    def fields(self, final_status=None):
        calls = deepcopy(self.calls)
        for call in calls:
            if final_status and call['status'] == 'started':
                call['status'] = final_status
        return {
            'provider_calls': calls, 'tool_calls': deepcopy(self.tools),
            'provider_call_count': len(calls),
            'tool_call_count': len(self.tools),
            'usage_call_count': sum(self.has_totals(c) for c in calls),
            'prompt_tokens': self.total('prompt_tokens'),
            'completion_tokens': self.total('completion_tokens'),
        }

    @staticmethod
    def has_totals(call):
        return all(key in call['usage']
                   for key in ('prompt_tokens', 'completion_tokens'))

    def total(self, key):
        values = [call['usage'][key] for call in self.calls
                  if key in call['usage']]
        return sum(values) if values else None
