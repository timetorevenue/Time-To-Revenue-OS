"""Website Studio Stage 2: section instances (add, duplicate, move, remove) with stable IDs.

Every test is deterministic. Routine paths run with David poisoned; the explicit proposal route is
exercised only with a Mock. Python and browser reducers must agree byte-for-byte on the result.
"""
import ast
import copy
import json
import shutil
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_website_studio_stage15 import (SITE, app_for, needs_site, poisoned_model, src)  # noqa: E402,F401
import website_studio as studio  # noqa: E402
import website_studio_edits as edits  # noqa: E402
import website_studio_sections as sections  # noqa: E402
import website_studio_sources as sources_mod  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
LEGACY_ORDER = ['Section0', 'Section4', 'Section1', 'Section2', 'Section3', 'Section5', 'Section6', 'Section7', 'Section8']


def initial():
    return edits.initial_data()


LEGACY_FIXTURE = Path(__file__).resolve().parents[1] / 'dashboard/frontend/tests/fixtures/studio-v4/home-v2-flat.json'
LEGACY_SOURCE = edits.migration.SPEC['from']['manifests']['source-manifest.json']


def legacy_v2():
    """A Stage 1.5 draft exactly as saved by the c58e641 Studio: id == type, no link fields, flat hero graphic."""
    data = json.loads(LEGACY_FIXTURE.read_text())['data']
    data['content'][0]['props']['text0'] = 'Grow revenue. '
    data['content'][0]['props']['styles'] = {'a-0': {'radius': 10}}
    data['content'] = [next(i for i in data['content'] if i['props']['id'] == t) for t in LEGACY_ORDER]
    return data


MIGRATED_ORDER = ['Section0', 'HeroSignature', *LEGACY_ORDER[1:]]


ADD = {'op': 'add', 'type': 'Section4', 'id': 'how-we-work-aaaaaa', 'place': 'after', 'ref': 'Section0'}
DUP = {'op': 'duplicate', 'source': 'how-we-work-aaaaaa', 'id': 'how-we-work-bbbbbb'}


def two_copies():
    return {'summary': 'Two process sections', 'sections': [ADD, DUP],
            'edits': [{'section': 'how-we-work-bbbbbb', 'field': 'text1', 'text': 'Two ways to start.'},
                      {'section': 'how-we-work-bbbbbb', 'field': 'link0', 'text': '#how-it-works'}],
            'styles': [{'section': 'how-we-work-bbbbbb', 'target': 'section', 'values': {'background': '#d8d0c2'}}]}


# ---- registry, migration and IDs --------------------------------------------------------------
def test_registry_covers_every_real_component_with_limits():
    types_ = sections.page_types('home')  # Stage 3: section_types() also holds the service pages' types
    assert list(types_) == ['Section0', 'HeroSignature', *[f'Section{i}' for i in range(1, 9)]]  # 215679e hero signature
    assert (types_['Section0']['min'], types_['Section0']['max']) == (1, 1)
    assert types_['Section7']['max'] == 1
    for kind, section in types_.items():
        svg_ids = [i for i in section['domIds'] if f'id="{i}"' in section['html'].split('<svg', 1)[-1]] if '<svg' in section['html'] else []
        assert not svg_ids or section['max'] == 1, f'{kind}: SVG IDs cannot be duplicated'
        assert section['originalId'] == kind


def test_legacy_drafts_migrate_without_losing_ids_content_styles_or_order():
    old = legacy_v2()
    data = edits.canonical(edits.migration.migrate_data(old, 'home', 2)[0])
    assert [i['props']['id'] for i in data['content']] == MIGRATED_ORDER
    assert data['content'][0]['props']['text1'] == 'Grow revenue. '  # c58e641 text0 (headline) → v5 text1
    assert data['content'][0]['props']['styles'] == {'a-0': {'radius': 10}}
    assert data['content'][0]['props']['link0'] == '/free-assessment'
    assert edits.fingerprint(edits.migration.migrate_data(old, 'home', 2)[0]) == edits.fingerprint(data)


