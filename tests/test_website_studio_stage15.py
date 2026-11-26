"""Website Studio Stage 1.5: sources, brand, versioned edit API and the deterministic-first guarantee.

No test here may reach a model. Every routine route runs with David poisoned; only the explicit
proposal route is allowed to construct David, and then only a Mock.
"""
import ast
import datetime as dt
import json
import shutil
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'dashboard/backend'))
sys.path.insert(0, str(REPO / 'tools'))
import website_brand as brandkit  # noqa: E402
import website_studio as studio  # noqa: E402
import website_studio_edits as edits  # noqa: E402
import website_studio_sources as sources_mod  # noqa: E402

SITE = 'default-site'
WEBSITE = Path('/home/operator/ttros-projects/default-site')
KIT = WEBSITE / 'brand/time-to-revenue-brand-kit.pdf'
needs_site = pytest.mark.skipif(not KIT.is_file(), reason='registered website checkout not present')
BRAND_KIT_ID = '1pAR0Hi83yb1jjyaHBjqniVJqROIAtwz_'
CONTEXT_FOLDER = '1-vIycn0FrVhnell03V8BGGkUXFzH6byo'
EXCLUDED = 'EXAMPLE_DRIVE_ID_0005_000000000000'
FOLDER = 'application/vnd.google-apps.folder'


class FakeDrive:
    def __init__(self, fail=None, version='3'):
        self.fail, self.version, self.stats, self.downloads = fail, version, {'calls': 0}, 0

    def metadata(self, ident):
        self.stats['calls'] += 1
        if self.fail:
            raise self.fail
        if ident in (CONTEXT_FOLDER, 'AssetFolder123456', EXCLUDED):
            return {'id': ident, 'name': 'website assets' if ident != CONTEXT_FOLDER else 'redesign', 'mimeType': FOLDER, 'modifiedTime': '2026-11-14T00:00:00Z'}
        if ident == 'NotAFolder1234567':
            return {'id': ident, 'name': 'x.pdf', 'mimeType': 'application/pdf'}
        return {'id': ident, 'name': 'time-to-revenue-brand-kit.pdf' if ident == BRAND_KIT_ID else 'logo.png',
                'mimeType': 'application/pdf' if ident == BRAND_KIT_ID else 'image/png', 'modifiedTime': '2026-04-27T22:52:48Z',
                'version': self.version, 'size': '1', 'webViewLink': f'https://drive.google.com/file/d/{ident}/view'}

    def get(self, path, **params):
        self.stats['calls'] += 1
        if self.fail:
            raise self.fail
        if params.get('alt') == 'media':
            self.downloads += 1
            return KIT.read_bytes()
        return {'files': [{'id': 'img1', 'name': 'hero.png', 'mimeType': 'image/png'}, {'id': 'doc1', 'name': 'brief', 'mimeType': 'application/vnd.google-apps.document'}]}


def ok_brain(reference):
    return {'success': True, 'content': f'# Record {reference}\nWebsite direction text.', 'authority': 'canonical'}


@pytest.fixture
def poisoned_model(monkeypatch):
    """Any import-and-use of aos_david or any David construction fails the test loudly."""
    calls = []

    class Poison(types.ModuleType):
        def __getattr__(self, name):
            calls.append(name)
            raise AssertionError(f'model module touched: aos_david.{name}')
    monkeypatch.setitem(sys.modules, 'aos_david', Poison('aos_david'))

    def get_david():
        calls.append('get_david')
        raise AssertionError('David constructed by a routine route')
    return calls, get_david


@pytest.fixture
def src(tmp_path, monkeypatch):
    brain_root = tmp_path / 'brain'
    (brain_root / 'memory').mkdir(parents=True)
    (brain_root / 'decisions').mkdir()
    for ref in ('memory/website_and_content.md', 'memory/positioning.md', 'decisions/DECISIONS.md'):
        (brain_root / ref).write_text('x')
    drive = FakeDrive()
    s = sources_mod.Sources(state=tmp_path / 'state', drive_factory=lambda: drive, brain_reader=ok_brain, brain_root=brain_root)
    s.fake_drive = drive
    monkeypatch.setattr(sources_mod.Sources, 'revision', lambda self, site: {'commit': 'c58e641', 'head': 'c58e641', 'files': 12, 'current': True, 'problems': []})
    return s


def app_for(s, get_david):
    app = FastAPI()
    app.include_router(studio.router(get_david, sources=s))
    return TestClient(app)


def initial():
    return edits.initial_data()


