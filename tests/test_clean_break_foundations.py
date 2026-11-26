"""Offline business foundations: fixture-only sources, models and delivery transports."""
import concurrent.futures
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest

from tools import aos_capture_live as gmail, aos_morning_brief as brief, david_ingress
from tools.aos_capture import CaptureStorage, CaptureEnvelope, DeltaBatch, capture_document
from tests.business_brain_test_support import make_registry, registry_data, write_note
import aos_jobs
import morning_brief_detector as detector

NOW = dt.datetime(2026, 11, 15, 16, tzinfo=dt.timezone.utc)


def registry():
    data = registry_data()
    for scope, sender in [('global', 'operator@example.org'), ('client-a', 'client@example.org')]:
        data['scopes'][scope]['capture_identities'] = [dict(match_type='sender',
            value_sha256=hashlib.sha256(sender.encode()).hexdigest())]
        data['scopes'][scope]['capture_evidence_prefixes'] = [f'capture:{scope}:']
        data['scopes'][scope]['search_source_identities'].append(
            dict(source='capture_metadata', path_prefix=f'capture:{scope}:'))
    return make_registry(data)


def envelope(sender='operator@example.org', message='one'):
    return CaptureEnvelope('gmail_composio', message, 'thread-one', '10', NOW.isoformat(),
                           sender, 'Please proceed with the proposed plan')


def storage(root):
    return CaptureStorage(root / 'capture/runtime', repo_root=root)


def test_capture_replay_scoped_projection_and_no_jobs(tmp_path):
    gate = registry()
    batch = DeltaBatch('10', (envelope(), envelope('client@example.org', 'two'),
                             envelope('unknown@example.org', 'three')))
    first = gmail.capture_batch(storage(tmp_path), batch, registry=gate)
    again = gmail.capture_batch(storage(tmp_path), batch, registry=gate)
    assert first['records_added'] == 2 and again['records_added'] == 0
    assert again['records_deduplicated'] == 2 and first['records_excluded'] == 1
    assert first['records_quarantined'] == 1 and not first['content_opened']
    assert not (tmp_path / 'capture/runtime/scopes/client-a').exists()
    excluded = [r for r in storage(tmp_path).processing_rows(gmail.PROVIDER_KEY) if r.get('captured') is False]
    assert len(excluded) == 1 and excluded[0]['provider_message_id'] == 'two'
    assert 'subject' not in excluded[0]
    row = storage(tmp_path).metadata_rows()[0]
    document = capture_document(row, registry=gate)
    assert document['body'] == '' and document['client_scope'] == 'global'
    assert row.subject_classification == 'inbound_claimed_approval'
    assert aos_jobs.list_jobs(tmp_path) == []
    evidence = list((tmp_path / 'capture/gmail').rglob('*.json'))
    assert len(evidence) == 2
    assert all(json.loads(p.read_text())['external_actions'] == 0 for p in evidence)
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in evidence)


def test_capture_crash_replays_raw_before_cursor_and_rejects_regression(tmp_path, monkeypatch):
    store = storage(tmp_path)
    batch = DeltaBatch('10', (envelope(),))
    original = store.append_derived
    monkeypatch.setattr(store, 'append_derived', Mock(side_effect=RuntimeError('fixture interruption')))
    with pytest.raises(RuntimeError):
        gmail.capture_batch(store, batch, registry=registry())
    assert store.cursor(gmail.PROVIDER_KEY) is None
    monkeypatch.setattr(store, 'append_derived', original)
    recovered = gmail.capture_batch(store, batch, registry=registry())
    assert recovered['records_deduplicated'] == 1 and recovered['cursor_advanced']
    with pytest.raises(gmail.CaptureError, match='regressing'):
        gmail.capture_batch(store, DeltaBatch('9', ()), registry=registry())
    assert store.cursor(gmail.PROVIDER_KEY) == '10'