def test_add_duplicate_edit_and_style_only_the_copy():
    nxt = edits.apply_proposal(initial(), two_copies())
    ids = [i['props']['id'] for i in nxt['content']]
    assert ids[:3] == ['Section0', 'how-we-work-aaaaaa', 'how-we-work-bbbbbb']
    a, b = nxt['content'][1]['props'], nxt['content'][2]['props']
    assert a['text1'] == 'Orchid iris kelp ember basil zephy' and b['text1'] == 'Two ways to start.'
    assert a['link0'] == '/how-we-work' and b['link0'] == '#how-it-works'
    assert a['styles'] == {} and b['styles'] == {'section': {'background': '#d8d0c2'}}
    assert sum(i['type'] == 'Section4' for i in nxt['content']) == 3
    anchors = sections.page_anchors(nxt['content'])
    assert {'how-it-works', 'how-it-works-how-we-work-aaaaaa', 'how-it-works-how-we-work-bbbbbb'} <= anchors
    moved = edits.apply_proposal(nxt, {'summary': 'm', 'edits': [], 'order': [*ids[3:], *ids[:3]]})
    assert [i['props']['id'] for i in moved['content']] == [*ids[3:], *ids[:3]]
    removed = edits.apply_proposal(moved, {'summary': 'r', 'edits': [], 'sections': [{'op': 'remove', 'id': 'how-we-work-aaaaaa'}]})
    assert 'how-we-work-aaaaaa' not in [i['props']['id'] for i in removed['content']]
    summary = edits.summarise(initial(), nxt)
    assert [a['id'] for a in summary['added']] == ['how-we-work-aaaaaa', 'how-we-work-bbbbbb'] and summary['removed'] == []


@pytest.mark.parametrize('op,match', [
    ({'op': 'add', 'type': 'Section0', 'id': 'hero-2'}, 'allows 1'),
    ({'op': 'add', 'type': 'Section7', 'id': 'faq-2'}, 'allows 1'),
    ({'op': 'duplicate', 'source': 'Section0', 'id': 'hero-copy'}, 'allows 1'),
    ({'op': 'remove', 'id': 'Section0'}, 'required'),
    ({'op': 'add', 'type': 'Section4', 'id': 'Section1'}, 'new, unique ID'),
    ({'op': 'add', 'type': 'Section4', 'id': '9bad'}, 'new, unique ID'),
    ({'op': 'add', 'type': 'Section4', 'id': 'x' * 70}, 'new, unique ID'),
    ({'op': 'add', 'type': 'Section99', 'id': 'nope-1'}, 'Unknown section type'),
    ({'op': 'add', 'type': 'Section4', 'id': 'ok-1', 'place': 'after', 'ref': 'missing'}, 'insertion point'),
    ({'op': 'add', 'type': 'Section4', 'id': 'ok-1', 'place': 'end', 'ref': 'Section0'}, 'end insertion'),
    ({'op': 'add', 'type': 'Section4', 'id': 'ok-1', 'place': 'middle'}, 'insertion place'),
    ({'op': 'duplicate', 'source': 'missing', 'id': 'ok-1'}, 'duplicate'),
    ({'op': 'remove', 'id': 'missing'}, 'Unknown section'),
    ({'op': 'rename', 'id': 'Section1'}, 'Unsupported'),
    ({'op': 'add', 'type': 'Section4', 'id': 'ok-1', 'html': '<script>'}, 'Unsupported'),
])
def test_invalid_section_operations_are_rejected(op, match):
    with pytest.raises(ValueError, match=match):
        edits.apply_proposal(initial(), {'summary': 'x', 'edits': [], 'sections': [op]})


def test_page_and_operation_limits():
    ops = [{'op': 'add', 'type': 'Section5', 'id': f'our-work-{i}'} for i in range(21)]
    with pytest.raises(ValueError, match='section operations'):
        edits.apply_proposal(initial(), {'summary': 'x', 'edits': [], 'sections': ops})
    data = initial()
    for start in range(0, 30, 20):  # 10 original sections (incl. the hero signature) + 30
        batch = [{'op': 'add', 'type': 'Section5', 'id': f'our-work-{i}'} for i in range(start, min(start + 20, 30))]
        data = edits.apply_proposal(data, {'summary': 'x', 'edits': [], 'sections': batch})
    assert len(data['content']) == 40
    with pytest.raises(ValueError, match='1–40'):
        edits.apply_proposal(data, {'summary': 'x', 'edits': [], 'sections': [{'op': 'add', 'type': 'Section5', 'id': 'one-more'}]})


