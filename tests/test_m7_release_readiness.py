"""Offline regression tests. All identity/probe/job sources below are SYNTHETIC fixtures."""
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock
import pytest

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT/'tools', ROOT/'dashboard/backend', ROOT/'tests/fixtures/m7'):
    sys.path.insert(0, str(path))
import aos_authority
import aos_jobs
import m7_fixture as fx
import remote_pc
from aos_agents import registry, release, reliability
from aos_agents.package import load_package

@pytest.fixture
def roots(tmp_path):
    return fx.make_root(tmp_path)

def command(root, digest=None, **headers):
    pkg = load_package('knowledge.source_classify', root)
    return aos_authority.authenticated_release_command(root, {'x-ttros-remote-pc':'1',
        'tailscale-user-login':'owner.personal@example.invalid', **headers}, pkg.ref, 'canary', digest or pkg.content_sha256)

def test_authenticated_command_reaches_real_gate(roots):
    root, _ = roots
    registry.promote('knowledge.source_classify', 'shadow', root)
    result = registry.promote('knowledge.source_classify', 'canary', root, approved_by='metadata', release_command=command(root))
    assert result['promoted']
    assert result['authorization']['basis'] == 'authenticated_explicit_release_command'
    event = registry.load(root)['agents']['knowledge.source_classify']['history'][-1]
    assert event['authorization']['operator']['identity'] == 'owner.personal@example.invalid'
    assert event['authorization']['package_sha256'] == load_package('knowledge.source_classify',root).content_sha256

@pytest.mark.parametrize('headers', [{}, {'x-ttros-remote-pc':'1'}, {'x-ttros-remote-pc':'1', 'tailscale-user-login':'stranger@example.com'},
    {'x-ttros-remote-pc':'1','cf-ray':'x','cf-access-jwt-assertion':'invalid'}])
def test_unauthenticated_command_denied(roots, headers):
    root, _ = roots
    with mock.patch.object(remote_pc, 'verify_dashboard_access_jwt', return_value=None):
        with pytest.raises(remote_pc.RemoteAccessDenied):
            aos_authority.authenticated_release_command(root, headers, 'knowledge.source_classify@0.1.0','canary','x')

@pytest.mark.parametrize('mutation', ['digest','expired','reused','wrong_target','json'])
def test_capability_cannot_widen_or_replay(roots, mutation):
    root, _ = roots
    pkg = load_package('knowledge.source_classify', root)
    cap = command(root, digest='0'*64) if mutation == 'digest' else command(root)
    if mutation == 'expired': cap.deadline = 0
    if mutation == 'reused': cap.used = True
    if mutation == 'wrong_target': cap.agent = 'other@0.1.0'
    if mutation == 'json': cap = {'authorized':True, 'approved_by':'Casey'}
    verdict = aos_authority.action_authorization(root, {'approved_by':'Casey'}, action='AGENT_RELEASE_CANARY',target=pkg.ref,release_command=cap)
    assert not verdict['authorized']

def test_receipt_attests_execution_and_preserves_replay(roots):
    root, runtime = roots
    job = fx.add_job(root, runtime, 1, ('knowledge.source_classify',))
    job['receipts'] = []
    aos_jobs.write_receipt(runtime, job)
    text = (runtime/job['receipts'][0]['path']).read_text()
    assert 'Execution identity: ' in text
    assert load_package('knowledge.source_classify',root).content_sha256 in text
    assert '"replayed":false' in text

def test_reliability_provenance_tampering_and_simulation_fail(roots):
    root, _ = roots
    path = fx.write_reliability(root,'knowledge.source_classify')
    pkg = load_package('knowledge.source_classify', root)
    assert release.gate7(pkg, root)['pass']
    data = json.loads(path.read_text())
    source = path.parent/data['provenance']['path']
    bundle = json.loads(source.read_text())
    bundle['simulated'] = True
    source.write_text(json.dumps(bundle))
    assert not release.gate7(pkg, root)['pass']
    data['provenance']['sha256'] = reliability.digest(bundle)
    path.write_text(json.dumps(data))
    assert not release.gate7(pkg, root)['pass']

