"""Offline integrated acceptance: real writers, HTTP, M1/M5, SIGKILL and ledger.

Only ModelPort/optional embedding-graph publication are stand-ins. No Telegram,
production service, network provider, release change or canonical Brain mutation.
"""
import datetime as dt
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading
import time
import uuid
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'tools'),str(ROOT/'dashboard/backend')]
import aos_david as david
import aos_jobs as jobs
import aos_entity_index as entities
import aos_indexer as indexer
import shared_brain_read as reader
import brain_memory
from tests.new_david_fixture import corpus
from tests import test_david_m1 as support

FIXTURE_HEADER='---\nid: fixture-callback-notes\ntype: knowledge\n---\n'


@pytest.fixture
def evidence(tmp_path):
    with corpus(tmp_path) as fixture:
        yield fixture


@pytest.mark.parametrize('after,before,expected',[
    ('2026-11-15','2026-11-15',{'current','proposal'}),
    ('2026-11-14','2026-11-14',{'yesterday','ambiguous'}),
    ('2026-10-25','2026-10-31',{'weeks'}),
    ('2026-08-01','2026-08-31',{'old'}),
])
def test_temporal_filters_before_pagination_and_authority(evidence,after,before,expected):
    found=reader.search('',after=after,before=before)
    assert found['success']
    assert {Path(r['reference']).stem for r in found['matches']}==expected
    assert all(r['date_basis']!='imported_at' for r in found['matches'])
    assert 'PRIVATE_CLIENT_SENTINEL' not in json.dumps(found)
    for row in found['matches']:
        assert row['source_refs'] and row['reference'].startswith('business_brain:')
        if row['status'] in {'superseded','unconfirmed'}:
            assert row['authority']!='canonical'
    page=reader.search('',after=after,before=before,limit=1)
    if len(expected)>1:
        second=reader.search('',after=after,before=before,limit=1,offset=page['next_offset'])
        assert page['matches'][0]['reference'] != second['matches'][0]['reference']


def test_entities_relationships_disambiguation_and_review_state(evidence):
    alex=reader.entity('Alex')
    assert alex['resolution']=='ambiguous'
    ids={e['entity_id'] for e in alex['entities']}
    assert {'person:alex-rivera','person:alex-rivers'} <= ids
    found=reader.search('',after='2026-11-14',before='2026-11-14',entity_id='person:alex-rivera')
    assert [Path(r['reference']).stem for r in found['matches']]==['yesterday']
    view=reader.entity('person:alex-rivera')['view']
    assert any(r['entity_id']=='project:callback-pilot' for r in view['related'])
    accepted=reader.search('Callback',status='confirmed')
    assert [Path(r['reference']).stem for r in accepted['matches']]==['current']
    assert not reader.search('',after='2026-02-30')['success']
    assert not reader.search('',after='2026-12-01',before='2026-01-01')['success']


def test_representative_historical_icm_sources_retrieve_without_invented_event_dates(evidence,tmp_path):
    vault,db,registry=evidence
    samples=json.loads((ROOT/'tests/fixtures/new_david_historical_samples.json').read_text())['records']
    data=json.loads((tmp_path/'scope.json').read_text())
    for row in samples:
        assert hashlib.sha256(row['content'].encode()).hexdigest()==row['sha256']
        target=vault/row['reference'].removeprefix('business_brain:')
        target.parent.mkdir(parents=True,exist_ok=True);target.write_text(row['content'])
        data['scopes']['global']['brain_pointers'].append(row['reference'])
        data['scopes']['global']['search_source_identities'][0]['paths'].append(row['reference'])
    (tmp_path/'scope.json').write_text(json.dumps(data))
    for row in samples:
        assert indexer.index_one(str(vault/row['reference'].removeprefix('business_brain:')))['status']=='success'
    for sample in samples:
        found=reader.search(sample['query'],hybrid=False)
        row=next(r for r in found['matches'] if r['reference']==sample['reference'])
        assert row['authority']=='historical_evidence'
        assert row['source_record'].startswith('business_brain:sources/intake/records/')
        assert len(row['source_sha256'])==64
        assert not row['date'], 'undated external advice must not become an event on its write/import day'
        assert not any(r['reference']==sample['reference'] for r in reader.search(sample['query'],after='2026-11-01',before='2026-11-30')['matches'])