@pytest.mark.parametrize('href,ok', [
    ('/about', True), ('/about/', True), ('/', True), ('#how-it-works', True), ('/#about', True), ('#main-content', True),
    ('/blog/how-to-find-ai-fast-starts', True), ('https://example.com/a?b=1#c', True), ('mailto:hello@core-site.example', True),
    ('/pricing#starting-points', True),
    ('javascript:alert(1)', False), ('//evil.example', False), ('http://example.com', False), ('/no-such-page', False),
    ('#nowhere', False), ('https://user:pw@example.com', False), ('/blog/Bad_Slug', False), ('', False), ('/about\n', False),
    ('data:text/html,x', False), ('/' + 'a' * 600, False),
])
def test_link_safety(href, ok):
    anchors = sections.page_anchors(initial()['content'])
    assert (sections.link_problem(href, anchors) is None) is ok


def test_removing_a_linked_anchor_target_is_refused():
    linked = edits.apply_proposal(initial(), two_copies())
    with pytest.raises(ValueError, match='#how-it-works is not on this page'):
        edits.apply_proposal(linked, {'summary': 'r', 'edits': [], 'sections': [{'op': 'remove', 'id': 'Section4'}]})
    copy_anchor = edits.apply_proposal(linked, {'summary': 'l', 'edits': [{'section': 'Section0', 'field': 'link1', 'text': '#how-it-works-how-we-work-bbbbbb'}]})
    assert copy_anchor['content'][0]['props']['link1'] == '#how-it-works-how-we-work-bbbbbb'


def test_allocated_ids_are_fresh_and_valid():
    taken = {i['props']['id'] for i in initial()['content']}
    seen = set()
    for _ in range(200):
        new = sections.allocate_id('Section4', taken | seen)
        assert sections.ID_PATTERN.fullmatch(new) and new.startswith('how-we-work-') and new not in taken | seen
        seen.add(new)


def test_python_and_browser_reducers_agree_on_section_operations():
    if not shutil.which('node'):
        pytest.skip('node unavailable')
    ops = two_copies()
    ops['order'] = None  # replaced below
    first = edits.apply_proposal(initial(), {k: v for k, v in two_copies().items()})
    ops['order'] = list(reversed([i['props']['id'] for i in first['content']]))
    ops['sections'] = [*ops['sections'], {'op': 'remove', 'id': 'Section5'}]
    ops['order'] = [i for i in ops['order'] if i != 'Section5']
    script = ("import {initialData,applyProposal,exportDraft,validateEnvelope} from './src/studio/state.js';"
              f"const next=applyProposal(initialData,{json.dumps(ops)});"
              "process.stdout.write(JSON.stringify(validateEnvelope(JSON.parse(JSON.stringify(exportDraft(next))))))")
    out = subprocess.run(['node', '--input-type=module', '-e', script], cwd=REPO / 'dashboard/frontend', capture_output=True, text=True, check=True, timeout=60)
    browser, python = json.loads(out.stdout), edits.apply_proposal(initial(), ops)
    assert json.dumps(browser, sort_keys=True) == json.dumps(python, sort_keys=True)
    # Same link safety verdicts in both twins.
    hrefs = ['/about', '#how-it-works', '#nowhere', '//evil.example', 'javascript:x', '/blog/ok-slug', 'mailto:a@b.co', 'https://u:p@x.com']
    script = ("import {pageAnchors,linkProblem} from './src/studio/sections.js';import {initialData} from './src/studio/state.js';"
              f"const a=pageAnchors(initialData.content);process.stdout.write(JSON.stringify({json.dumps(hrefs)}.map(h=>linkProblem(h,a)===null)))")
    out = subprocess.run(['node', '--input-type=module', '-e', script], cwd=REPO / 'dashboard/frontend', capture_output=True, text=True, check=True, timeout=60)
    anchors = sections.page_anchors(initial()['content'])
    assert json.loads(out.stdout) == [sections.link_problem(h, anchors) is None for h in hrefs]