def test_producer_collects_sources_without_queue_or_registry_mutation(roots):
    root, runtime = roots
    name = 'knowledge.filing_proposal'   # a tool: fixture ledger rows are marked synthetic, which model repeats refuse
    pkg = load_package(name,root)
    template = fx.write_reliability(root, name)
    data = json.loads(template.read_text())
    bundle = json.loads((template.parent/data['provenance']['path']).read_text())
    cases = {}
    for index in range(5):
        cases[str(index)] = [fx.add_job(root,runtime,index*3+j+1,(name,),output_label=f'case-{index}')['id'] for j in range(3)]
    before = release.Runtime(runtime).fingerprint()
    state = (root/registry.REGISTRY_PATH).read_bytes()
    path = reliability.produce(name,cases,bundle['kill'],bundle['forced'],root,runtime,simulated=True)
    assert path.is_file()
    assert not release.gate7(pkg,root)['pass']
    assert before == release.Runtime(runtime).fingerprint()
    assert state == (root/registry.REGISTRY_PATH).read_bytes()
    produced = json.loads(path.read_text())
    assert [len(set(row['verdicts'])) for row in produced['repeats']] == [1] * 5          # same decision per case
    assert all(len(set(row['output_sha256'])) == 3 for row in produced['repeats'])       # different wording each run
    undeclared = [fx.add_job(root,runtime,90+j,('marketing.post_check',))['id'] for j in range(3)]
    with pytest.raises(ValueError, match='no reliability verdict is declared'):
        reliability.produce('marketing.post_check',{'0':undeclared},bundle['kill'],bundle['forced'],root,runtime,simulated=True)

