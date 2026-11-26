"""The older customer route and Forge consume the same Hub binding and gates."""
import copy
import json
import shutil
from types import SimpleNamespace
from pathlib import Path

import pytest

from dashboard_identity import Identity
from account_connections import Broker
from connection_hub import router
from ttros_connections import Connections, ConnectionError


WHO = {'user': 'alice', 'workspace': 'customer-a', 'method': 'google_oidc'}
OTHER = {**WHO, 'workspace': 'customer-b'}
REF = 'shared.github_profile@0.1.0'


@pytest.fixture
def customer(tmp_path, monkeypatch):
    identity = Identity(tmp_path)
    with identity.db() as db:
        db.execute('INSERT INTO users VALUES(?,?)', (WHO['user'], 'alice@example.invalid'))
        db.execute('INSERT INTO memberships VALUES(?,?)', (WHO['user'], WHO['workspace']))
    state = {'status': 'ACTIVE', 'disabled': False, 'owner': Connections.managed_user(WHO), 'calls': []}
    def account():
        return {'id': 'ca_fixture', 'user_id': state['owner'], 'toolkit': {'slug': 'github'},
                'status': state['status'], 'is_disabled': state['disabled']}
    def transport(method, path, body=None):
        state['calls'].append((method, path, body))
        if path == 'connected_accounts':
            return {'items': [account()] if state['owner'] in str(body.get('user_ids')) else []}
        if path == 'connected_accounts/ca_fixture':
            return account()
        if path == 'toolkits/github':
            return {'slug': 'github', 'name': 'GitHub', 'composio_managed_auth_schemes': ['OAUTH2'], 'auth_config_details': [],
                    'get_current_user_endpoint': '/user'}
        if path == 'tools/execute/proxy':
            return {'status': 200, 'data': {'login': 'fixture-login'}}
        if path == 'tools/execute/GITHUB_GET_THE_AUTHENTICATED_USER':
            return {'successful': True, 'data': {'login': 'fixture-login', 'id': 7, 'token': 'withhold'}}
        pytest.fail(f'Unexpected provider call: {method} {path}')
    broker = Broker(identity, transport)
    broker.hub.managed_key = lambda: 'ak_fixture_managed_key_000000000'
    config = broker.config()
    config['providers']['github'].update(read_only_reviewed=True)
    broker.config = lambda: copy.deepcopy(config)
    broker.hub.use_managed_project(WHO)
    assert broker.hub.bind_project(WHO, 'ca_fixture')['connection_status'] == 'ACTIVE'
    row = broker.binding(WHO, 'github')
    assert broker.health(WHO, 'github')['status'] == 'ACTIVE'
    return broker, row, state


def test_customer_binding_is_discoverable_and_task_scoped(customer):
    broker, row, state = customer
    hub = broker.hub
    manifest = {'agent': {'name': 'shared.github_profile', 'version': '0.1.0'},
                'capabilities': {'grants': ['connector.read:github:profile']}}
    assert Connections(hub.identity).resolve(WHO, manifest)['resolved']['connector.read:github:profile'][0]['connection'] == row['id']
    assert hub.resolve(OTHER, manifest)['missing_required'] == ['connector.read:github:profile']
    package = SimpleNamespace(ref=REF, grants={'connector.read:github:profile'}, content_sha256='fixture-package')
    permission = frozenset([(row['id'], 'profile')])
    before = len(state['calls'])
    with pytest.raises(ConnectionError, match='task'):
        hub.execute_agent(WHO, package, 'github', 'profile', {}, permissions=frozenset())
    assert len(state['calls']) == before
    result = hub.execute_agent(WHO, package, 'github', 'profile', {}, permissions=permission)
    assert result['status'] == 'ok' and result['result']['login'] == 'fixture-login'
    assert 'withhold' not in json.dumps(result)
    for who in (OTHER, {**WHO, 'user': 'bob'}):
        with pytest.raises(ConnectionError):
            hub.execute_agent(who, package, 'github', 'profile', {}, permissions=permission)
    with pytest.raises(ConnectionError):
        hub.execute_agent(WHO, package, 'github', 'publish', {}, permissions=frozenset([(row['id'], 'publish')]))
    with pytest.raises(ConnectionError, match='manifest'):
        hub.execute_agent(WHO, package, 'github', 'tools', {}, permissions=frozenset([(row['id'], 'tools')]))


