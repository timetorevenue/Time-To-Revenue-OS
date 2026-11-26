"""Platform cutover contracts built from the observed live shape (2026-11-23).

Live: managed TTROS project linked; it has a GitHub auth config only and no Gmail account
for any user; the operator member's working Gmail is a For You (consumer) binding; an older
For You Gmail attempt expired unfinished. Doubles here are regression evidence only.
"""
import time

import pytest

from tests.test_composio_projects import env, A, A2, B, MANAGED_KEY
from ttros_connections import ConnectionError
from aos_david import David
import aos_jobs


def links(provider):
    return [c for c in provider.calls if c['path'] == '/connected_accounts/link']


def live_shape(env):
    hub, provider, identity, root = env
    provider.auth_configs[MANAGED_KEY].append({'id': 'ac_acceptance_github', 'toolkit': {'slug': 'github'}, 'is_disabled': False})
    hub.use_managed_project(A)
    hub.save(A, 'cx_foryou_gmail', 'composio', 'gmail', 'gmail_personal', 'gmail_personal',
             {'provider_account': {'id': 'ca_consumer_gmail', 'user_id': 'consumer-x'}}, 'ACTIVE', time.time())
    return hub, provider, identity, root


def test_for_you_gmail_never_blocks_platform_connect_and_platform_wins_routing(env, monkeypatch):
    hub, provider, identity, root = live_shape(env)
    started = hub.connect_app(A, 'gmail')
    # One hosted link for this member's own opaque Platform user; one managed Gmail auth config.
    assert started['status'] == 'PENDING' and started['url'].startswith('https://connect.composio.dev/')
    assert [c['body']['user_id'] for c in links(provider)] == [hub.managed_user(A)]
    assert provider.created == 1
    assert 'cx_foryou_gmail' not in [r['id'] for r in hub.rows(A)]      # For You removed from TTROS
    with identity.db() as db:                                            # ...but kept as history
        assert db.execute("SELECT status FROM hub_connections WHERE id='cx_foryou_gmail'").fetchone()['status'] == 'ACTIVE'
    # Composio completes the hosted OAuth; only the saved attempt is verified.
    account = hub.row(A, started['id'])['account']
    provider.projects[MANAGED_KEY][account]['status'] = 'ACTIVE'
    assert hub.change(A, started['id'], 'status')['status'] == 'ACTIVE'
    again = hub.connect_app(A, 'gmail')
    assert again == {'id': started['id'], 'status': 'ACTIVE', 'message': 'Connected.'}
    assert len(links(provider)) == 1 and provider.created == 1
    # David chooses the Platform account deterministically, with no Job.
    plans = iter([{'action': 'tool', 'service': 'gmail', 'operation': 'search', 'arguments': '{"query":"subject:receipt"}', 'reply': ''},
                  {'action': 'reply', 'service': '', 'operation': '', 'arguments': '', 'reply': 'Done.'}])
    monkeypatch.setattr(aos_jobs, 'create', lambda *a, **k: pytest.fail('App read launched a Job'))
    david = David(root, model=lambda *a, **k: {'ok': True, 'status': 'ok', 'json': next(plans), 'usage': {}}, warm=False)
    result = david.turn('cutover', 'Search Gmail for receipt emails', connection_context=(hub, A))
    assert result['success'] and result['connection_receipts'][0]['connection'] == started['id']
    execution = [c for c in provider.calls if c['path'].startswith('/tools/execute/')]
    assert [c['body']['connected_account_id'] for c in execution] == [account]
    assert execution[0]['body']['user_id'] == hub.managed_user(A)


def test_members_of_one_workspace_keep_separate_platform_accounts(env):
    hub, provider, *_ = live_shape(env)
    first = hub.connect_app(A, 'gmail')
    account = hub.row(A, first['id'])['account']
    provider.projects[MANAGED_KEY][account]['status'] = 'ACTIVE'
    hub.change(A, first['id'], 'status')
    assert hub.project_accounts(A2) == []
    with pytest.raises(ConnectionError, match='does not belong'):
        hub.bind_project(A2, account)
    other = hub.connect_app(A2, 'gmail')                                 # own link, own user, no reuse
    assert other['status'] == 'PENDING' and other['id'] != first['id']
    assert links(provider)[-1]['body']['user_id'] == hub.managed_user(A2) != hub.managed_user(A)
    assert provider.created == 1                                         # project auth config reused
    with pytest.raises(ConnectionError):
        hub.row(B, first['id'])                                          # other workspace sees nothing


