"""Security/integration tests. Provider doubles here are never acceptance proof."""
import copy
import json
import socket
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard_identity import Identity
from connection_hub import router
from ttros_connections import Connections, ConnectionError, SecretVault, endpoint, host_url, public_addresses, get_json, credential
from aos_david import David
import aos_jobs

WHO = {'user': 'local-operator', 'workspace': 'local-preview', 'email': '', 'method': 'local_preview'}
OTHER = {**WHO, 'workspace': 'customer'}
HEADERS = {'X-TTROS-Connections': '1', 'Origin': 'http://localhost:4390'}


class GithubPlatform:
    """Platform project double (documented v3.1 shapes) with one GitHub profile tool."""

    def __init__(self):
        from tests.test_composio_projects import FakeComposio
        self.fake = FakeComposio()
        self.calls = self.fake.calls

    def __call__(self, method, path, key, *, query=None, body=None):
        if method == 'POST' and path == '/tools/execute/GITHUB_GET_THE_AUTHENTICATED_USER':
            self.calls.append({'method': method, 'path': path, 'key': key, 'query': query, 'body': body})
            account = self.fake.projects[key].get(body['connected_account_id'])
            assert account and account['user_id'] == body['user_id']
            return {'successful': True, 'error': None, 'data': {'login': 'test-user', 'id': 123, 'access_token': 'must-not-return'}}
        if method == 'GET' and path == '/toolkits':
            self.calls.append({'method': method, 'path': path, 'key': key, 'query': query, 'body': body})
            apps = [{'slug': 'github', 'name': 'GitHub'}, {'slug': 'gmail', 'name': 'Gmail'}]
            return {'items': [a for a in apps if query['search'] in a['slug']]}
        return self.fake(method, path, key, query=query, body=body)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    from tests.test_composio_projects import MANAGED_KEY
    monkeypatch.setenv('TTROS_DASHBOARD_ORIGIN', 'http://localhost:4390')
    identity = Identity(tmp_path, local=True)
    provider = GithubPlatform()
    vault = SecretVault(tmp_path / 'vault', key_source=lambda: bytes(range(32)))
    hub = Connections(identity, composio=provider, vault=vault, managed_key=lambda: MANAGED_KEY,
                      fetch=lambda *args: {'status': 'ok'})
    hub.use_managed_project(WHO)
    provider.fake.add(MANAGED_KEY, 'ca_test', hub._composio_user(WHO), 'github')
    monkeypatch.setattr('ttros_connections.public_addresses', lambda host: ['93.184.216.34'])
    app = FastAPI(); app.include_router(router(identity, hub), prefix='/api/account-connections')
    return hub, TestClient(app), provider.calls, provider.fake.projects[MANAGED_KEY]['ca_test']


def github(hub, who=WHO):
    return hub.bind_project(who, 'ca_test')


def executions(calls):
    return [c for c in calls if c['path'].startswith('/tools/execute/')]


