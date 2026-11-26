"""Project-scoped Composio: contract, custody and isolation tests.

The Composio API here is a recording double that follows the documented v3.1 shapes
(x-api-key; connected_accounts list/get/link; auth_configs; tools/execute). It proves TTROS's
side of the contract. Real provider acceptance still needs a Composio test project key.
"""
import base64
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard_identity import Identity
from connection_hub import router
from ttros_connections import Connections, ConnectionError, ProviderRefused, SecretVault


def api_key_scheme(fields):
    return {'mode': 'API_KEY', 'name': 'api_key', 'fields': {'auth_config_creation': {'required': [], 'optional': []},
            'connected_account_initiation': {'required': fields, 'optional': []}}}


def oauth_byo(name):
    client = [{'name': 'client_id', 'displayName': 'Client id', 'description': 'Client id of the app', 'type': 'string', 'required': True, 'default': None},
              {'name': 'client_secret', 'displayName': 'Client secret', 'description': 'Client secret of the app', 'type': 'string', 'required': True, 'default': None}]
    return {'mode': 'OAUTH2', 'name': name, 'fields': {'auth_config_creation': {'required': client, 'optional': []},
            'connected_account_initiation': {'required': [], 'optional': []}}}


def api_key_toolkit(slug, name, fields):
    return {'slug': slug, 'name': name, 'no_auth': False, 'composio_managed_auth_schemes': [],
            'auth_config_details': [api_key_scheme(fields)], 'meta': {'logo': 'https://logos.composio.dev/api/' + slug}}

CUSTOMER_KEY = 'ak_customer_project_key_0123456789'
OTHER_KEY = 'ak_other_customer_key_9876543210'
MANAGED_KEY = 'ak_ttros_managed_project_key_000000'
A = {'user': 'u-alice', 'workspace': 'workspace-a', 'email': 'alice@example.invalid', 'method': 'google_oidc'}
A2 = {**A, 'user': 'u-amir', 'email': 'amir@example.invalid'}
B = {'user': 'u-bob', 'workspace': 'workspace-b', 'email': 'bob@example.invalid', 'method': 'google_oidc'}
HEADERS = {'X-TTROS-Connections': '1', 'Origin': 'http://localhost:4390'}