def test_provider_failure_is_reported_and_connect_starts_one_fresh_link(env):
    hub, provider, *_ = live_shape(env)
    first = hub.connect_app(A, 'gmail')
    account = hub.row(A, first['id'])['account']
    provider.projects[MANAGED_KEY][account].update(status='FAILED', status_reason='User denied consent')
    failed = hub.change(A, first['id'], 'status')
    assert failed['status'] == 'FAILED'
    assert failed['config']['provider_account']['status_reason'] == 'User denied consent'
    retry = hub.connect_app(A, 'gmail')
    assert retry['status'] == 'PENDING' and retry['id'] != first['id'] and len(links(provider)) == 2


@pytest.mark.parametrize('message', ['Composio rate-limited the request; retry later.',
                                     'Composio rejected the project API key for this request.'])
def test_provider_errors_during_connect_save_nothing(env, message):
    hub, provider, *_ = live_shape(env)
    original = hub.composio
    def failing(method, path, key, **kwargs):
        if path == '/connected_accounts/link':
            raise ConnectionError(message)
        return original(method, path, key, **kwargs)
    hub.composio = failing
    before = [r['id'] for r in hub.rows(A)]
    with pytest.raises(ConnectionError, match=message.split(' ')[1]):
        hub.connect_app(A, 'gmail')
    assert [r['id'] for r in hub.rows(A)] == before


# -- One operator principal; replaceable project; customer isolation -----------------------
from tests.test_composio_projects import CUSTOMER_KEY

DASHBOARD = {'user': 'operator-dash', 'workspace': 'ttros-operator', 'email': 'login@example.invalid', 'method': 'cloudflare_access'}
TELEGRAM = {'user': 'operator-pinned', 'workspace': 'ttros-operator', 'email': 'admin@example.invalid', 'method': 'telegram_bridge'}


def operator_env(env):
    hub, provider, identity, root = env
    with identity.db() as db:
        for who in (DASHBOARD, TELEGRAM):
            db.execute('INSERT OR IGNORE INTO users VALUES(?,?)', (who['user'], who['email']))
            db.execute('INSERT OR IGNORE INTO memberships VALUES(?,?)', (who['user'], who['workspace']))
    hub.use_managed_project(DASHBOARD)
    return hub, provider, identity, root


def gmail_search(hub, root, who, monkeypatch):
    plans = iter([{'action': 'tool', 'service': 'gmail', 'operation': 'search', 'arguments': '{"query":"x"}', 'reply': ''},
                  {'action': 'reply', 'service': '', 'operation': '', 'arguments': '', 'reply': 'Done.'}])
    monkeypatch.setattr(aos_jobs, 'create', lambda *a, **k: pytest.fail('App read launched a Job'))
    david = David(root, model=lambda *a, **k: {'ok': True, 'status': 'ok', 'json': next(plans), 'usage': {}}, warm=False)
    return david.turn('op-' + who['user'], 'Search Gmail for x', connection_context=(hub, who))


