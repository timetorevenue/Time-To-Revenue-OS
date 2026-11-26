"""Section instances for Website Studio (Stage 2): Python twin of frontend/src/studio/sections.js.

A section type is a real website component from homepage.json; an instance has a stable ID that
is independent of its type and position. The nine original instances keep their saved IDs; added
and duplicated instances get fresh IDs. Deterministic only: no model access.
Stage 3: section types belong to a page (pages.json): the homepage's nine and each service page's
five ServicePage sections (services.json). A page's content may only hold its own types.
Revisit when homepage.json/services.json is re-snapshotted or section-rules.json changes. Last touched: 2026-11-18.
"""
from __future__ import annotations

from website_studio_nested import nested_types, migrate_item, own_texts, slots_for, validate_children, walk_items, rekey_children
import copy
import html
import json
import re
import secrets
from pathlib import Path
from urllib.parse import urlsplit

STUDIO = Path(__file__).resolve().parents[1] / 'frontend/src/studio'
RULES = json.loads((STUDIO / 'section-rules.json').read_text())
_PAGES = json.loads((STUDIO / 'pages.json').read_text())
PAGES = {**_PAGES['pages'], 'site': _PAGES['shared']}
ID_PATTERN = re.compile(RULES['idPattern'])
LINKS = RULES['links']
PATTERNS = {k: re.compile(LINKS[k]) for k in ('https', 'mailto', 'internal', 'slugPattern')}
OP_KEYS = {'add': {'id', 'op', 'place', 'ref', 'type'}, 'duplicate': {'id', 'op', 'place', 'ref', 'source'}, 'remove': {'id', 'op'}}
ALPHABET = 'abcdefghijkmnpqrstuvwxyz23456789'


class SectionError(ValueError):
    pass


_SNAPSHOTS: dict = {}


def _snapshot(name: str) -> dict:
    """Committed, read-only snapshot files (parsed once; callers receive fresh dicts)."""
    if name not in _SNAPSHOTS:
        _SNAPSHOTS[name] = json.loads((STUDIO / name).read_text())
    return _SNAPSHOTS[name]


def _sources():
    """(page, section, rules) for every Studio page's section types, in page order."""
    for section in _snapshot('shared.json')['sections']:
        yield 'site', section, section
    for section in _snapshot('homepage.json')['sections']:
        yield 'home', section, RULES['types'][section['type']]
    for page, value in _snapshot('services.json')['pages'].items():
        for section in value['sections']:
            # Rules of the section's permanent index (type-name suffix), not its position.
            index = int(re.search(r'(\d+)$', section['type'])[1])
            yield page, section, {**RULES['serviceTypes'][index], 'originalId': section['type']}


def section_types() -> dict:
    out = nested_types()
    for page, section, meta in _sources():
        out[section['type']] = {
            **section, **meta, 'page': page, 'min': meta.get('min', 0), 'max': meta.get('max', RULES['maxSections']),
            'links': [html.unescape(m) for m in re.findall(r'<a\b[^>]*?\shref="([^"]*)"', section['html'])],
            'domIds': [html.unescape(m) for m in re.findall(r'\sid="([^"]+)"', section['html'])],
        }
    return out


def page_types(page: str) -> dict:
    return {kind: s for kind, s in section_types().items() if s['page'] == page and not s.get('nested')}


def page_of_content(content) -> str:
    """The page a list of instances belongs to (from its first instance's type)."""
    first = content[0] if isinstance(content, list) and content else None
    kind = first.get('type') if isinstance(first, dict) else None
    section = section_types().get(kind) if isinstance(kind, str) else None
    if not section:
        raise SectionError('Unknown section type.')
    return section['page']


def page_by_route(route: str) -> str | None:
    return next((page for page, info in PAGES.items() if info['route'] == route), None)


def original_anchors(page: str) -> set:
    return set(RULES['pageAnchors']) | {i for s in page_types(page).values() for i in s['domIds']}