def test_stale_source_and_changed_scope_refuse_before_open(evidence,tmp_path,monkeypatch):
    vault,db,registry=evidence
    target=vault/'decisions/current.md'
    target.write_text(target.read_text()+'\nA new unindexed claim.\n')
    assert not reader.read('business_brain:decisions/current.md')['success']
    assert all(not r['reference'].endswith('current.md') for r in reader.search('Callback')['matches'])
    assert indexer.index_one(str(target))['status']=='success'
    assert reader.read('business_brain:decisions/current.md')['success']
    data=json.loads((tmp_path/'scope.json').read_text())
    data['denied_brain_pointers'].append('business_brain:decisions/current.md')
    (tmp_path/'scope.json').write_text(json.dumps(data))
    real=Path.open
    def guarded(path,*args,**kwargs):
        assert path!=target, 'denied source opened'
        return real(path,*args,**kwargs)
    with patch.object(Path,'open',guarded):
        assert not reader.read('business_brain:decisions/current.md')['success']


def test_retirement_navigation_is_fresh_data_and_refuses_reactivation(evidence):
    vault,db,registry=evidence
    (vault/'HOME.md').write_text('# Fixture navigation root\n')
    (vault/'index').mkdir()
    history=vault/'index/HISTORY.md'
    history.write_text('---\nretired_paths: [decisions/current.md]\nretired_prefixes: []\n---\n# Historical records\n')
    assert all(not r['reference'].endswith('current.md') for r in reader.search('Callback')['matches'])
    row=next(r for r in reader.search('Callback',include_history=True)['matches'] if r['reference'].endswith('current.md'))
    assert row['authority']=='retired_history'
    assert not entities.classify('decisions/current.md',{'knowledge_type':'explicit_decision'},'',vault_root=vault)[3]
    original=(vault/'decisions/current.md').read_bytes()
    with pytest.raises(brain_memory.BrainMemoryError,match='retired original'):
        brain_memory.write_transaction({'decisions/current.md':original.decode()},source='fixture',session_id='retired-refusal')
    assert (vault/'decisions/current.md').read_bytes()==original
    # A second call sees new tombstones immediately; there is no cross-request cache.
    history.write_text('---\nretired_paths: [decisions/current.md, decisions/proposal.md]\nretired_prefixes: []\n---\n')
    assert all(not r['reference'].endswith(('current.md','proposal.md')) for r in reader.search('Callback')['matches'])
    history.write_text('malformed navigation')
    with pytest.raises(brain_memory.BrainMemoryError,match='refusing historical reactivation'):
        reader.search('Callback')


def test_checked_schema_cache_never_caches_permissions_or_schema_freshness(evidence,tmp_path):
    from business_brain_scope import ClientScopeRegistry,ClientScopeError,DEFAULT_SCHEMA_PATH
    registry_path=tmp_path/'scope.json'
    schema_path=tmp_path/'scope.schema.json'
    schema=json.loads(DEFAULT_SCHEMA_PATH.read_text())
    schema_path.write_text(json.dumps(schema))
    def load():return ClientScopeRegistry(registry_path=registry_path,schema_path=schema_path)
    load().validate_brain_pointer('global','business_brain:decisions/current.md')
    with pytest.raises(ClientScopeError):
        load().validate_brain_pointer('global','business_brain:clients/other.md')
    data=json.loads(registry_path.read_text())
    data['scopes']['global']['enabled']=False
    registry_path.write_text(json.dumps(data))
    with pytest.raises(ClientScopeError,match='disabled'):load().resolve_scope('global')
    data['scopes']['global']['enabled']=True
    registry_path.write_text(json.dumps(data))
    schema['required'].append('new_required_fixture_field')
    schema_path.write_text(json.dumps(schema))
    with pytest.raises(ClientScopeError,match='new_required_fixture_field'):load()
    schema_path.write_text('{invalid schema')
    with pytest.raises(ClientScopeError):load()
    schema_path.write_text('{"type":"not-a-valid-schema-type"}')
    with pytest.raises(ClientScopeError):load()
    schema_path.write_text(DEFAULT_SCHEMA_PATH.read_text())
    registry_path.write_text('{invalid registry')
    with pytest.raises(ClientScopeError):load()