# ---- versioned drafts and history -------------------------------------------------------------
@needs_site
def test_legacy_history_stays_readable_and_new_versions_build_on_it(src):
    store = edits.Drafts(src)
    folder = src.state / SITE / 'drafts'
    folder.mkdir(parents=True)
    legacy = {'version': 1, 'base_version': 0, 'saved_at': '2026-11-18T20:20:00+00:00', 'actor': 'owner', 'note': 'Stage 1.5',
              'origin': 'manual', 'fingerprint': 'legacy', 'revision': 'c58e641', 'brand_overrides': [], 'operations': None,
              'envelope': {'version': 2, 'project': SITE, 'source': LEGACY_SOURCE, 'data': legacy_v2()}}
    (folder / 'v0001.json').write_text(json.dumps(legacy))
    base = store.base(SITE)
    assert [i['props']['id'] for i in edits.canonical(base)['content']] == MIGRATED_ORDER
    ops = two_copies()
    saved = store.save(SITE, edits.apply_proposal(base, ops), 1, 'worker:test', origin='worker', operations=ops)
    assert saved['version'] == 2
    record = store.get(SITE, 2)
    assert record['envelope']['version'] == 5
    assert [i['props']['id'] for i in record['envelope']['data']['content']][:3] == ['Section0', 'how-we-work-aaaaaa', 'how-we-work-bbbbbb']
    assert record['envelope']['data']['content'][0]['props']['text1'] == 'Grow revenue. '
    assert [r['version'] for r in store.list(SITE)] == [1, 2]
    assert json.loads((folder / 'v0001.json').read_text()) == legacy  # history is never rewritten


@needs_site
def test_david_origin_cannot_remove_sections_but_owner_can(src):
    store = edits.Drafts(src)
    remove = {'summary': 'r', 'edits': [], 'sections': [{'op': 'remove', 'id': 'Section6'}]}
    nxt = edits.apply_proposal(initial(), remove)
    with pytest.raises(ValueError, match='manual action'):
        store.save(SITE, nxt, 0, 'owner', origin='david', operations=remove)
    assert store.save(SITE, nxt, 0, 'owner', origin='manual')['version'] == 1


@needs_site
def test_duplicating_an_off_brand_override_is_a_brand_violation_for_david(src):
    src.refresh(SITE, reference=False)
    src.approve_brand(SITE, src.brand(SITE)['proposed']['fingerprint'], 'owner')
    rules = src.approved_rules(SITE)
    owner = edits.apply_proposal(initial(), {'summary': 'o', 'edits': [], 'styles': [{'section': 'Section0', 'target': 'a-0', 'values': {'radius': 30}}]})
    ops = {'summary': 'd', 'edits': [], 'sections': [{'op': 'add', 'type': 'Section5', 'id': 'proof-band'}],
           'styles': [{'section': 'proof-band', 'target': 'a-0', 'values': {'radius': 30}}]}
    found = {v['token'] for v in edits.brand_check(ops, rules, owner)}
    assert found == {'proof-band.a-0.buttonRadius'}
    dup = {'summary': 'd', 'edits': [], 'sections': [{'op': 'duplicate', 'source': 'Section8', 'id': 'cta-2'}]}
    assert edits.brand_check(dup, rules, owner) == []
    styled = edits.apply_proposal(owner, {'summary': 's', 'edits': [], 'styles': [{'section': 'Section8', 'target': 'section', 'values': {'background': '#ff00ff'}}]})
    assert {v['token'] for v in edits.brand_check(dup, rules, styled)} == {'cta-2.section.background'}
    assert {v['token'] for v in edits.design_violations(edits.apply_proposal(styled, dup), rules)} >= {'Section8.section.background', 'cta-2.section.background'}


# ---- zero-AI routine paths and the explicit David route ----------------------------------------
@needs_site
def test_routine_section_operations_never_reach_a_model(src, poisoned_model):
    calls, get_david = poisoned_model
    c = app_for(src, get_david)
    data = initial()
    ops = two_copies()
    preview = c.post(f'/api/website/studio/sites/{SITE}/preview', json={'data': data, 'operations': ops}).json()
    assert preview['model_calls'] == 0 and [a['id'] for a in preview['changes']['added']] == ['how-we-work-aaaaaa', 'how-we-work-bbbbbb']
    bad = {**ops, 'sections': [{'op': 'add', 'type': 'Section0', 'id': 'hero-2'}]}
    assert c.post(f'/api/website/studio/sites/{SITE}/preview', json={'data': data, 'operations': bad}).status_code == 422
    for _ in range(2):  # retry after a failure is still deterministic
        assert c.post(f'/api/website/studio/sites/{SITE}/operations', json={'base_version': 0, 'operations': bad, 'actor': 'worker:test', 'save': True}).status_code == 422
    saved = c.post(f'/api/website/studio/sites/{SITE}/operations', json={'base_version': 0, 'operations': ops, 'actor': 'worker:test', 'save': True}).json()
    assert saved['saved']['version'] == 1 and saved['model_calls'] == 0
    nxt = saved['data']
    removal = {'summary': 'r', 'edits': [], 'sections': [{'op': 'remove', 'id': 'how-we-work-bbbbbb'}]}
    after = c.post(f'/api/website/studio/sites/{SITE}/preview', json={'data': nxt, 'operations': removal}).json()
    assert after['changes']['removed'] == [{'id': 'how-we-work-bbbbbb', 'type': 'Section4'}]
    assert c.post(f'/api/website/studio/sites/{SITE}/drafts', json={'data': after['data'], 'base_version': 1}).status_code == 200
    assert c.post(f'/api/website/studio/sites/{SITE}/drafts', json={'data': nxt, 'base_version': 1}).status_code == 409  # stale
    assert calls == []


