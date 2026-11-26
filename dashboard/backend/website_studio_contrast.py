"""Deterministic WCAG 2.x AA text-contrast checks for Website Studio pages (Stage 2.1).

Python twin of dashboard/frontend/src/studio/contrast.js (parity-tested). Every text field's
colour and background are resolved from the shared colour model (contrast-model.json, built from
the site CSS) through global design tokens and local overrides, including inherited ones.
No model access, no DOM access. Used to refuse David and worker operations that introduce failures
and to record known failures on every saved draft version.
Revisit when the colour model, design tokens or local style semantics change. Last touched: 2026-11-18.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from website_studio_nested import walk_items, migrate_item
from website_brand import luminance

MODEL = json.loads((Path(__file__).resolve().parents[1] / 'frontend/src/studio/contrast-model.json').read_text())
AA_NORMAL, AA_LARGE = 4.5, 3.0
MIN_HEADING = {'h1': 48, 'h2': 35, 'h3': 20}


def ratio(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def shown(value: float) -> float:
    """Floored to two decimals, so a value shown as 4.50 always passes 4.5."""
    return math.floor(value * 100) / 100


def _resolve(expr, index, nodes, styles, design):
    m = re.fullmatch(r'var\(--([\w-]+)\)', expr)
    if not m:
        return {'hex': expr.lower(), 'token': None}
    name = m.group(1)
    if name in ('ink', 'muted'):
        i = index
        while i is not None:
            text = nodes[i].get('target') and (styles.get(nodes[i]['target']) or {}).get('text')
            if text:
                return {'hex': text.lower(), 'token': None, 'inherited': nodes[i]['target']}
            i = nodes[i]['parent']
    token = MODEL['vars'][name]
    return {'hex': str(design[token]).lower(), 'token': token}


def _foreground(start, nodes, styles, design):
    i = start
    while i is not None:
        node = nodes[i]
        local = (styles.get(node['target']) or {}) if node.get('target') else {}
        if local.get('text'):
            return {'hex': local['text'].lower(), 'kind': 'override', 'target': node['target'], 'token': None}
        if node['tag'] == 'a' and local.get('buttonVariant'):
            expr = 'var(--paper)' if local['buttonVariant'] == 'primary' else 'var(--ink)'
            return {**_resolve(expr, i, nodes, styles, design), 'kind': 'variant', 'target': node['target']}
        if node.get('color'):
            return {**_resolve(node['color'], i, nodes, styles, design), 'kind': 'css', 'target': node.get('target')}
        i = node['parent']
    return {**_resolve(MODEL['root']['color'], None, nodes, styles, design), 'kind': 'page', 'target': None}


def _background(start, nodes, styles, design):
    i = start
    while i is not None:
        node = nodes[i]
        local = (styles.get(node['target']) or {}) if node.get('target') else {}
        variant = local.get('buttonVariant') if node['tag'] == 'a' else None
        if local.get('background'):
            return {'hex': local['background'].lower(), 'kind': 'override', 'target': node['target'], 'token': None}
        if variant and variant != 'outline':
            return {**_resolve(f'var(--studio-{variant})', i, nodes, styles, design), 'kind': 'variant', 'target': node['target']}
        if not variant and node.get('background'):
            return {**_resolve(node['background'], i, nodes, styles, design), 'kind': 'css', 'target': node.get('target')}
        i = node['parent']
    return {**_resolve(MODEL['root']['background'], None, nodes, styles, design), 'kind': 'page', 'target': None}


def _size(spec, nodes, styles, design):
    local = (styles.get(nodes[spec['sizeNode']]['target']) or {}) if spec['sizeNode'] is not None else {}
    role = spec['role']
    if 'fontSize' in local:
        size = local['fontSize']
    elif 'fixedSize' in spec:
        size = spec['fixedSize']
    else:
        size = min(design[f'{role}Size'], MIN_HEADING[role]) if role in MIN_HEADING else design[f'{role}Size']
    weight = local.get('fontWeight', design[f'{role}Weight'])
    return size, weight, size >= 24 or (size >= 18.66 and weight >= 700)


def checks(data: dict, state: str = 'default') -> list[dict]:
    """One check per visible text field of every instance; id is '<instance>:<field>'."""
    design = data['root']['props']['design']
    out = []
    for item in walk_items([migrate_item(i) for i in data['content']]):
        section = MODEL['sections'].get(item['type'])
        if not section:
            raise ValueError(f"Missing contrast model for {item['type']}")
        section = {**section, 'nodes': section.get('states', {}).get(state, section['nodes'])}
        for field in item['props']:
            if re.fullmatch(r'text\d+', field) and field not in section['fields']:
                raise ValueError(f"Missing contrast field {item['type']}:{field}")
        styles = item['props'].get('styles') or {}
        for field, spec in section['fields'].items():
            text = str(item['props'].get(field) or '').strip()
            if not text:
                continue
            fg = _foreground(spec['node'], section['nodes'], styles, design)
            bg = _background(spec['node'], section['nodes'], styles, design)
            size, weight, large = _size(spec, section['nodes'], styles, design)
            value = ratio(fg['hex'], bg['hex'])
            required = AA_LARGE if large else AA_NORMAL
            out.append({'id': f"{item['props']['id']}:{field}", 'section': item['props']['id'], 'type': item['type'],
                        'field': field, 'target': spec['target'], 'foreground': fg['hex'], 'background': bg['hex'],
                        'ratio': shown(value), 'required': required, 'large': large, 'pass': value >= required})
    return out


def failures(data: dict) -> list[dict]:
    return list({c['id']: c for state in ('default', 'hover') for c in checks(data, state) if not c['pass']}.values())


def new_failures(base: dict, nxt: dict) -> list[dict]:
    """Failures in `nxt` that `base` did not have (by instance and field)."""
    before = {f['id'] for f in failures(base)}
    return [f for f in failures(nxt) if f['id'] not in before]


def describe(items: list[dict]) -> str:
    return '; '.join(f"{f['id']} {f['foreground']} on {f['background']} {f['ratio']:.2f}:1 (needs {f['required']:g}:1)" for f in items)