def test_nightly_hygiene_has_no_executable_historical_release_gate(evidence,tmp_path):
    import nightly_knowledge_hygiene as hygiene
    from types import SimpleNamespace
    vault,db,registry=evidence
    (vault/'operating_context').mkdir()
    (vault/'inbox').mkdir()
    open_note='---\nid: fixture-open-loops\ntype: knowledge\n---\n# Open loops\n\n## One Brain release\n\n- Revenue Gate must prove restart recall, automatic canonical/activity retrieval\n'
    (vault/'operating_context/open_loops.md').write_text(open_note)
    (vault/'inbox/contradictions.md').write_text('---\nid: fixture-contradictions\ntype: knowledge\n---\n# Contradictions\n')
    proof=tmp_path/'proofs/unbound-hermes-one-brain-2026-08-04/REVENUE_GATE.md'
    proof.parent.mkdir(parents=True)
    proof.write_text('Verdict: **PASS** for Steps 0–3 and the Revenue Gate.')
    original=proof.read_bytes()
    real=Path.read_bytes
    def protected(path):
        assert path!=proof,'historical execution gate consulted by live maintenance'
        return real(path)
    with patch.object(hygiene,'detect',return_value=SimpleNamespace(findings=[])), \
         patch.object(Path,'read_bytes',protected):
        first=hygiene.build_plan(root=tmp_path,brain_root=vault,registry=registry)
        proof.write_text('Changed historical gate text; must have no runtime effect.')
        second=hygiene.build_plan(root=tmp_path,brain_root=vault,registry=registry)
    assert first.documents=={} and second.documents=={}
    assert first.source_state_sha256==second.source_state_sha256
    assert not first.changes and not second.changes
    assert (vault/'operating_context/open_loops.md').read_text()==open_note
    assert not hasattr(hygiene,'_reconcile_closed_release_gate')
    assert not hasattr(hygiene,'REVENUE_GATE_PROOF')
    assert original.startswith(b'Verdict:')  # original is fixture-only; repository proof is preserved


def test_example_city_days_calendar_months_and_fast_history(evidence,tmp_path):
    assert entities._resolve_date('source.md','source',{'event_date':'2026-11-15T02:00:00Z'})[0]=='2026-11-14'
    assert david.history_window('today',dt.datetime(2026,11,15,2,tzinfo=dt.timezone.utc))==('2026-11-14','2026-11-14')
    assert david.history_window('three months ago',dt.datetime(2026,11,15,12,tzinfo=dt.timezone.utc))==('2026-08-01','2026-08-31')
    def forbidden(*args,**kwargs):raise AssertionError('model invoked on factual fast path')
    agent=david.David(tmp_path,model=forbidden,threads=support.Threads())
    answer=agent.turn('offline-1','/history 2026-11-15 Callback')
    assert answer['model_calls']==0
    assert 'unconfirmed' in answer['response'] and 'confirmed' in answer['response']
    assert len(answer['evidence'])==2
    named=agent.turn('offline-1','/history 2026-11-14 with Alex Rivera')
    assert [Path(r['reference']).stem for r in named['evidence']]==['yesterday']
    ambiguous=agent.turn('offline-1','/history 2026-11-14 with Alex')
    assert ambiguous['action']=='disambiguate' and ambiguous['model_calls']==0
    old=agent.turn('offline-1','/history 2026-08-01..2026-08-31 Callback')
    assert 'Superseded by: business_brain:decisions/current.md' in old['response']
    missing=agent.turn('offline-1','/history 2025-01-01 Callback')
    assert 'does not prove' in missing['response']


def git(root,*args):
    return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()