# ---- brand extraction --------------------------------------------------------------------
@needs_site
def test_brand_kit_extraction_is_deterministic_and_complete():
    text, pages = brandkit.pdf_text(KIT.read_bytes())
    first, second = brandkit.extract(text), brandkit.extract(text)
    assert first == second and pages == 13 and first['unextracted'] == []
    assert {c['hex'] for c in first['palette']} >= {'#111315', '#F7F3EA', '#B89B63', '#D8D0C2', '#ECE9E2', '#7A746A'}
    assert len(first['palette']) == 12
    assert first['fonts']['heading'][0] == 'Geist' and first['fonts']['body'] == ['Inter']
    assert first['type_scale']['h1']['desktop'] == [64, 88] and first['components']['buttonRadius'] == [6, 10]
    assert first['messaging']['headline'] == 'Turn wasted time into revenue.'
    assert 'jargon' in first['voice']['avoid']


@needs_site
def test_proposed_tokens_are_nearest_compliant_with_accessibility_exception():
    brand = brandkit.extract(brandkit.pdf_text(KIT.read_bytes())[0])
    tokens, exceptions = brandkit.proposed_tokens(brand)
    assert tokens['h1Size'] == 88 and tokens['h2Size'] == 56 and tokens['h3Size'] == 27 and tokens['buttonRadius'] == 6
    assert tokens['secondary'] == '#ece9e2' and tokens['borderColor'] == '#d8d0c2' and tokens['muted'] == '#57564f'
    assert exceptions[0]['token'] == 'muted' and 'WCAG' in exceptions[0]['reason']
    assert brandkit.token_violations(tokens, brandkit.rules(brand, exceptions)) == []


def test_unreadable_brand_text_is_reported_not_guessed():
    brand = brandkit.extract('A brochure with no structured brand content.')
    assert brand['needs_interpretation'] and 'palette' in brand['unextracted']
    assert brand['palette'] == []


@needs_site
def test_brand_rules_reject_off_brand_and_allow_compliant_changes():
    brand = brandkit.extract(brandkit.pdf_text(KIT.read_bytes())[0])
    rules = brandkit.rules(brand, [{'token': 'muted', 'value': '#57564f'}])
    bad = {'design': {'buttonRadius': 14, 'accent': '#0969a5', 'headingFont': 'Georgia', 'background': '#B89B63'},
           'styles': [{'section': 'Section0', 'target': 'a-0', 'values': {'radius': 30}}]}
    found = {v['token'] for v in edits.brand_check(bad, rules)}
    assert found == {'buttonRadius', 'accent', 'headingFont', 'background', 'Section0.a-0.buttonRadius'}
    good = {'design': {'buttonRadius': 8, 'accent': '#C6A15B', 'muted': '#57564f'}, 'styles': [{'section': 'Section0', 'target': 'a-0', 'values': {'radius': 10}}]}
    assert edits.brand_check(good, rules) == []


# ---- sources -----------------------------------------------------------------------------
@needs_site
def test_refresh_reports_connected_sources_and_registered_scope_only(src, poisoned_model):
    result = src.refresh(SITE, reference=False)
    assert result['model_calls'] == 0 and result['brand_error'] is None
    assert result['drive']['state'] == 'connected' and result['brain']['state'] == 'connected'
    assert result['brand']['state'] == 'connected' and result['brand']['approval'] == 'proposed'
    assert EXCLUDED not in result['drive']['folders'] and CONTEXT_FOLDER in result['drive']['folders']
    assert result['drive']['asset_folder'] is None and 'not assumed' in result['drive']['detail']
    fingerprint = src.brand(SITE)['proposed']['fingerprint']
    src.refresh(SITE, reference=False)
    assert src.brand(SITE)['proposed']['fingerprint'] == fingerprint
    assert src.fake_drive.downloads == 1  # unchanged Drive version: no second download
    assert poisoned_model[0] == []


@needs_site
def test_missing_credentials_report_exact_capability_and_fall_back_deterministically(src, poisoned_model):
    def no_credentials():
        raise RuntimeError('DefaultCredentialsError')
    src.drive_factory = no_credentials
    result = src.refresh(SITE, reference=False)
    assert result['drive']['state'] == 'disconnected'
    assert 'drive.readonly' in result['drive']['detail']
    assert src.brand(SITE)['proposed']['source']['origin'] == 'local_copy'
    assert poisoned_model[0] == []