def test_existing_scope_change_refused_without_reattribution(tmp_path):
    batch = DeltaBatch('10', (envelope('unknown@example.org'),))
    gmail.capture_batch(storage(tmp_path), batch, registry=registry())
    gate = registry()
    gate.data['scopes']['global']['capture_identities'].append(dict(match_type='sender',
        value_sha256=hashlib.sha256(b'unknown@example.org').hexdigest()))
    with pytest.raises(gmail.CaptureError, match='scope changed'):
        gmail.capture_batch(storage(tmp_path), batch, registry=gate)


def test_poll_control_error_and_concurrency_use_existing_contract(tmp_path):
    store = storage(tmp_path)
    executor = Mock()
    assert gmail.poll_once(scheduled=True, root=tmp_path, executor=executor)['status'] == 'disabled'
    executor.execute.assert_not_called()
    store.set_control(live_capture_enabled=True, kill_switch=False)
    (store.root / 'control/activation.json').write_text(json.dumps(gmail.ACTIVATION_CONTRACT))
    executor.execute.side_effect = RuntimeError('private provider payload must not leak')
    result = gmail.poll_once(scheduled=True, root=tmp_path, executor=executor)
    assert result['status'] == 'failed' and not result['cursor_advanced']
    assert 'private provider' not in json.dumps(result)
    with gmail.PollLock(store.root / 'control/poll.lock'):
        assert gmail.poll_once(scheduled=True, root=tmp_path, executor=executor)['status'] == 'already_running'


def test_provider_bootstrap_truncation_and_page_bound_fail_closed():
    class Provider:
        def execute(self, action, payload):
            gmail.ComposioReadOnlyExecutor._validate(action, payload)
            if action == 'GMAIL_GET_PROFILE':
                return dict(historyId='100', emailAddress='operator@example.org')
            return dict(messages=[], history=[], nextPageToken='more')
    with pytest.raises(gmail.CaptureError, match='truncated'):
        gmail._prepare_delta(Provider(), cursor=None, bootstrap_hours=24)
    with pytest.raises(gmail.CaptureError, match='page limit'):
        gmail._prepare_delta(Provider(), cursor='10', bootstrap_hours=24)
    with pytest.raises(gmail.CaptureError):
        gmail.ComposioReadOnlyExecutor._validate('GMAIL_SEND_EMAIL', {})


class PagedMailbox:
    """Fixture Gmail history: records 11..10+n, each one INBOX addition, 100 per page."""

    def __init__(self, n, *, sent_until=0):
        self.head = 10 + n
        self.sent_until = sent_until  # history ids <= this carry SENT (filtered) additions
        self.action_counts = __import__('collections').Counter()
        self.pages_per_poll, self.fetches_per_poll = [], []

    def labels(self, hid):
        return ['SENT'] if hid <= self.sent_until else ['INBOX']

    def execute(self, action, payload):
        gmail.ComposioReadOnlyExecutor._validate(action, payload)
        self.action_counts[action] += 1
        if action == 'GMAIL_GET_PROFILE':
            self.pages_per_poll.append(0); self.fetches_per_poll.append(0)
            return dict(historyId=str(self.head), emailAddress='mailbox@example.org')
        if action == 'GMAIL_LIST_HISTORY':
            self.pages_per_poll[-1] += 1
            ids = list(range(int(payload['start_history_id']) + 1, self.head + 1))
            offset = int(payload.get('page_token') or 0)
            page = ids[offset:offset + payload['max_results']]
            rows = [dict(id=str(h), messagesAdded=[dict(message=dict(
                id=f'm{h}', threadId=f't{h}', labelIds=self.labels(h)))]) for h in page]
            more = offset + len(page) < len(ids)
            return dict(history=rows, historyId=str(self.head), **({'nextPageToken': str(offset + len(page))} if more else {}))
        self.fetches_per_poll[-1] += 1
        h = int(payload['message_id'][1:])
        return dict(id=f'm{h}', threadId=f't{h}', labelIds=self.labels(h), internalDate=str(1794700000000 + h),
                    headers=[dict(name='From', value='operator@example.org'), dict(name='Subject', value='fixture')])


