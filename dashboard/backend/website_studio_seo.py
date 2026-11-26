"""Page SEO and structured data for Website Studio pages (Stage 3): Python twin of
dashboard/frontend/src/studio/seo.js (parity-tested).

A service page's title, meta description, Open Graph/Twitter text and JSON-LD (WebPage,
BreadcrumbList, Service) follow its visible service name and description, exactly as
ServicePage.tsx and lib/commercial.ts derive them from content/services.ts. Routes and canonical
URLs never change. The homepage's metadata is source-owned; its FAQ JSON-LD follows the visible FAQ.
Deterministic only: no model, network or filesystem writes. Recorded with every saved version and
given to David as page context.
Revisit when ServicePage.tsx, lib/commercial.ts or the layout title template change. Last touched: 2026-11-18.
"""
from __future__ import annotations

import copy
import functools
import json
import re
from pathlib import Path

import ttros_site

STUDIO = Path(__file__).resolve().parents[1] / 'frontend/src/studio'
TITLE_TEMPLATE = '%s | Example Company'
LIMITS = {'titleWarn': 65, 'nameMax': 120, 'descriptionMin': 50, 'descriptionWarn': 160, 'descriptionMax': 300}
SITE = ttros_site.SITE_URL


@functools.lru_cache(maxsize=None)
def _json(name: str) -> dict:
    return json.loads((STUDIO / name).read_text())


def seo_text(value) -> str:
    return re.sub(r'\s+', ' ', str(value if value is not None else '')).strip()


def _instance(data: dict, kind: str) -> dict:
    items = [i for i in data['content'] if i['type'] == kind]
    if len(items) != 1:
        raise ValueError(f'{kind} must appear exactly once.')
    return items[0]


def page_seo(page: str, data: dict) -> dict:
    info = _json('pages.json')['pages'].get(page)
    if not info:
        raise ValueError('Unknown Studio page.')
    services = _json('services.json')
    if page == 'home':
        source = services['homeSeo']
        json_ld = copy.deepcopy(source['jsonLd'])
        faq = next((i for i in data['content'] if i['type'] == 'Section7'), None)
        if not faq:
            json_ld['@graph'] = [e for e in json_ld['@graph'] if e['@type'] != 'FAQPage']
        else:
            for entity in json_ld['@graph']:
                if entity['@type'] == 'FAQPage':
                    for index, entry in enumerate(entity['mainEntity']):
                        entry['name'] = faq['props'][f'text{index * 2 + 1}']
                        entry['acceptedAnswer']['text'] = faq['props'][f'text{index * 2 + 2}']
        title = source['title']['absolute']
        return {'page': page, 'route': info['route'], 'title': title, 'documentTitle': title, 'description': source['description'],
                'canonical': source['canonical'], 'jsonLd': json_ld}
    source = services['pages'][page]
    intro = _instance(data, source['seoFields']['name']['section'])
    name = seo_text(intro['props'][source['seoFields']['name']['field']])
    description = seo_text(intro['props'][source['seoFields']['description']['field']])
    json_ld = copy.deepcopy(source['seo']['jsonLd'])
    for entity in json_ld['@graph']:
        if entity['@type'] == 'WebPage':
            entity.update(name=name, description=description)
        if entity['@type'] == 'BreadcrumbList':
            entity['itemListElement'][-1]['name'] = name
        if entity['@type'] == 'Service':
            entity.update(name=name, serviceType=name, description=description)
    # Since 215679e the route's metadata title is the service name plus a fixed source suffix.
    title = name + source.get('titleSuffix', '')
    return {'page': page, 'route': info['route'], 'name': name, 'title': title, 'documentTitle': TITLE_TEMPLATE.replace('%s', title),
            'description': description, 'canonical': source['seo']['canonical'], 'jsonLd': json_ld}


