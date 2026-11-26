"""Website Manager: deterministic interpretation, the website_manager Job kind, the Studio panel API
(proposal validation, stale/cross-site/invalid-link refusals, accept/reject) and the model route's
release gate. Synthetic snapshot; the model is a fake ModelPort; no network, no Git writes."""
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'dashboard/backend'))
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'tests'))

import aos_jobs  # noqa: E402
import aos_website_manager as wm  # noqa: E402
import website_studio_manager as manager  # noqa: E402
from test_website_studio_live import SITE, HOME, studio  # noqa: E402,F401

SEL = {'id': 'home_9', 'link_id': 'home_9', 'text_id': 'home_10', 'classes': 'grid grid-cols-1 md:grid-cols-3'}


@pytest.mark.parametrize('request_text,expected', [
    ('use https://cal.example/booking/fit on this button', [{'op': 'set_link', 'id': 'home_9', 'href': 'https://cal.example/booking/fit'}]),
    ('change this link to /pricing and open it in a new tab', [{'op': 'set_link', 'id': 'home_9', 'href': '/pricing', 'newTab': True}]),
    ('make the font size 20px on mobile', [{'op': 'set_style', 'id': 'home_9', 'bp': 'm', 'values': {'fontSize': '20px'}}]),
    ('use the heading font and make it semibold', [{'op': 'set_style', 'id': 'home_9', 'bp': 'd', 'values': {'fontFamily': wm.FONTS['heading'], 'fontWeight': '600'}}]),
    ('on mobile stack the columns', [{'op': 'set_style', 'id': 'home_9', 'bp': 'm', 'values': {'gridTemplateColumns': '1fr'}}]),
    ('center the text on tablet and make it bold', [{'op': 'set_style', 'id': 'home_9', 'bp': 't', 'values': {'textAlign': 'center', 'fontWeight': '700'}}]),
    ('make the header auto-hide on mobile and tablet', [{'op': 'set_header', 'header': {'desktop': 'sticky-transparent', 'tablet': 'auto-hide', 'mobile': 'auto-hide', 'threshold': 8}}]),
    ('change the text to "Book your fit call"', [{'op': 'set_text', 'id': 'home_10', 'text': 'Book your fit call'}]),
    ('add css: .btn-primary{letter-spacing:.02em}', [{'op': 'set_css', 'css': '.btn-primary{letter-spacing:.02em}\n'}]),
    ('point this button to htps//broken', [{'op': 'set_link', 'id': 'home_9', 'href': 'htps//broken'}]),
])
def test_deterministic_rules(request_text, expected):
    found = wm.interpret(request_text, {'selected': SEL, 'breakpoint': 'd'})
    assert found and found['route'] == 'deterministic' and found['model_calls'] == 0
    assert found['ops'] == expected


@pytest.mark.parametrize('request_text', ['make it pop', 'make this headline a bit bigger', 'tidy up the spacing and make the font size 20px'])
def test_anything_not_fully_understood_goes_to_the_model_route(request_text):
    assert wm.interpret(request_text, {'selected': SEL, 'breakpoint': 'd'}) is None


class Identity:
    def principal(self, request):
        if request.headers.get('x-role') == 'guest':
            return {'workspace': 'public'}
        return {'workspace': 'ttros-operator', 'method': 'local_preview', 'email': 'owner@example.test'}

    def csrf(self, request):
        if request.method != 'GET' and request.headers.get('X-TTROS-Connections') != '1':
            raise HTTPException(403, 'CSRF')


def page_docs():
    tree = json.loads(json.dumps(HOME))
    tree[1]['props']['kids'].append({'type': 'El', 'props': {'id': 'home_9', 'tag': 'a', 'a': {'href': '/'}, 'cls': 'btn-primary',
                                                             'kids': [{'type': 'Txt', 'props': {'id': 'home_10', 'text': 'Book a call'}}]}})
    return {'app/page.tsx': tree}


class Port:
    def __init__(self, reply=None):
        self.calls, self.reply = [], reply

    def __call__(self, prompt, **kwargs):
        self.calls.append(kwargs)
        return {'status': 'ok', 'json': self.reply, 'usage': {'available': True, 'input_tokens': 900, 'output_tokens': 60}, 'model': kwargs.get('model'),
                'effort': kwargs.get('effort'), 'invocation_id': 'modelport-test'}