def armed(root):
    store = storage(root)
    store.set_control(live_capture_enabled=True, kill_switch=False)
    (store.root / 'control/activation.json').write_text(json.dumps(gmail.ACTIVATION_CONTRACT))
    store.publish_cursor(gmail.PROVIDER_KEY, '10')
    return store


def test_history_backlog_beyond_page_bound_drains_exactly_once(tmp_path):
    """Production 2026-11-15: five full pages, each with nextPageToken, failed every poll
    and never advanced the cursor. The backlog must drain in bounded polls instead."""
    store, mailbox, gate = armed(tmp_path), PagedMailbox(730), registry()
    cursors, added = [], 0
    for _poll in range(40):
        result = gmail.poll_once(scheduled=True, root=tmp_path, executor=mailbox, registry=gate)
        assert result['status'] == 'success', result
        assert result['provider_actions']['GMAIL_GET_PROFILE'] >= 1
        added += result['records_added']
        cursors.append(int(store.cursor(gmail.PROVIDER_KEY)))
        if cursors[-1] == mailbox.head:
            break
    assert cursors[-1] == mailbox.head and cursors == sorted(set(cursors))
    assert added == 730 and len(cursors) == 30  # ceil(730 / 25) bounded polls
    assert max(mailbox.pages_per_poll) <= gmail.MAX_HISTORY_PAGES
    assert max(mailbox.fetches_per_poll) <= gmail.MAX_HISTORY_MESSAGES_PER_POLL
    assert len({r['record_id'] for r in store.processing_rows(gmail.PROVIDER_KEY)}) == 730
    again = gmail.poll_once(scheduled=True, root=tmp_path, executor=mailbox, registry=gate)
    assert again['status'] == 'success' and again['records_added'] == 0 and not again['cursor_advanced']


def test_history_page_bound_with_only_filtered_rows_advances_to_last_fetched_row(tmp_path):
    store, mailbox = armed(tmp_path), PagedMailbox(700, sent_until=10 + 600)
    result = gmail.poll_once(scheduled=True, root=tmp_path, executor=mailbox, registry=registry())
    assert result['status'] == 'success' and result['history_backlog_truncated']
    assert result['records_added'] == 0 and store.cursor(gmail.PROVIDER_KEY) == str(10 + 500)
    assert mailbox.fetches_per_poll == [0]


def test_history_page_bound_failure_keeps_cursor_and_reports_counts(tmp_path):
    store, mailbox = armed(tmp_path), PagedMailbox(730)
    def failing(action, payload, original=mailbox.execute):
        if action == 'GMAIL_LIST_HISTORY' and mailbox.pages_per_poll[-1] == 3:
            mailbox.action_counts[action] += 1
            raise gmail.CaptureError('fixture provider failure')
        return original(action, payload)
    mailbox.execute = failing
    result = gmail.poll_once(scheduled=True, root=tmp_path, executor=mailbox, registry=registry())
    assert result['status'] == 'failed' and not result['cursor_advanced']
    assert result['provider_actions'] == {'GMAIL_GET_PROFILE': 1, 'GMAIL_LIST_HISTORY': 4}
    assert store.cursor(gmail.PROVIDER_KEY) == '10'


GONE_404 = '{"error": {"code": 404, "message": "Requested entity was not found.", "errors": [{"reason": "notFound"}]}}'


@pytest.mark.parametrize("error,gone", [
    (GONE_404, True),
    ('HTTP 404 Requested entity was not found.', True),
    ('HTTP 401 Invalid Credentials', False),
    ('HTTP 403 insufficientPermissions', False),
    ('HTTP 500 backendError', False),
    ('Composio CLI timed out after 60s', False),
])
def test_only_a_404_not_found_message_fetch_is_classified_gone(monkeypatch, error, gone):
    import ttros_connections
    for failure in (ttros_connections.ToolFailed('GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID did not succeed: ' + error, error),
                    ttros_connections.ProviderUnreachable('Composio API unreachable; nothing was changed.')):
        monkeypatch.setattr(ttros_connections, 'perform_operator', lambda *a, **k: (_ for _ in ()).throw(failure))
        with pytest.raises(gmail.CaptureError) as caught:
            gmail.ComposioReadOnlyExecutor().execute('GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID',
                                                     dict(user_id='me', message_id='m11', format='metadata'))
        if isinstance(failure, ttros_connections.ProviderUnreachable):
            assert not isinstance(caught.value, (gmail.MessageGone, gmail.HistoryCursorExpired))
            continue
        assert isinstance(caught.value, gmail.MessageGone) is gone
        assert not isinstance(caught.value, gmail.HistoryCursorExpired)
        assert error not in str(caught.value)


