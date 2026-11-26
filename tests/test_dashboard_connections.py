"""Security boundaries use isolated databases and provider doubles, never real consent."""
import copy
import io
import json
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import dashboard_identity as auth
import account_connections as connections
import current_website as current

HEADERS = {'X-TTROS-Connections': '1', 'Origin': 'http://127.0.0.1:4390'}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('TTROS_DASHBOARD_ORIGIN', 'http://127.0.0.1:4390')
    monkeypatch.delenv('TTROS_GOOGLE_CLIENT_ID', raising=False)
    monkeypatch.delenv('TTROS_GOOGLE_CLIENT_SECRET', raising=False)
    identity = auth.Identity(tmp_path, local=True)
    app = FastAPI()
    auth.install_session_boundary(app, identity)
    app.include_router(auth.router(identity))
    app.include_router(connections.router(identity))
    app.include_router(current.router(identity))
    @app.get('/api/legacy-operator')
    def legacy():
        return {'operator_only': True}
    return identity, TestClient(app)


def test_login_never_merges_by_email_and_link_requires_existing_identity(setup):
    identity, _ = setup
    claims = {'sub': 'subject-a', 'email': 'same@example.invalid'}
    first = identity.resolve(claims)
    second = identity.resolve({**claims, 'sub': 'subject-b'})
    assert first['user'] != second['user']
    assert first['workspace'] != second['workspace']
    with pytest.raises(ValueError):
        identity.resolve({**claims, 'sub': 'subject-b'}, first['user'])
    with pytest.raises(ValueError):
        identity.resolve({**claims, 'email': 'different@example.invalid'}, first['user'])
    assert identity.resolve(claims, first['user'])['user'] == first['user']


def test_oidc_state_cookie_pkce_expiry_replay_and_nonce(setup, monkeypatch):
    identity, client = setup
    monkeypatch.setenv('TTROS_GOOGLE_CLIENT_ID', 'unit-client')
    monkeypatch.setenv('TTROS_GOOGLE_CLIENT_SECRET', 'unit-only-secret')
    start = client.post('/api/auth/google/start', json={}, headers=HEADERS)
    assert start.status_code == 200, start.text
    query = parse_qs(urlsplit(start.json()['url']).query)
    assert query['scope'] == ['openid email profile']
    assert query['code_challenge_method'] == ['S256']
    assert 'httponly' in start.headers['set-cookie'].lower()
    assert client.get('/api/auth/google/callback?state=wrong&code=x').status_code == 400
    # Real RSA/JWT verification, with a local signing-key resolver only.
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(auth.GOOGLE_KEYS, 'get_signing_key_from_jwt', lambda value: SimpleNamespace(key=key.public_key()))
    claims = {'iss': auth.GOOGLE_ISSUER, 'aud': 'unit-client', 'exp': int(time.time()) + 300, 'iat': int(time.time()),
              'sub': 'google-subject', 'nonce': query['nonce'][0], 'email': 'signed@example.invalid', 'email_verified': True}
    for wrong in ({'nonce': 'bad'}, {'aud': 'other'}, {'iss': 'https://evil.invalid'}, {'exp': 1}, {'email_verified': False}):
        encoded = jwt.encode({**claims, **wrong}, key, algorithm='RS256')
        with pytest.raises((jwt.PyJWTError, ValueError)):
            identity.verify_google(encoded, query['nonce'][0])
    encoded = jwt.encode(claims, key, algorithm='RS256')
    captured = []
    def token_exchange(req, timeout):
        captured.append(parse_qs(req.data.decode()))
        return io.BytesIO(json.dumps({'id_token': encoded, 'access_token': 'never-persist-this-token'}).encode())
    monkeypatch.setattr(auth, 'urlopen', token_exchange)
    state = query['state'][0]
    done = client.get('/api/auth/google/callback', params={'state': state, 'code': 'test-code'}, follow_redirects=False)
    assert done.status_code == 303, done.text
    assert captured[0]['code_verifier']
    assert client.get('/api/auth/session').json()['identity']['method'] == 'google_oidc'
    assert client.get('/api/legacy-operator').status_code == 403
    assert client.get('/api/current-website/pages/home').status_code == 200
    assert client.get('/api/auth/google/callback', params={'state': state, 'code': 'test-code'}).status_code == 400
    raw = identity.path.read_bytes()
    assert b'never-persist-this-token' not in raw
    assert client.cookies.get(auth.COOKIE).encode() not in raw
    assert client.post('/api/auth/logout', json={}, headers=HEADERS).status_code == 200
    assert client.get('/api/auth/session').json()['identity']['method'] == 'local_preview'


