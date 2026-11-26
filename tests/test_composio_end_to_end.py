"""Focused repair contracts. Doubles are regression evidence, never live-provider acceptance."""
import json
from types import SimpleNamespace

import pytest

from tests.test_composio_projects import env, A, B, MANAGED_KEY
from ttros_connections import ConnectionError
from aos_david import David
import aos_jobs


def calendar(env):
    hub, provider, identity, root = env
    hub.use_managed_project(A)
    provider.add(MANAGED_KEY, 'ca_calendar1', hub.managed_user(A), 'googlecalendar')
    row = hub.bind_project(A, 'ca_calendar1')
    calls = []
    original = hub.composio
    def transport(method, path, key, **kwargs):
        if path.startswith('/tools/execute/') and path != '/tools/execute/proxy':
            calls.append((path, kwargs['body']))
            return {'successful': True, 'data': ({'response': {'event_id': 'event_verified'}} if path.endswith('/GOOGLECALENDAR_CREATE_EVENT') else
                {'event_data': {'id': 'event_verified', 'htmlLink': 'https://calendar.google.com/calendar/event?eid=verified',
                 'summary': ARGS['summary'], 'start': {'dateTime': ARGS['start_datetime']}, 'end': {'dateTime': ARGS['end_datetime']},
                 'visibility': 'private', 'attendees': []}})}
        return original(method, path, key, **kwargs)
    hub.composio = transport
    return hub, provider, row, calls


from datetime import datetime
from zoneinfo import ZoneInfo
ARGS = {'summary': 'Private meeting', 'start_datetime': datetime(2026, 12, 3, 8, tzinfo=ZoneInfo('America/Vancouver')).isoformat(),
        'end_datetime': datetime(2026, 12, 3, 9, tzinfo=ZoneInfo('America/Vancouver')).isoformat(), 'timezone': 'America/Vancouver'}


def test_private_calendar_write_confirmed_and_deduplicated_across_callers(env):
    hub, provider, row, calls = calendar(env)
    receipt = hub.execute(A, row['id'], 'create_event', ARGS)
    assert receipt['provider_verified'] and receipt['result']['event']['htmlLink'].startswith('https://calendar.google.com/')
    body = calls[0][1]
    assert body['connected_account_id'] == 'ca_calendar1' and body['user_id'] == hub.managed_user(A)
    assert body['arguments']['send_updates'] == 'none' and body['arguments']['visibility'] == 'private'
    assert body['arguments']['attendees'] == [] and body['arguments']['create_meeting_room'] is False
    assert calls[1][0].endswith('/GOOGLECALENDAR_EVENTS_GET')
    again = hub.execute(A, row['id'], 'create_event', ARGS)
    assert again['id'] == receipt['id'] and again['replayed'] and len(calls) == 2
    with pytest.raises(ConnectionError): hub.execute(B, row['id'], 'create_event', ARGS)
    assert len(calls) == 2


def test_ambiguous_dates_attendees_account_override_and_task_restrictions_fail_before_write(env):
    hub, _, row, calls = calendar(env)
    for bad in ({**ARGS, 'attendees': ['other@example.invalid']}, {**ARGS, 'user_id': 'other'},
                {**ARGS, 'end_datetime': '2026-12-03T07:00:00-08:00'}, {**ARGS, 'timezone': 'Europe/Dublin'},
                {k: v for k, v in ARGS.items() if k != 'end_datetime'}):
        with pytest.raises(ConnectionError): hub.execute(A, row['id'], 'create_event', bad)
    with pytest.raises(ConnectionError, match='policy'):
        hub.execute(A, row['id'], 'create_event', ARGS, item={'restrictions': ['no_calendar_writes']})
    with pytest.raises(ConnectionError): hub.submit_job(A, row['id'], 'create_event', ARGS)
    assert calls == []
    assert hub.receipts(A)[0]['status'] == 'denied'


def test_uncertain_write_never_retries(env):
    hub, _, row, calls = calendar(env)
    original = hub.composio
    def timeout(method, path, key, **kwargs):
        if path.endswith('/GOOGLECALENDAR_CREATE_EVENT'):
            calls.append(('uncertain', kwargs['body']))
            raise ConnectionError('Provider timed out after dispatch')
        return original(method, path, key, **kwargs)
    hub.composio = timeout
    with pytest.raises(ConnectionError, match='timed out'): hub.execute(A, row['id'], 'create_event', ARGS)
    with pytest.raises(ConnectionError, match='will not be repeated'): hub.execute(A, row['id'], 'create_event', ARGS)
    assert len(calls) == 1