def test_exact_version_restriction_and_live_revocation(customer):
    broker, row, state = customer
    hub = broker.hub
    package = SimpleNamespace(ref=REF, grants={'connector.read:github:profile'}, content_sha256='fixture-package')
    permission = frozenset([(row['id'], 'profile')])
    hub.access(WHO, row['id'], 'Restricted', {'shared.github_profile@9.0.0': ['profile']})
    with pytest.raises(ConnectionError, match='unique allowed'):
        hub.execute_agent(WHO, package, 'github', 'profile', {}, permissions=permission)
    with pytest.raises(ConnectionError, match='restricted'):
        hub.execute(WHO, row['id'], 'profile')
    with pytest.raises(Exception):
        broker.read(WHO, 'github', 'source')
    hub.access(WHO, row['id'], 'Restricted', {REF: ['profile']})
    assert hub.execute_agent(WHO, package, 'github', 'profile', {}, permissions=permission)['status'] == 'ok'
    before = len(state['calls'])
    with pytest.raises(ConnectionError, match='policy'):
        hub.execute_agent(WHO, package, 'github', 'profile', {}, permissions=permission,
                          item={'restrictions': ['no_connector_reads']})
    assert len(state['calls']) == before
    state['disabled'] = True
    with pytest.raises(ConnectionError, match='DISABLED'):
        hub.execute_agent(WHO, package, 'github', 'profile', {}, permissions=permission)
    assert state['calls'][-1][1] == 'connected_accounts/ca_fixture'
    state['disabled'] = False
    hub.access(WHO, row['id'], 'Disabled', {})
    before = len(state['calls'])
    with pytest.raises(ConnectionError, match='unique allowed'):
        hub.execute_agent(WHO, package, 'github', 'profile', {}, permissions=permission)
    assert len(state['calls']) == before


def test_restriction_changed_during_metadata_check_blocks_provider_read(customer):
    broker, row, state = customer
    hub = broker.hub
    package = SimpleNamespace(ref=REF, grants={'connector.read:github:profile'}, content_sha256='fixture-package')
    original = broker.transport
    def revoke(method, path, body=None):
        result = original(method, path, body)
        if path == 'connected_accounts/ca_fixture':
            hub.access(WHO, row['id'], 'Disabled', {})
        return result
    broker.transport = revoke
    before = len([call for call in state['calls'] if call[1] == 'tools/execute/GITHUB_GET_THE_AUTHENTICATED_USER'])
    with pytest.raises(ConnectionError, match='revoked|disabled'):
        hub.execute_agent(WHO, package, 'github', 'profile', {}, permissions=frozenset([(row['id'], 'profile')]))
    assert len([call for call in state['calls'] if call[1] == 'tools/execute/GITHUB_GET_THE_AUTHENTICATED_USER']) == before


def test_membership_revoked_during_status_check_blocks_provider_read(customer):
    broker, row, state = customer
    hub = broker.hub
    package = SimpleNamespace(ref=REF, grants={'connector.read:github:profile'}, content_sha256='fixture-package')
    original = broker.transport
    def revoke(method, path, body=None):
        result = original(method, path, body)
        if path == 'connected_accounts/ca_fixture':
            with hub.identity.db() as db:
                db.execute('DELETE FROM memberships WHERE user=? AND workspace=?', (WHO['user'], WHO['workspace']))
        return result
    broker.transport = revoke
    before = len([call for call in state['calls'] if call[1] == 'tools/execute/GITHUB_GET_THE_AUTHENTICATED_USER'])
    with pytest.raises(ConnectionError, match='membership was revoked'):
        hub.execute_agent(WHO, package, 'github', 'profile', {}, permissions=frozenset([(row['id'], 'profile')]))
    assert len([call for call in state['calls'] if call[1] == 'tools/execute/GITHUB_GET_THE_AUTHENTICATED_USER']) == before


def test_retired_application_key_rows_are_never_listed_or_used(tmp_path):
    identity = Identity(tmp_path)
    hub = Connections(identity, composio=lambda *a, **k: pytest.fail('Retired rows must not contact a provider'))
    hub.save(WHO, 'cx_old_app', 'composio', 'github', 'GitHub', 'ca_old', {'source': 'application'}, 'ACTIVE', None)
    assert hub.rows(WHO) == []
    with pytest.raises(ConnectionError, match='removed from TTROS'):
        hub.execute(WHO, 'cx_old_app', 'profile')


def test_forge_catalogue_reports_validated_versions_and_missing_operation(customer):
    broker, _, _ = customer
    root = broker.identity.path.parents[2]
    source = Path(__file__).resolve().parents[1]
    (root / 'agents/packages').mkdir(parents=True)
    shutil.copy(source / 'agents/tap.schema.json', root / 'agents/tap.schema.json')
    shutil.copytree(source / 'agents/packages/shared.github_profile', root / 'agents/packages/shared.github_profile')
    (root / 'queue').mkdir(exist_ok=True)
    shutil.copy(source / 'queue/model_routes.json', root / 'queue/model_routes.json')
    broker.identity.principal = lambda req: WHO
    endpoint = next(route.endpoint for route in router(broker.identity, broker.hub).routes if route.path == '/hub/agents')
    listed = endpoint(object())['agents']
    assert [agent['ref'] for agent in listed] == [REF]
    assert listed[0]['grants'] == ['connector.read:github:profile']
    # The Platform binding serves the package's named read directly; nothing is reported missing.
    assert listed[0]['connections']['missing_required'] == []
    assert listed[0]['connections']['resolved']['connector.read:github:profile'][0]['operations'] == ['profile']