def test_cross_origin_and_unconfigured_fail_closed(setup):
    identity, client = setup
    assert client.post('/api/auth/google/start', json={}, headers=HEADERS).status_code == 503
    for bad in ({}, {**HEADERS, 'Origin': 'http://127.0.0.1:4390.evil.invalid'}, {**HEADERS, 'Sec-Fetch-Site': 'cross-site'}):
        assert client.post('/api/auth/google/start', json={}, headers=bad).status_code == 403
    assert client.post('/api/account-connections/vercel/connect', json={}, headers=HEADERS).status_code == 403
    assert not client.get('/api/auth/session').json()['provider_data_authorized']
    assert identity.path.stat().st_mode & 0o777 == 0o600
    assert 'no-store' in client.get('/api/auth/session').headers['cache-control']
    assert 'no-store' in client.get('/api/current-website').headers['cache-control']


def test_missing_google_config_has_exact_setup_action(setup):
    _, client = setup
    response = client.post('/api/auth/google/start', json={}, headers=HEADERS)
    assert response.status_code == 503
    detail = response.json()['detail']
    assert detail['setup'] == 'google'
    assert detail['missing_configuration'] == ['TTROS_GOOGLE_CLIENT_ID', 'TTROS_GOOGLE_CLIENT_SECRET']
    assert detail['callback_url'] == 'http://127.0.0.1:4390/api/auth/google/callback'
    assert detail == {**client.get('/api/auth/session').json()['google_setup'], 'code': detail['code'], 'message': detail['message']}


def test_every_unconfigured_provider_action_reports_real_state_without_network(setup, monkeypatch):
    _, client = setup
    import ttros_connections
    monkeypatch.setattr(ttros_connections, 'composio_http', lambda *a, **kw: pytest.fail('Unconfigured actions must never contact a provider'))
    for provider in ('vercel', 'github', 'googledrive', 'gmail'):
        for action in ('connect', 'reconnect'):
            response = client.post(f'/api/account-connections/{provider}/{action}', json={}, headers=HEADERS)
            assert response.status_code == 403
            detail = response.json()['detail']
            assert detail['setup'] == provider and detail['identity_blocker']
            assert not any('COMPOSIO_API_KEY' in field for field in detail['missing_configuration'])   # one Platform path, no app key
        response = client.post(f'/api/account-connections/{provider}/status', json={}, headers=HEADERS)
        assert response.status_code == 200
        result = response.json()
        assert result['status'] == 'NOT_CONNECTED' and result['provider_checked'] is False and result['checked_at']
        assert result['health'] == 'not_connected'
        response = client.post(f'/api/account-connections/{provider}/disconnect', json={}, headers=HEADERS)
        assert response.status_code == 200
        assert response.json()['status'] == 'NOT_CONNECTED' and response.json()['changed'] is False
        assert 'nothing was disconnected' in response.json()['message']
    assert client.get('/api/account-connections/audit').json()['receipts'] == []


def platform_broker(identity, monkeypatch):
    from tests.test_composio_projects import FakeComposio, MANAGED_KEY
    from ttros_connections import Connections, SecretVault
    fake = FakeComposio()
    broker = connections.Broker(identity)
    broker.hub = Connections(identity, composio=fake, managed_key=lambda: MANAGED_KEY,
                             vault=SecretVault(identity.path.parent / 'v', key_source=lambda: bytes(32)))
    cfg = broker.config()
    cfg['providers']['vercel'].update(read_only_reviewed=True)
    monkeypatch.setattr(broker, 'config', lambda: copy.deepcopy(cfg))
    return broker, fake, MANAGED_KEY