def test_deleted_backlog_messages_are_skipped_not_a_permanent_block(tmp_path):
    """Production 2026-11-15 after cutover: the first message in the 17-day backlog had been
    deleted, its fetch answered 404 notFound, and every poll failed on it with the cursor stuck."""
    store, mailbox, gate = armed(tmp_path), PagedMailbox(60), registry()
    deleted = {11, 12, 40}
    def fetch(action, payload, original=mailbox.execute):
        if action == 'GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID' and int(payload['message_id'][1:]) in deleted:
            mailbox.action_counts[action] += 1
            mailbox.fetches_per_poll[-1] += 1
            raise gmail.MessageGone('Gmail message no longer exists')
        return original(action, payload)
    mailbox.execute = fetch
    added, gone = 0, 0
    for _poll in range(5):
        result = gmail.poll_once(scheduled=True, root=tmp_path, executor=mailbox, registry=gate)
        assert result['status'] == 'success', result
        added, gone = added + result['records_added'], gone + result['messages_gone']
        if store.cursor(gmail.PROVIDER_KEY) == str(mailbox.head):
            break
    assert store.cursor(gmail.PROVIDER_KEY) == str(mailbox.head)
    assert gone == 3 and added == 57
    assert len({r['record_id'] for r in store.processing_rows(gmail.PROVIDER_KEY)}) == 57


def test_other_message_fetch_failures_still_fail_closed(tmp_path):
    store, mailbox = armed(tmp_path), PagedMailbox(30)
    def fetch(action, payload, original=mailbox.execute):
        if action == 'GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID':
            mailbox.action_counts[action] += 1
            raise gmail.CaptureError('Composio read-only Gmail result was unsuccessful')
        return original(action, payload)
    mailbox.execute = fetch
    result = gmail.poll_once(scheduled=True, root=tmp_path, executor=mailbox, registry=registry())
    assert result['status'] == 'failed' and not result['cursor_advanced']
    assert store.cursor(gmail.PROVIDER_KEY) == '10'


def test_provider_history_prefix_keeps_checkpoint_whole():
    rows = [('10', {'id': str(i)}) for i in range(20)] + [('11', {'id': str(i)}) for i in range(20, 30)]
    selected, cursor, truncated = gmail._bounded_history_refs(rows)
    assert len(selected) == 20 and cursor == '10' and truncated


def prepare_brief(root):
    brain = root / 'brain'
    write_note(brain / 'memory/global.md', 'global', 'Company', 'Current operating context.')
    # This valid filename is outside the global registry and must never be opened.
    write_note(brain / 'prospects/client-a.md', 'client-a', 'Private client', 'fixture private content')
    return brain


@pytest.mark.parametrize('large_id', [False, True])
def test_detector_reads_new_jobs_and_denies_other_client_notes_before_open(tmp_path, monkeypatch, large_id):
    brain = prepare_brief(tmp_path)
    if large_id:
        folder = tmp_path / 'queue/jobs'
        folder.mkdir(parents=True)
        year = dt.datetime.now(dt.timezone.utc).strftime('%Y')
        (folder / '.sequence.json').write_text(json.dumps({'version': 1, 'years': {year: '9' * 140}}))
    job, _ = aos_jobs.create(tmp_path, conversation_id='dashboard', topic='followup', request='Prepare a followup',
                             executor={'kind': 'agent', 'agent': 'fixture@0.1.0', 'input': {}})
    original = Path.read_text
    def read(path, *args, **kwargs):
        assert path.name != 'client-a.md', 'out-of-scope content opened'
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', read)
    result = detector.detect(root=tmp_path, brain_root=brain, client_scope='global', registry=registry(), now=NOW)
    assert any(row.entity_id == job['id'] and row.rule == 'job_queued' for row in result.findings)
    first_hash = result.source_state_sha256
    job['status'] = 'done'
    aos_jobs.save(tmp_path, job)
    result = detector.detect(root=tmp_path, brain_root=brain, client_scope='global', registry=registry(), now=NOW)
    assert not any(row.entity_id == job['id'] for row in result.findings)
    assert first_hash != result.source_state_sha256