@pytest.fixture()
def api(studio, tmp_path, monkeypatch):
    calls = []

    def source_diff(site, docs, code, before=None):
        calls.append({'docs': docs, 'before': before})
        return {'files': [{'path': 'app/page.tsx', 'status': 'modified', 'diff': 'diff'}], 'changed': ['app/page.tsx']}
    studio.source_diff = source_diff
    studio.diff_calls = calls
    port = Port()
    app = FastAPI()
    app.include_router(manager.router(None, Identity(), studio, jobs_root=tmp_path / 'jobs', package_root=ROOT, modelport=port))
    client = TestClient(app, headers={'X-TTROS-Connections': '1'})
    client.port, client.studio, client.jobs = port, studio, tmp_path / 'jobs'
    return client


BASE = f'/api/website/studio/sites/{SITE}/live/manager'


def turn(api, text, **extra):
    body = {'text': text, 'page': 'home', 'selected': 'home_9', 'bp': 'd', 'base_version': api.studio.latest(SITE), 'docs': page_docs(), 'code': {}}
    body.update(extra)
    return api.post(BASE + '/turns', json=body)


def test_link_request_becomes_a_job_and_a_validated_proposal_that_applies_only_on_accept(api):
    r = turn(api, 'use https://cal.com/timetorevenue/fit-call on this button')
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['outcome'] == 'proposal' and body['route'] == 'deterministic' and not api.port.calls
    proposal = body['proposal']
    link = proposal['docs']['app/page.tsx'][1]['props']['kids'][-1]
    assert link['props']['a'] == {'href': 'https://cal.com/timetorevenue/fit-call'}
    assert proposal['source_diff']['changed'] == ['app/page.tsx'] and proposal['status'] == 'open'
    # The diff compares the whole page before and after (other documents unchanged on both sides).
    call = api.studio.diff_calls[-1]
    assert set(call['docs']) == set(call['before']['docs']) == set(page_docs())
    job = aos_jobs.load(api.jobs, body['job_id'])
    assert job['status'] == 'done' and job['executor']['kind'] == 'website_manager'
    assert job['notification']['status'] == 'in_app'  # never re-delivered by the runner's notice retry
    assert (api.jobs / 'queue/receipts' / f"{job['id']}.md").is_file() and job['artifacts'][0]['sha256']
    assert api.studio.latest(SITE) == 0  # nothing saved by a proposal
    # Stale: the page changed since the proposal was made.
    changed = page_docs()
    changed['app/page.tsx'][1]['props']['kids'][0]['props']['kids'][0]['props']['text'] = 'Edited meanwhile'
    stale = api.post(BASE + f"/proposals/{proposal['id']}/accept", json={'base_version': 0, 'docs': changed, 'code': {}})
    assert stale.status_code == 409 and 'changed since' in stale.json()['detail']
    ok = api.post(BASE + f"/proposals/{proposal['id']}/accept", json={'base_version': 0, 'docs': page_docs(), 'code': {}})
    assert ok.status_code == 200 and ok.json()['status'] == 'accepted'
    again = api.post(BASE + f"/proposals/{proposal['id']}/accept", json={'base_version': 0, 'docs': page_docs(), 'code': {}})
    assert again.status_code == 422
    outcomes = [t['outcome'] for t in api.get(BASE).json()['turns']]
    assert outcomes == ['proposal', 'accepted']


def test_a_newer_saved_version_makes_a_proposal_stale(api):
    proposal = turn(api, 'make the font size 20px on mobile').json()['proposal']
    api.studio.save(SITE, page_docs(), 0, 'owner:test')
    r = api.post(BASE + f"/proposals/{proposal['id']}/accept", json={'base_version': 0, 'docs': page_docs(), 'code': {}})
    assert r.status_code == 409 and 'moved on' in r.json()['detail']
    assert turn(api, 'make the font size 20px on mobile', base_version=0).status_code == 409


def test_reject_records_the_decision_and_header_and_css_proposals_change_the_code_layer(api):
    r = turn(api, 'make the header auto-hide on mobile and tablet', selected=None).json()
    assert r['outcome'] == 'proposal' and r['proposal']['code_changed'] and r['proposal']['code']['header']['mobile'] == 'auto-hide'
    rej = api.post(BASE + f"/proposals/{r['proposal']['id']}/reject", json={'base_version': 0, 'docs': page_docs(), 'code': {}})
    assert rej.json()['status'] == 'rejected'
    css = turn(api, 'add css: .btn-primary{letter-spacing:.02em}').json()
    assert css['proposal']['code'] == {'css': '.btn-primary{letter-spacing:.02em}\n'}