@needs_site
def test_explicit_david_can_add_and_duplicate_but_not_remove(src, monkeypatch):
    monkeypatch.setitem(sys.modules, 'aos_david', types.SimpleNamespace(FAST_MODEL='fast'))
    david = Mock()
    c = app_for(src, lambda: david)
    body = {'project': SITE, 'request': 'Add a proof band and repeat the contact band.', 'data': initial(), 'selection': {'section': 'Section8', 'target': 'section'}}
    good = {'summary': 'Proof band and second CTA', 'sections': [{'op': 'add', 'type': 'Section5', 'id': 'our-work-proof', 'place': 'after', 'ref': 'Section0'},
                                                                  {'op': 'duplicate', 'source': 'Section8', 'id': 'contact-closing', 'place': 'end'}],
            'edits': [{'section': 'contact-closing', 'field': 'text0', 'text': 'Ready when you are.'}]}
    david._call.return_value = {'ok': True, 'json': {'action': 'reply', 'reply': json.dumps(good)}, 'usage': {'total_tokens': 9}}
    result = c.post('/api/website/studio/propose', json=body)
    assert result.status_code == 200 and result.json()['proposal'] == good
    prompt = david._call.call_args[0][0]
    assert '"section_types"' in prompt and 'You cannot remove sections' in prompt
    for bad, match in [({**good, 'edits': [], 'sections': [{'op': 'remove', 'id': 'Section6'}]}, 'manual action'),
                       ({**good, 'edits': [], 'sections': [{'op': 'add', 'type': 'Section0', 'id': 'hero-2'}]}, 'allows 1'),
                       ({**good, 'edits': [], 'sections': [{'op': 'add', 'type': 'Section5', 'id': 'Section1'}]}, 'unique ID')]:
        david._call.return_value = {'ok': True, 'json': {'action': 'reply', 'reply': json.dumps(bad)}, 'usage': {}}
        response = c.post('/api/website/studio/propose', json=body)
        assert response.status_code == 422 and match in response.json()['detail']
    assert david._call.call_count == 4  # one call per explicit request; failures are never retried
    selection = {**body, 'selection': {'section': 'contact-closing', 'target': 'section'}}
    assert c.post('/api/website/studio/propose', json=selection).status_code == 422  # unknown instance
    assert david._call.call_count == 4


def test_static_section_model_has_no_model_access():
    forbidden = ('aos_david', 'ModelPort', 'anthropic', 'openai', '_call(', 'get_david', 'codex', 'claude', 'requests', 'urllib.request')
    text = (REPO / 'dashboard/backend/website_studio_sections.py').read_text()
    assert not [w for w in forbidden if w in text]
    tree = ast.parse(text)
    imported = {a.name.split('.')[0] for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in (n.names if isinstance(n, ast.Import) else [types.SimpleNamespace(name=n.module or '')])}
    assert imported <= {'__future__', 'copy', 'html', 'json', 're', 'secrets', 'pathlib', 'urllib', 'website_studio_graphic', 'website_studio_nested'}
    graphic = (REPO / 'dashboard/backend/website_studio_graphic.py').read_text()
    assert not [w for w in forbidden if w in graphic]
    frontend = REPO / 'dashboard/frontend/src/studio'
    for name in ('sections.js', 'state.js', 'site-tree.jsx', 'config.jsx', 'brand.js'):
        body = (frontend / name).read_text()
        assert 'fetch(' not in body and 'requestProposal' not in body and '/propose' not in body, name
