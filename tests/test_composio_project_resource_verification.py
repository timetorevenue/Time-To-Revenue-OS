"""New alternative project proof: resource isolation, exact ownership, no adoption or mutation."""
import json
import pytest
import composio_provider_acceptance as acceptance
import ttros_connections

PROJECT = 'pr_sJPPh34mNls8'
KEY = 'ak_secret_must_not_appear'

@pytest.mark.parametrize('changed', [None, 'id', 'user_id', 'auth_config'])
def test_project_resource_requires_every_independent_identity_field(changed):
    account = {'id': 'ca_EXAMPLEACCT01', 'user_id': 'ttros-acceptance-casey-20261120',
               'toolkit': {'slug': 'github'}, 'auth_config': {'id': 'ac_EXAMPLEAUTH01'},
               'state': {'secret': KEY}}
    if changed:
        account[changed] = {'id': 'ac_other'} if changed == 'auth_config' else 'different'
    calls = []
    def request(method, path, key):
        calls.append((method, path)); assert key == KEY
        return account
    result = acceptance.verify_project_witness(PROJECT, KEY, request)
    assert result['result'] == ('UNVERIFIED' if changed else 'PASS')
    assert KEY not in json.dumps(result)
    assert calls == [('GET', '/connected_accounts/ca_EXAMPLEACCT01')]


def test_known_project_uses_direct_resource_proof_without_restricted_identity_endpoint(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(acceptance, 'STORE', tmp_path)
    monkeypatch.setattr(acceptance, 'test_key', lambda: KEY)
    calls = []
    def request(method, path, key, **kwargs):
        calls.append((method, path)); assert key == KEY
        if path == '/auth/session/info':
            raise ttros_connections.ConnectionError('Composio rejected the project API key for this request.')
        if path == '/connected_accounts': return {'items': []}
        return {'id': 'ca_EXAMPLEACCT01', 'user_id': 'ttros-acceptance-casey-20261120',
                'toolkit': {'slug': 'github'}, 'auth_config': {'id': 'ac_EXAMPLEAUTH01'}}
    monkeypatch.setattr(ttros_connections, 'composio_http', request)
    assert acceptance.verify_project_identity(PROJECT) == 0
    output = capsys.readouterr().out
    assert KEY not in output
    result = json.loads(output)
    assert result['credential_accepted'] and result['exact_account_owner_config_match']
    assert result['stage'] == 'project_resource_identity'
    assert calls == [('GET', '/connected_accounts'), ('GET', '/connected_accounts/ca_EXAMPLEACCT01')]