def test_brief_dry_run_has_no_effect_or_model_and_uncertain_delivery_never_repeats(tmp_path):
    brain = prepare_brief(tmp_path)
    args = dict(root=tmp_path, brain_root=brain, registry=registry(), now=NOW)
    sender = Mock(return_value={'acknowledged': True, 'provider_message_id': 'fixture'})
    dry = brief.run(**args, dry_run=True, telegram_send=sender, agentmail_send=sender)
    assert dry['model_invocations'] == 0 and len(dry['artifact'].split()) <= 400
    assert not (tmp_path / brief.OUTPUT_REL).exists()
    sender.assert_not_called()
    assert not (tmp_path / 'queue/jobs').exists()
    uncertain = Mock(side_effect=TimeoutError('fixture transport uncertainty'))
    result = brief.run(**args, deliver=True, telegram_send=uncertain, agentmail_send=sender)
    assert result['status'] == 'delivery_uncertain'
    replay = brief.run(**args, deliver=True, telegram_send=uncertain, agentmail_send=sender)
    assert uncertain.call_count == sender.call_count == 1
    assert {r['status'] for r in replay['deliveries']} == {'already_sent', 'ambiguous_not_retried'}
    assert result['artifact_sha256'] == replay['artifact_sha256']


def test_brief_concurrent_delivery_once(tmp_path):
    brain = prepare_brief(tmp_path)
    sender = Mock(return_value={'acknowledged': True})
    def run(_):
        return brief.run(root=tmp_path, brain_root=brain, registry=registry(), now=NOW,
                         deliver=True, telegram_send=sender, agentmail_send=sender)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(run, range(3)))
    assert sender.call_count == 2 and all(r['status'] == 'complete' for r in results)


@pytest.mark.parametrize('state', ['intent', 'uncertain', 'failed'])
def test_legacy_brief_delivery_intent_or_failure_never_retries(tmp_path, state):
    path = tmp_path / 'delivery.jsonl'
    path.write_text(json.dumps(dict(run_id='day', channel='telegram', status=state, artifact_sha256='old')) + '\n')
    sender = Mock()
    result = brief._send_once(path, 'day', 'telegram', 'old', sender, NOW)
    assert result['status'] == 'ambiguous_not_retried'
    sender.assert_not_called()


def test_legacy_brief_channels_and_receipt_preserved_without_duplicate_send(tmp_path):
    body = '# Original exact brief body\n'
    artifact = tmp_path / brief.OUTPUT_REL
    artifact.parent.mkdir(parents=True)
    artifact.write_text(body)
    digest = hashlib.sha256(body.encode()).hexdigest()
    day = NOW.astimezone(brief.LOCAL_TIMEZONE).date().isoformat()
    log = tmp_path / brief.DELIVERY_LOG_REL
    log.parent.mkdir(parents=True)
    log.write_text(''.join(json.dumps(dict(run_id=day, channel=channel, status='sent', artifact_sha256=digest))
                          + '\n' for channel in ('email', 'telegram')))
    receipt = tmp_path / 'queue/receipts' / f'david-morning-brief-{day}.md'
    receipt.parent.mkdir(parents=True)
    receipt.write_text('# Original delivery metadata\n')
    sender = Mock()
    result = brief.run(root=tmp_path, now=NOW, deliver=True, telegram_send=sender, agentmail_send=sender)
    sender.assert_not_called()
    assert result['artifact'] == body and result['status'] == 'complete'
    assert receipt.read_text() == '# Original delivery metadata\n'
    artifact.write_text('different')
    # A different retained immutable artifact cannot silently be marked delivered.
    (tmp_path / result['artifact_path']).write_text('different')
    with pytest.raises(brief.MorningBriefRunError, match='hash'):
        brief.run(root=tmp_path, now=NOW, deliver=True, telegram_send=sender, agentmail_send=sender)


