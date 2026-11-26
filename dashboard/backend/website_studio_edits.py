"""The one validated Website Studio edit API: structured operations, preview and versioned drafts.

Manual editor saves, David's proposals and any other authorised worker use these functions;
there is no second write path. Deterministic only: this module cannot import or reach a model.
Drafts are stored under Studio state, never in a website checkout.
Stage 3: drafts are per Studio page. The homepage keeps its folder (<state>/<site>/drafts) and
envelope shape; other pages use <state>/<site>/pages/<page>/drafts and name their page.
Revisit: when the Studio draft or proposal schema changes. Last touched: 2026-11-18.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

import ttros_site
import website_brand as brandkit
import website_studio_contrast as contrast
import website_studio_sections as section_model
import website_studio_seo as seo
from website_studio_design import SCHEMA, targets, validate_tokens, validate_styles

FRONTEND = Path(__file__).resolve().parents[1] / 'frontend/src/studio'
ACTOR = re.compile(r'[a-z][a-z0-9_.:-]{1,63}\Z')
MAX_DRAFTS = 500
import website_studio_migrate as migration
DRAFT_VERSION = migration.SCHEMA_VERSION  # v5 = website 215679e. v1–v4 (c58e641) drafts migrate on read.


class EditError(ValueError):
    pass


class StaleError(EditError):
    pass


def sections() -> dict:
    """Every Studio page's section types (homepage and service pages), by type."""
    return section_model.section_types()


def page_info(page: str) -> dict:
    if page not in section_model.PAGES:
        raise EditError('Unknown Studio page.')
    return section_model.PAGES[page]


def manifest(page: str = 'home') -> dict:
    return json.loads((FRONTEND / page_info(page)['manifest']).read_text())


def initial_data(page: str = 'home') -> dict:
    """Python twin of state.js initialDataFor: the actual site's text and style defaults."""
    from website_studio_design import DEFAULTS
    page_info(page)
    return {'root': {'props': {'design': dict(DEFAULTS)}},
            'content': [section_model.new_instance(kind, kind) for kind in section_model.page_types(page)]}


def envelope(data: dict, page: str, project: str = ttros_site.SITE_ID) -> dict:
    """Same shape as state.js exportDraft: the homepage envelope is unchanged from Stage 2."""
    base = {'version': DRAFT_VERSION, 'project': project}
    return {**base, 'source': manifest(page), 'data': data} if page == 'home' else {**base, 'page': page, 'source': manifest(page), 'data': data}


def canonical(data: dict, page: str | None = None) -> dict:
    """Same normal form as state.js validateData: root design plus ordered instances with links and styles."""
    from website_studio import validate_data
    content = validate_data(data, page)
    design = data.get('root', {}).get('props', {}).get('design')
    if design is None:
        from website_studio_design import DEFAULTS
        design = DEFAULTS
    validate_tokens(design, SCHEMA['globalTokens'])
    props = {'design': copy.deepcopy(design)}
    if 'pageStyles' in data.get('root', {}).get('props', {}):
        props['pageStyles'] = copy.deepcopy(data['root']['props']['pageStyles'])
    if 'unsupported' in data.get('root', {}).get('props', {}):
        props['unsupported'] = copy.deepcopy(data['root']['props']['unsupported'])
    return {'root': {'props': props},
            'content': [{'type': item['type'], 'props': {**copy.deepcopy(item['props']), 'styles': copy.deepcopy(item['props'].get('styles', {}))}} for item in content]}