def test_operator_logins_and_telegram_share_one_platform_user_and_connection(env, monkeypatch):
    hub, provider, identity, root = operator_env(env)
    shared = hub.operator_user()
    # Independent of any login, Composio admin or connected-app email.
    assert {hub._composio_user(DASHBOARD), hub._composio_user(TELEGRAM)} == {shared}
    assert all(e.split('@')[0] not in shared for e in (DASHBOARD['email'], TELEGRAM['email']))
    assert shared not in (hub.managed_user(A), hub.managed_user(B))
    started = hub.connect_app(DASHBOARD, 'gmail')
    assert links(provider)[-1]['body']['user_id'] == shared
    account = hub.row(DASHBOARD, started['id'])['account']
    provider.projects[MANAGED_KEY][account]['status'] = 'ACTIVE'
    hub.change(DASHBOARD, started['id'], 'status')
    # Telegram's principal gets the same account at once, without a second OAuth or click.
    telegram_rows = [r for r in hub.rows(TELEGRAM) if r['service'] == 'gmail']
    assert [(r['account'], r['status']) for r in telegram_rows] == [(account, 'ACTIVE')]
    assert hub.connect_app(TELEGRAM, 'gmail')['status'] == 'ACTIVE' and len(links(provider)) == 1
    result = gmail_search(hub, root, TELEGRAM, monkeypatch)
    assert result['success']
    assert [c['body']['connected_account_id'] for c in provider.calls if c['path'].startswith('/tools/execute/')] == [account]
    # Either login may manage the operator project; one disconnect applies to both logins.
    assert hub.project_state(TELEGRAM)['owner'] is True
    hub.change(TELEGRAM, telegram_rows[0]['id'], 'disconnect')
    assert not [r for r in hub.rows(DASHBOARD) if r['service'] == 'gmail' and r['enabled']]
    hub.refresh(DASHBOARD)                                               # a disconnect is never resurrected
    assert not [r for r in hub.rows(DASHBOARD) if r['service'] == 'gmail' and r['enabled']]


def test_customer_members_and_other_workspaces_stay_isolated_from_operator(env):
    hub, provider, *_ = operator_env(env)
    provider.add(MANAGED_KEY, 'ca_operator_gmail', hub.operator_user())
    hub.refresh(DASHBOARD)
    hub.use_managed_project(A)
    assert hub.project_accounts(A) == [] and hub.project_accounts(A2) == []
    for who in (A, A2):
        with pytest.raises(ConnectionError, match='does not belong'):
            hub.bind_project(who, 'ca_operator_gmail')
    assert hub._composio_user(A) != hub._composio_user(A2)
    with pytest.raises(ConnectionError, match='Only the member who linked'):
        hub.link_project(A2, CUSTOMER_KEY)


def test_project_replaced_in_settings_moves_david_forge_and_jobs_to_new_project(env, monkeypatch):
    hub, provider, identity, root = operator_env(env)
    provider.add(MANAGED_KEY, 'ca_old_project_gmail', hub.operator_user())
    hub.refresh(TELEGRAM)
    old = next(r for r in hub.rows(TELEGRAM) if r['account'] == 'ca_old_project_gmail')
    # The other operator login replaces the project from Settings; no code or config change.
    provider.add(CUSTOMER_KEY, 'ca_new_project_gmail', hub.operator_user())
    state = hub.link_project(DASHBOARD, CUSTOMER_KEY)
    assert state['mode'] == 'customer' and state['accounts_connected'] == 1
    for who in (DASHBOARD, TELEGRAM):
        statuses = {r['account']: r['status'] for r in hub.rows(who) if r['service'] == 'gmail'}
        assert statuses == {'ca_old_project_gmail': 'PROJECT_CHANGED', 'ca_new_project_gmail': 'ACTIVE'}
    with pytest.raises(ConnectionError, match='linked project changed'):
        hub.execute(TELEGRAM, old['id'], 'search', {'query': 'x'})
    result = gmail_search(hub, root, TELEGRAM, monkeypatch)
    assert result['success']
    execution = [c for c in provider.calls if c['path'].startswith('/tools/execute/')]
    assert [(c['key'], c['body']['connected_account_id']) for c in execution] == [(CUSTOMER_KEY, 'ca_new_project_gmail')]
    manifest = {'agent': {'name': 'shared.mail', 'version': '1.0.0'}, 'capabilities': {'grants': ['connector.read:gmail:search']}}
    resolved = hub.resolve(TELEGRAM, manifest)['resolved']['connector.read:gmail:search']
    assert [m['account'] for m in resolved] == ['ca_new_project_gmail']