def test_ingress_rejects_spoof_and_accepts_exact_existing_bridge_receipt(tmp_path):
    text, cid, delivery = 'hello', 'telegram-111', 'fixture-update'
    with pytest.raises(ValueError):
        david_ingress.verify_telegram(tmp_path, text, cid, delivery)
    path = tmp_path / david_ingress.STATE
    path.parent.mkdir(parents=True)
    key = hashlib.sha256(f'111\0{delivery}'.encode()).hexdigest()
    row = dict(chat_id='111', delivery_id=delivery, status='submitting',
               payload=dict(text=text, conversation_id=cid, delivery_id=delivery))
    path.write_text(json.dumps({'david_deliveries': {key: row}}))
    assert david_ingress.verify_telegram(tmp_path, text, cid, delivery) == '111'
    for altered in [('spoof', cid, delivery), (text, 'telegram-222', delivery), (text, cid, '')]:
        with pytest.raises(ValueError):
            david_ingress.verify_telegram(tmp_path, *altered)
    assert david_ingress.verify_telegram(tmp_path, text, 'dashboard', '') == ''


def test_retired_canary_tombstones_survive_no_active_window(tmp_path):
    with pytest.raises(ValueError):
        david_ingress.reject_retired_test(tmp_path, 'run FAST-DAVID-AUTO-OLD-001')
    path = tmp_path / david_ingress.TESTS
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'marker': 'arbitrary-retired-marker', 'request_sha256': 'old'}) + '\n')
    with pytest.raises(ValueError):
        david_ingress.reject_retired_test(tmp_path, 'ARBITRARY-RETIRED-MARKER')
    david_ingress.reject_retired_test(tmp_path, 'ordinary work')
    path.write_text('corrupt')
    with pytest.raises(ValueError):
        david_ingress.reject_retired_test(tmp_path, 'ordinary work')


@pytest.mark.parametrize('provider', ['codex', 'claude'])
def test_native_workbench_source_launcher_with_fixture_binary(tmp_path, provider):
    executable = tmp_path / provider
    executable.write_text('#!/usr/bin/python3\nimport os,sys,json\nprint(json.dumps(dict(cwd=os.getcwd(), args=sys.argv[1:], no_push=os.environ.get("TTROS_CODEX_NO_PUSH"))))\n')
    executable.chmod(0o755)
    launcher = Path(__file__).resolve().parents[1] / 'scripts/workbench' / ('aos-' + provider)
    # Execute the exact source bytes under a neutral fixture path; the guard
    # continues refusing production-named wrappers. PATH resolves only our stub.
    fixture_launcher = tmp_path / 'native-entry-fixture.sh'
    fixture_launcher.write_bytes(launcher.read_bytes())
    env = dict(os.environ, PATH=str(tmp_path) + ':' + os.environ['PATH'], AOS_ROOT=str(tmp_path))
    result = subprocess.run(['bash', str(fixture_launcher), 'literal $(no-shell-expansion)\nnext line'],
                            env=env, text=True, capture_output=True, check=True)
    value = json.loads(result.stdout)
    assert value == dict(cwd=str(tmp_path), args=['literal $(no-shell-expansion)\nnext line'], no_push='1')


def test_one_canonical_brain_destination_for_readers_and_writer():
    """Readers, the writer and David's handoffs must name the same vault (PATHS.md)."""
    env = {k: v for k, v in os.environ.items() if k != 'TTROS_BRAIN_ROOT'}
    code = ('import sys; sys.path[:0]=["tools","."]; import brain_memory, business_brain;'
            'print(brain_memory.VAULT_ROOT == business_brain.BUSINESS_BRAIN_ROOT.resolve(), business_brain.BUSINESS_BRAIN_ROOT)')
    out = subprocess.run([os.sys.executable, '-c', code], env=env, capture_output=True, text=True, check=True).stdout.split(' ', 1)
    assert out[0] == 'True'
    assert out[1].strip() == '/var/lib/ttros/brain'