def test_http_release_uses_existing_authentication(roots, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import agent_release
    root, _ = roots
    registry.promote('knowledge.source_classify','shadow',root)
    monkeypatch.setattr(agent_release,'aos_root',lambda:root)
    monkeypatch.setattr(remote_pc,'_record_denial',lambda *a:None)
    app = FastAPI()
    app.include_router(agent_release.router)
    client = TestClient(app)
    pkg = load_package('knowledge.source_classify',root)
    body = {'agent':pkg.ref,'state':'canary','package_sha256':pkg.content_sha256}
    assert client.post('/api/agents/release',json=body).status_code == 403
    result = client.post('/api/agents/release',json=body,headers={'x-ttros-remote-pc':'1','tailscale-user-login':'owner.personal@example.invalid'})
    assert result.status_code == 200
    assert result.json()['promoted']

@pytest.mark.parametrize('change', ['wrong_probe_binding','missing_probe_sources','duplicated_effect','billed_fallback','repeat_job','receipt_hash','checkpoint_hash','missing_attempts','duplicate_attempt'])
def test_gate7_recomputes_negative_probe_and_run_evidence(roots, change):
    root, _ = roots
    path = fx.write_reliability(root,'knowledge.source_classify')
    pkg = load_package('knowledge.source_classify',root)
    data = json.loads(path.read_text())
    source = path.parent/data['provenance']['path']
    bundle = json.loads(source.read_text())
    if change == 'wrong_probe_binding':
        bundle['kill']['source']['job']['result']['steps'][0]['execution_identity']['binding'] = 'other'
    elif change == 'missing_probe_sources': del bundle['kill']['source']
    elif change == 'duplicated_effect': bundle['kill']['source']['job']['effect_attempts'] = [{'key':'E-1'},{'key':'E-1'}]
    elif change == 'billed_fallback': bundle['forced']['source']['ledger'][0]['provider'] = 'billed-api'
    elif change == 'repeat_job': bundle['cases'][0]['runs'][1] = bundle['cases'][0]['runs'][0]
    elif change == 'receipt_hash': bundle['cases'][0]['runs'][0]['receipt'] += 'altered'
    elif change == 'checkpoint_hash': bundle['kill']['checkpoint_sha256'] = '0'*64
    elif change == 'missing_attempts': bundle['kill']['source']['call_attempts'] = []
    elif change == 'duplicate_attempt': bundle['kill']['source']['call_attempts'] *= 3
    source.write_text(json.dumps(bundle))
    data['provenance']['sha256'] = reliability.digest(bundle)
    path.write_text(json.dumps(data))
    assert not release.gate7(pkg,root)['pass']

@pytest.mark.parametrize('journal,passes', [('refused_then_launched', True), ('closed_without_start', False),
                                            ('unknown_status', False)])
def test_gate7_counts_only_launched_model_attempts(roots, journal, passes):
    # A selector refusal before launch journals started + not_launched; only real launches count.
    root, _ = roots
    path = fx.write_reliability(root,'knowledge.source_classify')
    pkg = load_package('knowledge.source_classify',root)
    data = json.loads(path.read_text())
    source = path.parent/data['provenance']['path']
    bundle = json.loads(source.read_text())
    started = bundle['kill']['source']['call_attempts'][0]
    closed = {**started, 'result': {'status': 'not_launched', 'failure_class': 'needs_authentication'}}
    if journal == 'refused_then_launched':
        bundle['kill']['source']['call_attempts'] = [started, closed, started]
    elif journal == 'closed_without_start':
        bundle['kill']['source']['call_attempts'] = [closed, started]
    else:
        bundle['kill']['source']['call_attempts'] = [{**started, 'result': {'status': 'done'}}]
    source.write_text(json.dumps(bundle))
    data['provenance']['sha256'] = reliability.digest(bundle)
    path.write_text(json.dumps(data))
    assert release.gate7(pkg,root)['pass'] is passes

def test_selector_auth_refusal_is_a_provider_stop_everywhere():
    from aos_agents import factory, runtime
    assert 'needs_authentication' in runtime.BLOCKED_FAILURE_CLASSES
    assert 'needs_authentication' in factory.PROVIDER_STOP_CLASSES

def test_sigkill_capture_observes_owned_subprocess_offline(roots):
    import subprocess
    from aos_agents.journal import StepJournal
    root, runtime = roots
    job = fx.add_job(root,runtime,1,('marketing.post_check',))
    journal = StepJournal(runtime,job['id'])
    journal.append(plan_ref='fixture',step='one',agent='marketing.post_check@0.1.0',package_sha256='fixture',input_sha256='fixture',result={'status':'ok'})
    process = subprocess.Popen([sys.executable,'-c','import sys; sys.stdin.read()'],stdin=subprocess.PIPE)
    try:
        captured = reliability.capture_sigkill(process,runtime,job['id'],lambda _:None,simulated=True)
        assert captured['returncode'] == -9
        assert captured['simulated']
        assert captured['source']['job']['id'] == job['id']
        assert captured['checkpoint_before_kill'] == journal.path.read_text()
    finally:
        if process.poll() is None: process.kill()
        process.wait()

def test_quota_job_is_truthfully_blocked_and_never_retried(roots, monkeypatch):
    root, _ = roots
    monkeypatch.setenv(aos_jobs.RELEASE_STATES_ENV,'candidate,shadow,canary,active')
    calls = []
    def port(*args, **kw):
        calls.append(kw)
        return {'status':'failed','failure_class':'quota_unavailable','detail':'forced fixture quota',
                'usage':{'available':False},'invocation_id':'fixture-quota'}
    job, _ = aos_jobs.create(root,conversation_id='fixture',topic='fixture',request='fixture',executor={'kind':'agent',
        'planner':'shared.planner@0.1.0','input':{'request':'research fixture','records':[{'ref':'business_brain:fixture.md','about':'fixture'}],'people':[]}})
    done = aos_jobs.execute(root,job['id'],modelport=port)
    assert done['status'] == 'blocked'
    assert done['failure_class'] == 'quota_unavailable'
    assert len(calls) == 1
    from aos_agents.journal import StepJournal
    attempts = StepJournal(root,job['id']).path.with_suffix('.model-attempts.jsonl')
    assert len(attempts.read_text().splitlines()) == 1
    assert json.loads(attempts.read_text())['result']['status'] == 'started'
    assert done['result']['steps'][0]['execution_identity']['package_sha256']
    text = (root/done['receipts'][0]['path']).read_text()
    assert 'Status: blocked' in text
    assert 'Execution identity: ' in text
