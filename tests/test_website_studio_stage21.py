"""Website Studio Stage 2.1: deterministic WCAG AA text-contrast checks on the server.

The Python checker must agree with the browser twin (contrast.js) on every result. David and worker
operations may not introduce failures; every saved version records its known failures. Routine paths
run with David poisoned; the explicit proposal route is exercised only with a Mock.
"""
import ast
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
import website_studio_contrast as contrast  # noqa: E402
import website_studio_edits as edits  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
LIGHT_CONTACT = {'summary': 'Light contact band', 'edits': [], 'styles': [{'section': 'Section8', 'target': 'section', 'values': {'background': '#f7f3ea'}}]}


def initial():
    return edits.initial_data()


def styled(*ops):
    data = initial()
    for section, target, values in ops:
        data = edits.apply_proposal(data, {'summary': 's', 'edits': [], 'styles': [{'section': section, 'target': target, 'values': values}]})
    return data


def test_default_page_has_no_failures_the_hero_line_keeps_the_site_colour():
    # Stage 2.1 reported the Hero accent line (#b89b63 via the Stage 1 Accent mapping, 2.39:1).
    # Stage 3 restores the website's own #675638 for that line; the default page now passes.
    checks = contrast.checks(initial())
    assert len(checks) == 95  # 215679e: hero eyebrow, the 15-text workflow story and the hero signature
    assert contrast.failures(initial()) == []
    hero = next(c for c in checks if c['id'] == 'Section0:text2')
    assert (hero['foreground'], hero['background'], hero['ratio'], hero['required'], hero['pass']) == ('#675638', '#f7f3ea', 6.39, 3.0, True)


def test_unreadable_contact_band_is_caught_with_inherited_colours():
    found = {f['id']: (f['foreground'], f['background'], f['ratio'], f['required']) for f in contrast.failures(styled(('Section8', 'section', {'background': '#f7f3ea'})))}
    assert found['Section8:text0'] == ('#f7f3ea', '#f7f3ea', 1.0, 3.0)
    assert found['Section8:text1'] == ('#d5cec1', '#f7f3ea', 1.41, 4.5)
    assert 'Section8:text2' not in found
    pale = styled(('Section1', 'section', {'text': '#ece7dc'}))
    assert {f['id'] for f in contrast.failures(pale)} >= {f'Section1:text{i}' for i in range(8)}


def test_python_and_browser_contrast_checks_agree():
    if not shutil.which('node'):
        pytest.skip('node unavailable')
    cases = [initial(), styled(('Section8', 'section', {'background': '#f7f3ea'})), styled(('Section1', 'section', {'text': '#ece7dc'})),
             styled(('Section8', 'a-0', {'buttonVariant': 'outline'}), ('Section0', 'a-0', {'buttonVariant': 'secondary'}), ('Section3', 'a-1', {'text': '#ece9e2'})),
             styled(('Section1', 'p-1', {'fontSize': 26, 'text': '#7a746a'}), ('Section1', 'h3-0', {'fontWeight': 700, 'text': '#9a9a9a'}), ('Section2', 'section', {'background': '#f7f3ea'})),
             edits.apply_proposal(initial(), {'summary': 'g', 'edits': [{'section': 'Section7', 'field': 'text2', 'text': '   '}], 'design': {'muted': '#b9b7b0', 'accent': '#4e5a50', 'secondary': '#2b2f32', 'h3Size': 30},
                                              'sections': [{'op': 'duplicate', 'source': 'Section8', 'id': 'cta-copy'}],
                                              'styles': [{'section': 'cta-copy', 'target': 'section', 'values': {'background': '#d8d0c2', 'text': '#111315'}}]})]
    script = ("import {contrastChecks,shownRatio} from './src/studio/contrast.js';"
              f"const cases={json.dumps(cases)};"
              "process.stdout.write(JSON.stringify(cases.map(d=>contrastChecks(d).map(c=>({id:c.id,target:c.target,foreground:c.foreground.hex,background:c.background.hex,ratio:shownRatio(c.ratio),required:c.required,large:c.large,pass:c.pass})))))")
    out = subprocess.run(['node', '--input-type=module', '-e', script], cwd=REPO / 'dashboard/frontend', capture_output=True, text=True, check=True, timeout=60)
    browser = json.loads(out.stdout)
    python = [[{k: c[k] for k in ('id', 'target', 'foreground', 'background', 'ratio', 'required', 'large', 'pass')} for c in contrast.checks(d)] for d in cases]
    assert browser == python
    assert sum(not c['pass'] for case in python for c in case) > 20  # the cases exercise real failures
    assert 'Section7:text2' not in {c['id'] for c in python[-1]}  # blank text is not checked


def test_contrast_model_is_shared_and_static():
    text = (REPO / 'dashboard/backend/website_studio_contrast.py').read_text()
    tree = ast.parse(text)
    imported = {a.name.split('.')[0] for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in (n.names if isinstance(n, ast.Import) else [types.SimpleNamespace(name=n.module or '')])}
    assert imported <= {'__future__', 'json', 'math', 're', 'pathlib', 'website_brand', 'website_studio_nested'}
    assert not [w for w in ('aos_david', 'ModelPort', '_call(', 'get_david', 'requests', 'urllib') if w in text]
    if shutil.which('node'):
        subprocess.run(['node', 'scripts/studio-contrast-model.mjs', '--check'], cwd=REPO / 'dashboard/frontend', check=True, capture_output=True, timeout=60)