@pytest.mark.parametrize('error, page_token, expired', [
    ('HTTP 404 Requested entity was not found.', '', True),
    ('{"code": 404, "status": "NOT_FOUND", "message": "startHistoryId is too old"}', '', True),
    ('HTTP 404 Requested entity was not found.', 'p2', False),   # a later page is not the checkpoint
    ('HTTP 401 Invalid Credentials', '', False),
    ('Composio CLI timed out after 60s', '', False),
])
def test_only_a_rejected_first_history_page_is_classified_expired(monkeypatch, error, page_token, expired):
    import ttros_connections
    for failure in (ttros_connections.ToolFailed('GMAIL_LIST_HISTORY did not succeed: ' + error, error),):
        monkeypatch.setattr(ttros_connections, 'perform_operator', lambda *a, **k: (_ for _ in ()).throw(failure))
        payload = dict(user_id='me', start_history_id='10', history_types=['messageAdded'], label_id='INBOX', max_results=100)
        if page_token:
            payload['page_token'] = page_token
        with pytest.raises(gmail.CaptureError) as caught:
            gmail.ComposioReadOnlyExecutor().execute('GMAIL_LIST_HISTORY', payload)
        assert isinstance(caught.value, gmail.HistoryCursorExpired) is expired
        assert error not in str(caught.value)   # provider text never reaches the receipt


class ExpiredMailbox(PagedMailbox):
    """History before ``valid_from`` is gone (Gmail 404); messages carry receipt times for window queries."""

    def __init__(self, times, *, valid_from):
        super().__init__(len(times))
        self.times = {10 + i + 1: t for i, t in enumerate(times)}   # history id -> epoch seconds
        self.valid_from = valid_from
        self.windows = []

    def execute(self, action, payload):
        if action == 'GMAIL_LIST_HISTORY' and int(payload['start_history_id']) < self.valid_from:
            gmail.ComposioReadOnlyExecutor._validate(action, payload)
            self.action_counts[action] += 1
            raise gmail.HistoryCursorExpired('fixture: Gmail returned 404 for the checkpoint')
        if action == 'GMAIL_FETCH_EMAILS':
            gmail.ComposioReadOnlyExecutor._validate(action, payload)
            self.action_counts[action] += 1
            after, before = (int(part.split(':')[1]) for part in payload['query'].split())
            self.windows.append((after, before))
            hits = [h for h, t in sorted(self.times.items()) if after < t < before]
            page = hits[:payload['max_results']]
            rows = [dict(id=f'm{h}', threadId=f't{h}', labelIds=['INBOX'], internalDate=str(self.times[h] * 1000),
                         headers=[dict(name='From', value='operator@example.org')]) for h in page]
            return dict(messages=rows, **({'nextPageToken': 'more'} if len(hits) > len(page) else {}))
        result = super().execute(action, payload)
        if action == 'GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID':
            result['internalDate'] = str(self.times[int(payload['message_id'][1:])] * 1000)
        return result