def test_supported_submit_confirm_index_retrieve_keeps_original_and_working_memory(evidence,tmp_path,monkeypatch):
    import shared_brain_submit as submit
    import shared_brain_confirm as confirm
    import aos_embeddings
    import brain_graph_links
    vault,db,registry=evidence
    data=json.loads((tmp_path/'scope.json').read_text())
    data['scopes']['global']['brain_pointer_prefixes']=['business_brain:inbox/distilled_packets/']
    data['scopes']['global']['search_source_identities'].append({'source':'business_brain','path_prefix':'business_brain:inbox/distilled_packets/'})
    (tmp_path/'scope.json').write_text(json.dumps(data))
    subprocess.run(['git','init','-b','main',str(vault)],capture_output=True,check=True)
    git(vault,'add','.')
    git(vault,'-c','user.name=Fixture','-c','user.email=fixture@local','commit','-m','fixture source originals')
    git(vault,'update-ref','refs/remotes/origin/main','HEAD')
    monkeypatch.setenv('TTROS_BRAIN_CLOSURE_STATE',str(tmp_path/'closure.json'))
    monkeypatch.setenv('TTROS_CODEX_NO_PUSH','1')
    monkeypatch.setattr(aos_embeddings,'safe_refresh',lambda:None)
    monkeypatch.setattr(brain_graph_links,'after_brain_change',lambda *a,**kw:None)
    original=(vault/'sources/yesterday.md').read_bytes()
    attribution={'authenticated_identity':'fixture-operator','surface':'david','actor_class':'authorised_client','surface_source':'local-cli'}
    result=submit.submit('decision','Callback fixture accepted decision','Callback fixture decision: one hour.',
        ['business_brain:sources/yesterday.md','https://example.org/original'], 'callback-fixture-20261115',attribution=attribution)
    assert result['success'] and result['status']=='unconfirmed' and result['index_status']=='refreshed',result
    before=reader.search('Callback fixture decision',hybrid=False)
    row=next(r for r in before['matches'] if r['reference']==result['reference'])
    assert row['status']=='unconfirmed' and row['authority']!='canonical'
    pending=(vault/result['reference'].removeprefix('business_brain:')).read_text()
    checked=confirm.confirm(result['record_id'],attribution=attribution)
    assert checked['success'] and checked['status']=='confirmed' and checked['index_status']=='refreshed',checked
    assert confirm.confirm(result['record_id'])['already_confirmed']
    after=reader.search('Callback fixture decision',status='confirmed')
    row=next(r for r in after['matches'] if r['reference']==result['reference'])
    assert row['status']=='confirmed' and row['authority']!='canonical'  # confirmation is not promotion
    assert row['source_refs']==['business_brain:sources/yesterday.md','https://example.org/original']
    accepted=(vault/result['reference'].removeprefix('business_brain:')).read_text()
    from brain_memory import parse_frontmatter
    oldfm,oldbody=parse_frontmatter(pending);newfm,newbody=parse_frontmatter(accepted)
    assert oldbody==newbody and oldfm['payload_fingerprint']==newfm['payload_fingerprint']
    assert (vault/'sources/yesterday.md').read_bytes()==original
    agent=david.David(tmp_path,threads=david.BrainThreads(),model=lambda *a,**k:None)
    assert not agent.write_handoff('offline-1','callbacks',{'decisions':'Working discussion only','open_questions':'Confirm recipient'},session_id='fixture')
    note=agent._read_handoff('offline-1','callbacks')
    assert 'Working discussion only' in note
    assert not git(vault,'ls-files','sessions/')
    assert result['sync_status']=='pending' and checked['sync_status']=='pending'


def test_continuity_failures_do_not_replace_and_topics_stay_scoped(tmp_path):
    threads=support.Threads()
    port=support.Port(david=[support.reply(reply='Pricing confirmed in conversation.',topic='pricing',handoff={'decisions':'Per project','open_questions':'Who signs?'}),
        support.reply(reply='Other topic.',topic='dentist',handoff={'goal':'UNRELATED_TOPIC_SENTINEL'}),
        support.reply(reply='Pricing continues.',topic='pricing')])
    agent=david.David(tmp_path,model=port,threads=threads)
    agent.turn('offline-1','/topic pricing Set pricing')
    agent.turn('offline-1','/topic dentist dentist')
    restarted=david.David(tmp_path,model=port,threads=threads)
    answer=restarted.turn('offline-1','Back to pricing please')
    prompt=port.prompts[-1][2]
    assert 'Who signs?' in prompt and 'Per project' in prompt
    assert 'UNRELATED_TOPIC_SENTINEL' not in prompt and 'Other topic.' not in prompt
    assert answer['context'][0]['bytes']<=david.CONTEXT_MAX_BYTES
    identity=agent.handoff_identity('offline-1','pricing')
    original=threads.notes[identity]
    assert agent.write_handoff('offline-1','pricing',{'done':'x'*3000},session_id='too-large')
    assert threads.notes[identity]==original
    with patch.object(threads,'read',side_effect=OSError('interrupted read')):
        assert agent.write_handoff('offline-1','pricing',{'done':'new'},session_id='read-failure')
    assert threads.notes[identity]==original
    port.david=[lambda prompt:{'status':'ok','json':support.reply(reply='partial',topic='pricing',handoff={'decisions':'bad'}),'finish_reason':'length','usage':support.usage()}]
    failed=restarted.turn('offline-1','/topic pricing continue')
    assert not failed['success'] and threads.notes[identity]==original
    assert not agent._read_handoff('offline-2','pricing')
    # A successful later merge retains unresolved questions; no new memory authority.
    assert not agent.write_handoff('offline-1','pricing',{'done':'Complete'},session_id='later')
    assert 'Who signs?' in threads.notes[identity]
    path=agent._state_path('offline-1')
    path.write_text('{incomplete state')
    with pytest.raises(ValueError,match='saved state retained'):
        restarted.turn('offline-1','continue')
    assert path.read_text()=='{incomplete state'
    assert threads.notes[identity].startswith('Topic: pricing')