def fingerprint(data: dict) -> str:
    return hashlib.sha256(json.dumps(canonical(data), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def apply_proposal(data: dict, proposal: dict, allow_remove: bool = True, page: str | None = None) -> dict:
    """Python twin of state.js applyProposal; validates first, then applies atomically."""
    from website_studio import validate_proposal, apply_operations
    validate_proposal(proposal, data, allow_remove, page)
    return canonical(apply_operations(data, proposal, allow_remove, page), page)


def tag_lookup(section_type: str) -> dict:
    section = sections().get(section_type)
    return targets(section) if section else {}


def brand_check(proposal: dict, rules: dict | None, data: dict | None = None) -> list[dict]:
    """Values a proposal introduces: design, style patches and the local styles a duplicate copies.
    Instance types come from the page the proposal applies to, including instances it adds."""
    if not rules:
        return []
    types = section_model.instance_types(data, proposal)

    out = brandkit.proposal_violations(proposal, rules, lambda instance: tag_lookup(types.get(instance, instance)))
    for op in proposal.get('components') or []:
        graphic = op.get('values', {}).get('graphic', {}) if isinstance(op, dict) and isinstance(op.get('values'), dict) else {}
        for key in ('accent', 'line'):
            colour = graphic.get(key) if isinstance(graphic, dict) else None
            if colour:
                out += brandkit.token_violations({'accent': colour}, rules, op['component'] + f'.graphic.{key}.')
    content = list((data or {}).get('content') or [])
    for op in proposal.get('sections') or []:
        try:
            content = section_model.apply_section_operation(content, op)
        except (ValueError, KeyError, TypeError):
            continue
        if op.get('op') == 'duplicate':
            copy_item = next(i for i in content if i['props']['id'] == op['id'])
            lookup = tag_lookup(copy_item['type'])
            for target, values in (copy_item['props'].get('styles') or {}).items():
                out += brandkit.style_violations(op['id'], target, lookup.get(target, ''), values, rules)
    return out


def contrast_check(proposal: dict, data: dict, allow_remove: bool = True, page: str | None = None) -> list[dict]:
    """WCAG AA text-contrast failures the operations would introduce (not ones the page already has).
    David and workers may not introduce any; the owner sees and acknowledges known failures."""
    return contrast.new_failures(canonical(data, page), apply_proposal(data, proposal, allow_remove, page))


def design_violations(data: dict, rules: dict | None) -> list[dict]:
    """Every current deviation from approved brand rules: shown as visible owner overrides."""
    if not rules:
        return []
    data = canonical(data)
    out = brandkit.token_violations(data['root']['props']['design'], rules)
    for item in section_model.walk_items(data['content']):
        lookup = tag_lookup(item['type'])
        for target, values in item['props'].get('styles', {}).items():
            out += brandkit.style_violations(item['props']['id'], target, lookup.get(target, ''), values, rules)
    return out


def summarise(base: dict, nxt: dict) -> dict:
    base, nxt = canonical(base), canonical(nxt)
    texts, styles = [], []
    old = {i['props']['id']: i for i in section_model.walk_items(base['content'])}
    new_ids = [i['props']['id'] for i in nxt['content']]
    all_new_ids = {i['props']['id'] for i in section_model.walk_items(nxt['content'])}
    for item in section_model.walk_items(nxt['content']):
        if item['props']['id'] not in old:
            continue
        before = old[item['props']['id']]['props']
        for key, value in item['props'].items():
            if key not in ('id', 'styles', *section_model.slots_for(item['type'])) and before.get(key) != value:
                texts.append({'section': item['props']['id'], 'field': key, 'before': before.get(key), 'after': value})
        if before.get('styles') != item['props'].get('styles'):
            styles.append({'section': item['props']['id'], 'before': before.get('styles'), 'after': item['props'].get('styles')})
    design = {k: {'before': base['root']['props']['design'][k], 'after': v}
              for k, v in nxt['root']['props']['design'].items() if base['root']['props']['design'][k] != v}
    return {'texts': texts, 'design': design, 'styles': styles,
            'added': [{'id': i['props']['id'], 'type': i['type']} for i in nxt['content'] if i['props']['id'] not in old],
            'removed': [{'id': i, 'type': old[i]['type']} for i in old if i not in all_new_ids],
            'order': new_ids if new_ids != [i['props']['id'] for i in base['content']] else None}


class Drafts:
    """Versioned, append-only Studio drafts with whole-draft stale protection."""

    def __init__(self, sources):
        self.sources = sources

    def _dir(self, site_id: str, page: str = 'home') -> Path:
        self.sources.site(site_id)
        return self.sources.state / site_id / page_info(page)['draftFolder']

    def list(self, site_id: str, page: str = 'home') -> list[dict]:
        folder = self._dir(site_id, page)
        rows = []
        for path in sorted(folder.glob('v*.json')) if folder.is_dir() else []:
            value = json.loads(path.read_text())
            rows.append({k: value.get(k) for k in ('version', 'base_version', 'saved_at', 'actor', 'note', 'fingerprint', 'origin', 'brand_overrides', 'contrast_failures', 'page', 'seo')})
            rows[-1]['page'] = rows[-1]['page'] or 'home'
        return rows

    def latest(self, site_id: str, page: str = 'home') -> int:
        rows = self.list(site_id, page)
        return rows[-1]['version'] if rows else 0

    def get(self, site_id: str, version: int, page: str = 'home') -> dict:
        path = self._dir(site_id, page) / f'v{int(version):04d}.json'
        if not path.is_file():
            raise KeyError('Unknown draft version')
        return json.loads(path.read_text())

    def base(self, site_id: str, page: str = 'home') -> dict:
        latest = self.latest(site_id, page)
        if latest:
            # Saved v1–v4 records stay byte-identical on disk; their data migrates to v5 on read.
            return migration.migrate_envelope(self.get(site_id, latest, page)['envelope'])[0]['data']
        data = initial_data(page)
        if page == 'site' and self.latest(site_id, 'home'):
            home = self.base(site_id, 'home')['root']['props']
            if 'pageStyles' not in home:
                data['root']['props']['design'] = copy.deepcopy(home['design'])
        return data

    def new_contrast_failures(self, site_id, base, next_data, page):
        from website_studio_shared import site_new_failures
        drafts = {p:self.base(site_id,p) for p in section_model.PAGES if p != 'site'}
        return site_new_failures(page, base, next_data, drafts, self.base(site_id,'site'))

    def save(self, site_id: str, data: dict, base_version: int, actor: str, note: str = '', origin: str = 'manual',
             operations: dict | None = None, page: str = 'home') -> dict:
        import fcntl
        folder = self._dir(site_id, page)
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / '.save.lock').open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            return self._save(site_id, data, base_version, actor, note, origin, operations, page)

    def _save(self, site_id: str, data: dict, base_version: int, actor: str, note: str = '', origin: str = 'manual',
              operations: dict | None = None, page: str = 'home') -> dict:
        from website_studio_sources import now, _atomic
        if not isinstance(actor, str) or not ACTOR.fullmatch(actor):
            raise EditError('Invalid actor')
        if origin not in ('manual', 'david', 'worker'):
            raise EditError('Invalid origin')
        if not isinstance(note, str) or len(note) > 500:
            raise EditError('Invalid note')
        site = self.sources.site(site_id)
        revision = self.sources.revision(site)
        if not revision['current']:
            raise StaleError('Website source drifted from the Studio adapter: ' + ' '.join(revision['problems']))
        page_info(page)
        latest = self.latest(site_id, page)
        if not isinstance(base_version, int) or base_version != latest:
            raise StaleError(f'Draft v{latest} is newer than your base v{base_version}. Reload the latest draft before saving.')
        if latest >= MAX_DRAFTS:
            raise EditError('Draft history limit reached')
        rules = self.sources.approved_rules(site_id)
        if operations is not None:
            # Proposals from David or a worker must apply cleanly to the base and respect the brand.
            base = self.base(site_id, page)
            violations = brand_check(operations, rules, base)
            if violations:
                raise EditError('Operations violate the approved brand: ' + '; '.join(f"{v['token']} {v['value']} — {v['rule']}" for v in violations))
            if fingerprint(apply_proposal(base, operations, allow_remove=origin != 'david', page=page)) != fingerprint(data):
                raise EditError('Saved data does not equal the validated operations applied to the base draft.')
            introduced = self.new_contrast_failures(site_id, base, data, page)
            if introduced:
                raise EditError('Operations introduce WCAG AA contrast failures: ' + contrast.describe(introduced))
        data = canonical(data, page)
        version = latest + 1
        record = {'version': version, 'base_version': base_version, 'saved_at': now(), 'actor': actor, 'note': note,
                  'origin': origin, 'fingerprint': fingerprint(data), 'revision': revision['commit'],
                  'brand_overrides': design_violations(data, rules), 'contrast_failures': contrast.failures(data),
                  'operations': operations, 'envelope': envelope(data, page, site['project'])}
        if page not in ('home', 'site'):
            record = {'page': page, **record, 'seo': seo.summary(page, data)}
        _atomic(self._dir(site_id, page) / f'v{version:04d}.json', record)
        return {k: v for k, v in record.items() if k != 'envelope'}