def test_expired_history_cursor_resyncs_by_bounded_windows_exactly_once(tmp_path):
    start = NOW
    # 40 already-captured messages, then 90 uncaptured in receipt order (a dense one-hour burst included).
    old = [int((start - dt.timedelta(days=10) + dt.timedelta(hours=4 * i)).timestamp()) for i in range(40)]  # ends 3.5 days ago
    burst = [int((start - dt.timedelta(days=2) + dt.timedelta(seconds=60 * i)).timestamp()) for i in range(60)]
    late = [int((start - dt.timedelta(hours=20) + dt.timedelta(minutes=37 * i)).timestamp()) for i in range(30)]
    mailbox, gate = ExpiredMailbox(old + burst + late, valid_from=10 + 41), registry()
    store = armed(tmp_path)
    mailbox.pages_per_poll.append(0); mailbox.fetches_per_poll.append(0)
    # Pre-expiry state: the first 40 were captured through history; cursor stopped at their checkpoint.
    pre = DeltaBatch('50', tuple(gmail._normalize_message(mailbox.execute('GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID',
              dict(user_id='me', message_id=f'm{h}', format='metadata')), history_id=str(h), mailbox_sender_sha256='x')
              for h in range(11, 51)))
    gmail.capture_batch(store, pre, registry=gate)
    mailbox.valid_from = 10 + 130   # now even cursor 50 is expired; the mailbox head is 140
    original_records = (tmp_path / 'capture/runtime').rglob('*.jsonl')
    before = {p: p.read_bytes() for p in original_records}

    results, added = [], 40
    for _poll in range(40):
        result = gmail.poll_once(scheduled=True, root=tmp_path, executor=mailbox, registry=gate, now=start)
        assert result['status'] == 'success', result
        results.append(result); added += result['records_added']
        if result.get('mode') != 'history_resync' or result['resync_complete']:
            break
    assert results[0]['mode'] == 'history_resync' and results[0]['resync_anchor'] == 'last_captured_message'
    assert all(r['cursor_advanced'] is False for r in results[:-1]) and results[-1]['resync_complete']
    assert store.cursor(gmail.PROVIDER_KEY) == str(mailbox.head)   # resumed at the checkpoint read when resync began
    assert not (store.root / gmail.RESYNC_PATH).exists()
    assert added == 130 and len({r['record_id'] for r in store.processing_rows(gmail.PROVIDER_KEY)}) == 130
    assert max(r['authorized_envelopes'] for r in results) <= gmail.MAX_HISTORY_MESSAGES_PER_POLL
    assert sum(r['records_deduplicated'] for r in results) >= 1   # the overlap re-read was absorbed, not duplicated
    for path, data in before.items():                           # originals are only ever appended to
        assert path.read_bytes().startswith(data)
    resumed = gmail.poll_once(scheduled=True, root=tmp_path, executor=mailbox, registry=gate, now=start)
    assert resumed['status'] == 'success' and 'mode' not in resumed and resumed['records_added'] == 0


def test_resync_crash_replays_window_and_invalid_plan_fails_closed(tmp_path, monkeypatch):
    times = [int((NOW - dt.timedelta(hours=10 - i)).timestamp()) for i in range(10)]
    mailbox, gate, store = ExpiredMailbox(times, valid_from=10 + 11), registry(), armed(tmp_path)
    original = gmail.capture_batch
    monkeypatch.setattr(gmail, 'capture_batch', Mock(side_effect=RuntimeError('fixture interruption')))
    failed = gmail.poll_once(scheduled=True, root=tmp_path, executor=mailbox, registry=gate, now=NOW)
    assert failed['status'] == 'failed' and store.cursor(gmail.PROVIDER_KEY) == '10'
    plan = json.loads((store.root / gmail.RESYNC_PATH).read_text())
    assert plan['anchor'] == 'cursor_published_at' and plan['expired_cursor'] == '10'
    monkeypatch.setattr(gmail, 'capture_batch', original)
    total = 0
    for _ in range(10):
        result = gmail.poll_once(scheduled=True, root=tmp_path, executor=mailbox, registry=gate, now=NOW)
        assert result['status'] == 'success', result
        total += result['records_added']
        if result.get('resync_complete'):
            break
    assert total == 10 and store.cursor(gmail.PROVIDER_KEY) == str(mailbox.head)
    store.publish_cursor(gmail.PROVIDER_KEY, '500')
    (store.root / gmail.RESYNC_PATH).write_text(json.dumps(dict(plan, resume_history_id='20')))  # would regress
    refused = gmail.poll_once(scheduled=True, root=tmp_path, executor=mailbox, registry=gate, now=NOW)
    assert refused['status'] == 'failed' and store.cursor(gmail.PROVIDER_KEY) == '500'
