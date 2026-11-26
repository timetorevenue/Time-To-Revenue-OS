"""Typed Studio contract; shared JSON schema with Puck, no execution or model access."""
import copy
import math
import re
import json
from pathlib import Path
SCHEMA = json.loads((Path(__file__).parents[1] / 'frontend/src/studio/design-schema.json').read_text())
DEFAULTS = {k: v['default'] for k, v in SCHEMA['globalTokens'].items()}


def validate_tokens(value, fields, partial=False):
    if not isinstance(value, dict) or (not partial and set(value) != set(fields)):
        raise ValueError('Invalid or incomplete style object')
    for key, item in value.items():
        field = fields.get(key)
        if not field:
            raise ValueError('Unsupported style token')
        kind = field['type']
        if kind == 'colour' and (not isinstance(item, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', item)):
            raise ValueError('Invalid colour')
        if kind == 'enum' and item not in field['options']:
            raise ValueError('Invalid style option')
        if kind == 'number' and (type(item) not in (float, int) or not math.isfinite(item) or not field['min'] <= item <= field['max']):
            raise ValueError('Invalid style number')
    return copy.deepcopy(value)


def targets(section):
    if section.get('targets'):
        return {t['id']: t['tag'] for t in section['targets']}
    result = {'section': 'section'}
    counts = {}
    nested = json.loads((Path(__file__).parents[1] / 'frontend/src/studio/nested-schema.json').read_text())
    markup = section['html']
    # Elements inside a native slot child (the WorkflowFigure) belong to that child, not the section.
    for spec in nested['components'].values():
        if spec['sourceType'] == section.get('type'):
            markup = re.sub(r'(<' + spec['sourceTag'] + r'\b[^>]*class="' + spec['sourceClass'] + r'"[^>]*>)[\s\S]*?(</' + spec['sourceTag'] + '>)', r'\1\2', markup, count=1)
    for tag in re.findall(r'<(h[123]|p|a|figure|summary)(?=[\s>])', markup):
        index = counts.get(tag, 0)
        counts[tag] = index + 1
        result[f'{tag}-{index}'] = tag
    for spec in nested['slots'].get(section.get('type'),{}).values():
        result.pop(spec.get('legacy',{}).get('styleTarget'),None)
    return result


def local_fields(tag):
    return {k: v for k, v in SCHEMA['localTokens'].items()
            if (k != 'buttonVariant' or tag == 'a')
            and (k not in ('fontSize', 'fontWeight') or tag in ('h1', 'h2', 'h3', 'p', 'a', 'summary'))}


def validate_styles(value, section):
    if not isinstance(value, dict):
        raise ValueError('Invalid local styles')
    known = targets(section)
    for target, styles in value.items():
        if target not in known:
            raise ValueError('Unknown style destination')
        validate_tokens(styles, local_fields(known[target]), partial=True)
    return copy.deepcopy(value)
