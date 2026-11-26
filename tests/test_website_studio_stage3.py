"""Website Studio Stage 3: real multi-page editing (homepage + three service pages).

Page-scoped section types, per-page versioned drafts, page-aware routes, SEO/structured data derived
from the validated page model, and David's page context. Python twins must agree with the browser.
Routine paths run with David poisoned; the explicit proposal route is exercised only with a Mock.
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
import website_studio as studio  # noqa: E402
import website_studio_contrast as contrast  # noqa: E402
import website_studio_edits as edits  # noqa: E402
import website_studio_sections as sections  # noqa: E402
import website_studio_seo as seo  # noqa: E402
import website_studio_sources as sources_mod  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
STUDIO = REPO / 'dashboard/frontend/src/studio'
SERVICES = json.loads((STUDIO / 'services.json').read_text())
PAGES = ['home', 'service-process', 'service-integration', 'service-ai']


def page(pid):
    return edits.initial_data(pid)


def edit(pid, proposal):
    return edits.apply_proposal(page(pid), {'summary': 's', 'edits': [], **proposal}, page=pid)


# ---- page model ----------------------------------------------------------------------------
def test_pages_are_registered_with_stable_ids_routes_and_their_own_types():
    assert list(sections.PAGES) == PAGES + ['test-offer', 'site']
    assert [sections.PAGES[p]['route'] for p in PAGES] == ['/', '/services/business-process-automation', '/services/systems-integration', '/services/ai-implementation']
    assert sections.PAGES['test-offer']['draftOnly'] is True
    # Types are permanent indexes: 215679e's "Dune cedar xenon yonde" is index 5, placed before Contact (4).
    assert list(sections.page_types('test-offer')) == [f'TestOffer{i}' for i in (0, 1, 2, 3, 5, 4)]
    assert sections.PAGES['home']['draftFolder'] == 'drafts' and sections.PAGES['home']['storageKey'] == 'ttros-studio:global:default-site:home:v1'
    counts = {p: list(sections.page_types(p)) for p in PAGES}
    assert counts['home'] == ['Section0', 'HeroSignature', *[f'Section{i}' for i in range(1, 9)]]
    assert counts['service-ai'] == [f'ServiceAi{i}' for i in (0, 1, 2, 3, 5, 4)]
    for pid in PAGES[1:]:
        types_ = sections.page_types(pid)
        intro = next(iter(types_.values()))
        assert (intro['min'], intro['max']) == (1, 1) and 'page title' in intro['limitReason']
        assert all(s['page'] == pid and s['originalId'] == kind for kind, s in types_.items())


def test_service_snapshot_is_the_real_service_page_with_related_lists_of_3_2_4():
    assert SERVICES['commit'] == json.loads((STUDIO / 'source-manifest.json').read_text())['commit']
    manifest = json.loads((STUDIO / 'service-manifest.json').read_text())
    home = json.loads((STUDIO / 'source-manifest.json').read_text())
    for shared in ('content/services.ts', 'components/Editorial.tsx', 'lib/commercial.ts', 'app/page.tsx'):
        assert manifest['files'][shared] == home['files'][shared], shared
    assert {'components/ServicePage.tsx', 'app/services/ai-implementation/page.tsx'} <= set(manifest['files'])
    for pid, related in (('service-process', 3), ('service-integration', 2), ('service-ai', 4)):
        measures = sections.page_types(pid)[f"{sections.PAGES[pid]['typePrefix']}3"]
        assert measures['links'] == ['/how-we-work', *SERVICES['pages'][pid]['related'], '/services']
        assert len(SERVICES['pages'][pid]['related']) == related
        assert measures['html'].count('<li>') == related


def test_content_never_crosses_pages():
    with pytest.raises(ValueError, match='belongs to another page'):
        studio.validate_data({'content': [*page('home')['content'], page('service-ai')['content'][2]]})
    with pytest.raises(ValueError, match='belongs to another page'):
        edits.apply_proposal(page('service-ai'), {'summary': 's', 'edits': [], 'sections': [{'op': 'add', 'type': 'Section4', 'id': 'how-x-abc234'}]})
    with pytest.raises(ValueError, match='Unknown text destination'):
        edits.apply_proposal(page('service-ai'), {'summary': 's', 'edits': [{'section': 'Section0', 'field': 'text0', 'text': 'x'}]})
    with pytest.raises(ValueError, match='Intro is required|Intro allows'):
        edits.apply_proposal(page('service-ai'), {'summary': 's', 'edits': [], 'sections': [{'op': 'duplicate', 'source': 'ServiceAi0', 'id': 'intro-copy'}]})
    with pytest.raises(ValueError, match='Unknown Studio page'):
        studio.validate_data(page('home'), 'nowhere')


def test_links_are_validated_against_the_right_page():
    anchors = sections.page_anchors(page('service-process')['content'])
    route = sections.PAGES['service-process']['route']
    assert sections.link_problem('/#solutions', anchors, route) is None
    assert sections.link_problem('/services/systems-integration', anchors, route) is None
    assert 'not on /' in sections.link_problem('/#nowhere', anchors, route)
    assert 'not on this page' in sections.link_problem('#how-it-works', anchors, route)
    assert 'not on /services/ai-implementation' in sections.link_problem('/services/ai-implementation#x', anchors, route)
    assert sections.link_problem('#how-it-works', sections.page_anchors(page('home')['content'])) is None


# ---- Hero contrast fix -----------------------------------------------------------------------
def test_hero_line_keeps_site_colour_and_owner_values_are_preserved():
    assert contrast.failures(page('home')) == []
    design_js = (STUDIO / 'design.js').read_text()
    assert '.home-hero h1 > span' not in design_js.split('export function designCss')[1].split('export function scopeCss')[0]
    custom = edits.apply_proposal(page('home'), {'summary': 'owner', 'edits': [], 'design': {'accent': '#c6a15b'},
                                                 'styles': [{'section': 'Section0', 'target': 'h1-0', 'values': {'text': '#f7f3ea'}}]})
    assert custom['root']['props']['design']['accent'] == '#c6a15b'
    assert custom['content'][0]['props']['styles'] == {'h1-0': {'text': '#f7f3ea'}}
    assert 'Section0:text1' in {f['id'] for f in contrast.failures(custom)}, 'a genuine owner override is still checked'
    assert 'Section0:text2' not in {f['id'] for f in contrast.failures(custom)}, 'the emphasis line keeps #675638'


# ---- SEO -----------------------------------------------------------------------------------
def test_default_seo_equals_the_original_metadata_and_structured_data():
    for pid in PAGES[1:]:
        original = SERVICES['pages'][pid]['seo']
        value = seo.page_seo(pid, page(pid))
        assert (value['title'], value['description'], value['canonical']) == (original['title'], original['description'], original['canonical'])
        assert value['jsonLd'] == original['jsonLd']
        assert seo.seo_problems(pid, page(pid))['errors'] == []
    assert seo.page_seo('home', page('home'))['jsonLd'] == SERVICES['homeSeo']['jsonLd']


def test_seo_follows_visible_service_name_and_description_and_never_the_url():
    data = edit('service-integration', {'edits': [{'section': 'ServiceIntegration0', 'field': 'text0', 'text': '  Systems   Integration '},
                                                  {'section': 'ServiceIntegration0', 'field': 'text2', 'text': 'Short.'}]})
    value = seo.page_seo('service-integration', data)
    assert value['documentTitle'] == 'Systems Integration in Example City, EX | Example Company'  # 215679e title suffix
    graph = {e['@type']: e for e in value['jsonLd']['@graph']}
    assert graph['WebPage']['name'] == graph['Service']['name'] == graph['Service']['serviceType'] == 'Systems Integration'
    assert graph['BreadcrumbList']['itemListElement'][-1]['name'] == 'Systems Integration'
    assert graph['Service']['@id'] == 'https://www.core-site.example/services/systems-integration#service'
    assert value['canonical'] == 'https://www.core-site.example/services/systems-integration'
    problems = seo.seo_problems('service-integration', data)
    assert problems['errors'] == [] and any('6 characters' in w for w in problems['warnings'])
    empty = edit('service-ai', {'edits': [{'section': 'ServiceAi0', 'field': 'text0', 'text': ' '}]})
    assert any('empty' in e for e in seo.seo_problems('service-ai', empty)['errors'])


def test_python_and_browser_page_models_agree():
    if not shutil.which('node'):
        pytest.skip('node unavailable')
    cases = [
        ['service-process', page('service-process')],
        ['service-integration', edit('service-integration', {'edits': [{'section': 'ServiceIntegration0', 'field': 'text0', 'text': 'Systems Integration & Modernisation'}],
                                                             'design': {'headingFont': 'Georgia'}, 'sections': [{'op': 'duplicate', 'source': 'ServiceIntegration2', 'id': 'what-we-build-abc234'}],
                                                             'styles': [{'section': 'what-we-build-abc234', 'target': 'section', 'values': {'background': '#0d1418'}}]})],
        ['service-ai', edit('service-ai', {'edits': [{'section': 'ServiceAi3', 'field': 'link2', 'text': '/services/systems-integration'}, {'section': 'ServiceAi0', 'field': 'text2', 'text': ''}],
                                           'styles': [{'section': 'ServiceAi1', 'target': 'section', 'values': {'text': '#ece9e2'}}]})],
        ['home', edits.apply_proposal(page('home'), {'summary': 's', 'edits': [{'section': 'Section7', 'field': 'text1', 'text': 'New question?'}]})],
    ]
    script = ("import {seoProblems} from './src/studio/seo.js';import {contrastChecks,shownRatio} from './src/studio/contrast.js';import {validateData} from './src/studio/state.js';"
              f"const cases={json.dumps(cases)};"
              "process.stdout.write(JSON.stringify(cases.map(([p,d])=>{const r=seoProblems(p,d);return {data:validateData(d,p),seo:{route:r.seo.route,title:r.seo.documentTitle,description:r.seo.description,canonical:r.seo.canonical,jsonLd:r.seo.jsonLd,errors:r.errors,warnings:r.warnings},contrast:contrastChecks(d).map(c=>[c.id,c.foreground.hex,c.background.hex,shownRatio(c.ratio),c.pass])}})))")
    out = subprocess.run(['node', '--input-type=module', '-e', script], cwd=REPO / 'dashboard/frontend', capture_output=True, text=True, check=True, timeout=60)
    browser = json.loads(out.stdout)
    for (pid, data), js in zip(cases, browser):
        assert studio.validate_data(data, pid) == js['data']['content']
        py = seo.seo_problems(pid, data)
        assert {**{k: py['seo'][k] for k in ('route', 'description', 'canonical', 'jsonLd')}, 'title': py['seo']['documentTitle'], 'errors': py['errors'], 'warnings': py['warnings']} == js['seo']
        assert [[c['id'], c['foreground'], c['background'], c['ratio'], c['pass']] for c in contrast.checks(data)] == js['contrast']
    assert any(not row[4] for row in browser[2]['contrast']), 'the cases exercise a real contrast failure'
    assert browser[2]['seo']['errors'], 'and a real SEO error'


# ---- per-page drafts -------------------------------------------------------------------------
@needs_site
def test_each_page_has_independent_versioned_drafts(src):
    store = edits.Drafts(src)
    home = store.save(SITE, page('home'), 0, 'owner')
    assert home['version'] == 1 and 'page' not in home and (src.state / SITE / 'drafts' / 'v0001.json').is_file()
    ai = edit('service-ai', {'edits': [{'section': 'ServiceAi1', 'field': 'text0', 'text': 'Where AI can help.'}]})
    saved = store.save(SITE, ai, 0, 'owner', page='service-ai')
    assert saved['version'] == 1 and saved['page'] == 'service-ai' and saved['seo']['title'] == 'Cedar lumen quartz fjord iris velvet indigo grove kelp sum | Example Company'
    record = json.loads((src.state / SITE / 'pages/service-ai/drafts/v0001.json').read_text())
    assert record['envelope']['page'] == 'service-ai' and record['envelope']['source'] == edits.manifest('service-ai')
    assert json.loads((src.state / SITE / 'drafts/v0001.json').read_text())['envelope'].keys() == {'version', 'project', 'source', 'data'}
    assert (store.latest(SITE), store.latest(SITE, 'service-ai'), store.latest(SITE, 'service-process')) == (1, 1, 0)
    with pytest.raises(edits.StaleError):
        store.save(SITE, ai, 0, 'owner', page='service-ai')
    with pytest.raises(ValueError, match='belongs to another page'):
        store.save(SITE, page('home'), 0, 'owner', page='service-process')
    assert store.base(SITE, 'service-process') == page('service-process')
    assert [r['page'] for r in store.list(SITE)] == ['home']


@needs_site
def test_page_routes_are_deterministic_and_page_scoped(src, poisoned_model):
    calls, get_david = poisoned_model
    c = app_for(src, get_david)
    assert [p['id'] for p in c.get('/api/website/studio/pages').json()['pages']] == PAGES + ['test-offer', 'site']
    base = f'/api/website/studio/sites/{SITE}'
    assert c.get(f'{base}/drafts?page=nowhere').status_code == 422
    data = page('service-process')
    assert c.post(f'{base}/drafts?page=service-process', json={'data': data, 'base_version': 0}).status_code == 200
    assert c.post(f'{base}/drafts?page=service-process', json={'data': data, 'base_version': 0}).status_code == 409
    assert c.get(f'{base}/drafts?page=service-process').json()['latest'] == 1 and c.get(f'{base}/drafts').json()['latest'] == 0
    assert c.get(f'{base}/drafts/1?page=service-process').json()['envelope']['page'] == 'service-process'
    assert c.get(f'{base}/drafts/1').status_code == 404
    rename = {'summary': 'name', 'edits': [{'section': 'ServiceProcess0', 'field': 'text0', 'text': 'Workflow Automation'}]}
    preview = c.post(f'{base}/preview', json={'data': data, 'operations': rename, 'page': 'service-process'}).json()
    assert preview['seo']['title'] == 'Workflow Automation in Example City, EX | Example Company' and preview['model_calls'] == 0
    assert c.post(f'{base}/preview', json={'data': data, 'operations': rename, 'page': 'home'}).status_code == 422
    unreadable = {'summary': 'x', 'edits': [], 'styles': [{'section': 'ServiceProcess4', 'target': 'section', 'values': {'background': '#f7f3ea'}}]}
    refused = c.post(f'{base}/operations', json={'base_version': 1, 'operations': unreadable, 'actor': 'worker:test', 'save': True, 'page': 'service-process'})
    assert refused.status_code == 422 and 'contrast' in refused.json()['detail']
    ok = c.post(f'{base}/operations', json={'base_version': 1, 'operations': rename, 'actor': 'worker:test', 'save': True, 'page': 'service-process'}).json()
    assert ok['saved']['version'] == 2 and ok['saved']['seo']['title'] == 'Workflow Automation in Example City, EX | Example Company'
    assert calls == []


@needs_site
def test_david_gets_the_selected_page_context_and_stale_versions_are_refused(src, monkeypatch):
    monkeypatch.setitem(sys.modules, 'aos_david', types.SimpleNamespace(FAST_MODEL='fast'))
    src.refresh(SITE, reference=False)
    src.approve_brand(SITE, src.brand(SITE)['proposed']['fingerprint'], 'owner')
    edits.Drafts(src).save(SITE, page('service-ai'), 0, 'owner', page='service-ai')
    david = Mock()
    c = app_for(src, lambda: david)
    body = {'project': SITE, 'page': 'service-ai', 'base_version': 1, 'request': 'Sharpen the signals heading.', 'data': page('service-ai')}
    stale = c.post('/api/website/studio/propose', json={**body, 'base_version': 0})
    assert stale.status_code == 409 and not david._call.called
    assert c.post('/api/website/studio/propose', json={**body, 'page': 'home'}).status_code == 422 and not david._call.called
    good = {'summary': 'Signals', 'edits': [{'section': 'ServiceAi1', 'field': 'text0', 'text': 'Where AI can help.'}]}
    david._call.return_value = {'ok': True, 'json': {'action': 'reply', 'reply': json.dumps(good)}, 'usage': {'total_tokens': 9}}
    result = c.post('/api/website/studio/propose', json=body).json()
    assert result['proposal'] == good and result['page']['id'] == 'service-ai' and result['page']['revision'] == {'website_commit': 'c58e641', 'draft_version': 1}
    prompt = david._call.call_args[0][0]
    pack = json.loads(prompt.split('\n', 1)[1])
    assert pack['studio_page']['route'] == '/services/ai-implementation' and pack['studio_page']['seo']['title'].startswith('AI Strategy')
    assert set(pack['section_types']) == {f'ServiceAi{i}' for i in range(6)} and set(pack['style_targets']) == set(pack['section_types'])
    assert pack['context']['brand_status'] == 'approved' and pack['context']['brand']['rules']
    assert [b['reference'] for b in pack['context']['brain']] == ['business_brain:memory/website_and_content.md', 'business_brain:memory/positioning.md', 'business_brain:decisions/DECISIONS.md']
    assert 'Edit only the selected Studio page' in prompt
    other = {'summary': 'Hijack', 'edits': [{'section': 'Section0', 'field': 'text0', 'text': 'x'}]}
    david._call.return_value = {'ok': True, 'json': {'action': 'reply', 'reply': json.dumps(other)}, 'usage': {'total_tokens': 9}}
    assert c.post('/api/website/studio/propose', json=body).status_code == 422
    assert david._call.call_count == 2


def test_revision_check_covers_the_service_adapter_files(tmp_path):
    s = sources_mod.Sources(state=tmp_path, brain_root=tmp_path)
    site = {'repository': Path('/home/operator/ttros-projects/default-site')}
    if not site['repository'].is_dir():
        pytest.skip('website checkout not present')
    revision = s.revision(site)
    manifest = json.loads((STUDIO / 'service-manifest.json').read_text())
    assert revision['files'] == len({**json.loads((STUDIO / 'source-manifest.json').read_text())['files'], **manifest['files']})
    assert revision['current'], revision['problems']


def test_stage3_modules_have_no_model_access():
    for name in ('website_studio_seo.py', 'website_studio_sections.py', 'website_studio_edits.py'):
        text = (REPO / 'dashboard/backend' / name).read_text()
        tree = ast.parse(text)
        imported = {a.name.split('.')[0] for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in (n.names if isinstance(n, ast.Import) else [types.SimpleNamespace(name=n.module or '')])}
        assert not imported & {'aos_david', 'requests', 'httpx', 'openai', 'anthropic'}, name
        assert '_call(' not in text and 'get_david' not in text, name
    for name in ('seo.js', 'pages.json', 'state.js', 'sections.js', 'site-tree.jsx'):
        body = (STUDIO / name).read_text()
        assert 'fetch(' not in body and '/propose' not in body, name
