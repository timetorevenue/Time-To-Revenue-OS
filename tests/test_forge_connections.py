"""Forge uses Connections as runtime scope, never as a portable grant."""
import json
import shutil
from pathlib import Path

import pytest

from tests.test_connections_hub import setup, WHO, OTHER, github, executions
from aos_agents import factory
from aos_agents.package import load_package
from aos_agents.runtime import RunEnv, run_agent
from ttros_connections import Connections, ConnectionError

ROOT = Path(__file__).resolve().parents[1]
AGENT = 'shared.github_profile@0.1.0'


def install(root, context=None):
    shutil.copytree(ROOT / 'agents', root / 'agents', ignore=shutil.ignore_patterns('__pycache__', 'dist'), dirs_exist_ok=True)
    (root / 'queue').mkdir(exist_ok=True)
    shutil.copy(ROOT / 'queue/model_routes.json', root / 'queue/model_routes.json')
    return factory.build(AGENT, root, connection_context=context)


def test_forge_discovers_only_scoped_connections_and_records_missing(setup):
    hub, _, _, _ = setup
    row = github(hub)
    report = install(hub.identity.path.parents[2], (hub, WHO))
    assert report['status'] == 'candidate', report
    assert report['connections']['missing_required'] == []
    assert report['connections']['resolved']['connector.read:github:profile'] == 1
    package = load_package(AGENT, hub.identity.path.parents[2])
    assert hub.resolve(OTHER, package.manifest)['missing_required'] == ['connector.read:github:profile']
    evidence = json.loads((package.directory / 'evidence/factory.json').read_text())
    assert evidence['connections']['advisory']
    assert 'ca_test' not in json.dumps(evidence['connections'])


def test_runtime_revocation_restriction_task_manifest_and_tenant_denials(setup):
    hub, _, calls, account = setup
    root = hub.identity.path.parents[2]
    row = github(hub)
    install(root, (hub, WHO))
    def run(who=WHO, permissions=None, **kw):
        return run_agent(AGENT, {'action': 'read'}, RunEnv(root=root, connection_context=(hub, who),
            connection_permissions=frozenset(permissions if permissions is not None else [(row['id'], 'profile')]), **kw))
    assert run()['output']['profile']['login'] == 'test-user'
    def denied(**kw):
        before = len(executions(calls))
        assert run(**kw)['failure_class'] == 'connection_denied'
        assert len(executions(calls)) == before
    denied(who=OTHER)
    denied(permissions=[])
    denied(item={'restrictions': ['no_connector_reads']})
    hub.access(WHO, row['id'], 'Restricted', {})
    denied()
    hub.access(WHO, row['id'], 'Restricted', {AGENT: ['profile']})
    assert run()['status'] == 'ok'
    hub.access(WHO, row['id'], 'Disabled', {})
    denied()
    with pytest.raises(ConnectionError): hub.execute(WHO, row['id'], 'profile')
    assert Connections(hub.identity, vault=hub.vault).receipts(WHO)[0]['status'] == 'denied'
    hub.access(WHO, row['id'], 'Automatic', {})
    account['user_id'] = 'foreign'
    denied()


def test_job_grants_cannot_be_forged_or_reused_for_another_job(setup):
    hub, _, _, _ = setup
    root = hub.identity.path.parents[2]
    row = github(hub)
    install(root, (hub, WHO))
    job = hub.submit_agent_job(WHO, AGENT, {'action': 'read'}, [(row['id'], 'profile')])
    result = hub.run_job(job)
    assert result['status'] == 'ok'
    assert result['steps'][0]['execution_identity']['package_sha256'] == result['package_sha256']
    assert hub.receipts(WHO)[0]['job'] == job['id']
    with pytest.raises(ConnectionError, match='scope grant'):
        hub.run_job({**job, 'executor': {**job['executor'], 'grant': 'forged'}})
    with pytest.raises(ConnectionError, match='scope grant'):
        hub.run_job({**job, 'id': 'AOS-2026-99999'})
    hub.access(WHO, row['id'], 'Disabled', {})
    assert hub.run_job(job)['failure_class'] == 'connection_denied'
    assert run_agent(AGENT, {'action': 'read', 'grant': job['executor']['grant']}, RunEnv(root=root))['status'] == 'refused'


def test_restriction_during_provider_metadata_check_denies_before_data_read(setup):
    hub, _, calls, _ = setup
    row = github(hub)
    root = hub.identity.path.parents[2]
    install(root)
    original = hub.composio
    def provider(method, path, key, **kwargs):
        if method == 'GET' and path == '/connected_accounts/ca_test':
            hub.access(WHO, row['id'], 'Restricted', {})     # restricted during the ownership check
        return original(method, path, key, **kwargs)
    hub.composio = provider
    before = len(executions(calls))
    result = run_agent(AGENT, {'action':'read'}, RunEnv(root=root, connection_context=(hub, WHO),
        connection_permissions=frozenset([(row['id'],'profile')])))
    assert result['failure_class'] == 'connection_denied'
    assert len(executions(calls)) == before


def test_access_api_rejects_cross_origin_forged_principals_and_unknown_operations(setup):
    from tests.test_connections_hub import HEADERS, custom
    hub, client, _, _ = setup
    row = github(hub)
    url = '/api/account-connections/hub/connections/' + row['id'] + '/access'
    assert client.put(url, json={'mode':'Disabled'}, headers={}).status_code == 403
    assert client.put(url, json={'mode':'Disabled','workspace':'foreign'}, headers=HEADERS).status_code == 422
    assert client.put(url, json={'mode':'Restricted','agents':{AGENT:['send_email']}}, headers=HEADERS).status_code == 409
    assert client.put(url, json={'mode':'Restricted','agents':{AGENT:['profile']}}, headers=HEADERS).status_code == 200
    install(hub.identity.path.parents[2])
    url = '/api/account-connections/hub/agents/' + AGENT + '/jobs'
    assert client.post(url, json={'input':{'action':'read'},'permissions':[[row['id'],'profile']],'grant':'forged'}, headers=HEADERS).status_code == 422
    saved = hub.custom(WHO, custom())
    hub.access(WHO, saved['id'], 'Disabled', {})
    assert hub.custom(WHO, custom(name='Edited'), saved['id'])['access']['mode'] == 'Disabled'
    hub.change(WHO, saved['id'], 'disable')
    assert hub.custom(WHO, custom(name='Still disabled'), saved['id'])['enabled'] == 0


def test_manifest_cannot_widen_task_or_inherit_portable_discovery(setup):
    hub, _, _, _ = setup
    row = github(hub)
    root = hub.identity.path.parents[2]
    install(root, (hub,WHO))
    package = load_package(AGENT, root)
    # A discovered binding in Factory evidence grants no runtime context.
    assert run_agent(AGENT, {'action':'read'}, RunEnv(root=root))['failure_class'] == 'connection_scope_missing'
    with pytest.raises(ConnectionError, match='manifest'):
        hub.execute_agent(WHO, package, 'gmail', 'profile', {}, permissions=frozenset([(row['id'],'profile')]))
    hub.access(WHO,row['id'],'Restricted',{'shared.github_profile@9.0.0':['profile']})
    assert hub.resolve(WHO,package.manifest)['missing_required'] == ['connector.read:github:profile']


def test_production_agent_jobs_keep_existing_release_admission(setup):
    hub, _, _, _ = setup
    row = github(hub)
    install(hub.identity.path.parents[2])
    hub.identity.local = False
    with pytest.raises(ConnectionError,match='candidate release state'):
        hub.submit_agent_job(WHO,AGENT,{'action':'read'},[(row['id'],'profile')])