@needs_site
def test_drive_error_retry_and_staleness_are_truthful_without_models(src, poisoned_model):
    src.refresh(SITE, reference=False)
    good = src.status(SITE)['drive']['last_success']
    src.fake_drive.fail = RuntimeError('Drive HTTP 500')
    for _ in range(2):  # retries stay deterministic
        status = src.refresh(SITE, reference=False)['drive']
        assert status['state'] == 'error' and 'Drive HTTP 500' in status['detail'] and status['last_success'] == good
    src.fake_drive.fail = None
    src.refresh(SITE, reference=False)
    record = json.loads((src.state / SITE / 'drive.json').read_text())
    record['last_success'] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=48)).isoformat()
    (src.state / SITE / 'drive.json').write_text(json.dumps(record))
    assert src.status(SITE)['drive']['state'] == 'stale'
    assert poisoned_model[0] == []


def test_drive_folder_selection_is_verified_and_restricted(src):
    with pytest.raises(sources_mod.SourceError, match='excluded'):
        src.select_drive_folder(SITE, f'https://drive.google.com/drive/folders/{EXCLUDED}')
    with pytest.raises(sources_mod.SourceError, match='not an active'):
        src.select_drive_folder(SITE, 'NotAFolder1234567')
    with pytest.raises(sources_mod.SourceError, match='folder link or folder ID'):
        src.select_drive_folder(SITE, '../../etc/passwd')
    assert not (src.state / SITE / 'drive-selection.json').exists()
    chosen = src.select_drive_folder(SITE, 'https://drive.google.com/drive/folders/AssetFolder123456?usp=sharing')['asset_folder']
    assert chosen['id'] == 'AssetFolder123456' and chosen['images'] == 1 and chosen['documents'] == 1
    assert src.select_drive_folder(SITE, None) == {'asset_folder': None}


def test_brain_states_are_truthful(src, tmp_path):
    assert src.brain_status(src.site(SITE))['state'] == 'connected'
    src.brain_reader = lambda ref: {'success': False, 'error': 'unknown or disallowed record reference'}
    assert src.brain_status(src.site(SITE))['state'] == 'stale'
    src.brain_reader = Mock(side_effect=OSError('index locked'))
    assert src.brain_status(src.site(SITE))['state'] == 'error'
    src.brain_root = tmp_path / 'missing'
    assert src.brain_status(src.site(SITE))['state'] == 'disconnected'


@needs_site
def test_only_registered_assets_and_sites_are_served(src):
    assert src.asset(SITE, 'header-logo').name == 'site-logo-dark.svg'
    for bad in ('../../etc/passwd', 'unknown'):
        with pytest.raises(KeyError):
            src.asset(SITE, bad)
    with pytest.raises(KeyError):
        src.site('/home/operator')


@needs_site
def test_brand_approval_requires_the_reviewed_fingerprint(src):
    src.refresh(SITE, reference=False)
    with pytest.raises(sources_mod.SourceError, match='changed'):
        src.approve_brand(SITE, 'stale', 'owner')
    fingerprint = src.brand(SITE)['proposed']['fingerprint']
    approved = src.approve_brand(SITE, fingerprint, 'owner')
    assert approved['tokens']['buttonRadius'] == 6 and src.brand_status(src.site(SITE))['approval'] == 'approved'


# ---- versioned edit API ------------------------------------------------------------------
@needs_site
def test_drafts_are_versioned_stale_protected_and_brand_checked(src):
    store = edits.Drafts(src)
    off_brand = initial()
    off_brand['root']['props']['design']['buttonRadius'] = 3
    saved = store.save(SITE, off_brand, 0, 'owner')
    assert saved['version'] == 1 and saved['brand_overrides'] == []  # no approved brand yet
    with pytest.raises(edits.StaleError):
        store.save(SITE, off_brand, 0, 'owner')
    src.refresh(SITE, reference=False)
    src.approve_brand(SITE, src.brand(SITE)['proposed']['fingerprint'], 'owner')
    manual = store.save(SITE, off_brand, 1, 'owner', 'Owner keeps 3px buttons')
    assert {o['token'] for o in manual['brand_overrides']} == {'buttonRadius', 'h1Size', 'h2Size', 'secondary', 'borderColor'}
    bad_ops = {'summary': 'x', 'edits': [], 'design': {'buttonRadius': 30}}
    with pytest.raises(edits.EditError, match='approved brand'):
        store.save(SITE, edits.apply_proposal(off_brand, bad_ops), 2, 'david', origin='david', operations=bad_ops)
    ops = {'summary': 'x', 'edits': [{'section': 'Section0', 'field': 'text0', 'text': 'Grow faster. '}], 'design': {'buttonRadius': 8}}
    with pytest.raises(edits.EditError, match='does not equal'):
        store.save(SITE, off_brand, 2, 'david', origin='david', operations=ops)
    third = store.save(SITE, edits.apply_proposal(off_brand, ops), 2, 'david', origin='david', operations=ops)
    assert third['version'] == 3 and store.get(SITE, 3)['envelope']['data']['content'][0]['props']['text0'] == 'Grow faster. '
    assert store.get(SITE, 3)['envelope']['version'] == 5 and store.get(SITE, 3)['envelope']['project'] == SITE