def custom(**changes):
    return {'name': 'Status', 'service': 'status_service', 'host': 'https://api.example.com', 'auth': 'none', 'operations': [{'name': 'status', 'path': '/status', 'method': 'GET', 'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False}, 'response_schema': {'type': 'object', 'required': ['status']}}], **changes}


def test_platform_account_reuse_identity_and_durability(setup):
    hub, client, calls, account = setup
    row = github(hub)
    assert row['config']['provider_account']['id'] == 'ca_test'
    result = hub.execute(WHO, row['id'], 'profile')
    assert result['result']['login'] == 'test-user' and result['provider_verified']
    assert 'must-not-return' not in json.dumps(result)
    assert Connections(hub.identity, vault=hub.vault).receipts(WHO)[0] == result
    assert not any(c['path'] == '/connected_accounts/link' for c in calls)
    account['user_id'] = 'foreign'
    with pytest.raises(ConnectionError, match='belong'):
        hub.execute(WHO, row['id'], 'profile')


def test_wrong_tenant_account_arguments_permissions_and_disable(setup):
    hub, client, calls, _ = setup
    row = github(hub)
    before = len(executions(calls))
    for principal in (OTHER, {**WHO, 'user': 'other'}):
        with pytest.raises(ConnectionError): hub.execute(principal, row['id'], 'profile')
        with pytest.raises(ConnectionError): github(hub, principal)
    assert len(executions(calls)) == before
    denied_start = len(calls)
    for operation in ('send', 'GMAIL_SEND_EMAIL', 'delete', 'proxy', 'profile_proxy'):
        with pytest.raises(ConnectionError): hub.execute(WHO, row['id'], operation)
    with pytest.raises(ConnectionError): hub.execute(WHO, row['id'], 'profile', {'user_id': 'foreign'})
    assert len(calls) == denied_start
    with pytest.raises(ConnectionError): hub.bind_project(WHO, 'ca_foreign')
    hub.change(WHO, row['id'], 'disconnect')
    before = len(calls)
    with pytest.raises(ConnectionError): hub.execute(WHO, row['id'], 'profile')
    assert len(calls) == before
    assert len(executions(calls)) == 1  # The initial binding's account-identity probe.


def test_old_for_you_bindings_are_hidden_and_unusable(setup):
    hub, *_ = setup
    hub.save(WHO, 'cx_for_you', 'composio', 'gmail', 'gmail_personal', 'gmail_personal',
             {'provider_account': {'id': 'ca_consumer', 'user_id': 'consumer-x'}}, 'ACTIVE', 1.0)
    assert 'cx_for_you' not in [r['id'] for r in hub.rows(WHO)]
    for call in (lambda: hub.row(WHO, 'cx_for_you'), lambda: hub.execute(WHO, 'cx_for_you', 'search', {'query': 'x'}),
                 lambda: hub.change(WHO, 'cx_for_you', 'status')):
        with pytest.raises(ConnectionError, match='For You connection was removed'):
            call()
    for removed in ('operator', 'available_accounts', 'metadata', 'bind', 'authorize', 'discover', 'approve_tool', 'command'):
        assert not hasattr(hub, removed), removed


def test_custom_schema_secret_isolation_and_leak_negative(setup, monkeypatch, tmp_path):
    hub, client, _, _ = setup
    secret = 'only-a-fixture-secret-123'
    encrypted = tmp_path / 'encrypted'; encrypted.mkdir()
    blob = encrypted / 'TTROS_CONNECTOR_SECRET_STATUS.cred'; blob.write_bytes(b'encrypted-fixture-placeholder'); blob.chmod(0o600)
    monkeypatch.setenv('TTROS_CONNECTOR_ENCRYPTED_DIRECTORY', str(encrypted))
    from types import SimpleNamespace
    def decrypt(command, **kwargs):
        assert command == ['/usr/bin/systemd-creds', 'decrypt', '--name=TTROS_CONNECTOR_SECRET_STATUS', '-', '-']
        assert kwargs['input'] == b'encrypted-fixture-placeholder'
        return SimpleNamespace(returncode=0, stdout=secret.encode())
    monkeypatch.setattr('ttros_connections.subprocess.run', decrypt)
    payload = custom(auth='bearer', secret_ref='TTROS_CONNECTOR_SECRET_STATUS')
    with pytest.raises(ConnectionError, match='not assigned'): hub.custom(WHO, payload)
    monkeypatch.setenv('TTROS_CONNECTOR_SECRET_BINDINGS', json.dumps({'local-preview:local-operator': {'TTROS_CONNECTOR_SECRET_STATUS': payload['host']}}))
    row = hub.custom(WHO, payload)
    def fetch(origin, path, headers):
        assert headers['Authorization'] == 'Bearer ' + secret
        return {'status': 'ok', 'authorization': secret, 'nested': {'apiKey': secret}, 'echo': 'received ' + secret}
    hub.fetch = fetch
    result = aos_jobs.connection_read(hub, WHO, row['id'], 'status')
    assert secret not in json.dumps(result)
    assert secret not in hub.identity.path.read_bytes().decode('latin1')
    assert hub.receipts(OTHER) == []
    with pytest.raises(ConnectionError): hub.execute(OTHER, row['id'], 'status')
    monkeypatch.setenv('TTROS_CONNECTOR_SECRET_BINDINGS', '{}')
    with pytest.raises(ConnectionError, match='not assigned'): hub.execute(WHO, row['id'], 'status')
    assert hub.row(WHO, row['id'])['status'] == 'ACTIVE'


def test_custom_schema_validation_edit_disable_and_import(setup):
    hub, _, _, _ = setup
    row = hub.custom(WHO, custom())
    assert row['status'] == 'UNVERIFIED'
    hub.execute(WHO, row['id'], 'status')
    for arguments in ({'url': 'http://127.0.0.1'}, {'owner': 'foreign'}):
        with pytest.raises(Exception): hub.execute(WHO, row['id'], 'status', arguments)
    hub.fetch = lambda *a: {'wrong': True}
    with pytest.raises(ConnectionError, match='schema'): hub.execute(WHO, row['id'], 'status')
    with pytest.raises(ConnectionError): hub.custom(OTHER, custom(), row['id'])
    edited = hub.custom(WHO, custom(name='Edited'), row['id'])
    assert edited['status'] == 'UNVERIFIED'
    hub.change(WHO, row['id'], 'disable')
    with pytest.raises(ConnectionError): hub.execute(WHO, row['id'], 'status')
    hub.change(WHO, row['id'], 'enable')
    spec = {'openapi': '3.0.3', 'paths': {'/status': {'get': {'operationId': 'status'}, 'post': {'operationId': 'write'}}}}
    imported = hub.custom(WHO, custom(openapi=spec))
    assert imported['operations'] == ['status']
    for change in ({'operations': [{'name': 'write', 'path': '/', 'method': 'POST'}]}, {'openapi': {**spec, '$ref': 'https://evil.invalid/spec'}}, {'service': 'github'}):
        with pytest.raises(ConnectionError): hub.custom(WHO, custom(**change))


@pytest.mark.parametrize('url', ['http://example.com', 'https://a:b@example.com', 'https://example.com:8443', 'https://example.com/path', 'https://127.0.0.1', 'https://localhost', 'https://example.com?secret=x'])
def test_bad_hosts(url):
    if url == 'https://127.0.0.1':
        with pytest.raises(ConnectionError): public_addresses('127.0.0.1')
    else:
        with pytest.raises((ConnectionError, ValueError)): host_url(url)


@pytest.mark.parametrize('path', ['//evil.invalid', '/%2e%2e/admin', '/%252e%252e/a', '/a\\b', '/a?url=x', '/a#x', '/a\r\nHost:evil', '/{url}'])
def test_bad_paths(path):
    with pytest.raises(ConnectionError): endpoint(path)


def test_dns_mixed_private_and_redirect_blocked():
    with patch('socket.getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, '', ('93.184.216.34', 443)), (socket.AF_INET, socket.SOCK_STREAM, 0, '', ('169.254.169.254', 443))]):
        with pytest.raises(ConnectionError): public_addresses('api.example.com')
    class Response:
        status = 302
    with patch('ttros_connections.public_addresses', return_value=['93.184.216.34']), patch('socket.create_connection') as connect, patch('ssl.create_default_context') as context, patch('http.client.HTTPSConnection') as https:
        https.return_value._context = context.return_value
        https.return_value.getresponse.return_value = Response()
        with pytest.raises(ConnectionError, match='redirects'): get_json('https://api.example.com', '/', {'X-API-Key': 'fixture'})
        connect.assert_called_once_with(('93.184.216.34', 443), timeout=12)
        context.return_value.wrap_socket.assert_called_once_with(connect.return_value, server_hostname='api.example.com')
        assert https.return_value.request.call_args.args[0] == 'GET'


def test_http_strict_ownership_csrf_and_david_integration(setup, tmp_path):
    hub, client, _, _ = setup
    bind = '/api/account-connections/hub/project/bind'
    assert client.post(bind, json={'account_id': 'ca_test'}, headers={}).status_code == 403
    assert client.post(bind, json={'account_id': 'ca_test', 'workspace': 'other'}, headers=HEADERS).status_code == 422
    assert client.post('/api/account-connections/hub/bind', json={}, headers=HEADERS).status_code in (404, 405)
    assert client.get('/api/account-connections/hub/connections/x/tools').status_code in (404, 405)
    row = client.post(bind, json={'account_id': 'ca_test'}, headers=HEADERS).json()
    david = David(tmp_path, warm=False, model=lambda *a, **kw: pytest.fail('No model call permitted'))
    try:
        assert 'authenticated' in david.turn('test', '/connections')['response']
        result = david.turn('test', '/connection ' + row['id'] + ' profile', connection_context=(hub, WHO))
        assert result['connection_receipt']['provider_verified'] is True
        assert result['connection_receipt']['result']['login'] == 'test-user'
        assert david.turn('test', '/connection ' + row['id'] + ' profile', connection_context=(hub, OTHER))['connection_error']
    finally:
        david.close()


def test_real_jobs_claim_receipt_and_unforgeable_scope_grant(setup, monkeypatch):
    hub, _, calls, _ = setup
    row = hub.custom(WHO, custom())
    root = hub.identity.path.parents[2]
    monkeypatch.setattr('ttros_connections.run_connection_job', lambda root, job: hub.run_job(job))
    job = hub.submit_job(WHO, row['id'], 'status', {})
    done = aos_jobs.execute(root, job['id'])
    assert done['status'] == 'done', done
    assert done['usage']['model_calls'] == 0
    assert done['receipts']
    assert hub.receipts(WHO)[0]['operation'] == 'status'
    before = len(hub.receipts(WHO))
    assert aos_jobs.execute(root, job['id'])['status'] == 'done'
    assert len(hub.receipts(WHO)) == before
    forged, _ = aos_jobs.create(root, conversation_id='forged', topic='test', request='forged', executor=job['executor'])
    rejected = aos_jobs.execute(root, forged['id'])
    assert rejected['status'] == 'failed'
    assert 'scope grant' in rejected['detail']
    assert len(hub.receipts(WHO)) == before
    pending = hub.submit_job(WHO, row['id'], 'status', {})
    hub.change(WHO, row['id'], 'disable')
    assert aos_jobs.execute(root, pending['id'])['status'] == 'failed'


def test_api_keys_require_encrypted_private_os_custody(monkeypatch, tmp_path):
    monkeypatch.delenv('CREDENTIALS_DIRECTORY', raising=False)
    monkeypatch.delenv('TTROS_CONNECTOR_ENCRYPTED_DIRECTORY', raising=False)
    monkeypatch.setenv('TTROS_CONNECTOR_SECRET_EXAMPLE', 'plaintext-must-not-be-used')
    with pytest.raises(ConnectionError, match='encrypted systemd'): credential('TTROS_CONNECTOR_SECRET_EXAMPLE')
    monkeypatch.setenv('TTROS_CONNECTOR_ENCRYPTED_DIRECTORY', str(tmp_path))
    path = tmp_path / 'TTROS_CONNECTOR_SECRET_EXAMPLE.cred'
    path.write_bytes(b'ciphertext-fixture'); path.chmod(0o644)
    with pytest.raises(ConnectionError, match='private regular'): credential('TTROS_CONNECTOR_SECRET_EXAMPLE')
    path.chmod(0o600)
    with patch('ttros_connections.subprocess.run') as decrypt:
        decrypt.return_value.returncode = 1
        decrypt.return_value.stderr = b'plaintext-must-not-leak'
        with pytest.raises(ConnectionError) as result: credential('TTROS_CONNECTOR_SECRET_EXAMPLE')
        assert 'plaintext-must-not-leak' not in str(result.value)
        assert decrypt.call_args.kwargs['input'] == b'ciphertext-fixture'
    monkeypatch.setenv('CREDENTIALS_DIRECTORY', str(tmp_path))
    with pytest.raises(ConnectionError, match='systemd runtime'): credential('TTROS_CONNECTOR_SECRET_EXAMPLE')
    with pytest.raises(ConnectionError, match='Invalid encrypted'): credential('../secret')


def test_scoped_david_endpoint_uses_authenticated_principal(setup, monkeypatch):
    hub, client, calls, _ = setup
    row = hub.custom(WHO, custom())
    path = '/api/account-connections/hub/connections/' + row['id'] + '/david'
    result = client.post(path, json={'operation': 'status'}, headers=HEADERS)
    assert result.status_code == 200 and result.json()['connection_receipt']['provider_verified']
    assert result.json()['model_calls'] == 0
    monkeypatch.setattr(hub.identity, 'principal', lambda req: OTHER)
    denied = client.post(path, json={'operation': 'status'}, headers=HEADERS)
    assert denied.status_code == 200 and denied.json()['success'] is False and denied.json()['connection_error']
    assert 'connection_receipt' not in denied.json()
    assert client.post('/api/account-connections/hub/connections/' + row['id'] + '/jobs', json={'operation': 'status'}, headers=HEADERS).status_code == 409