def link_keys(section: dict) -> list[str]:
    return [f'link{i}' for i in range(len(section['links']))]


def new_instance(kind: str, instance_id: str) -> dict:
    section = section_types().get(kind)
    if not section:
        raise SectionError('Unknown section type.')
    return migrate_item({'type': kind, 'props': {'id': instance_id, **section['texts'],
                                    **{f'link{i}': href for i, href in enumerate(section['links'])}, 'styles': {}}})


def allocate_id(kind: str, existing) -> str:
    section = section_types().get(kind)
    if not section:
        raise SectionError('Unknown section type.')
    taken = set(existing)
    for _ in range(50):
        candidate = f"{section['slug']}-{''.join(secrets.choice(ALPHABET) for _ in range(6))}"
        if candidate not in taken and ID_PATTERN.fullmatch(candidate):
            return candidate
    raise SectionError('Could not allocate a unique section ID.')


def is_original(item: dict) -> bool:
    return item['props']['id'] == section_types()[item['type']]['originalId']


def anchor_map(item: dict) -> dict:
    section = section_types()[item['type']]
    return {i: i if is_original(item) else f"{i}-{item['props']['id']}" for i in section['domIds']}


def page_anchors(content: list) -> set:
    out = set(RULES['pageAnchors'])
    for item in content:
        out.update(anchor_map(item).values())
    return out


def link_problem(href, anchors: set, route: str = '/') -> str | None:
    if not isinstance(href, str) or not href or len(href) > LINKS['maxLength']:
        return 'Links must be 1–500 characters.'
    if PATTERNS['https'].fullmatch(href):
        try:
            parts = urlsplit(href)
        except ValueError:
            return 'Invalid web address.'
        return 'Links may not contain credentials.' if parts.username or parts.password else None
    if PATTERNS['mailto'].fullmatch(href):
        return None
    match = PATTERNS['internal'].fullmatch(href)
    if not match or (not match.group(1) and not match.group(2)) or '//' in (match.group(1) or ''):
        return 'Use a site page (/about), an anchor on this page (#how-it-works), https:// or mailto:.'
    path = (match.group(1).rstrip('/') or '/') if match.group(1) else None
    if path:
        prefix = next((p for p in LINKS['routePrefixes'] if path.startswith(p)), None)
        known = path in LINKS['routes'] or (prefix and PATTERNS['slugPattern'].fullmatch(path[len(prefix):]))
        if not known:
            return f'{path} is not a page on this website.'
    anchor = match.group(2)[1:] if match.group(2) else None
    if anchor and path in (None, route) and anchor not in anchors:
        return f'#{anchor} is not on this page.'
    other = page_by_route(path) if anchor and path and path != route else None
    if other and anchor not in original_anchors(other):
        return f'#{anchor} is not on {path}.'
    return None


def apply_section_operation(content: list, op, allow_remove: bool = True) -> list:
    if not isinstance(op, dict):
        raise SectionError('Invalid section operation.')
    keys = OP_KEYS.get(op.get('op'))
    if not keys or set(op) - keys:
        raise SectionError('Unsupported section operation.')
    ids = [i['props']['id'] for i in content]
    if op['op'] == 'remove':
        if not allow_remove:
            raise SectionError('Section removal is a manual action.')
        if op.get('id') not in ids:
            raise SectionError('Unknown section.')
        return [i for i in content if i['props']['id'] != op['id']]
    new_id = op.get('id')
    if not isinstance(new_id, str) or not ID_PATTERN.fullmatch(new_id) or new_id in ids:
        raise SectionError('A new section needs a new, unique ID.')
    if op['op'] == 'add':
        if not isinstance(op.get('type'), str):
            raise SectionError('Unknown section type.')
        item = new_instance(op['type'], new_id)
    else:
        source = next((i for i in content if i['props']['id'] == op.get('source')), None)
        if source is None:
            raise SectionError('Unknown section to duplicate.')
        item = copy.deepcopy(source)
        item['props']['id'] = new_id
        rekey_children(item)
    place = op.get('place', 'after' if op['op'] == 'duplicate' else 'end')
    ref = op.get('ref', op.get('source') if op['op'] == 'duplicate' else None)
    if place == 'end':
        if 'ref' in op:
            raise SectionError('An end insertion has no reference section.')
        index = len(content)
    elif place in ('before', 'after'):
        if ref not in ids:
            raise SectionError('Unknown insertion point.')
        index = ids.index(ref) + (place == 'after')
    else:
        raise SectionError('Invalid insertion place.')
    return [*content[:index], item, *content[index:]]