def test_configured_http_actions_reconnect_health_errors_and_disconnect(tmp_path, monkeypatch):
    monkeypatch.setenv('TTROS_DASHBOARD_ORIGIN', 'https://ttros.core-site.example')
    headers = {**HEADERS, 'Origin': 'https://ttros.core-site.example'}
    identity = auth.Identity(tmp_path)
    who = {'user': 'unit-user', 'workspace': 'unit-workspace', 'method': 'google_oidc'}
    monkeypatch.setattr(identity, 'principal', lambda *a, **kw: who)
    broker, fake, key = platform_broker(identity, monkeypatch)
    app = FastAPI(); app.include_router(connections.router(identity, broker)); client = TestClient(app)
    for action in ('connect', 'reconnect'):
        response = client.post(f'/api/account-connections/vercel/{action}', json={}, headers=headers)
        assert response.status_code == 200 and response.json()['url'].startswith('https://connect.composio.dev/')
        assert response.json()['status'] in ('PENDING', 'INITIATED')
    assert sum(c['path'] == '/connected_accounts/link' for c in fake.calls) == 1          # reconnect resumes the saved link
    account = next(iter(fake.projects[key].values()))
    account['status'] = 'ACTIVE'
    result = client.post('/api/account-connections/vercel/status', json={}, headers=headers).json()
    assert result['status'] == 'ACTIVE' and result['provider_checked'] and result['health'] == 'active'
    account['status'] = 'EXPIRED'
    result = client.post('/api/account-connections/vercel/status', json={}, headers=headers).json()
    assert result['health'] == 'authorization_required' and 'reconnect' in result['message']
    account['user_id'] = 'someone-else'
    assert client.post('/api/account-connections/vercel/status', json={}, headers=headers).status_code == 403
    response = client.post('/api/account-connections/vercel/disconnect', json={}, headers=headers)
    assert response.status_code == 200 and response.json()['changed'] is True
    count = len(fake.calls)
    assert client.post('/api/account-connections/vercel/status', json={}, headers=headers).json()['status'] == 'DISCONNECTED'
    assert len(fake.calls) == count
    assert client.post('/api/account-connections/vercel/disconnect', json={}, headers=headers).json()['changed'] is False
    assert not any(c['method'] == 'DELETE' for c in fake.calls)


def test_stripe_runtime_binding_refuses_live_or_missing_keys(setup, monkeypatch):
    _, client = setup
    for value in ('', 'sk_live_forbidden'):
        monkeypatch.setenv('TTROS_STRIPE_TEST_KEY', value)
        monkeypatch.setenv('TTROS_STRIPE_TEST_ACCOUNT', 'acct_unit')
        response = client.post('/api/account-connections/stripe-test/connection/verify', json={}, headers=HEADERS)
        assert response.status_code == 503
        assert 'Live keys refused' in response.text


def test_scoped_broker_ownership_permissions_disconnect_and_read_only(setup, monkeypatch):
    identity, _ = setup
    who = {'user': 'user-a', 'workspace': 'workspace-a', 'method': 'google_oidc'}
    other = {**who, 'workspace': 'workspace-b'}
    foreign = {**who, 'user': 'user-b'}
    broker, fake, key = platform_broker(identity, monkeypatch)
    site = broker.config()['website']
    original = fake.__call__
    def provider(method, path, api_key, **kw):
        if path == '/tools/execute/proxy':
            assert kw['body']['method'] == 'GET' and kw['body']['endpoint'] == '/v13/deployments/' + site['deployment']
            return {'status': 200, 'data': {'id': site['deployment'], 'projectId': site['project'], 'meta': {'githubCommitSha': site['source_commit']}, 'token': 'must-not-return'}}
        return original(method, path, api_key, **kw)
    broker.hub.composio = provider
    broker.hub.use_managed_project(who)
    fake.add(key, 'ca_unit', broker.hub.managed_user(who), toolkit='vercel')
    assert broker.connect(who, 'vercel')['status'] == 'ACTIVE'       # existing Platform account reused, no OAuth
    for principal in (other, foreign):
        with pytest.raises(HTTPException):
            broker.read(principal, 'vercel', 'deployment')
    fake.projects[key]['ca_unit']['user_id'] = 'someone-else'
    with pytest.raises(HTTPException):
        broker.read(who, 'vercel', 'deployment')
    fake.projects[key]['ca_unit']['user_id'] = broker.hub.managed_user(who)
    assert broker.read(who, 'vercel', 'deployment')['matches_registered'] is True
    assert 'must-not-return' not in json.dumps(broker.read(who, 'vercel', 'deployment'))
    for operation in ('deploy', 'delete', 'publish'):
        with pytest.raises(HTTPException):
            broker.read(who, 'vercel', operation)
    bound = broker.binding(who, 'vercel')
    with identity.db() as db:
        db.execute("UPDATE hub_connections SET config=json_set(config,'$.panel_permissions',json('[]')) WHERE id=?", (bound['id'],))
    with pytest.raises(HTTPException):
        broker.read(who, 'vercel', 'deployment')
    broker.disconnect(who, 'vercel')
    assert broker.binding(who, 'vercel')['enabled'] == 0
    assert not any(c['method'] == 'DELETE' for c in fake.calls)