def json_ld_problems(seo: dict) -> list[str]:
    graph = (seo.get('jsonLd') or {}).get('@graph')
    if (seo.get('jsonLd') or {}).get('@context') != 'https://schema.org' or not isinstance(graph, list) or not graph:
        return ['Structured data must be a schema.org @graph.']
    out, ids = [], set()
    for entity in graph:
        if not isinstance(entity.get('@type'), str):
            out.append('Every structured-data entity needs a @type.')
        if entity.get('@id'):
            if entity['@id'] in ids:
                out.append(f"Duplicate structured-data @id {entity['@id']}.")
            ids.add(entity['@id'])
        for key in ('name', 'description'):
            if key in entity and not seo_text(entity[key]):
                out.append(f"{entity['@type']} {key} is empty.")
    web = next((e for e in graph if e['@type'] == 'WebPage'), None)
    if not web or web.get('url') != seo['canonical'] or not str(web.get('@id', '')).startswith(seo['canonical']):
        out.append('WebPage URL/@id must be the canonical URL.')
    routes = _json('section-rules.json')['links']['routes']
    if seo['page'] != 'home':
        service = next((e for e in graph if e['@type'] == 'Service'), None)
        crumbs = next((e for e in graph if e['@type'] == 'BreadcrumbList'), None)
        if (not service or service.get('url') != seo['canonical'] or service.get('@id') != f"{seo['canonical']}#service"
                or service.get('name') != seo['name'] or service.get('description') != seo['description']):
            out.append('Service structured data must match the visible service name, description and canonical URL.')
        items = (crumbs or {}).get('itemListElement') or []
        if (not items or any(c.get('position') != i + 1 or not str(c.get('item')).startswith(SITE) for i, c in enumerate(items))
                or items[-1].get('item') != seo['canonical'] or items[-1].get('name') != seo['name']):
            out.append('Breadcrumbs must end at this page with its visible name.')
        for crumb in items:
            path = str(crumb.get('item'))[len(SITE):] or '/'
            if path not in routes:
                out.append(f"Breadcrumb {crumb.get('item')} is not a page on this website.")
    else:
        faq = next((e for e in graph if e['@type'] == 'FAQPage'), None)
        for entry in (faq or {}).get('mainEntity') or []:
            if not seo_text(entry.get('name')) or not seo_text((entry.get('acceptedAnswer') or {}).get('text')):
                out.append('FAQ structured data has an empty question or answer.')
    return out


def seo_problems(page: str, data: dict) -> dict:
    """{'seo', 'errors', 'warnings'}: errors block export for the site adapter; warnings are shown."""
    seo = page_seo(page, data)
    errors, warnings = [], []
    services = _json('services.json')
    original = services['homeSeo'] if page == 'home' else services['pages'][page]['seo']
    if seo['canonical'] != original['canonical'] or seo['canonical'] != (SITE if seo['route'] == '/' else SITE + seo['route']):
        errors.append('Canonical URL changed.')
    if page != 'home':
        title, description = seo['name'], seo['description']
        if not title:
            errors.append('The service name (Intro label) is empty: it is the page title.')
        elif len(title) > LIMITS['nameMax']:
            errors.append(f"The service name is {len(title)} characters (max {LIMITS['nameMax']}).")
        # The website's own title is not flagged (215679e ships longer titles); a changed long title is.
        elif len(seo['documentTitle']) > LIMITS['titleWarn'] and seo['title'] != original['title']:
            warnings.append(f"Page title is {len(seo['documentTitle'])} characters; search results usually show about {LIMITS['titleWarn']}.")
        if not description:
            errors.append('The service description (Intro lead) is empty: it is the meta description.')
        elif len(description) > LIMITS['descriptionMax']:
            errors.append(f"The description is {len(description)} characters (max {LIMITS['descriptionMax']}).")
        elif len(description) > LIMITS['descriptionWarn']:
            warnings.append(f"Meta description is {len(description)} characters; search results usually show about {LIMITS['descriptionWarn']}.")
        elif len(description) < LIMITS['descriptionMin']:
            warnings.append(f"Meta description is {len(description)} characters; {LIMITS['descriptionMin']}–{LIMITS['descriptionWarn']} is recommended.")
    errors += json_ld_problems(seo)
    return {'seo': seo, 'errors': errors, 'warnings': warnings}


def summary(page: str, data: dict) -> dict:
    """Compact SEO record stored with every saved version and given to David."""
    result = seo_problems(page, data)
    seo = result['seo']
    return {'route': seo['route'], 'title': seo['documentTitle'], 'description': seo['description'], 'canonical': seo['canonical'],
            'errors': result['errors'], 'warnings': result['warnings']}