def test_apps_connected_directly_in_composio_are_adopted_only_when_unambiguous(env):
    hub, provider, *_ = operator_env(env)
    hub.save(TELEGRAM, 'cx_foryou', 'composio', 'gmail', 'gmail_personal', 'gmail_personal',
             {'provider_account': {'id': 'ca_consumer', 'user_id': 'consumer-x'}}, 'ACTIVE', time.time())
    provider.add(MANAGED_KEY, 'ca_direct_calendar', hub.operator_user(), 'googlecalendar')
    provider.add(MANAGED_KEY, 'ca_drive_one', hub.operator_user(), 'googledrive')
    provider.add(MANAGED_KEY, 'ca_drive_two', hub.operator_user(), 'googledrive')
    provider.add(MANAGED_KEY, 'ca_someone_else', 'another-user', 'gmail')
    hub.refresh(DASHBOARD)
    for who in (DASHBOARD, TELEGRAM):
        project = sorted(r['account'] for r in hub.rows(who) if r['config'].get('source') == 'project')
        assert project == ['ca_direct_calendar']
    manifest = {'agent': {'name': 'shared.mail', 'version': '1.0.0'}, 'capabilities': {'grants': ['connector.read:gmail:search']}}
    # For You was removed: without a Platform Gmail nothing serves Forge, and it says so.
    resolved = hub.resolve(TELEGRAM, manifest)
    assert resolved['resolved']['connector.read:gmail:search'] == []
    assert resolved['missing_required'] == ['connector.read:gmail:search']


# -- Natural app questions reach the Connections Hub (live miss 2026-11-24) ----------------
from aos_david import is_connection_request


@pytest.mark.parametrize('text', [
    'can you see my emails from today?', 'Any new emails?', 'Do I have unread mail?',
    'What came in my inbox this morning?', 'Show me my latest emails', 'emails received yesterday',
    'Did Sam reply to my email?', "What's on my schedule today?", 'What meetings do I have? Check my meetings tomorrow',
    'Search Gmail for receipts', 'find the Eventbrite confirmation email', 'What is on my calendar?'])
def test_natural_mailbox_and_schedule_questions_route_to_connections(text):
    assert is_connection_request(text)


@pytest.mark.parametrize('text', [
    'Draft my welcome email sequence for the launch', 'Write an email to the new client introducing EXCO',
    'Improve the email copy on the landing page', 'Update the newsletter section on my website',
    'Review my email campaign plan', 'Add a testimonials section to the services page',
    'Summarise the Business Brain notes on pricing', 'What are my priorities this week?'])
def test_content_website_and_brain_requests_keep_their_route(text):
    assert not is_connection_request(text)


# -- Business Brain history questions never reach the Connections Hub (live miss 2026-11-25) --
from aos_david import is_brain_question

LUCIAN = ("Based on our previous conversations with Lucian, explain how his calendar appointments should be synchronized "
        "across his phone and computer, and what approval safeguards we previously discussed.\n\nUse the Business Brain, "
        "cite the relevant historical conversations, and do not use external research or modify anything.")


@pytest.mark.parametrize('text', [
    LUCIAN, 'What did we previously discuss with Lucian about his calendar?',
    'Check the Business Brain: what did Lucian say about Gmail drafts?',
    'Summarise our earlier calls about calendar approvals', 'In past meetings, how did we handle inbox triage?'])
def test_brain_history_questions_stay_out_of_connections(text):
    assert is_brain_question(text)
    assert not is_connection_request(text)


@pytest.mark.parametrize('text', [
    'Search Gmail for the emails we previously discussed', 'Check my calendar for the meetings from previous calls',
    'Based on previous conversations, add the follow-up to my calendar', 'Did Sam reply to my email from our earlier call?'])
def test_explicit_app_reads_and_writes_still_route_to_connections(text):
    assert not is_brain_question(text)
    assert is_connection_request(text)


