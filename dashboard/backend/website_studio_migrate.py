"""Deterministic Studio draft migration to schema v5: Python twin of frontend/src/studio/migrate.js.

The rules are data (frontend/src/studio/migrations.json). Untouched source text takes the new source
text, owner-edited text is carried to the field with the same meaning, new source sections are
inserted as their original instances, and values with no destination are retained verbatim in
root.props.unsupported (never rendered). Pure: no model, network, file writes or clock. Stored draft
records are never rewritten; migration happens on read. Revisit with a new schema version.
Last touched: 2026-11-21.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import ttros_site

STUDIO = Path(__file__).resolve().parents[1] / 'frontend/src/studio'
SPEC = json.loads((STUDIO / 'migrations.json').read_text())
SCHEMA_VERSION = SPEC['version']
_PAGES = json.loads((STUDIO / 'pages.json').read_text())


class MigrationError(ValueError):
    pass


def _manifest(name: str) -> dict:
    return json.loads((STUDIO / name).read_text())


def _page_meta(page: str):
    return _PAGES['shared'] if page == 'site' else _PAGES['pages'].get(page)


def _same(a, b) -> bool:
    # Same comparison as JSON.stringify equality for JSON values with stable key order.
    return json.dumps(a, ensure_ascii=False) == json.dumps(b, ensure_ascii=False)


def _native_workflow(item: dict) -> dict:
    f, p = SPEC['flatWorkflow'], item['props']
    if item['type'] == 'Section0' and 'graphic' in p and f['slot'] in p:
        raise MigrationError('Mixed legacy and native graphic data.')
    if item['type'] != 'Section0' or f['slot'] in p:
        return item
    if not all(k in p for k in f['fields']):
        raise MigrationError('The saved hero graphic text is incomplete.')
    from website_studio_nested import child_id
    styles = dict(p['styles']) if isinstance(p.get('styles'), dict) else {}
    child = {'type': f['type'], 'props': {'id': child_id(p['id'], f['slot']), **{to: p[frm] for frm, to in f['fields'].items()},
             'graphic': copy.deepcopy(p['graphic'] if 'graphic' in p else f['graphicDefault']),
             'styles': {'section': copy.deepcopy(styles[f['styleTarget']])} if styles.get(f['styleTarget']) else {}}}
    props = {**p, 'styles': styles, f['slot']: [child]}
    for key in f['fields']:
        props.pop(key)
    props.pop('graphic', None)
    styles.pop(f['styleTarget'], None)
    return {'type': item['type'], 'props': props}


def _carry_texts(rule: dict, old: dict, nxt: dict, identity, report: dict) -> None:
    for frm, to in rule['texts'].items():
        if frm not in old:
            raise MigrationError(f'Saved {identity} is missing {frm}.')
        if not isinstance(old[frm], str):
            raise MigrationError(f'Saved {identity}.{frm} is not text.')
        destinations = to if isinstance(to, list) else [to]
        if old[frm] == rule['originals'][frm]:
            report['sourceText'].append({'id': identity, 'from': frm, 'to': destinations})
            continue
        nxt[destinations[0]] = old[frm]
        for extra in destinations[1:]:
            nxt[extra] = ''
        report['carried'].append({'id': identity, 'from': frm, 'to': destinations})


def _carry_styles(mapping: dict, styles, identity) -> dict:
    if styles is None:
        return {}
    if not isinstance(styles, dict):
        raise MigrationError(f'Saved {identity} has invalid styles.')
    out = {}
    for target, values in styles.items():
        if target not in mapping:
            raise MigrationError(f'Saved {identity} styles {target}, which has no destination.')
        out[mapping[target]] = copy.deepcopy(values)
    return out


def _migrate_workflow(old, fresh: dict, report: dict, unsupported: list) -> dict:
    rule = SPEC['components']['WorkflowFigure']
    if not isinstance(old, dict) or old.get('type') != 'WorkflowFigure' or not isinstance(old.get('props'), dict):
        raise MigrationError('The saved hero has no workflow graphic.')
    p = old['props']
    unknown = next((k for k in p if k not in {'id', 'styles', 'graphic', *rule['texts']}), None)
    if unknown:
        raise MigrationError(f'Saved workflow graphic field {unknown} has no destination.')
    props = {**copy.deepcopy(fresh['props']), 'id': p.get('id')}
    _carry_texts(rule, p, props, p.get('id'), report)
    graphic = p['graphic'] if isinstance(p.get('graphic'), dict) else SPEC['flatWorkflow']['graphicDefault']
    for key in rule['graphic']['carry']:
        if key in graphic:
            props['graphic'][key] = graphic[key]
    for key in rule['graphic']['retire']:
        if key in graphic and not _same(graphic[key], SPEC['flatWorkflow']['graphicDefault'][key]):
            unsupported.append({'path': f"{p.get('id')}.graphic.{key}", 'value': copy.deepcopy(graphic[key]), 'reason': rule['graphic']['reason']})
    props['styles'] = _carry_styles(rule['styles'], p.get('styles'), p.get('id'))
    return {'type': 'WorkflowFigure', 'props': props}


def _migrate_item(value, report: dict, unsupported: list) -> dict:
    from website_studio_sections import new_instance
    if not isinstance(value, dict) or not isinstance(value.get('props'), dict) or not isinstance(value.get('type'), str):
        raise MigrationError('Invalid saved section.')
    item = _native_workflow(value)
    if item['type'] in SPEC['unchanged']:
        return copy.deepcopy(item)
    rule = SPEC['types'].get(item['type'])
    if not rule:
        raise MigrationError(f"Saved section type {item['type']} is not part of the {SPEC['from']['commit'][:7]} schema.")
    p, identity = item['props'], item['props'].get('id')
    allowed = {'id', 'styles', *rule['texts'], *rule['links'], *([SPEC['flatWorkflow']['slot']] if item['type'] == 'Section0' else [])}
    unknown = next((k for k in p if k not in allowed), None)
    if unknown:
        raise MigrationError(f'Saved {identity} field {unknown} has no destination.')
    fresh = new_instance(item['type'], identity if isinstance(identity, str) else item['type'])
    props = {**fresh['props'], 'id': identity}
    _carry_texts(rule, p, props, identity, report)
    for frm, to in rule['links'].items():
        if frm in p:
            props[to] = p[frm]
    props['styles'] = _carry_styles(rule['styles'], p.get('styles'), identity)
    if item['type'] == 'Section0':
        slot = SPEC['flatWorkflow']['slot']
        if not isinstance(p.get(slot), list) or len(p[slot]) != 1:
            raise MigrationError('The saved hero must hold exactly one workflow graphic.')
        props[slot] = [_migrate_workflow(p[slot][0], fresh['props'][slot][0], report, unsupported)]
    # Same key order as the JavaScript object: fresh source keys first.
    return {'type': item['type'], 'props': props}


def _insert_sections(content: list, page: str, report: dict) -> None:
    from website_studio_sections import new_instance
    for rule in (r for r in SPEC['insert'] if r['page'] == page):
        if any(i['type'] == rule['type'] for i in content):
            continue
        if any(i['props'].get('id') == rule['type'] for i in content):
            raise MigrationError(f"Section ID {rule['type']} is already used; it is reserved for the new source section.")
        types = [i['type'] for i in content]
        before = types.index(rule['before']) if rule.get('before') in types else -1
        after = len(types) - 1 - types[::-1].index(rule['after']) if rule.get('after') in types else -1
        at = before if before >= 0 else after + 1 if after >= 0 else len(content)
        content.insert(at, new_instance(rule['type'], rule['type']))
        report['inserted'].append({'id': rule['type'], 'type': rule['type'], 'index': at})


def migrate_data(data, page: str, version: int) -> tuple[dict, dict]:
    if not isinstance(data, dict) or not isinstance(data.get('content'), list):
        raise MigrationError('Draft must contain a list of sections.')
    root = data.get('root')
    if root is not None and (not isinstance(root, dict) or ('props' in root and not isinstance(root['props'], dict))):
        raise MigrationError('Invalid root.')
    if 'unsupported' in ((root or {}).get('props') or {}):
        raise MigrationError('A pre-v5 draft cannot carry retained values.')
    report = {'from': version, 'to': SCHEMA_VERSION, 'page': page, 'carried': [], 'sourceText': [], 'inserted': [], 'unsupported': []}
    unsupported: list = []
    content = [_migrate_item(item, report, unsupported) for item in data['content']]
    _insert_sections(content, page, report)
    report['unsupported'] = [u['path'] for u in unsupported]
    props = copy.deepcopy((root or {}).get('props') or {})
    if unsupported:
        props['unsupported'] = unsupported
    out = {} if root is None and not unsupported else {'root': {'props': props}}
    out['content'] = content
    if 'zones' in data:
        out['zones'] = copy.deepcopy(data['zones'])
    return out, report


def migrate_envelope(value) -> tuple[dict, dict | None]:
    """Returns (envelope, report); report is None when the envelope is already v5."""
    if not isinstance(value, dict):
        raise MigrationError('Invalid Studio draft.')
    if value.get('version') == SCHEMA_VERSION:
        return value, None
    page = value.get('page', 'home')
    meta = _page_meta(page) if isinstance(page, str) else None
    if not meta:
        raise MigrationError('Draft belongs to an unknown Studio page.')
    version = value.get('version')
    if version not in SPEC['from']['versions']:
        raise MigrationError(f'Studio draft version {version} is not supported.')
    if version == 1:
        if page != 'home':
            raise MigrationError('Draft source/version does not match this website adapter.')
    elif (page == 'home' and 'page' in value) or (page != 'home' and version < 3) or value.get('project') != ttros_site.SITE_ID \
            or not _same(value.get('source'), SPEC['from']['manifests'][meta['manifest']]):
        raise MigrationError('Draft source/version does not match this website adapter.')
    data, report = migrate_data(value.get('data'), page, version)
    envelope = {'version': SCHEMA_VERSION, 'project': ttros_site.SITE_ID, **({} if page == 'home' else {'page': page}),
                'source': _manifest(meta['manifest']), 'data': data}
    return envelope, report


def validate_unsupported(value) -> list:
    """Retained migration values: bounded, verbatim, never rendered (twin of state.js validateUnsupported)."""
    if not isinstance(value, list) or len(value) > 20:
        raise ValueError('Invalid retained values.')
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {'path', 'reason', 'value'} or not isinstance(entry['path'], str) or not entry['path'] \
                or len(entry['path']) > 200 or not isinstance(entry['reason'], str) or len(entry['reason']) > 300 \
                or len(json.dumps(entry['value'], ensure_ascii=False, separators=(',', ':'))) > 400000:
            raise ValueError('Invalid retained value.')
    return copy.deepcopy(value)