def test_draft_save_refuses_source_drift(src, monkeypatch):
    monkeypatch.setattr(sources_mod.Sources, 'revision', lambda self, site: {'commit': 'a', 'current': False, 'problems': ['app/page.tsx changed']})
    with pytest.raises(edits.StaleError, match='drifted'):
        edits.Drafts(src).save(SITE, initial(), 0, 'owner')


def test_python_and_browser_reducers_agree():
    if not shutil.which('node'):
        pytest.skip('node unavailable')
    ops = {'summary': 's', 'edits': [{'section': 'Section0', 'field': 'text1', 'text': 'New headline. '}],
           'order': ['Section0', 'HeroSignature', 'Section4', 'Section1', 'Section2', 'Section3', 'Section5', 'Section6', 'Section7', 'Section8'],
           'design': {'accent': '#c6a15b', 'buttonRadius': 8}, 'styles': [{'section': 'Section0', 'target': 'a-0', 'values': {'radius': 10}}]}
    script = ("import {initialData,applyProposal} from './src/studio/state.js';"
              f"process.stdout.write(JSON.stringify(applyProposal(initialData,{json.dumps(ops)})))")
    out = subprocess.run(['node', '--input-type=module', '-e', script], cwd=REPO / 'dashboard/frontend', capture_output=True, text=True, check=True, timeout=60)
    assert edits.fingerprint(json.loads(out.stdout)) == edits.fingerprint(edits.apply_proposal(initial(), ops))


# ---- deterministic-first guarantee -------------------------------------------------------
@needs_site
def test_routine_routes_never_reach_a_model_including_error_paths(src, poisoned_model):
    calls, get_david = poisoned_model
    c = app_for(src, get_david)
    data = initial()
    assert c.get('/api/website/studio/sites').status_code == 200
    assert c.get(f'/api/website/studio/sites/{SITE}/sources').status_code == 200
    assert c.post(f'/api/website/studio/sites/{SITE}/sources/refresh?reference=false').status_code == 200
    src.fake_drive.fail = RuntimeError('Drive HTTP 503')
    assert c.post(f'/api/website/studio/sites/{SITE}/sources/refresh?reference=false').json()['drive']['state'] == 'error'
    assert c.put(f'/api/website/studio/sites/{SITE}/drive-folder', json={'folder': 'AssetFolder123456'}).status_code == 422
    src.fake_drive.fail = None
    assert c.put(f'/api/website/studio/sites/{SITE}/drive-folder', json={'folder': EXCLUDED}).status_code == 422
    brand = c.get(f'/api/website/studio/sites/{SITE}/brand').json()
    assert c.post(f'/api/website/studio/sites/{SITE}/brand/approve', json={'fingerprint': 'old'}).status_code == 422
    assert c.post(f'/api/website/studio/sites/{SITE}/brand/approve', json={'fingerprint': brand['proposed']['fingerprint']}).status_code == 200
    assert c.get(f'/api/website/studio/sites/{SITE}/assets/header-logo').status_code == 200
    assert c.get(f'/api/website/studio/sites/{SITE}/assets/..%2F..%2Fetc').status_code == 404
    assert c.get(f'/api/website/studio/sites/{SITE}/context').status_code == 200
    ops = {'summary': 'manual', 'edits': [], 'design': {'buttonRadius': 8}}
    assert c.post(f'/api/website/studio/sites/{SITE}/preview', json={'data': data, 'operations': ops}).json()['model_calls'] == 0
    assert c.post(f'/api/website/studio/sites/{SITE}/preview', json={'data': data, 'operations': {'edits': 'bad'}}).status_code == 422
    assert c.post(f'/api/website/studio/sites/{SITE}/drafts', json={'data': data, 'base_version': 0}).status_code == 200
    assert c.post(f'/api/website/studio/sites/{SITE}/drafts', json={'data': data, 'base_version': 0}).status_code == 409
    assert c.post(f'/api/website/studio/sites/{SITE}/drafts', json={'data': {'content': []}, 'base_version': 1}).status_code == 422
    assert c.get(f'/api/website/studio/sites/{SITE}/drafts').json()['latest'] == 1
    assert c.get(f'/api/website/studio/sites/{SITE}/drafts/1').status_code == 200
    assert c.get(f'/api/website/studio/sites/{SITE}/drafts/9').status_code == 404
    worker = {'base_version': 1, 'operations': ops, 'actor': 'worker:test', 'save': True}
    assert c.post(f'/api/website/studio/sites/{SITE}/operations', json=worker).json()['saved']['version'] == 2
    assert c.post(f'/api/website/studio/sites/{SITE}/operations', json=worker).status_code == 409
    worker['base_version'] = 2
    worker['operations'] = {'summary': 'x', 'edits': [], 'design': {'accent': '#ff00ff'}}
    assert c.post(f'/api/website/studio/sites/{SITE}/operations', json=worker).status_code == 422
    assert c.get('/api/website/studio/sites/unknown/sources').status_code == 404
    assert calls == []


