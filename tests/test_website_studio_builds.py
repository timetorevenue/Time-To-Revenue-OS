"""Saved version pinning, isolation and operator/CSRF boundaries for real-site preview."""
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import website_studio_builds as builds


@pytest.fixture
def records(monkeypatch):
    rows = {('home', 2): {'version': 2, 'fingerprint': 'home2', 'contrast_failures': [], 'envelope': {'data': 'saved-home'}},
            ('site', 3): {'version': 3, 'fingerprint': 'shared3', 'contrast_failures': [], 'envelope': {'data': 'saved-shared'}},
            ('service-ai', 4): {'version': 4, 'fingerprint': 'ai4', 'contrast_failures': [], 'envelope': {'version': 4, 'page': 'service-ai', 'data': 'saved-ai'}}}
    class Store:
        def __init__(self, _): pass
        def get(self, site, version, page): return rows[(page, version)]
    monkeypatch.setattr(builds.edits, 'Drafts', Store)
    return rows


def test_exact_saved_versions_no_client_draft_or_source_mutation(tmp_path, records, monkeypatch):
    manager = builds.Builds(SimpleNamespace(site=lambda _: {'repository': '/read-only/site'}), tmp_path)
    def work(folder, row):
        row['status'] = 'fixture-ready'
        manager.lock.release()
    monkeypatch.setattr(manager, '_build', work)
    row = manager.start('default-site', builds.BuildRequest(version=2, shared_version=3))
    request = json.loads((tmp_path / row['id'] / 'request.json').read_text())
    assert request['envelopes'] == [records[('home', 2)]['envelope'], records[('site', 3)]['envelope']]
    assert row['versions']['home']['version'] == 2
    assert row['versions']['site']['fingerprint'] == 'shared3'
    assert row['modelCalls'] == 0 and row['publication'] == 'held'
    assert request['repository'] == '/read-only/site'
    # Schema v5: saved service-page versions build too; their stored (pre-v5) envelope is passed unchanged.
    row = manager.start('default-site', builds.BuildRequest(page='service-ai', version=2, shared_version=3, pages={'service-ai': 4}))
    request = json.loads((tmp_path / row['id'] / 'request.json').read_text())
    assert request['envelopes'][2] == records[('service-ai', 4)]['envelope']
    assert row['versions']['service-ai'] == {**row['versions']['service-ai'], 'version': 4, 'fingerprint': 'ai4', 'schema_version': 4}
    with pytest.raises(KeyError): manager.get('../../other')


def test_unknown_versions_conflicts_and_contrast_fail_closed(tmp_path, records):
    manager = builds.Builds(None, tmp_path)
    with pytest.raises(KeyError): manager.start('default-site', builds.BuildRequest(version=99, shared_version=3))
    records[('home', 2)]['contrast_failures'] = [{'id': 'low-contrast'}]
    with pytest.raises(ValueError, match='contrast'): manager.start('default-site', builds.BuildRequest(version=2, shared_version=3))
    with pytest.raises(ValueError, match='Save this service page'): manager.start('default-site', builds.BuildRequest(page='service-ai', version=2, shared_version=3))
    for pages in ({'home': 2}, {'site': 3}, {'service-ai': 0}, {'unknown': 1}):
        with pytest.raises(ValueError, match='Service-page versions'): manager.start('default-site', builds.BuildRequest(version=2, shared_version=3, pages=pages))
    with pytest.raises(KeyError): manager.start('default-site', builds.BuildRequest(version=2, shared_version=3, pages={'service-ai': 9}))
    with pytest.raises(ValueError, match='Unknown Studio page'): manager.start('default-site', builds.BuildRequest(page='nowhere', version=2, shared_version=3))
    records[('home', 2)]['contrast_failures'] = []
    manager.lock.acquire()
    with pytest.raises(ValueError, match='Another'): manager.start('default-site', builds.BuildRequest(version=2, shared_version=3))
    manager.lock.release()


def test_http_customer_denied_and_operator_csrf(monkeypatch):
    class Identity:
        def principal(self, request):
            return {'workspace': request.headers.get('x-fixture-workspace', 'customer-a'), 'method': 'fixture'}
        def csrf(self, request):
            if request.headers.get('x-ttros-connections') != '1': raise HTTPException(403, 'CSRF')
    monkeypatch.setattr(builds.Builds, 'start', lambda self, site, body: {'id': 'fixture', 'version': body.version})
    app = FastAPI()
    app.include_router(builds.router(None, Identity()))
    client = TestClient(app)
    url = '/api/website/studio/sites/default-site/builds'
    body = {'version': 2, 'shared_version': 3}
    assert client.post(url, json=body).status_code == 403
    assert client.post(url, json=body, headers={'x-fixture-workspace': 'ttros-operator'}).status_code == 403
    response = client.post(url, json=body, headers={'x-fixture-workspace': 'ttros-operator', 'x-ttros-connections': '1'})
    assert response.status_code == 202 and response.json()['version'] == 2
    assert client.get(url + '/' + 'a' * 32).status_code == 403


def test_worker_does_not_inherit_credentials_or_operator_npm_config(tmp_path, monkeypatch):
    monkeypatch.setenv('COMPOSIO_API_KEY', 'must-not-reach-preview')
    monkeypatch.setenv('BREVO_API_KEY', 'must-not-reach-preview')
    monkeypatch.setenv('GUIDE_CAPTURE_ENABLED', 'true')
    manager = builds.Builds(None, tmp_path)
    manager.lock.acquire()
    row = {'id': 'fixture', 'status': 'building'}
    checked = []
    def popen(command, **options):
        env = options['env']
        assert 'COMPOSIO_API_KEY' not in env and 'BREVO_API_KEY' not in env
        assert env['GUIDE_CAPTURE_ENABLED'] == 'false'
        assert env['NEWSLETTER_CAPTURE_ENABLED'] == 'false'
        assert env['NEWSLETTER_RELAYS_ENABLED'] == 'false'
        user, glob = env['npm_config_userconfig'], env['npm_config_globalconfig']
        assert user != glob and user.startswith(str(tmp_path)) and glob.startswith(str(tmp_path))
        assert open(user).read() == open(glob).read() == ''
        checked.append(True)
        raise RuntimeError('fixture stops before executing dependency installation')
    monkeypatch.setattr(builds.subprocess, 'Popen', popen)
    manager._build(tmp_path, row)
    assert checked == [True]
    assert row['status'] == 'failed'
    assert manager.lock.acquire(blocking=False)
    manager.lock.release()