def limit_problem(content: list, page: str | None = None) -> str | None:
    for kind, section in page_types(page or page_of_content(content)).items():
        count = sum(1 for i in content if i['type'] == kind)
        reason = f": {section['limitReason']}" if section.get('limitReason') else '.'
        if count < section['min']:
            return f"{section['label']} is required{reason}"
        if count > section['max']:
            return f"{section['label']} allows {section['max']}{reason}"
    return None


def validate_content(data: dict, validate_styles, page: str | None = None) -> list:
    """Normalised content of one page: its own section types, required texts, link destinations
    (migrated defaults) and local styles."""
    content = data.get('content')
    if not isinstance(content, list):
        raise SectionError('Draft must contain a list of sections.')
    if not 1 <= len(content) <= RULES['maxSections']:
        raise SectionError(f"A page holds 1–{RULES['maxSections']} sections.")
    page = page or page_of_content(content)
    if page not in PAGES:
        raise SectionError('Unknown Studio page.')
    route = PAGES[page]['route']
    known = section_types()
    seen, out, count = set(), [], [0]
    for item in content:
        if not isinstance(item, dict) or not isinstance(item.get('props'), dict):
            raise SectionError('Invalid section.')
        item = migrate_item(item)
        kind = item.get('type')
        section = known.get(kind) if isinstance(kind, str) else None
        if not section:
            raise SectionError('Unknown section type.')
        if section.get('nested') or section['page'] != page:
            raise SectionError(f"{section['label']} ({kind}) belongs to another page.")
        props = item['props']
        instance_id = props.get('id')
        if not isinstance(instance_id, str) or not ID_PATTERN.fullmatch(instance_id) or instance_id in seen:
            raise SectionError('Invalid or duplicate section ID.')
        seen.add(instance_id)
        texts, links = own_texts(kind,section['texts']), link_keys(section)
        if set(props) - {'id', 'styles', *texts, *links, *slots_for(kind)}:
            raise SectionError('Unknown field')
        normal = {'id': instance_id}
        for key in slots_for(kind):normal[key] = props.get(key)
        for key in texts:
            if not isinstance(props.get(key), str) or len(props[key]) > 4000:
                raise SectionError('Invalid text')
            normal[key] = props[key]
        for index, key in enumerate(links):
            value = props.get(key, section['links'][index])
            if not isinstance(value, str):
                raise SectionError('Invalid link.')
            normal[key] = value
        normal['styles'] = validate_styles(props.get('styles', {}), section)
        out.append(validate_children({'type': kind, 'props': normal},seen,validate_styles,count=count))
    problem = limit_problem(out, page)
    if problem:
        raise SectionError(problem)
    anchors = page_anchors(out)
    for item in out:
        section = known[item['type']]
        for index, key in enumerate(link_keys(section)):
            problem = link_problem(item['props'][key], anchors, route)
            if problem:
                raise SectionError(f"{section['label']} link {index + 1}: {problem}")
    return out


def instance_types(data: dict | None, proposal: dict | None = None) -> dict:
    """Instance ID → type for a page, including instances a proposal's section operations add."""
    content = list((data or {}).get('content') or [])
    for op in (proposal or {}).get('sections') or []:
        try:
            content = apply_section_operation(content, op)
        except (SectionError, KeyError, TypeError):
            continue
    return {i['props']['id']: i['type'] for i in walk_items(content) if isinstance(i, dict) and isinstance(i.get('props'), dict)}
