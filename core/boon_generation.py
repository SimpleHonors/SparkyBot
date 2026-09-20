"""Read the combiner's weighted all-fights boon chart without executing code."""
import ast
import math
import re

_BOONS = {'Might', 'Fury', 'Quickness', 'Alacrity', 'Protection', 'Regeneration',
          'Vigor', 'Aegis', 'Stability', 'Swiftness', 'Resistance', 'Resolution'}
_SUFFIX = '-Total-Squad-Boon-Generation'


def _source_literal(text):
    match = re.search(r'\bdataset\s*:\s*\[\s*\{\s*source\s*:\s*(\[)', text)
    if not match:
        raise ValueError('weighted boon dataset unavailable')
    start = match.start(1)
    depth, quote, escaped = 0, None, False
    for pos in range(start, len(text)):
        char = text[pos]
        if quote:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == '[':
            depth += 1
        elif char == ']':
            depth -= 1
            if not depth:
                return ast.literal_eval(text[start:pos + 1])
    raise ValueError('unterminated weighted boon dataset')


def parse_boon_generation(tiddlers, session_tag=None):
    candidates = [t for t in tiddlers if t.get('title', '').endswith(_SUFFIX)
                  and (not session_tag or t['title'] == session_tag + _SUFFIX)]
    if len(candidates) != 1:
        return None
    source = candidates[0]
    data = _source_literal(source.get('text', ''))
    if not isinstance(data, list) or not data or not isinstance(data[0], list):
        raise ValueError('invalid weighted boon dataset')
    headers = data[0]
    if (not all(isinstance(h, str) for h in headers)
            or len(set(headers)) != len(headers)
            or not {'Player', 'Profession', 'Total'} <= set(headers)
            or not set(headers) - {'Player', 'Profession', 'Total'} <= _BOONS):
        raise ValueError('unknown weighted boon columns')
    boon_keys = [h for h in headers if h in _BOONS]
    if not boon_keys:
        raise ValueError('no weighted boon columns')
    rows = []
    for values in data[1:]:
        if not isinstance(values, list) or len(values) != len(headers):
            raise ValueError('invalid weighted boon row')
        row = dict(zip(headers, values))
        for key in boon_keys + ['Total']:
            value = row[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError('invalid weighted boon value')
        if not isinstance(row['Player'], str) or not isinstance(row['Profession'], str):
            raise ValueError('invalid weighted boon player')
        name = re.sub(r'^\{\{[^{}]+\}\}\s*-\s*', '', row['Player']).strip()
        if not name or not row['Profession'].strip():
            raise ValueError('missing weighted boon identity')
        rows.append({'name': name, 'profession': row['Profession'],
                     'boons': {key: row[key] for key in boon_keys},
                     'source_total': row['Total']})
    return {'scope': 'session', 'unit': 'weighted_generation', 'rows': rows,
            'source_tiddler': source['title'],
            'methodology': 'Exported squad boon generation per active second, using the combiner’s configured boon weights. '
                           'Values are weighted generation, not uptime or percentages; no new weights are applied. '
                           'Each character/profession row is retained. Segment sums can differ slightly from the exported total due to rounding.'}