def test_real_page_draft_stale_proposals_and_tenant_isolation(setup):
    identity, client = setup
    base = client.get('/api/current-website/pages/home').json()
    assert base['capture']['url'] == 'https://www.core-site.example/'
    assert len(base['capture']['sections']) == 14
    data = copy.deepcopy(base['data'])
    data['sections'][0]['texts']['s0:t1'] = '<script>literal text, never HTML</script>'
    body = {'data': data, 'base_version': 0, 'source_hash': base['source_hash']}
    assert client.post('/api/current-website/pages/home/drafts', json=body, headers=HEADERS).status_code == 200
    assert client.post('/api/current-website/pages/home/drafts', json=body, headers=HEADERS).status_code == 409
    assert client.get('/api/current-website/pages/home').json()['data'] == data
    proposal = {'data': data, 'base_version': 1, 'section': 's0', 'field': 's0:t1', 'request': 'Replace selected text with: Draft proposal only'}
    result = client.post('/api/current-website/pages/home/propose', json=proposal, headers=HEADERS)
    assert result.status_code == 200, result.text
    assert result.json()['author'] == 'David' and result.json()['model_calls'] == 0
    assert not result.json()['applied'] and not result.json()['published']
    assert client.get('/api/current-website/pages/home').json()['data'] == data
    proposal['request'] = 'Generate a whole new website and publish it'
    assert client.post('/api/current-website/pages/home/propose', json=proposal, headers=HEADERS).status_code == 422
    for mutate in (lambda x: x.update(html='<script>bad</script>'), lambda x: x['sections'][0]['styles'].update(backgroundColor='url(https://evil.invalid)'), lambda x: x['sections'][0].update(source='unknown')):
        bad = copy.deepcopy(data); mutate(bad)
        assert client.post('/api/current-website/pages/home/drafts', json={**body, 'data': bad, 'base_version': 1}, headers=HEADERS).status_code == 422
    site = current.Website(identity)
    assert site.latest({'user': 'another', 'workspace': 'local-preview'}, 'home') is None
    assert site.latest({'user': 'local-operator', 'workspace': 'another'}, 'home') is None
    assert client.post('/api/current-website/assets', content=b'<svg onload="bad()"/>', headers=HEADERS).status_code == 422


def test_public_capture_provenance_and_no_active_content():
    for path in current.CAPTURES.glob('*.json'):
        page = json.loads(path.read_text())
        assert page['deployment'] in page['deployment_marker']
        for section in page['sections']:
            assert '<script' not in section['html'].lower()
            assert 'javascript:' not in section['html'].lower()
        current.validate(current.initial(page), page)


def test_google_reuses_encrypted_os_credentials_without_returning_values(setup, monkeypatch):
    identity, client = setup
    values = {'TTROS_CONNECTOR_SECRET_GOOGLE_CLIENT_ID': 'existing-web-client',
              'TTROS_CONNECTOR_SECRET_GOOGLE_CLIENT_SECRET': 'private-existing-client-secret'}
    monkeypatch.setattr('ttros_connections.credential', lambda ref: values[ref])
    assert identity.configured()
    response = client.get('/api/auth/session')
    assert response.json()['google_configured'] is True
    assert response.json()['google_setup']['missing_configuration'] == []
    assert all(value not in response.text for value in values.values())
    started = client.post('/api/auth/google/start', json={}, headers=HEADERS)
    assert started.status_code == 200
    assert 'client_id=existing-web-client' in started.json()['url']
    assert values['TTROS_CONNECTOR_SECRET_GOOGLE_CLIENT_SECRET'] not in started.text


def test_hosted_identity_return_never_defaults_to_development_port(tmp_path, monkeypatch):
    monkeypatch.delenv('TTROS_DASHBOARD_ORIGIN', raising=False)
    hosted = auth.Identity(tmp_path / 'hosted')
    assert hosted.connection_return_url() == 'https://ttros.core-site.example/settings/connections?composio=returned'
    local = auth.Identity(tmp_path / 'local', local=True)
    assert local.connection_return_url() == 'http://127.0.0.1:4390/settings/connections?composio=returned'
    for origin in ('http://127.0.0.1:4390', 'https://127.0.0.1:4390', 'https://ttros.core-site.example/path',
                   'https://ttros.core-site.example?redirect=bad', 'https://user:password@ttros.core-site.example'):
        monkeypatch.setenv('TTROS_DASHBOARD_ORIGIN', origin)
        with pytest.raises(ValueError):
            hosted.connection_return_url()
    monkeypatch.setenv('TTROS_DASHBOARD_ORIGIN', 'https://ttros.core-site.example')
    assert hosted.connection_return_url().startswith('https://ttros.core-site.example/')