@needs_site
def test_explicit_proposal_is_the_only_model_route_and_is_validated_first(src, monkeypatch):
    monkeypatch.setitem(sys.modules, 'aos_david', types.SimpleNamespace(FAST_MODEL='fast'))
    src.refresh(SITE, reference=False)
    src.approve_brand(SITE, src.brand(SITE)['proposed']['fingerprint'], 'owner')
    david = Mock()
    constructed = []
    c = app_for(src, lambda: constructed.append(1) or david)
    body = {'project': SITE, 'request': 'Use our brand.', 'data': initial()}
    assert c.post('/api/website/studio/propose', json={**body, 'data': {'content': []}}).status_code == 422
    monkeypatch.setattr(sources_mod.Sources, 'revision', lambda self, site: {'commit': 'a', 'current': False, 'problems': ['drift']})
    assert c.post('/api/website/studio/propose', json=body).status_code == 409
    assert constructed == [] and not david._call.called
    monkeypatch.setattr(sources_mod.Sources, 'revision', lambda self, site: {'commit': 'c58e641', 'current': True, 'problems': []})
    off = {'summary': 'Neon', 'edits': [], 'design': {'accent': '#7c3aed'}}
    david._call.return_value = {'ok': True, 'json': {'action': 'reply', 'reply': json.dumps(off)}, 'usage': {'total_tokens': 9}}
    rejected = c.post('/api/website/studio/propose', json=body)
    assert rejected.status_code == 422 and 'brand' in rejected.json()['detail']
    good = {'summary': 'Brand', 'edits': [{'section': 'Section0', 'field': 'text1', 'text': 'Velvet basil iris n '}],
            'order': ['Section0', 'HeroSignature', 'Section4', 'Section1', 'Section2', 'Section3', 'Section5', 'Section6', 'Section7', 'Section8'],
            'design': {'accent': '#C6A15B', 'buttonRadius': 8}}
    david._call.return_value = {'ok': True, 'json': {'action': 'reply', 'reply': json.dumps(good)}, 'usage': {'total_tokens': 9}}
    accepted = c.post('/api/website/studio/propose', json=body).json()
    assert accepted['proposal'] == good and accepted['brand_status'] == 'approved'
    assert 'business_brain:memory/website_and_content.md' in accepted['context_sources']
    prompt = david._call.call_args[0][0]
    assert '"rules"' in prompt and 'business_brain:memory/positioning.md' in prompt
    assert len(constructed) == 2  # exactly the two explicit proposal requests that passed validation


def test_static_only_the_proposal_route_can_reach_david():
    backend = REPO / 'dashboard/backend'
    forbidden = ('aos_david', 'ModelPort', 'anthropic', 'openai', '_call(', 'get_david', 'codex', 'claude')
    for name in ('website_studio_sources.py', 'website_studio_edits.py', 'website_brand.py', 'website_studio_design.py'):
        text = (backend / name).read_text()
        assert not [w for w in forbidden if w in text], name
    tree = ast.parse((backend / 'website_studio.py').read_text())
    owners = set()
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef):
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and getattr(node.func, 'id', getattr(node.func, 'attr', '')) in ('get_david', '_call'):
                    owners.add(fn.name)
    assert owners <= {'router', 'proposal', 'propose'}
    route = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'proposal')
    assert any(isinstance(d, ast.Call) and d.args and getattr(d.args[0], 'value', '') == '/api/website/studio/propose' for d in route.decorator_list)