class FakeComposio:
    """Documented request/response shapes; one account store per project key."""

    def __init__(self):
        self.calls = []
        self.secrets_seen = []
        self.probes = {}
        self.inbox_scopes = {}
        self.projects = {CUSTOMER_KEY: {}, OTHER_KEY: {}, MANAGED_KEY: {}}
        self.auth_configs = {CUSTOMER_KEY: [{'id': 'ac_cust_gmail', 'toolkit': {'slug': 'gmail'}, 'is_disabled': False}],
                             OTHER_KEY: [], MANAGED_KEY: []}
        self.created = 0
        # Toolkit auth metadata as GET /toolkits/{slug} reports it (real shapes, 2026-11-24).
        self.toolkits = {'agent_mail': api_key_toolkit('agent_mail', 'Agent Mail', [
                             {'name': 'region', 'displayName': 'API Region', 'description': 'Use us unless EU.', 'type': 'string', 'required': True, 'default': 'us'},
                             {'name': 'generic_api_key', 'displayName': 'API Key', 'description': 'Create one at https://console.agentmail.to.', 'type': 'string', 'required': True, 'default': None}]),
                         'brevo': {**api_key_toolkit('brevo', 'Brevo', [
                             {'name': 'generic_api_key', 'displayName': 'Brevo API Key', 'description': 'Settings → SMTP & API → API keys.', 'type': 'string', 'required': True, 'default': None}]),
                             'auth_config_details': [oauth_byo('brevo_oauth2'), api_key_scheme([
                                 {'name': 'generic_api_key', 'displayName': 'Brevo API Key', 'description': 'Settings → SMTP & API → API keys.', 'type': 'string', 'required': True, 'default': None}])]},
                         'weather': {'slug': 'weather', 'name': 'Weather', 'no_auth': True, 'composio_managed_auth_schemes': [], 'auth_config_details': []}}

    def add(self, key, account_id, user_id, toolkit='gmail', **extra):
        self.projects[key][account_id] = {'id': account_id, 'user_id': user_id, 'status': 'ACTIVE', 'is_disabled': False,
                                          'toolkit': {'slug': toolkit}, 'auth_config': {'id': 'ac_x', 'auth_scheme': 'OAUTH2',
                                          'is_composio_managed': True}, 'alias': None, 'word_id': account_id + '_w', **extra}

    def __call__(self, method, path, key, *, query=None, body=None):
        self.calls.append({'method': method, 'path': path, 'key': key, 'query': query, 'body': body})
        if key not in self.projects:
            raise ConnectionError('Composio rejected the project API key for this request.')
        store = self.projects[key]
        if method == 'GET' and path == '/connected_accounts':
            users = str(query.get('user_ids', '')).split(',')
            # Documented: user_id may be omitted from list items.
            return {'items': [{k: v for k, v in a.items() if k != 'user_id'} for a in store.values() if a['user_id'] in users],
                    'next_cursor': None}
        if method == 'GET' and path.startswith('/connected_accounts/'):
            account = store.get(path.rsplit('/', 1)[1])
            if not account:
                raise ConnectionError('Composio could not find that project resource.')
            return dict(account)
        if method == 'GET' and path.startswith('/toolkits/'):
            slug = path.rsplit('/', 1)[1]
            return self.toolkits.get(slug) or {'slug': slug, 'name': slug.title(), 'no_auth': False,
                                               'composio_managed_auth_schemes': ['OAUTH2'], 'get_current_user_endpoint': '/me',
                                               'auth_config_details': [{'mode': 'OAUTH2', 'name': slug, 'fields': {}}]}
        if method == 'GET' and path == '/auth_configs':
            return {'items': [c for c in self.auth_configs[key] if c['toolkit']['slug'] == query['toolkit_slug']], 'next_cursor': None}
        if method == 'POST' and path == '/auth_configs':
            kind = body['auth_config']['type']
            assert kind in ('use_composio_managed_auth', 'use_custom_auth')
            self.created += 1
            scheme = body['auth_config'].get('authScheme', 'OAUTH2')
            config = {'id': f"ac_{'managed' if kind == 'use_composio_managed_auth' else 'custom'}_{body['toolkit']['slug']}",
                      'toolkit': {'slug': body['toolkit']['slug']}, 'auth_scheme': scheme}
            self.auth_configs[key].append(config)
            return {'toolkit': config['toolkit'], 'auth_config': {'id': config['id'], 'auth_scheme': scheme,
                                                                  'is_composio_managed': kind == 'use_composio_managed_auth'}}
        if method == 'POST' and path == '/connected_accounts':
            config = next(c for c in self.auth_configs[key] if c['id'] == body['auth_config']['id'])
            state = body['connection']['state']
            assert state['authScheme'] == config['auth_scheme'] and state['val']['status'] == 'ACTIVE'
            if state['val'].get('generic_api_key') == 'wrong-key-123':
                raise ProviderRefused('Composio refused the request (HTTP 400): The provider rejected the credential (ConnectedAccount_CredentialValidationRejected)',
                                      400, 'The provider rejected the credential (ConnectedAccount_CredentialValidationRejected)')
            self.secrets_seen.append(dict(state['val']))
            account_id = f'ca_key{len(store)}'
            self.add(key, account_id, body['connection']['user_id'], toolkit=config['toolkit']['slug'])
            self.projects[key][account_id]['auth_config'] = {'id': config['id'], 'auth_scheme': state['authScheme'], 'is_composio_managed': False}
            if config['toolkit']['slug'] == 'agent_mail':
                value = state['val'].get('generic_api_key')
                scopes = {'fixture-first': 'first@example.invalid', 'fixture-second': 'second@example.invalid',
                          'am_fixture_customer_secret_0001': 'fixture@example.invalid'}
                self.inbox_scopes[(key, account_id)] = scopes.get(value, value)
            return {'id': account_id, 'status': 'ACTIVE'}
        if method == 'POST' and path == '/connected_accounts/link':
            assert set(body) == {'auth_config_id', 'user_id', 'callback_url'}
            account_id = f'ca_new{len(store)}'
            self.add(key, account_id, body['user_id'], toolkit=body['auth_config_id'].rsplit('_', 1)[-1], status='INITIATED')
            return {'link_token': 'lt_x', 'redirect_url': 'https://connect.composio.dev/link/lt_x',
                    'expires_at': (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(), 'connected_account_id': account_id}
        if method == 'GET' and path.startswith('/tools/'):
            slug = path.rsplit('/', 1)[1]
            toolkit = slug.split('_')[0].lower() if not slug.startswith('AGENT_MAIL') else 'agent_mail'
            return {'slug': slug, 'name': slug, 'toolkit': {'slug': toolkit}, 'version': '20261120_00', 'tags': [],
                    'input_parameters': {'type': 'object', 'properties': {'inbox_id': {'type': 'string'}, 'to': {'type': 'array'},
                                         'subject': {'type': 'string'}, 'text': {'type': 'string'}, 'query': {'type': 'string'}},
                                         'additionalProperties': True}}
        if method == 'GET' and path == '/tools':
            return {'items': [{'slug': 'BREVO_GET_ACCOUNT', 'description': 'Account details', 'tags': ['readOnlyHint'],
                               'input_parameters': {'type': 'object', 'properties': {}}},
                              {'slug': 'BREVO_SEND_TRANSACTIONAL_EMAIL', 'description': 'Send an email', 'tags': [],
                               'input_parameters': {'type': 'object', 'properties': {'to': {'type': 'array'}}, 'required': ['to']}}]}
        if method == 'POST' and path == '/tools/execute/proxy':
            account_id, endpoint = body['connected_account_id'], body['endpoint']
            assert store[account_id]['user_id'] == body['user_id'] and body['method'] == 'GET'
            if (key, account_id) in self.probes:
                return self.probes[key, account_id]
            if endpoint == '/auth/me':
                inbox = self.inbox_scopes.get((key, account_id))
                if inbox == 'fixture-invalid':
                    return {'status': 401, 'data': {'name': 'UnknownApiKeyError'}}
                if inbox == 'fixture-organization':
                    return {'status': 200, 'data': {'scope_type': 'organization', 'scope_id': 'org_fixture'}}
                return {'status': 200, 'data': {'scope_type': 'inbox', 'scope_id': inbox, 'inbox_id': inbox}}
            if endpoint.startswith('/inboxes/'):
                from urllib.parse import unquote
                assert endpoint.count('/') == 2
                assert unquote(endpoint.split('/')[-1]) == self.inbox_scopes[key, account_id]
                return {'status': 200, 'data': {'inbox_id': self.inbox_scopes[key, account_id]}}
            return {'status': 200, 'data': {'emailAddress': 'owner@example.invalid'}} if endpoint == '/me' else {
                'status': 200, 'data': {'fixture': True}}
        if method == 'POST' and path.startswith('/tools/execute/'):
            account = store.get(body['connected_account_id'])
            assert account and account['user_id'] == body['user_id']
            return {'successful': True, 'error': None, 'data': {'emailAddress': 'owner@example.invalid', 'access_token': key}}
        raise AssertionError(f'undocumented call {method} {path}')


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('TTROS_DASHBOARD_ORIGIN', 'http://localhost:4390')
    identity = Identity(tmp_path, local=True)
    with identity.db() as db:
        for who in (A, A2, B):
            db.execute('INSERT OR IGNORE INTO memberships VALUES(?,?)', (who['user'], who['workspace']))
    composio = FakeComposio()
    master = bytes(range(32))
    vault = SecretVault(tmp_path / 'vault', key_source=lambda: master)

    hub = Connections(identity, composio=composio, vault=vault, managed_key=lambda: MANAGED_KEY)
    return hub, composio, identity, tmp_path


def all_bytes(root):
    return b''.join(p.read_bytes() for p in root.rglob('*') if p.is_file())


def test_existing_customer_project_is_verified_sealed_and_reused_by_stable_id(env):
    hub, composio, identity, root = env
    composio.add(CUSTOMER_KEY, 'ca_existing1', 'casey-in-acme')
    composio.add(CUSTOMER_KEY, 'ca_someone_else', 'other-user')
    state = hub.link_project(A, CUSTOMER_KEY, 'casey-in-acme')
    assert state['mode'] == 'customer' and state['owner'] and state['accounts_found'] == 1
    assert composio.calls[0] == {'method': 'GET', 'path': '/connected_accounts', 'key': CUSTOMER_KEY,
                                 'query': {'user_ids': 'casey-in-acme', 'limit': 50}, 'body': None}
    # Custody: the key exists nowhere in TTROS storage, responses or receipts in plaintext.
    assert CUSTOMER_KEY.encode() not in all_bytes(root)
    assert CUSTOMER_KEY not in json.dumps(state) and CUSTOMER_KEY not in json.dumps(hub.project_state(A))
    accounts = hub.project_accounts(A)
    assert [a['account'] for a in accounts] == ['ca_existing1']
    row = hub.bind_project(A, 'ca_existing1')
    assert row['account'] == 'ca_existing1' and row['config']['source'] == 'project'
    assert row['config']['provider_account']['user_id'] == 'casey-in-acme'
    assert hub.bind_project(A, 'ca_existing1')['id'] == row['id']      # stable, idempotent
    with pytest.raises(ConnectionError, match='does not belong'):
        hub.bind_project(A, 'ca_someone_else')
    # Exact-account identity verification uses read-only provider requests.
    assert all(c['path'] == '/tools/execute/proxy' and c['body']['method'] == 'GET'
               for c in composio.calls if c['method'] != 'GET')


def test_rejected_key_or_missing_vault_stores_nothing(env, tmp_path):
    hub, composio, identity, root = env
    with pytest.raises(ConnectionError, match='rejected'):
        hub.link_project(A, 'ak_wrong_key_that_composio_rejects')
    with pytest.raises(ConnectionError, match='API key'):
        hub.link_project(A, 'not a key!')
    assert hub.project_state(A) is None
    unprovisioned = SecretVault(tmp_path / 'v2', key_source=lambda: (_ for _ in ()).throw(ConnectionError('Project secret storage is not provisioned')))
    hub.vault = unprovisioned
    with pytest.raises(ConnectionError, match='not provisioned'):
        hub.link_project(A, CUSTOMER_KEY)
    assert hub.project_state(A) is None


def test_new_customer_managed_connect_link_and_server_side_verification(env):
    hub, composio, identity, root = env
    hub.use_managed_project(B)
    user_b = hub.managed_user(B)
    assert user_b.startswith('ttros_') and 'bob' not in user_b
    started = hub.connect_project(B, 'gmail')
    assert started['url'].startswith('https://connect.composio.dev/') and started['status'] == 'PENDING'
    link_call = [c for c in composio.calls if c['path'] == '/connected_accounts/link'][0]
    assert link_call['key'] == MANAGED_KEY and link_call['body']['user_id'] == user_b
    assert link_call['body']['auth_config_id'] == 'ac_managed_gmail'
    assert link_call['body']['callback_url'] == 'http://localhost:4390/settings/connections?composio=returned'
    hub.connect_project(B, 'gmail')
    assert composio.created == 1                                  # managed auth config reused, not recreated
    # Not active until Composio itself reports ACTIVE for the exact issued account.
    assert hub.change(B, started['id'], 'status')['status'] == 'INITIATED'
    issued = hub.row(B, started['id'])['account']
    composio.projects[MANAGED_KEY][issued]['status'] = 'ACTIVE'
    assert hub.change(B, started['id'], 'status')['status'] == 'ACTIVE'
    receipt = hub.execute(B, started['id'], 'profile')
    execute_call = composio.calls[-1]
    assert execute_call['path'] == '/tools/execute/GMAIL_GET_PROFILE'
    assert execute_call['body'] == {'connected_account_id': issued, 'user_id': user_b, 'arguments': {'user_id': 'me'}}
    assert receipt['provider_verified'] and MANAGED_KEY not in json.dumps(receipt)


def test_customer_project_without_auth_config_gets_one_from_metadata(env):
    """Clean install: a newly linked customer project needs no pre-made auth configuration."""
    hub, composio, identity, root = env
    hub.link_project(A, CUSTOMER_KEY, 'casey-in-acme')
    started = hub.connect_project(A, 'github')
    assert started['status'] == 'PENDING' and started['url'].startswith('https://connect.composio.dev/')
    created = [c for c in composio.calls if c['method'] == 'POST' and c['path'] == '/auth_configs']
    assert [c['body']['auth_config']['type'] for c in created] == ['use_composio_managed_auth']
    assert hub.connect_project(A, 'gmail')['status'] == 'PENDING'      # the existing config is reused
    assert len([c for c in composio.calls if c['method'] == 'POST' and c['path'] == '/auth_configs']) == 1


def test_tenant_member_and_operator_isolation(env):
    hub, composio, identity, root = env
    hub.use_managed_project(A)
    hub.use_managed_project(B)
    composio.add(MANAGED_KEY, 'ca_alice_gmail', hub.managed_user(A))
    alice = hub.bind_project(A, 'ca_alice_gmail')
    # Another tenant on the same managed project cannot see, bind or execute Alice's account.
    assert hub.project_accounts(B) == []
    with pytest.raises(ConnectionError, match='does not belong'):
        hub.bind_project(B, 'ca_alice_gmail')
    with pytest.raises(ConnectionError):
        hub.execute(B, alice['id'], 'profile')
    # A second member of Alice's workspace has a different Composio user and no access either.
    assert hub.project_accounts(A2) == []
    with pytest.raises(ConnectionError):
        hub.bind_project(A2, 'ca_alice_gmail')
    with pytest.raises(ConnectionError):
        hub.execute(A2, alice['id'], 'profile')
    # A customer project key sealed for workspace A cannot be opened from workspace B.
    ref = hub.vault.put(A['workspace'], CUSTOMER_KEY)
    with pytest.raises(ConnectionError):
        hub.vault.get(ref, B['workspace'])
    with sqlite3.connect(hub.vault.path) as db:
        db.execute('UPDATE secrets SET workspace=? WHERE ref=?', (B['workspace'], ref))
    with pytest.raises(ConnectionError, match='integrity'):
        hub.vault.get(ref, B['workspace'])                           # AAD binds the ciphertext to its workspace


def test_only_the_linking_member_can_replace_or_unlink(env):
    hub, composio, identity, root = env
    hub.link_project(A, CUSTOMER_KEY, 'casey-in-acme')
    for action in (lambda: hub.link_project(A2, OTHER_KEY), lambda: hub.use_managed_project(A2), lambda: hub.unlink_project(A2)):
        with pytest.raises(ConnectionError, match='Only the member'):
            action()
    # A Composio user ID already mapped to another member cannot be claimed by re-linking.
    with identity.db() as db:
        db.execute('INSERT INTO hub_project_users VALUES(?,?,?)', (A2['workspace'], A2['user'], 'amir-in-acme'))
    with pytest.raises(ConnectionError, match='another member'):
        hub.link_project(A, CUSTOMER_KEY, 'amir-in-acme')
    assert hub.project_state(A)['composio_user_id'] == 'casey-in-acme'


def test_access_modes_are_enforced_for_project_connections(env):
    hub, composio, identity, root = env
    hub.link_project(A, CUSTOMER_KEY, 'casey-in-acme')
    composio.add(CUSTOMER_KEY, 'ca_existing1', 'casey-in-acme')
    row = hub.bind_project(A, 'ca_existing1')
    manifest = {'agent': {'name': 'shared.mail_reader', 'version': '0.1.0'}, 'capabilities': {'grants': ['connector.read:gmail:profile']}}
    assert hub.resolve(A, manifest)['resolved']['connector.read:gmail:profile'][0]['connection'] == row['id']
    hub.access(A, row['id'], 'Restricted', {'shared.other@0.1.0': ['profile']})
    assert hub.resolve(A, manifest)['missing_required'] == ['connector.read:gmail:profile']
    hub.access(A, row['id'], 'Restricted', {'shared.mail_reader@0.1.0': ['profile']})
    assert hub.resolve(A, manifest)['missing_required'] == []
    hub.access(A, row['id'], 'Disabled', {})
    before = len(composio.calls)
    with pytest.raises(ConnectionError, match='disabled'):
        hub.execute(A, row['id'], 'profile')
    assert len(composio.calls) == before                             # no provider request when Disabled
    assert hub.resolve(A, manifest)['missing_required'] == ['connector.read:gmail:profile']
    hub.access(A, row['id'], 'Automatic', {})
    for bad in (('profile', {'user_id': 'someone'}), ('GMAIL_SEND_EMAIL', {}), ('profile_proxy', {})):
        with pytest.raises(ConnectionError):
            hub.execute(A, row['id'], *bad)


def test_managed_account_agent_execution_intersects_task_version_and_live_status(env):
    from types import SimpleNamespace
    hub, provider, _, _ = env
    hub.use_managed_project(A)
    account = 'ca_agent_fixture'
    provider.add(MANAGED_KEY, account, hub.managed_user(A))
    row = hub.bind_project(A, account)
    package = SimpleNamespace(ref='shared.mail_reader@0.1.0', grants={'connector.read:gmail:profile'}, content_sha256='fixture')
    grant = frozenset([(row['id'], 'profile')])
    hub.access(A, row['id'], 'Restricted', {package.ref: ['profile']})
    assert hub.execute_agent(A, package, 'gmail', 'profile', {}, permissions=grant)['status'] == 'ok'
    before = len([call for call in provider.calls if call['path'].startswith('/tools/execute/')])
    for who, permissions, operation in ((B, grant, 'profile'), (A, frozenset(), 'profile'), (A, grant, 'send')):
        with pytest.raises(ConnectionError):
            hub.execute_agent(who, package, 'gmail', operation, {}, permissions=permissions)
    assert len([call for call in provider.calls if call['path'].startswith('/tools/execute/')]) == before
    provider.projects[MANAGED_KEY][account]['is_disabled'] = True
    with pytest.raises(ConnectionError, match='DISABLED'):
        hub.execute_agent(A, package, 'gmail', 'profile', {}, permissions=grant)


def test_unlink_removes_key_and_disables_bindings_without_touching_composio(env):
    hub, composio, identity, root = env
    hub.link_project(A, CUSTOMER_KEY, 'casey-in-acme')
    composio.add(CUSTOMER_KEY, 'ca_existing1', 'casey-in-acme')
    row = hub.bind_project(A, 'ca_existing1')
    ref = hub._project_row(A)['secret']
    writes = len([c for c in composio.calls if c['method'] != 'GET'])
    assert hub.unlink_project(A)['status'] == 'UNLINKED'
    assert hub.row(A, row['id'])['enabled'] == 0
    with pytest.raises(ConnectionError):
        hub.vault.get(ref, A['workspace'])
    assert len([c for c in composio.calls if c['method'] != 'GET']) == writes
    assert 'ca_existing1' in composio.projects[CUSTOMER_KEY]


def test_redirect_and_account_ids_from_composio_are_validated(env):
    hub, composio, identity, root = env
    hub.use_managed_project(B)
    original = composio.__call__

    def evil(method, path, key, *, query=None, body=None):
        result = original(method, path, key, query=query, body=body)
        if path == '/connected_accounts/link':
            result['redirect_url'] = 'https://connect.composio.dev.evil.example/phish'
        return result
    hub.composio = evil
    with pytest.raises(ConnectionError, match='Connect Link'):
        hub.connect_project(B, 'gmail')
    with pytest.raises(ConnectionError, match='exact Composio connected-account'):
        hub.bind_project(B, '../../connected_accounts')


def test_http_routes_require_same_origin_and_never_echo_the_key(env, monkeypatch):
    hub, composio, identity, root = env
    app = FastAPI(); app.include_router(router(identity, hub), prefix='/api/account-connections')
    client = TestClient(app)
    body = {'mode': 'customer', 'api_key': CUSTOMER_KEY, 'composio_user_id': 'casey-in-acme'}
    assert client.post('/api/account-connections/hub/project', json=body).status_code == 403
    assert client.post('/api/account-connections/hub/project', json=body,
                       headers={**HEADERS, 'Origin': 'https://evil.example'}).status_code == 403
    response = client.post('/api/account-connections/hub/project', json=body, headers=HEADERS)
    assert response.status_code == 200 and CUSTOMER_KEY not in response.text
    composio.add(CUSTOMER_KEY, 'ca_existing1', 'casey-in-acme')
    state = client.get('/api/account-connections/hub/state')
    assert state.status_code == 200 and CUSTOMER_KEY not in state.text
    assert [a['account'] for a in state.json()['project_accounts']] == ['ca_existing1']
    bad = client.post('/api/account-connections/hub/project', json={**body, 'api_key': 'x' * 300}, headers=HEADERS)
    assert bad.status_code == 409 and 'x' * 300 not in bad.text
    bound = client.post('/api/account-connections/hub/project/bind', json={'account_id': 'ca_existing1'}, headers=HEADERS)
    assert bound.status_code == 200 and bound.json()['account'] == 'ca_existing1'
    assert client.delete('/api/account-connections/hub/project', headers=HEADERS).status_code == 200


@pytest.mark.parametrize('change', [
    {'mode': 'invalid', 'unexpected': CUSTOMER_KEY},
    {'api_key': {'secret': CUSTOMER_KEY}},
    {'composio_user_id': CUSTOMER_KEY * 10},
])
def test_validation_errors_never_return_secret_input(env, change):
    hub, _, identity, _ = env
    app = FastAPI()
    app.include_router(router(identity, hub), prefix='/api/account-connections')
    body = {'mode': 'customer', 'api_key': CUSTOMER_KEY, **change}
    response = TestClient(app).post('/api/account-connections/hub/project', json=body, headers=HEADERS)
    assert response.status_code == 422
    assert CUSTOMER_KEY not in response.text
    assert hub.project_state(A) is None


def test_unfiltered_provider_list_cannot_disclose_another_users_account(env):
    hub, provider, _, _ = env
    hub.link_project(A, CUSTOMER_KEY, 'alice')
    provider.add(CUSTOMER_KEY, 'ca_private', 'bob')
    original = hub.composio

    def unfiltered(method, path, key, **kwargs):
        if path == '/connected_accounts':
            return {'items': [{'id': 'ca_private', 'toolkit': {'slug': 'gmail'}}]}
        return original(method, path, key, **kwargs)

    hub.composio = unfiltered
    assert hub.project_accounts(A) == []


def test_project_replacement_and_toolkit_changes_require_rebinding(env):
    hub, provider, _, _ = env
    hub.link_project(A, CUSTOMER_KEY, 'alice')
    provider.add(CUSTOMER_KEY, 'ca_shared_id', 'alice')
    row = hub.bind_project(A, 'ca_shared_id')
    provider.projects[CUSTOMER_KEY]['ca_shared_id']['toolkit']['slug'] = 'github'
    with pytest.raises(ConnectionError, match='toolkit changed'):
        hub.execute(A, row['id'], 'profile')
    hub.link_project(A, OTHER_KEY, 'alice')
    provider.add(OTHER_KEY, 'ca_shared_id', 'alice')
    before = len(provider.calls)
    with pytest.raises(ConnectionError, match='project changed'):
        hub.execute(A, row['id'], 'profile')
    assert len(provider.calls) == before


def test_customer_connect_needs_no_project_setup_and_duplicate_connect_is_safe(env):
    hub, provider, identity, root = env
    app = FastAPI()
    identity.principal = lambda req: A
    app.include_router(router(identity, hub), prefix='/api/account-connections')
    client = TestClient(app)
    response = client.post('/api/account-connections/hub/connect', json={'service': 'gmail'}, headers=HEADERS)
    assert response.status_code == 200
    started = response.json()
    assert hub.project_state(A)['mode'] == 'managed'
    assert started['status'] == 'PENDING'
    writes = [c for c in provider.calls if c['method'] == 'POST']
    duplicate = client.post('/api/account-connections/hub/connect', json={'service': 'gmail'}, headers=HEADERS)
    assert duplicate.status_code == 200
    assert duplicate.json()['id'] == started['id']
    assert duplicate.json()['url'] == started['url']
    assert [c for c in provider.calls if c['method'] == 'POST'] == writes
    account = hub.row(A, started['id'])['account']
    provider.projects[MANAGED_KEY][account]['status'] = 'ACTIVE'
    assert client.post(f'/api/account-connections/hub/connections/{started["id"]}/status', json={}, headers=HEADERS).json()['connection_status'] == 'ACTIVE'
    manifest = {'agent': {'name': 'shared.mail_reader', 'version': '0.1.0'}, 'capabilities': {'grants': ['connector.read:gmail:profile']}}
    assert hub.resolve(A, manifest)['missing_required'] == []
    assert hub.rows(B) == []
    assert hub.execute(A, started['id'], 'profile')['provider_verified'] is True
    hub.access(A, started['id'], 'Disabled', {})
    before = len(provider.calls)
    with pytest.raises(ConnectionError):
        hub.execute(A, started['id'], 'profile')
    assert len(provider.calls) == before


def test_customer_catalogue_uses_managed_project_without_operator_access(env):
    hub, provider, _, _ = env
    original = hub.composio

    def catalogue(method, path, key, **kwargs):
        if path == '/toolkits':
            assert key == MANAGED_KEY
            assert kwargs['query']['search'] == 'mail'
            return {'items': [{'slug': 'gmail', 'name': 'Gmail'}, {'slug': '../bad', 'name': 'bad'}]}
        return original(method, path, key, **kwargs)

    hub.composio = catalogue
    listed = hub.catalogue('mail', who=A)
    assert [row['name'] for row in listed['items']] == ['Gmail']
    assert listed['items'][0]['logo'] == 'https://logos.composio.dev/api/gmail'
    assert hub.project_state(A) is None  # browsing never links a project


def test_managed_unavailable_customer_gets_support_message_not_key_setup(env):
    hub, _, identity, _ = env
    hub.managed_key = lambda: (_ for _ in ()).throw(ConnectionError('Missing TTROS_CONNECTOR_SECRET_COMPOSIO_MANAGED'))
    for action in (lambda: hub.catalogue(who=A), lambda: hub.connect_app(A, 'gmail')):
        with pytest.raises(ConnectionError) as error:
            action()
        assert 'support' in str(error.value)
        assert 'SECRET' not in str(error.value)
    assert hub.project_state(A) is None


def test_hosted_acceptance_persists_exact_dedicated_user_and_no_duplicate(env, monkeypatch, capsys):
    import composio_provider_acceptance as acceptance
    hub, provider, _, root = env
    monkeypatch.setattr(acceptance, 'hosted_runtime', lambda: (hub, B, MANAGED_KEY, root))
    acceptance.start_hosted('github', None, 'ttros-acceptance-casey-20261120', True)
    started = json.loads(capsys.readouterr().out)
    assert started['composio_user_id'] == 'ttros-acceptance-casey-20261120'
    assert started['status'] == 'PENDING'
    assert MANAGED_KEY not in json.dumps(started)
    call = next(c for c in provider.calls if c['path'] == '/auth_configs' and c['method'] == 'POST')
    assert call['body']['auth_config']['credentials']['scopes'] == 'read:user'
    assert call['body']['auth_config']['restrict_to_following_tools'] == ['GITHUB_GET_THE_AUTHENTICATED_USER']
    call = next(c for c in provider.calls if c['path'] == '/connected_accounts/link')
    assert call['body']['user_id'] == 'ttros-acceptance-casey-20261120'
    attempt = json.loads((root / 'attempt.json').read_text())
    assert attempt['account'] == started['account']
    assert 'authorization_url' not in attempt
    assert (root / 'attempt.json').stat().st_mode & 0o777 == 0o600
    before = len(provider.calls)
    with pytest.raises(ValueError, match='already exists'):
        acceptance.start_hosted('github', None, 'ttros-acceptance-casey-20261120', True)
    assert len(provider.calls) == before
    acceptance.verify_hosted()
    pending = json.loads(capsys.readouterr().out)
    assert pending['active'] is False
    assert not any(c['path'].startswith('/tools/execute') for c in provider.calls)


def test_hosted_acceptance_does_one_read_and_denies_other_tenants(env, monkeypatch, capsys):
    import composio_provider_acceptance as acceptance
    hub, provider, _, root = env
    original = hub.composio
    def profile(method, path, key, **kwargs):
        if path.startswith('/tools/execute'):
            original(method, path, key, **kwargs)
            return {'status': 200, 'successful': True, 'data': {'login': 'chosen-test-user', 'id': 123}}
        return original(method, path, key, **kwargs)
    hub.composio = profile
    monkeypatch.setattr(acceptance, 'hosted_runtime', lambda: (hub, B, MANAGED_KEY, root))
    acceptance.start_hosted('github', None, 'ttros-acceptance-casey-20261120', True)
    started = json.loads(capsys.readouterr().out)
    provider.projects[MANAGED_KEY][started['account']]['status'] = 'ACTIVE'
    acceptance.verify_hosted()
    result = json.loads(capsys.readouterr().out)
    for name in ('active', 'existing_account_reused', 'wrong_workspace_ownership_denied', 'wrong_member_ownership_denied',
                 'wrong_workspace_execution_denied', 'wrong_member_execution_denied', 'forge_discovers_connection',
                 'restricted_agent_not_discovered', 'disabled_execution_denied', 'permitted_read', 'provider_identity_verified'):
        assert result[name] is True, name
    assert result['provider_identity'] == 'chosen-test-user'
    assert len([c for c in provider.calls if c['path'] == '/tools/execute/GITHUB_GET_THE_AUTHENTICATED_USER']) == 1


def test_invalid_hosted_callback_stops_before_provider_config_or_link_creation(env, monkeypatch):
    hub, provider, identity, _ = env
    hub.use_managed_project(B)
    identity.local = False
    monkeypatch.setenv('TTROS_DASHBOARD_ORIGIN', 'http://127.0.0.1:4390')
    before = len(provider.calls)
    with pytest.raises(ValueError, match='hosted HTTPS'):
        hub.connect_project(B, 'github')
    assert len(provider.calls) == before


def test_return_status_ignores_forged_query_and_verifies_saved_owner(env):
    hub, provider, identity, _ = env
    hub.use_managed_project(A)
    started = hub.connect_project(A, 'github')
    app = FastAPI()
    identity.principal = lambda req: A
    app.include_router(router(identity, hub), prefix='/api/account-connections')
    client = TestClient(app)
    path = '/api/account-connections/hub/connections/' + started['id'] + '/status?status=success&connected_account_id=ca_foreign'
    assert client.post(path, json={}, headers=HEADERS).json()['status'] == 'INITIATED'
    issued = hub.row(A, started['id'])['account']
    provider.projects[MANAGED_KEY][issued]['status'] = 'ACTIVE'
    assert client.post(path, json={}, headers=HEADERS).json()['status'] == 'ACTIVE'
    provider.projects[MANAGED_KEY][issued]['user_id'] = 'different-owner'
    assert client.post(path, json={}, headers=HEADERS).status_code == 409
    assert all(call['path'] != '/connected_accounts/ca_foreign' for call in provider.calls)
def test_expired_platform_link_can_restart_without_reauthorizing_consumer(env):
    hub, provider, identity, _ = env
    hub.use_managed_project(A)
    first = hub.connect_app(A, 'gmail')
    with identity.db() as db:
        db.execute("UPDATE hub_connections SET config=json_set(config,'$.expires_at',?) WHERE id=?",
                   ('2020-01-01T00:00:00Z', first['id']))
    second = hub.connect_app(A, 'gmail')
    assert second['id'] != first['id']
    assert len([c for c in provider.calls if c['path'] == '/connected_accounts/link']) == 2
    assert hub.row(A, first['id'])['status'] in ('INITIATED', 'PENDING')
    assert hub.row(A, second['id'])['config']['source'] == 'project'


def test_platform_account_status_reason_is_reported_without_secrets(env):
    hub, provider, identity, _ = env
    hub.use_managed_project(A)
    started = hub.connect_app(A, 'gmail')
    account = provider.projects[MANAGED_KEY][hub.row(A, started['id'])['account']]
    account.update(status='EXPIRED', status_reason='Connection initiation did not complete within 10 minutes')
    failed = hub.change(A, started['id'], 'status')
    assert failed['status'] == 'EXPIRED'
    assert failed['config']['provider_account']['status_reason'] == 'Connection initiation did not complete within 10 minutes'
    account['status_reason'] = 'Bearer secret-token example@example.invalid'
    redacted = hub.change(A, started['id'], 'status')
    assert 'secret-token' not in json.dumps(redacted)
    assert 'example@example.invalid' not in json.dumps(redacted)
    assert redacted['config']['provider_account']['status_reason'].startswith('Composio reported')

def test_old_project_pending_attempt_does_not_block_current_platform(env):
    hub, provider, _, _ = env
    hub.use_managed_project(A)
    old = hub.connect_app(A, 'gmail')
    provider.add(CUSTOMER_KEY, 'ca_customer_existing', hub.managed_user(A), toolkit='github')
    hub.link_project(A, CUSTOMER_KEY, hub.managed_user(A))
    current = hub.connect_app(A, 'gmail')
    assert current['id'] != old['id']
    assert hub.row(A, current['id'])['config']['source'] == 'project'
    assert hub.row(A, current['id'])['config']['project'] == hub._project_row(A)['fingerprint']
    assert hub.row(A, old['id'])['config']['project'] != hub._project_row(A)['fingerprint']
    assert len([c for c in provider.calls if c['path'] == '/connected_accounts/link']) == 2

def test_connect_and_refresh_never_choose_an_existing_platform_account(env):
    hub, provider, _, _ = env
    hub.use_managed_project(A)
    for ident in ('ca_first', 'ca_second'):
        provider.add(MANAGED_KEY, ident, hub.managed_user(A))
    assert hub.refresh(A)['connections'] == []
    assert [r['account'] for r in hub.project_accounts(A)] == ['ca_first', 'ca_second']
    attempt = hub.connect_app(A, 'gmail')
    assert attempt['status'] == 'PENDING'
    assert [r['account'] for r in hub.rows(A)] == [hub.row(A, attempt['id'])['account']]
    connection = hub.bind_project(A, 'ca_second', service='gmail')
    assert connection['account'] == 'ca_second' and connection['connection_status'] == 'ACTIVE'
    assert connection['identity'] == 'owner@example.invalid'


def test_removed_and_disconnected_accounts_cannot_be_rebound(env):
    hub, provider, _, _ = env
    hub.use_managed_project(A)
    provider.add(MANAGED_KEY, 'ca_first', hub.managed_user(A))
    old = hub.bind_project(A, 'ca_first')
    assert hub.change(A, old['id'], 'disconnect')['status'] == 'DISCONNECTED'
    assert hub.row(A, old['id'])['enabled'] == 0
    assert hub.remove(A, old['id'])['status'] == 'REMOVED'
    assert hub.project_accounts(A) == []
    assert hub.refresh(A)['connections'] == []
    with pytest.raises(ConnectionError, match='already used'):
        hub.bind_project(A, 'ca_first')
    fresh = hub.connect_app(A, 'gmail')
    assert fresh['status'] == 'PENDING' and hub.row(A, fresh['id'])['account'] != 'ca_first'
    assert set(provider.projects[MANAGED_KEY]) == {'ca_first', hub.row(A, fresh['id'])['account']}


def test_provider_error_payload_never_becomes_connected_or_identity(env):
    hub, provider, _, _ = env
    hub.use_managed_project(A)
    provider.add(MANAGED_KEY, 'ca_first', hub.managed_user(A), alias='UnknownApiKeyError')
    old = hub.bind_project(A, 'ca_first')
    assert old['connection_status'] == 'ACTIVE'
    provider.probes[MANAGED_KEY, 'ca_first'] = {'status': 200, 'data': {
        'error': {'name': 'UnknownApiKeyError', 'email': 'untrusted@example.invalid'}}}
    row = hub.change(A, old['id'], 'status')
    assert row['connection_status'] == 'INVALID_CREDENTIALS' and not row['identity']
    assert 'UnknownApiKeyError' not in row['label']
    provider.probes[MANAGED_KEY, 'ca_first'] = {'status': 401, 'data': {}}
    assert hub.change(A, old['id'], 'status')['connection_status'] == 'EXPIRED'
    provider.probes[MANAGED_KEY, 'ca_first'] = {'status': 503, 'data': {}}
    assert hub.change(A, old['id'], 'status')['connection_status'] == 'UNVERIFIED'


def test_inbox_scope_and_credential_replacement_use_only_selected_resource(env):
    hub, provider, _, root = env
    def connect(key, inbox, replace=None):
        return hub.connect_app(A, 'agent_mail', scheme='API_KEY', resource=inbox,
                               fields={'generic_api_key': key}, replace=replace)
    form = hub.connect_app(A, 'agent_mail')
    assert form['status'] == 'NEEDS_INPUT'
    assert any(f['name'] == 'generic_api_key' and f['secret'] for f in form['fields'])
    first = connect('fixture-first', 'first@example.invalid')
    assert first['status'] == 'ACTIVE'
    old = hub.row(A, first['id'])
    assert old['identity'] == 'first@example.invalid'
    for credential, status in [('wrong-key-123', 'INVALID_CREDENTIALS'),
                               ('fixture-invalid', 'INVALID_CREDENTIALS'),
                               ('fixture-organization', 'SCOPE_MISMATCH'),
                               ('fixture-first', 'SCOPE_MISMATCH')]:
        assert connect(credential, 'second@example.invalid', old['id'])['status'] == status
        assert hub.row(A, old['id'])['connection_status'] == 'ACTIVE'
    new = connect('fixture-second', 'second@example.invalid', old['id'])
    assert new['status'] == 'ACTIVE' and new['id'] != old['id']
    assert [r['identity'] for r in hub.rows(A)] == ['second@example.invalid']
    assert hub.rows(B) == []
    assert old['account'] not in [r['account'] for r in hub.project_accounts(A)]
    assert 'fixture-second' not in json.dumps(hub.rows(A))
    assert b'fixture-second' not in all_bytes(root)
    endpoints = [call['body']['endpoint'] for call in provider.calls if call['path'] == '/tools/execute/proxy']
    assert endpoints == ['/auth/me', '/inboxes/first%40example.invalid', '/auth/me', '/auth/me',
                         '/auth/me', '/auth/me', '/inboxes/second%40example.invalid']


def test_toolkit_without_probe_can_verify_with_an_authenticated_read_before_swap(env):
    hub, provider, _, _ = env
    hub.use_managed_project(A)
    provider.toolkits['gmail'] = {**provider('GET', '/toolkits/gmail', MANAGED_KEY), 'get_current_user_endpoint': ''}
    for ident in ('ca_initial', 'ca_replacing'):
        provider.add(MANAGED_KEY, ident, hub.managed_user(A))
    old = hub.bind_project(A, 'ca_initial')
    assert old['connection_status'] == 'UNVERIFIED'
    hub.execute(A, old['id'], 'profile')
    assert hub.row(A, old['id'])['connection_status'] == 'ACTIVE'
    candidate = hub.bind_project(A, 'ca_replacing', replace=old['id'])
    assert candidate['connection_status'] == 'UNVERIFIED'
    assert hub.row(A, old['id'])['connection_status'] == 'ACTIVE'
    hub.execute(A, candidate['id'], 'profile')
    assert [r['id'] for r in hub.rows(A)] == [candidate['id']]
    assert hub.row(A, candidate['id'])['connection_status'] == 'ACTIVE'