def test_screenshot_question_reads_the_platform_gmail(env, monkeypatch):
    hub, provider, identity, root = operator_env(env)
    provider.add(MANAGED_KEY, 'ca_platform_gmail', hub.operator_user())
    hub.refresh(TELEGRAM)
    plans = iter([{'action': 'tool', 'service': 'gmail', 'operation': 'search', 'arguments': '{"query":"newer_than:1d"}', 'reply': ''},
                  {'action': 'reply', 'service': '', 'operation': '', 'arguments': '', 'reply': 'Here are today’s emails.'}])
    monkeypatch.setattr(aos_jobs, 'create', lambda *a, **k: pytest.fail('App read launched a Job'))
    david = David(root, model=lambda *a, **k: {'ok': True, 'status': 'ok', 'json': next(plans), 'usage': {}}, warm=False)
    for who in (DASHBOARD, TELEGRAM):
        result = david.turn('shot-' + who['user'], 'can you see my emails from today?', connection_context=(hub, who))
        assert result['success'] and result['connection_receipts'][0]['operation'] == 'search'
        plans = iter([{'action': 'tool', 'service': 'gmail', 'operation': 'search', 'arguments': '{"query":"newer_than:1d"}', 'reply': ''},
                      {'action': 'reply', 'service': '', 'operation': '', 'arguments': '', 'reply': 'Done.'}])
    accounts = [c['body']['connected_account_id'] for c in provider.calls if c['path'].startswith('/tools/execute/')]
    assert accounts == ['ca_platform_gmail', 'ca_platform_gmail']


# -- Platform app catalogue (live 400 after For You removal, 2026-11-24) --------------------
APPS = [{'slug': 'gmail', 'name': 'Gmail', 'meta': {'description': 'Email'}},
        {'slug': 'googlecalendar', 'name': 'Google Calendar', 'meta': {'description': 'Events'}},
        {'slug': '../bad', 'name': 'bad'}]


def test_catalogue_never_sends_an_empty_search_and_uses_the_linked_project(env):
    hub, provider, *_ = operator_env(env)
    seen = []
    original = hub.composio
    def composio(method, path, key, **kwargs):
        if path == '/toolkits':
            seen.append((key, kwargs['query']))
            if kwargs['query'].get('search') == '':
                raise ConnectionError('Composio refused the request (HTTP 400); no success recorded.')
            return {'items': APPS}
        return original(method, path, key, **kwargs)
    hub.composio = composio
    for who in (DASHBOARD, TELEGRAM):
        assert [a['slug'] for a in hub.catalogue('', who=who)['items']] == ['gmail', 'googlecalendar']
    hub.catalogue('cal', who=DASHBOARD)
    # Dashboard and Telegram share the operator project: the second page comes from the short cache.
    assert seen == [(MANAGED_KEY, {'limit': 48, 'sort_by': 'usage'}), (MANAGED_KEY, {'limit': 48, 'sort_by': 'usage', 'search': 'cal'})]


def test_catalogue_400_falls_back_to_minimal_request_filtered_locally(env):
    hub, provider, *_ = operator_env(env)
    seen = []
    original = hub.composio
    def composio(method, path, key, **kwargs):
        if path == '/toolkits':
            seen.append(kwargs['query'])
            if kwargs['query'] != {'limit': 1000}:
                raise ConnectionError('Composio refused the request (HTTP 400); no success recorded.')
            return {'items': APPS}
        return original(method, path, key, **kwargs)
    hub.composio = composio
    assert [a['slug'] for a in hub.catalogue('calendar', who=DASHBOARD)['items']] == ['googlecalendar']
    assert [a['slug'] for a in hub.catalogue('', who=DASHBOARD)['items']] == ['gmail', 'googlecalendar']
    # The full index is fetched once (cursor-paged, cached) and serves every refused search.
    assert seen == [{'limit': 48, 'sort_by': 'usage', 'search': 'calendar'}, {'limit': 1000},
                    {'limit': 48, 'sort_by': 'usage'}]
    hub.composio = lambda *a, **k: (_ for _ in ()).throw(ConnectionError('Composio rejected the project API key for this request.'))
    with pytest.raises(ConnectionError, match='rejected'):                 # other errors are not masked
        hub.catalogue('', who=DASHBOARD)