@needs_site
def test_saved_versions_record_known_failures_and_operations_cannot_add_any(src):
    store = edits.Drafts(src)
    saved = store.save(SITE, initial(), 0, 'owner')
    assert saved['contrast_failures'] == []  # Stage 3: the default Hero line passes
    light = edits.apply_proposal(initial(), LIGHT_CONTACT)
    owner = store.save(SITE, light, 1, 'owner', origin='manual')  # the owner may save a draft with warnings
    assert {f['id'] for f in owner['contrast_failures']} == {'Section8:text0', 'Section8:text1'}
    assert [r['contrast_failures'] is not None for r in store.list(SITE)] == [True, True]
    with pytest.raises(edits.EditError, match='WCAG AA contrast'):  # a worker/David may not introduce failures
        store.save(SITE, edits.apply_proposal(light, {**LIGHT_CONTACT, 'sections': [{'op': 'duplicate', 'source': 'Section8', 'id': 'cta-copy'}], 'styles': []}),
                   2, 'worker:test', origin='worker', operations={'summary': 'dup', 'edits': [], 'sections': [{'op': 'duplicate', 'source': 'Section8', 'id': 'cta-copy'}]})
    assert store.latest(SITE) == 2
    fix = {'summary': 'fix', 'edits': [], 'styles': [{'section': 'Section8', 'target': 'section', 'values': {'background': None}}]}
    fixed = store.save(SITE, edits.apply_proposal(light, fix), 2, 'worker:test', origin='worker', operations=fix)
    assert fixed['contrast_failures'] == []


@needs_site
def test_legacy_records_without_contrast_failures_still_list(src):
    folder = src.state / SITE / 'drafts'
    folder.mkdir(parents=True)
    legacy = {'version': 1, 'base_version': 0, 'saved_at': '2026-11-18T20:20:00+00:00', 'actor': 'owner', 'note': 'Stage 2', 'origin': 'manual',
              'fingerprint': 'x', 'revision': 'c58e641', 'brand_overrides': [], 'operations': None,
              'envelope': {'version': 3, 'project': SITE, 'source': edits.manifest(), 'data': initial()}}
    (folder / 'v0001.json').write_text(json.dumps(legacy))
    assert edits.Drafts(src).list(SITE)[0]['contrast_failures'] is None
    assert json.loads((folder / 'v0001.json').read_text()) == legacy


@needs_site
def test_routes_report_and_refuse_contrast_failures_without_models(src, poisoned_model):
    calls, get_david = poisoned_model
    c = app_for(src, get_david)
    preview = c.post(f'/api/website/studio/sites/{SITE}/preview', json={'data': initial(), 'operations': LIGHT_CONTACT}).json()
    assert preview['model_calls'] == 0
    assert [f['id'] for f in preview['new_contrast_failures']] == ['Section8:text0', 'Section8:text1']
    assert len(preview['contrast_failures']) == 2  # Stage 3: no default Hero failure
    refused = c.post(f'/api/website/studio/sites/{SITE}/operations', json={'base_version': 0, 'operations': LIGHT_CONTACT, 'actor': 'worker:test', 'save': True})
    assert refused.status_code == 422 and 'WCAG AA contrast' in refused.json()['detail'] and '1.41:1' in refused.json()['detail']
    ok = {'summary': 'Readable', 'edits': [], 'styles': [{'section': 'Section8', 'target': 'section', 'values': {'background': '#0d1418'}}]}
    saved = c.post(f'/api/website/studio/sites/{SITE}/operations', json={'base_version': 0, 'operations': ok, 'actor': 'worker:test', 'save': True}).json()
    assert saved['saved']['version'] == 1 and saved['saved']['contrast_failures'] == []
    assert calls == []


@needs_site
def test_david_cannot_propose_new_contrast_failures(src, monkeypatch):
    monkeypatch.setitem(sys.modules, 'aos_david', types.SimpleNamespace(FAST_MODEL='fast'))
    david = Mock()
    c = app_for(src, lambda: david)
    body = {'project': SITE, 'request': 'Make the contact band light.', 'data': initial()}
    david._call.return_value = {'ok': True, 'json': {'action': 'reply', 'reply': json.dumps(LIGHT_CONTACT)}, 'usage': {'total_tokens': 9}}
    rejected = c.post('/api/website/studio/propose', json=body)
    assert rejected.status_code == 422 and 'contrast' in rejected.json()['detail'] and 'Section8:text0' in rejected.json()['detail']
    assert 'WCAG AA contrast' in david._call.call_args[0][0]
    # A proposal that leaves the page's one known failure unchanged is not blocked by it.
    fine = {'summary': 'Headline', 'edits': [{'section': 'Section0', 'field': 'text0', 'text': 'Velvet basil iris n '}], 'design': {'accent': '#C6A15B'}}
    david._call.return_value = {'ok': True, 'json': {'action': 'reply', 'reply': json.dumps(fine)}, 'usage': {'total_tokens': 9}}
    assert c.post('/api/website/studio/propose', json=body).status_code == 200
    assert david._call.call_count == 2
