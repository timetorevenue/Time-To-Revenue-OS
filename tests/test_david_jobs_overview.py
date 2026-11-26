"""David answers read-only Jobs questions from the real aos_jobs store.

A Dashboard conversation owns no Jobs, so the conversation-scoped `/jobs` list and the
model prompt (active topic only) left David with nothing to report. The overview reads
every Job, never calls a model, never creates a Job and refuses customer workspaces.
"""
import json
import multiprocessing
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'dashboard/backend')]
import aos_david as david
import aos_jobs as jobs

EXECUTOR = {'kind': 'agent', 'agent': 'shared.planner@0.1.0'}
SEED = [  # conversation, topic, final status, extra fields
    ('telegram-1', 'source-filing', 'running', {'started_at': '2026-11-20T10:00:00+00:00'}),
    ('telegram-1', 'research', 'queued', {}),
    ('dashboard-a', 'website', 'waiting_input', {'detail': 'Which page?'}),
    ('telegram-1', 'source-filing', 'needs_input', {'failure_class': 'planner_no_plan'}),
    ('dashboard-b', 'crm', 'failed', {'failure_class': 'transport', 'detail': 'model unavailable'}),
    ('telegram-1', 'source-filing', 'done', {'finished_at': '2026-11-16T06:50:50+00:00'}),
    ('dashboard-a', 'research', 'done', {'finished_at': '2026-11-18T09:00:00+00:00'}),
    ('telegram-2', 'misc', 'mystery_state', {}),
]


def forbidden(*args, **kwargs):
    raise AssertionError('model invoked for a read-only Jobs question')


def seed(root):
    ids = []
    for n, (conversation, topic, status, extra) in enumerate(SEED):
        job, _ = jobs.create(root, conversation_id=conversation, topic=topic, request=f'Fixture request {n}',
                             executor=EXECUTOR)
        path = next((Path(root) / jobs.JOBS_DIR).glob(jobs.storage_key(job['id']) + '.json'))
        job.update(status=status, **extra)
        path.write_text(json.dumps(job))
        ids.append(job['id'])
    return ids


def snapshot(root):
    return sorted(str(p.relative_to(root)) for p in Path(root).rglob('*') if p.is_file())


@pytest.mark.parametrize('text', [
    'What is the current status of my Jobs? Read only, do not start anything.',
    'what jobs are running?', 'Any failed jobs?', 'Show me the job status overview',
    "how's the job queue looking", 'Which jobs are blocked or stuck?', 'list my recent completed jobs',
])
def test_status_questions_route_to_overview(text):
    assert david.jobs_overview_question(text)


@pytest.mark.parametrize('text', [
    'Start a job to research callbacks', 'Retry the failed job', 'Create a new job for the website',
    'run the jobs again', 'cancel all queued jobs', 'jobs', '/jobs', 'job AOS-2026-10002',
    'What is the status of the website redesign?', 'Please resume the waiting job',
    'What happened with the filing job?', 'is the research job done?',
])
def test_requests_to_act_or_other_routes_do_not(text):
    assert not david.jobs_overview_question(text)


def test_overview_reports_every_conversation_with_real_states(tmp_path):
    ids = seed(tmp_path)
    before = snapshot(tmp_path)
    agent = david.David(tmp_path, model=forbidden, warm=False)
    reply = agent.turn('dashboard-new', 'What is the current status of my Jobs? Read only, do not start anything.')
    text = reply['response']
    assert reply['success'] and reply['model_calls'] == 0 and reply['action'] == 'jobs_overview'
    assert reply['token_usage']['total_tokens'] == 0
    assert reply['job_total'] == len(SEED)
    assert reply['job_counts'] == {'Running': 1, 'Queued': 1, 'Paused, waiting for your answer': 1,
                                   'Blocked or ended without running': 1, 'Failed': 1, 'Completed': 2, 'Other': 1}
    for job_id in ids:  # every Job is reported, including other conversations and an unknown state
        assert job_id in text
    assert 'reason: planner_no_plan' in text and 'reason: transport' in text
    # Most recent completion first.
    assert text.index(ids[6]) < text.index(ids[5])
    assert 'Nothing was started or changed' in text
    # Read-only: no Job, no conversation state, no file of any kind.
    assert snapshot(tmp_path) == before
    assert len(jobs.list_jobs(tmp_path, limit=None)) == len(SEED)


def test_empty_store_is_reported_not_invented(tmp_path):
    reply = david.David(tmp_path, model=forbidden, warm=False).turn('dashboard-x', 'any failed jobs?')
    assert reply['job_total'] == 0 and 'Failed (0): none' in reply['response']
    assert 'Running (0): none' in reply['response']


def test_customer_workspace_is_denied_and_operator_allowed(tmp_path):
    ids = seed(tmp_path)
    agent = david.David(tmp_path, model=forbidden, warm=False)
    customer = (object(), {'user': 'u-1', 'workspace': 'ws-acme', 'method': 'google_oidc'})
    denied = agent.turn('connections-ws-acme', 'what jobs are running?', connection_context=customer)
    assert denied.get('denied') is True and denied['model_calls'] == 0
    assert not any(job_id in denied['response'] for job_id in ids)
    operator = (object(), {'user': 'op', 'workspace': 'ttros-operator', 'method': 'cloudflare_access'})
    allowed = agent.turn('connections-ttros-operator', 'what jobs are running?', connection_context=operator)
    assert allowed['job_total'] == len(SEED) and ids[0] in allowed['response']


def test_conversation_scoped_jobs_command_is_unchanged(tmp_path):
    ids = seed(tmp_path)
    reply = david.David(tmp_path, model=forbidden, warm=False).turn('dashboard-a', 'jobs')
    assert reply['action'] == 'jobs'
    assert ids[2] in reply['response'] and ids[0] not in reply['response']


def _http_child(root, pipe):
    """The real backend app and ask-david route, without a server, runner recovery or model."""
    import main as backend
    from fastapi.testclient import TestClient
    backend.BASE_DIR = Path(root)
    backend.start_david(model=forbidden, warm=False, recover=False)
    client = TestClient(backend.app)
    response = client.post('/api/dashboard/ask-david', json={
        'conversation_id': 'dashboard-http', 'delivery_id': '',
        'text': 'What is the current status of my Jobs? Read only, do not start anything.'})
    data = response.json()
    if data.get('kind') == 'david_job':
        import time
        for _ in range(200):
            polled = client.get('/api/dashboard/ask-david/jobs/' + data['job_id']).json()
            if polled['status'] != 'running':
                data = polled.get('result') or polled
                break
            time.sleep(.02)
    listed = client.get('/api/david/jobs?limit=100').json()
    backend.RUNNER.shutdown(wait=True)
    backend.stop_david()
    pipe.send({'status': response.status_code, 'reply': data, 'listed': [job['id'] for job in listed['jobs']]})


def test_dashboard_ask_david_http_route_answers_from_jobs_store(tmp_path):
    ids = seed(tmp_path)
    ctx = multiprocessing.get_context('spawn')
    parent, child = ctx.Pipe()
    proc = ctx.Process(target=_http_child, args=(str(tmp_path), child))
    proc.start()
    try:
        assert parent.poll(60), 'backend child did not answer'
        result = parent.recv()
    finally:
        proc.join(20)
    assert result['status'] == 200
    reply = result['reply']
    assert reply['action'] == 'jobs_overview' and reply['model_calls'] == 0
    assert all(job_id in reply['response'] for job_id in ids)
    # Same Jobs the Dashboard Jobs view lists; the question created none.
    assert sorted(result['listed']) == sorted(ids)