def test_forge_named_write_manifest_and_task_intersection(env):
    hub, provider, row, calls = calendar(env)
    package = SimpleNamespace(ref='shared.calendar@1.0.0', grants={'connector.write:googlecalendar:create_event'}, content_sha256='pinned')
    manifest = {'agent': {'name': 'shared.calendar', 'version': '1.0.0'}, 'capabilities': {'grants': sorted(package.grants)}}
    assert not hub.resolve(A, manifest)['missing_required']
    permissions = frozenset([(row['id'], 'create_event')])
    with pytest.raises(ConnectionError, match='matching'): hub.execute_agent(A, package, 'googlecalendar', 'create_event', ARGS, permissions=permissions)
    with pytest.raises(ConnectionError, match='unique'): hub.execute_agent(A, package, 'googlecalendar', 'create_event', ARGS, permissions=frozenset(), mode='write')
    hub.access(A, row['id'], 'Restricted', {package.ref: ['create_event']})
    receipt = hub.execute_agent(A, package, 'googlecalendar', 'create_event', ARGS, permissions=permissions, mode='write')
    assert receipt['agent'] == package.ref and receipt['package_sha256'] == 'pinned'
    provider.projects[MANAGED_KEY]['ca_calendar1']['is_disabled'] = True
    with pytest.raises(ConnectionError, match='DISABLED'):
        hub.execute_agent(A, package, 'googlecalendar', 'create_event', ARGS, permissions=permissions, mode='write')
    assert len(calls) == 2


def test_natural_gmail_uses_hub_with_no_job_or_search_agent(env, monkeypatch):
    hub, provider, identity, root = env
    hub.use_managed_project(A)
    provider.add(MANAGED_KEY, 'ca_gmail1', hub.managed_user(A))
    hub.bind_project(A, 'ca_gmail1')
    plans = iter([{'action': 'tool', 'service': 'gmail', 'operation': 'search', 'arguments': '{"query":"subject:confirmation"}', 'reply': ''},
                  {'action': 'reply', 'service': '', 'operation': '', 'arguments': '', 'reply': 'Provider search completed.'}])
    def model(*args, **kwargs):
        assert kwargs['model'] == 'gpt-6-luna'
        return {'ok': True, 'status': 'ok', 'json': next(plans), 'usage': {'available': True, 'input_tokens': 1, 'output_tokens': 1}}
    monkeypatch.setattr(aos_jobs, 'create', lambda *a, **k: pytest.fail('Routine Gmail launched a Job'))
    david = David(root, model=model, warm=False)
    result = david.turn('scoped-mail', 'Search Gmail for confirmation emails', connection_context=(hub, A))
    assert result['success'] and result['connection_receipts'][0]['operation'] == 'search'
    execution = next(c for c in provider.calls if c['path'].endswith('/GMAIL_FETCH_EMAILS'))
    assert execution['body']['arguments']['user_id'] == 'me' and execution['body']['connected_account_id'] == 'ca_gmail1'


def test_jobs_effects_run_on_the_platform_connection_with_job_authority(tmp_path, monkeypatch):
    """Jobs effects no longer start the For You adapter: one Platform tool call, policy rechecked with the job."""
    import ttros_connections
    calls = []
    def perform(root, tool, arguments, **kwargs):
        calls.append((tool, arguments, kwargs))
        return {'id': 'cr_job1', 'result': {'id': 'provider-id'}}
    monkeypatch.setattr(ttros_connections, 'perform_operator', perform)
    result = aos_jobs.composio_perform(tmp_path, 'GOOGLECALENDAR_CREATE_EVENT', 'primary', ARGS, job_id='AOS-2026-12345')
    assert result == {'ok': True, 'reference': 'provider-id', 'receipt': 'cr_job1', 'detail': ''}
    tool, arguments, kwargs = calls[0]
    assert tool == 'GOOGLECALENDAR_CREATE_EVENT' and arguments == ARGS
    assert kwargs['target'] == 'primary' and kwargs['idempotency_key'] == 'AOS-2026-12345' and kwargs['item']['id'] == 'AOS-2026-12345'
    monkeypatch.setattr(ttros_connections, 'perform_operator', lambda *a, **k: (_ for _ in ()).throw(
        ttros_connections.ConnectionError('Runtime policy held GMAIL_SEND_EMAIL')))
    assert aos_jobs.composio_perform(tmp_path, 'GMAIL_SEND_EMAIL', 'x@example.invalid', {})['ok'] is False
    monkeypatch.setattr(ttros_connections, 'perform_operator', lambda *a, **k: (_ for _ in ()).throw(
        ttros_connections.ProviderUnreachable('Composio API unreachable')))
    assert aos_jobs.composio_perform(tmp_path, 'GMAIL_CREATE_EMAIL_DRAFT', 'draft', {})['ok'] is None   # uncertain, never repeated