class MeteredPort(support.Port):
    """Scripted responses, real canonical ledger. Can stop after metering before journaling."""
    def __init__(self,*args,barrier=None,blocked=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.barrier,self.blocked=barrier,blocked

    def __call__(self,prompt,**kw):
        # The retained recording predates the fixture's stable note-ID frontmatter.
        # Match its reply at the fake provider boundary; the real runtime receives
        # the complete scoped note, and quote/citation verification stays real.
        result=super().__call__(prompt.replace(FIXTURE_HEADER,''),**kw)
        result['invocation_id']='offline-'+uuid.uuid4().hex
        item=kw.get('work_item_id') or ''
        if item:
            from step6_cost_control import Scope,record_invocation
            result['ledger']=record_invocation(Scope('session',result['invocation_id']),
                invocation_id=result['invocation_id'],provider='fixture',model=result['model'],usage=result['usage'],
                root=kw['root'],work_item_id=item,surface='offline-new-david')
            from aos_queue_storage import durable_append_text
            durable_append_text(Path(kw['root']),Path(kw['root'])/'context-measurements.jsonl',json.dumps({
                'invocation_id':result['invocation_id'],'job_id':item,'agent':kw['agent'],
                'bytes':len(prompt.encode()),'estimated_tokens':(len(prompt.encode())+3)//4})+'\n')
        if self.barrier is not None and kw.get('agent','').startswith('research.synthesise'):
            self.blocked.put(item)
            self.barrier.wait(30)
        return result


def backend_process(root,pipe,barrier,blocked):
    """Actual backend wiring/HTTP in a disposable child; no server/listening socket."""
    import main as backend
    from fastapi.testclient import TestClient
    backend.BASE_DIR=Path(root)
    backend.JOB_WORKERS=6
    backend.ASK_DAVID_INLINE_WAIT_SECONDS=.05
    port=MeteredPort(barrier=barrier,blocked=blocked)
    def dynamic(prompt,**kw):
        if kw.get('agent')=='david':
            message=prompt.rsplit("Casey's message:\n",1)[-1]
            marker=message.split()[-1]
            if 'Lookup results' not in prompt:
                return port._ok(support.reply(action='lookup',query='callbacks',topic=marker),'david')
            return port._ok(support.reply(action='delegate',reply='Research queued.',topic=marker,
                request='Research callback speed '+marker,records=[support.NOTES]),'david')
        return port(prompt,**kw)
    # Runtime shared Brain thread writer is the actual interface, rooted in the fixture Git Brain.
    with patch.object(brain_memory,'VAULT_ROOT',Path(root)/'brain'):
        from business_brain_context import ScopedBrainLoader
        from tests.business_brain_test_support import make_registry,registry_data
        data=registry_data()
        data['scopes']['global']['brain_pointers']=[support.NOTES]
        registry=make_registry(data)
        loader=ScopedBrainLoader(registry=registry,vault_root=Path(root)/'brain')
        def scoped_read(scope,pointer):
            result=loader.retrieve(work={'client_scope':scope},pointers=[pointer])
            return {'content':result.contents[0],'provenance':result.brain_context_used[0]}
        report=backend.start_david(model=dynamic,search=support.search,threads=david.BrainThreads(),modelport=dynamic,
            brain_read=scoped_read,perform=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('unexpected effect')),
            warm=False)
        client=TestClient(backend.app)
        pipe.send({'ready':True,'recovery':report})
        while True:
            request=pipe.recv()
            if request['method']=='stop':
                backend.RUNNER.shutdown(wait=True);backend.stop_david();pipe.send({'stopped':True});break
            if request['method']=='get':response=client.get(request['path'])
            else:response=client.post(request['path'],json=request['body'])
            pipe.send({'status':response.status_code,'json':response.json()})


def test_three_conversations_six_jobs_http_sigkill_restart_exact_lifetime(tmp_path,monkeypatch):
    # Existing packages are copied by reference only; release override is confined to this process tree.
    (tmp_path/'agents').mkdir()
    for name in ('packages','plans','tap.schema.json'):
        (tmp_path/'agents'/name).symlink_to(ROOT/'agents'/name)
    support.write_fixture_registry(tmp_path/'agents/registry.lock.json')
    (tmp_path/'queue').mkdir()
    (tmp_path/'queue/model_routes.json').write_bytes((ROOT/'queue/model_routes.json').read_bytes())
    (tmp_path/'queue/notifications.json').write_text('{"allowlist":{"telegram":[]}}')
    vault=tmp_path/'brain';vault.mkdir()
    (vault/'research').mkdir()
    (vault/'research/callback-notes.md').write_text(FIXTURE_HEADER+support.BRAIN[support.NOTES])
    subprocess.run(['git','init','-b','main',str(vault)],capture_output=True,check=True)
    (vault/'README.md').write_text('# Fixture working Brain\n')
    git(vault,'add','README.md');git(vault,'-c','user.name=Fixture','-c','user.email=fixture@local','commit','-m','fixture')
    monkeypatch.setenv(jobs.RELEASE_STATES_ENV,'candidate,shadow,canary,active')
    monkeypatch.setenv('TTROS_BRAIN_CLOSURE_STATE',str(tmp_path/'closure.json'))
    ctx=multiprocessing.get_context('spawn')
    barrier=ctx.Event();blocked=ctx.Queue();parent,child=ctx.Pipe()
    proc=ctx.Process(target=backend_process,args=(str(tmp_path),child,barrier,blocked))
    def rpc(method,path='',body=None):
        parent.send({'method':method,'path':path,'body':body})
        assert parent.poll(20),'offline backend did not respond'
        return parent.recv()
    def answer(conversation,text,delivery):
        response=rpc('post','/api/dashboard/ask-david',{'conversation_id':conversation,'text':text,'delivery_id':delivery})
        assert response['status']==200,response
        data=response['json']
        deadline=time.monotonic()+15
        while data.get('kind')=='david_job':
            polled=rpc('get','/api/dashboard/ask-david/jobs/'+data['job_id'])['json']
            if polled['status']=='done':return polled['result']
            assert time.monotonic()<deadline
            time.sleep(.01)
        return data
    proc.start()
    try:
        assert parent.poll(20) and parent.recv()['ready']
        ids=[];bodies=[];latencies=[]
        for round_ in range(2):
            for n in range(3):
                body=(f'offline-{n}',f'Research callback job topic-{n}-{round_}',f'offline-delivery-{n}-{round_}')
                start=time.perf_counter();result=answer(*body);latencies.append((time.perf_counter()-start)*1000)
                ids.append(result['job_id']);bodies.append(body)
        assert len(set(ids))==6
        assert {blocked.get(timeout=15) for _ in range(6)}==set(ids)
        assert {jobs.load(tmp_path,i)['status'] for i in ids}=={'running'}
        # David remains responsive while all six specialists wait at the interrupted call boundary.
        start=time.perf_counter();status=answer('offline-0','job '+ids[0],'short-status');short_ms=(time.perf_counter()-start)*1000
        assert status['model_calls']==0 and ids[0] in status['response']
        assert 'is running' in status['response'] and 'failed' not in status['response']
        assert short_ms<1000
        assert ids[1] not in answer('offline-0','jobs','short-list')['response']
        os.kill(proc.pid,signal.SIGKILL);proc.join(10)
        assert proc.exitcode==-signal.SIGKILL
        parent,child=ctx.Pipe()
        proc=ctx.Process(target=backend_process,args=(str(tmp_path),child,None,blocked));proc.start()
        assert parent.poll(20)
        restarted=parent.recv()
        assert set(restarted['recovery']['requeued'])==set(ids)
        # Redelivery returns the completed conversational receipt; no second allocation/model turn.
        for original,body in zip(ids,bodies):assert answer(*body)['job_id']==original
        deadline=time.monotonic()+20
        while True:
            completed=[jobs.load(tmp_path,i) for i in ids]
            if all(j['status']=='done' and (j.get('notification') or {}).get('status')=='unavailable' for j in completed):break
            assert time.monotonic()<deadline,[(j['status'],j.get('detail')) for j in completed]
            time.sleep(.02)
        from step6_cost_control import work_item_usage
        ledger=[json.loads(line) for line in (tmp_path/'queue/token_ledger.jsonl').read_text().splitlines()]
        context_rows=[json.loads(line) for line in (tmp_path/'context-measurements.jsonl').read_text().splitlines()]
        totals={}
        for index,job in enumerate(completed):
            assert job['attempts']==2
            assert job['conversation_id']==bodies[index][0]
            assert not jobs.verify_artifacts(tmp_path,job['artifacts'])[1]
            for receipt in job['receipts']:
                assert hashlib.sha256((tmp_path/receipt['path']).read_bytes()).hexdigest()==receipt['sha256']
            aggregate=work_item_usage(job['id'],root=tmp_path)
            rows=[r for r in ledger if r['item_id']==job['id']]
            assert aggregate['invocation_count']==3 # planner + killed, metered synth + replacement synth
            assert aggregate['unknown_invocations']==0
            assert aggregate['known_tokens']==sum(r['fuse']['invocation_tokens'] for r in rows)
            assert job['accounting']==aggregate
            assert job['result']['steps'][0]['replayed']
            assert job['notification']['handoff']['written']
            assert [row['path'] for row in job['brain_context_used']]==[support.NOTES]
            journal=[json.loads(line) for line in (tmp_path/'queue/threads'/f"{job['id']}.jsonl").read_text().splitlines()]
            for step in journal:
                for ref in step.get('result',{}).get('evidence_refs',[]):
                    if ref['ref'].startswith('business_brain:'):assert ref['ref']==support.NOTES
            assert all(row['bytes']<16000 for row in context_rows if row['job_id']==job['id'])
            check=answer(job['conversation_id'],'tokens '+job['id'],'tokens-'+job['id'])
            assert check['accounting']==aggregate
            assert len(list((tmp_path/'queue/jobs').glob('AOS-*.json')))==6
            note=(vault/brain_memory.thread_relative(david.David(tmp_path).handoff_identity(job['conversation_id'],job['topic']))).read_text()
            assert job['id'] in note
            assert all(other['id'] not in note for other in completed if other['conversation_id']!=job['conversation_id'])
            totals[job['id']]=aggregate
        # No actual outbound transport; unavailable is durable and never misreported as sent.
        assert not any(j.get('effects') for j in completed)
        evidence={'conversations':3,'simultaneous_jobs':6,'kill':'SIGKILL after synth ledger append before step journal',
            'short_status_ms':short_ms,'ack_ms':latencies,'job_accounting':totals,
            'notifications':{j['id']:j['notification'] for j in completed},'provider':'scripted/recorded stand-in',
            'context_measurements':context_rows,'context_token_method':'UTF-8 bytes / 4 estimate; exact invocation usage in ledger',
            'artifacts':{j['id']:j['artifacts'] for j in completed}}
        output=os.environ.get('TTROS_ACCEPTANCE_EVIDENCE')
        if output:Path(output).write_text(json.dumps(evidence,indent=2)+'\n')
        assert rpc('stop')['stopped']
        proc.join(15);assert proc.exitcode==0
        # Restart a completed backend too: no reallocation, step execution or resend.
        parent,child=ctx.Pipe()
        proc=ctx.Process(target=backend_process,args=(str(tmp_path),child,None,blocked));proc.start()
        assert parent.poll(20) and not parent.recv()['recovery']['requeued']
        for n in range(3):
            state=json.loads(david.David(tmp_path)._state_path(f'offline-{n}').read_text())
            assert set(state['topics'])=={f'topic-{n}-0',f'topic-{n}-1'}
            assert len(state['recent'])%2==0
            status=answer(f'offline-{n}','jobs',f'completed-restart-{n}')
            assert status['model_calls']==0
            assert all(job['id'] not in status['response'] for job in completed if job['conversation_id']!=f'offline-{n}')
        assert len((tmp_path/'queue/token_ledger.jsonl').read_text().splitlines())==len(ledger)
        evidence['completed_backend_restart_verified']=True
        if output:Path(output).write_text(json.dumps(evidence,indent=2)+'\n')
        assert rpc('stop')['stopped']
        proc.join(15);assert proc.exitcode==0
    finally:
        if proc.is_alive():proc.kill();proc.join(10)