@pytest.mark.parametrize('text,reason', [('point this button to htps//broken', 'https://'), ('change this link to /no-such-page', 'no page or file'),
                                         ('use javascript:alert(1) on this button', 'Unsafe')])
def test_invalid_links_are_rejected_by_validation(api, text, reason):
    r = turn(api, text).json()
    assert r['outcome'] == 'rejected' and reason in r['reason'] and 'proposal' not in r


def test_cross_site_and_unauthorised_requests_are_refused(api):
    body = {'text': 'use https://cal.example/booking on this button', 'page': 'home', 'selected': 'home_9', 'bp': 'd', 'base_version': 0, 'docs': page_docs()}
    assert api.post('/api/website/studio/sites/other-site/live/manager/turns', json=body).status_code == 404
    proposal = turn(api, 'use https://cal.example/booking on this button').json()['proposal']
    assert api.post(f"/api/website/studio/sites/other-site/live/manager/proposals/{proposal['id']}/accept", json={'base_version': 0, 'docs': page_docs()}).status_code == 404
    assert api.post(BASE + '/turns', json=body, headers={'X-TTROS-Connections': ''}).status_code == 403
    assert api.post(BASE + '/turns', json=body, headers={'x-role': 'guest'}).status_code == 403
    assert api.post(BASE + '/turns', json={**body, 'docs': {'app/elsewhere.tsx': []}}).status_code == 422
    assert api.post(BASE + '/turns', json={**body, 'selected': 'nope_1'}).status_code == 422


def test_model_route_is_held_until_the_package_is_released(api, monkeypatch):
    # Hermetic: the repository's registry has released website.manager (canary) since 4e52604.
    monkeypatch.setattr(aos_jobs, 'refs_release_problem', lambda root, refs: 'website.manager is a candidate, not released')
    r = turn(api, 'make this feel a bit more premium').json()
    assert r['outcome'] == 'refused' and r['failure_class'] == 'agent_not_released' and not api.port.calls
    assert api.get(BASE).json()['model_route'] == 'held'


def test_model_route_runs_the_forge_package_and_its_operations_are_validated(api, monkeypatch):
    monkeypatch.setattr(aos_jobs, 'refs_release_problem', lambda root, refs: '')
    null = {'id': None, 'bp': None, 'styles': [], 'href': None, 'new_tab': None, 'text': None, 'attr': None, 'value': None, 'css': None, 'header': None}
    api.port.reply = {'reply': 'Made the button text larger on mobile.', 'needs_input': False, 'missing': [],
                      'ops': [{**null, 'op': 'set_style', 'id': 'home_9', 'bp': 'm', 'styles': [{'prop': 'fontSize', 'value': '18px'}]}]}
    r = turn(api, 'make this a little bigger on phones').json()
    assert r['outcome'] == 'proposal' and r['route'] == 'model', r
    assert api.port.calls and api.port.calls[0]['model'] == 'gpt-6-luna' and api.port.calls[0]['effort'] == 'low'
    assert r['proposal']['ops'] == [{'op': 'set_style', 'id': 'home_9', 'bp': 'm', 'values': {'fontSize': '18px'}}]
    job = aos_jobs.load(api.jobs, r['job_id'])
    assert job['usage']['model_calls'] == 1 and job['usage']['total_tokens'] == 960
    # An operation on an element that is not on the page is refused by the package's own checks.
    api.port.reply = {**api.port.reply, 'ops': [{**null, 'op': 'set_style', 'id': 'elsewhere_1', 'bp': 'm', 'styles': [{'prop': 'color', 'value': 'red'}]}]}
    bad = turn(api, 'make this a little bigger on phones').json()
    assert bad['outcome'] == 'failed' and bad['failure_class'] == 'validation_failed'
    # Unsafe requests never reach the model.
    calls = len(api.port.calls)
    unsafe = turn(api, 'please add <script>alert(1)</script> under this button').json()
    assert unsafe['outcome'] == 'refused' and unsafe['failure_class'] == 'unsafe_request' and len(api.port.calls) == calls
