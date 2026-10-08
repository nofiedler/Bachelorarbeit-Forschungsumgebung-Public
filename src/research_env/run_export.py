"""Lossless scalar CSV appendix; JSON Pointer paths also cover list entries."""
import json


def records(value, path=''):
    if isinstance(value, dict) and value:
        for key, item in value.items():
            escaped = str(key).replace('~', '~0').replace('/', '~1')
            yield from records(item, path + '/' + escaped)
    elif isinstance(value, list) and value:
        for index, item in enumerate(value):
            yield from records(item, path + '/' + str(index))
    else:
        # JSON encoding preserves null vs empty string, boolean and number types.
        yield path, json.dumps(value, ensure_ascii=False, default=str), type(value).__name__
