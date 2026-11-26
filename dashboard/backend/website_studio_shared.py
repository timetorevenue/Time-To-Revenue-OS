"""Deterministic shared-field validation, twin of shared.js. No model or website write path."""
import json
import re
from website_studio_sections import RULES, PAGES, original_anchors, page_anchors, _snapshot
NAVIGATION = {'SharedHeader': ['navigation'], 'SharedFooter': ['learning', 'legal']}

def link_problem(href, drafts=None):
    if not isinstance(href, str):
        return 'Invalid destination.'
    parts = href.split('#')
    route = parts[0]
    if len(parts) > 2 or route not in RULES['links']['routes']:
        return 'Choose an existing website route.'
    if len(parts) == 2:
        page = next((p for p, v in PAGES.items() if v['route'] == route), None)
        anchors = page_anchors(drafts[page]['content']) if drafts and page in drafts else original_anchors(page) if page else set()
        if not parts[1] or parts[1] not in anchors:
            return f'Anchor #{parts[1]} does not exist on {route}.'
    return None

def validate_shared(content, drafts=None):
    for item in content:
        props = item['props']
        for key, value in props.items():
            if key in ('id', 'styles', *NAVIGATION[item['type']]):
                continue
            if not isinstance(value, str) or not value.strip() or len(value) > 500 or re.search(r'[<>\x00-\x1f]', value):
                raise ValueError(f'Invalid shared {key}.')
        if props['logo'] not in _snapshot('shared.json')['logos']:
            raise ValueError('Choose a registered site logo.')
        error = link_problem(props['destination'], drafts)
        if error:
            raise ValueError(error)
        if item['type'] == 'SharedFooter' and not re.fullmatch(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', props['email']):
            raise ValueError('Invalid footer email.')
        for key in NAVIGATION[item['type']]:
            try:
                rows = json.loads(props[key])
            except (ValueError, TypeError):
                raise ValueError('Invalid navigation rows.')
            if not isinstance(rows, list) or len(rows) > 12:
                raise ValueError('Navigation holds at most 12 items per group.')
            ids, hrefs = set(), set()
            for row in rows:
                if not isinstance(row, dict) or set(row) != {'id', 'label', 'href'} or not isinstance(row['id'], str) or not re.fullmatch(r'[a-z][a-z0-9-]{1,63}', row['id']) or row['id'] in ids:
                    raise ValueError('Navigation needs unique stable IDs.')
                if not isinstance(row['label'], str) or not row['label'].strip() or len(row['label']) > 100 or re.search(r'[<>\x00-\x1f]', row['label']):
                    raise ValueError('Navigation needs a plain-text label (1–100 characters).')
                error = link_problem(row['href'], drafts)
                if error:
                    raise ValueError(error)
                if row['href'] in hrefs:
                    raise ValueError('Duplicate navigation destination in this group.')
                ids.add(row['id']); hrefs.add(row['href'])
    return content


def effective_data(data, site):
    import copy
    from website_studio_design import DEFAULTS
    result = copy.deepcopy(data)
    root = data['root']['props']
    own = root.get('pageStyles', {k:v for k,v in root.get('design', DEFAULTS).items() if v != DEFAULTS[k]})
    result['root']['props']['design'] = {**site['root']['props']['design'], **own}
    return result


def site_new_failures(page, base, next_data, drafts, site):
    import website_studio_contrast as contrast
    if page != 'site':
        return contrast.new_failures(effective_data(base, site), effective_data(next_data, site))
    return contrast.new_failures(base, next_data) + [dict(f, page=pid) for pid,data in drafts.items() for f in contrast.new_failures(effective_data(data, base), effective_data(data, next_data))]