def test_telegram_identity_requires_configured_chat_and_existing_unique_member(env, monkeypatch):
    hub, _, identity, root = env
    from aos_authority import NOTIFICATIONS_PATH
    target = root / NOTIFICATIONS_PATH
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps({'allowlist': {'telegram': ['123']}}))
    monkeypatch.setenv('TTROS_CONNECTIONS_COMPOSIO_MEMBER', 'casey@example.invalid')
    with identity.db() as db:
        db.execute('INSERT INTO users VALUES(?,?)', ('operator-exact', 'casey@example.invalid'))
        db.execute('INSERT INTO memberships VALUES(?,?)', ('operator-exact', 'ttros-operator'))
    assert identity.telegram_operator('123')['user'] == 'operator-exact'
    with pytest.raises(ConnectionError): identity.telegram_operator('456')
    monkeypatch.setenv('TTROS_CONNECTIONS_COMPOSIO_MEMBER', 'other@example.invalid')
    with pytest.raises(ConnectionError): identity.telegram_operator('123')


def test_refresh_failure_does_not_starve_other_saved_attempts(env):
    hub, provider, _, _ = env
    hub.use_managed_project(A)
    for ident in ('ca_pending1', 'ca_pending2'):
        provider.add(MANAGED_KEY, ident, hub.managed_user(A))
        hub.bind_project(A, ident)
    provider.projects[MANAGED_KEY]['ca_pending1']['user_id'] = 'foreign-owner'
    result = hub.refresh(A)['connections']
    assert len(result) == 2 and len([r for r in result if r.get('error')]) == 1
    assert any(r['status'] == 'ACTIVE' and not r.get('error') for r in result)


def test_managed_project_reentry_preserves_exact_member_mapping(env):
    hub, provider, identity, _ = env
    hub.use_managed_project(A)
    with identity.db() as db:
        db.execute('INSERT INTO hub_project_users VALUES(?,?,?)', (A['workspace'], A['user'], 'existing-production-user'))
    hub.use_managed_project(A)
    assert hub.project_state(A)['composio_user_id'] == 'existing-production-user'


def test_natural_read_task_cannot_be_turned_into_calendar_write_by_model(env):
    hub, provider, row, calls = calendar(env)
    def model(*args, **kwargs):
        return {'status': 'ok', 'json': {'action': 'tool', 'service': 'googlecalendar', 'operation': 'create_event',
                'arguments': json.dumps(ARGS), 'reply': ''}, 'usage': {'available': False}}
    result = David(hub.identity.path.parents[2], model=model, warm=False).turn('read-only-task',
        'Read my Calendar. Do not create anything.', connection_context=(hub, A))
    assert not result['success'] and 'does not authorize' in result['response'] and calls == []


def test_live_pending_link_resumes_without_new_attempt_or_public_link_leak(env):
    hub, provider, _, _ = env
    original = hub.composio
    def transport(method, path, key, **kwargs):
        result = original(method, path, key, **kwargs)
        if path == '/connected_accounts/link': result['expires_at'] = '2099-12-31T00:00:00Z'
        return result
    hub.composio = transport
    first = hub.connect_app(A, 'gmail')
    second = hub.connect_app(A, 'gmail')
    assert first['id'] == second['id'] and first['url'] == second['url']
    assert len([c for c in provider.calls if c['path'] == '/connected_accounts/link']) == 1
    assert 'authorization_url' not in hub.rows(A)[0]['config']


def test_simultaneous_deliveries_share_one_provider_effect(env):
    from concurrent.futures import ThreadPoolExecutor
    hub, _, row, calls = calendar(env)
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(lambda _: hub.execute(A, row['id'], 'create_event', ARGS), range(2)))
    assert receipts[0]['id'] == receipts[1]['id'] and len(calls) == 2
