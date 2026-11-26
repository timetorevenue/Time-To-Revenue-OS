"""Universal Composio Platform connections: metadata-driven auth, full catalogue, dynamic tools.

Recording double with the real Composio shapes observed 2026-11-24 (AgentMail = API_KEY only,
no managed auth; Brevo = bring-your-own OAuth2 or API_KEY; Discord/Gmail = managed OAuth2).
Automated contract proof only; real provider proof is recorded separately.
"""
import json

import pytest

from tests.test_composio_projects import A, B, MANAGED_KEY, env  # noqa: F401  (fixture)
import ttros_connections
from ttros_connections import ConnectionError, perform_operator

OP = {'user': 'u-operator', 'workspace': 'ttros-operator', 'email': 'op@example.invalid', 'method': 'cloudflare_access'}


def all_bytes(root):
    return b''.join(p.read_bytes() for p in root.rglob('*') if p.is_file())


def test_api_key_app_on_a_clean_install_gets_a_secure_form_then_connects(env):
    hub, composio, identity, root = env
    form = hub.connect_app(B, 'agent_mail')                     # no project, no auth config, no account
    assert form['status'] == 'NEEDS_INPUT' and form['stage'] == 'account' and form['scheme'] == 'API_KEY'
    assert [f['name'] for f in form['fields']] == ['region', 'generic_api_key']
    key_field = form['fields'][1]
    assert key_field['secret'] and key_field['required'] and key_field['label'] == 'API Key'
    assert form['fields'][0]['default'] == 'us' and not form['fields'][0]['secret']
    assert 'https://console.agentmail.to' in form['help_urls']
    assert not [c for c in composio.calls if c['method'] == 'POST']          # nothing created before the user submits
    secret = 'am_fixture_customer_secret_0001'
    done = hub.connect_app(B, 'agent_mail', resource='fixture@example.invalid', fields={'region': 'us', 'generic_api_key': secret})
    assert done['status'] == 'ACTIVE'
    created = next(c for c in composio.calls if c['path'] == '/auth_configs' and c['method'] == 'POST')
    assert created['body']['auth_config'] == {'type': 'use_custom_auth', 'authScheme': 'API_KEY',
                                              'name': 'TTROS Agent Mail api_key', 'credentials': {}}
    assert composio.secrets_seen == [{'region': 'us', 'generic_api_key': secret, 'status': 'ACTIVE'}]
    row = hub.row(B, done['id'])
    assert row['service'] == 'agent_mail' and row['status'] == 'ACTIVE' and 'tool' in row['operations']
    # Custody: the key went to Composio only. Not in TTROS storage, responses or receipts.
    assert secret.encode() not in all_bytes(root)
    assert secret not in json.dumps(done) and secret not in json.dumps(hub.rows(B)) and secret not in json.dumps(hub.receipts(B))
    # A new Connect asks for credentials. The currently attached account stays enabled.
    assert hub.connect_app(B, 'agent_mail')['status'] == 'NEEDS_INPUT'
    assert hub.row(B, row['id'])['connection_status'] == 'ACTIVE'


def test_cloudflare_rechecks_existing_binding_with_a_single_active_zone(env, monkeypatch):
    hub, composio, *_ = env
    hub.use_managed_project(B)
    user = hub.managed_user(B)
    composio.add(MANAGED_KEY, 'ca_cf_fixture', user, toolkit='cloudflare')
    zone = {'id': 'a' * 32, 'name': 'example.invalid', 'status': 'active'}
    payload = {'success': True, 'result': [zone], 'result_info': {'total_count': 1, 'total_pages': 1}}
    reply = {'successful': True, 'data': {**payload, 'success': False, 'errors': [{'code': 6103}]}}
    original = hub.composio
    checks = []

    def provider(method, path, key, **kwargs):
        assert path != '/tools/execute/proxy'
        if path != '/tools/execute/CLOUDFLARE_LIST_ZONES':
            return original(method, path, key, **kwargs)
        checks.append((method, key, kwargs['body']))
        return reply

    monkeypatch.setattr(hub, 'composio', provider)
    bound = hub.bind_project(B, 'ca_cf_fixture')
    ident = bound['id']
    assert bound['connection_status'] == 'UNVERIFIED'
    for data in (
        {'success': True, 'result': []},
        {**payload, 'result': [zone, {**zone, 'name': 'other.invalid'}]},
        {**payload, 'result_info': {'total_pages': 2}},
        {**payload, 'result': [{**zone, 'status': 'pending'}]},
        {**payload, 'result': [{**zone, 'name': 'https://example.invalid'}]},
    ):
        reply = {'successful': True, 'data': data}
        assert hub.change(B, ident, 'status')['connection_status'] == 'UNVERIFIED'
    reply = {'successful': True, 'data': {'data': payload}}
    active = hub.change(B, ident, 'status')
    assert active['id'] == ident and active['account'] == 'ca_cf_fixture'
    assert active['connection_status'] == 'ACTIVE' and active['identity'] == 'example.invalid'
    assert all(check == ('POST', MANAGED_KEY, {
        'connected_account_id': 'ca_cf_fixture', 'user_id': user, 'version': 'latest',
        'arguments': {'status': 'active', 'per_page': 5},
    }) for check in checks)


def test_brevo_prefers_api_key_and_offers_custom_oauth_without_assuming_managed_auth(env):
    hub, composio, identity, root = env
    form = hub.connect_app(B, 'brevo')
    assert form['scheme'] == 'API_KEY' and form['fields'][0]['name'] == 'generic_api_key'
    assert form['alternatives'] == [{'scheme': 'OAUTH2', 'managed': False, 'oauth': True, 'needs_developer_app': True}]
    # Custom OAuth: the customer's own client fields create the auth config, then hosted consent.
    oauth = hub.connect_app(B, 'brevo', scheme='OAUTH2')
    assert oauth['status'] == 'NEEDS_INPUT' and oauth['stage'] == 'auth_config'
    assert [f['name'] for f in oauth['fields']] == ['client_id', 'client_secret'] and oauth['fields'][1]['secret']
    started = hub.connect_app(B, 'brevo', scheme='OAUTH2', config_fields={'client_id': 'cid-123456', 'client_secret': 'csecret-abcdef'})
    assert started['status'] == 'PENDING' and started['url'].startswith('https://connect.composio.dev/')
    created = next(c for c in composio.calls if c['path'] == '/auth_configs' and c['method'] == 'POST')
    assert created['body']['auth_config']['credentials'] == {'client_id': 'cid-123456', 'client_secret': 'csecret-abcdef'}
    assert not any(c['body'] and c['body'].get('auth_config', {}).get('type') == 'use_composio_managed_auth'
                   for c in composio.calls if c['method'] == 'POST')
    assert b'csecret-abcdef' not in all_bytes(root)


def test_rejected_credential_is_actionable_and_never_echoed(env):
    hub, composio, identity, root = env
    result = hub.connect_app(B, 'brevo', fields={'generic_api_key': 'wrong-key-123'})
    assert result['status'] == 'INVALID_CREDENTIALS' and 'provider rejected' in result['message']
    assert 'wrong-key-123' not in json.dumps(result) and b'wrong-key-123' not in all_bytes(root)
    with pytest.raises(ConnectionError, match='Unexpected field'):
        hub.connect_app(B, 'brevo', fields={'password': 'x' * 10})


def test_no_auth_app_is_available_at_once(env):
    hub, composio, *_ = env
    result = hub.connect_app(B, 'weather')
    assert result['status'] == 'ACTIVE'
    assert hub.change(B, result['id'], 'status')['status'] == 'ACTIVE'
    assert not [c for c in composio.calls if c['method'] == 'POST']


def test_x_removes_expired_attempts_without_provider_checks_and_active_accounts_stay_removed(env):
    hub, composio, identity, root = env
    started = hub.connect_app(B, 'github')
    before = len(composio.calls)
    assert hub.change(B, started['id'], 'remove')['status'] == 'REMOVED'
    assert len(composio.calls) == before and started['id'] not in [r['id'] for r in hub.rows(B)]
    composio.add(MANAGED_KEY, 'ca_b_gmail', hub.managed_user(B))
    bound = hub.bind_project(B, 'ca_b_gmail')
    hub.remove(B, bound['id'])
    hub.refresh(B)                                            # never re-adopted silently
    assert not [r for r in hub.rows(B) if r['account'] == 'ca_b_gmail']
    assert hub.connect_app(B, 'gmail')['status'] == 'PENDING'
    assert not [r for r in hub.rows(B) if r['account'] == 'ca_b_gmail']


def test_catalogue_pages_by_cursor_and_searches_beyond_the_first_page(env):
    hub, composio, *_ = env
    apps = [{'slug': f'app{n:04d}', 'name': f'App {n:04d}', 'meta': {'description': 'generic'}} for n in range(250)]
    apps[237] = {'slug': 'agent_mail', 'name': 'Agent Mail', 'meta': {'description': 'Email inboxes for agents', 'logo': 'https://logos.composio.dev/api/agent_mail'}}
    seen = []
    original = hub.composio

    def provider(method, path, key, **kwargs):
        if path != '/toolkits':
            return original(method, path, key, **kwargs)
        query = kwargs['query']
        seen.append(dict(query))
        rows = [a for a in apps if query.get('search', '').casefold() in (a['name'] + a['slug']).casefold()]
        start = int(query.get('cursor') or 0)
        page = rows[start:start + query['limit']]
        more = start + query['limit'] < len(rows)
        return {'items': page, 'next_cursor': str(start + query['limit']) if more else None, 'total_items': len(rows)}
    hub.composio = provider
    first = hub.catalogue(who=B, limit=100)
    assert len(first['items']) == 100 and first['next_cursor'] == '100' and first['total'] == 250
    pages, cursor = first['items'], first['next_cursor']
    while cursor:
        page = hub.catalogue(who=B, cursor=cursor, limit=100)
        pages += page['items']; cursor = page['next_cursor']
    assert len({a['slug'] for a in pages}) == 250                       # nothing hidden behind page one
    found = hub.catalogue('agent mail', who=B)['items']
    assert [a['slug'] for a in found] == ['agent_mail'] and found[0]['logo'].endswith('/agent_mail')
    assert seen[-1]['search'] == 'agent mail'
    assert [a['slug'] for a in hub.find_apps(B, 'Connect AgentMail', limit=1)] == ['agent_mail']   # full index, no model


def operator(env, tmp_path):
    hub, composio, identity, root = env
    with identity.db() as db:
        db.execute('INSERT OR IGNORE INTO users VALUES(?,?)', (OP['user'], OP['email']))
        db.execute('INSERT OR IGNORE INTO memberships VALUES(?,?)', (OP['user'], OP['workspace']))
    hub.use_managed_project(OP)
    composio.add(MANAGED_KEY, 'ca_op_mail', hub.operator_user(), toolkit='agent_mail')
    composio.inbox_scopes[MANAGED_KEY, 'ca_op_mail'] = 'inbox@example.invalid'
    row = hub.bind_project(OP, 'ca_op_mail', resource='inbox@example.invalid')
    (root / 'queue').mkdir(exist_ok=True)
    (root / 'queue/notifications.json').write_text(json.dumps({'allowlist': {'agentmail_internal': ['ops@example.invalid']}}))
    return hub, composio, row


def test_services_send_allowlisted_notifications_on_the_platform_and_policy_holds_others(env, tmp_path):
    hub, composio, row = operator(env, tmp_path)
    args = {'inbox_id': 'inbox@example.invalid', 'to': ['ops@example.invalid'], 'subject': 'Brief', 'text': 'Body'}
    receipt = perform_operator(tmp_path, 'AGENT_MAIL_SEND_EMAIL', args, target='ops@example.invalid',
                               notification_kind='operational_notification', idempotency_key='brief-1', hub=hub)
    assert receipt['provider_verified'] and receipt['tool'] == 'AGENT_MAIL_SEND_EMAIL'
    assert receipt['authorization']['basis'] == 'internal_mail_allowlist'
    sent = [c for c in composio.calls if c['path'] == '/tools/execute/AGENT_MAIL_SEND_EMAIL']
    assert sent[0]['body']['connected_account_id'] == 'ca_op_mail' and sent[0]['body']['version'] == '20261120_00'
    again = perform_operator(tmp_path, 'AGENT_MAIL_SEND_EMAIL', args, target='ops@example.invalid',
                             notification_kind='operational_notification', idempotency_key='brief-1', hub=hub)
    assert again['replayed'] and len([c for c in composio.calls if c['path'] == '/tools/execute/AGENT_MAIL_SEND_EMAIL']) == 1
    for target, kind in (('someone@example.invalid', 'operational_notification'), ('ops@example.invalid', None)):
        with pytest.raises(ConnectionError, match='Runtime policy held'):
            perform_operator(tmp_path, 'AGENT_MAIL_SEND_EMAIL', {**args, 'to': [target], 'subject': 'x'}, target=target,
                             notification_kind=kind, hub=hub)
    with pytest.raises(ConnectionError, match='recipients do not match'):
        perform_operator(tmp_path, 'AGENT_MAIL_SEND_EMAIL', {**args, 'to': ['someone@example.invalid']},
                         target='ops@example.invalid', notification_kind='operational_notification', hub=hub)
    assert len([c for c in composio.calls if c['path'] == '/tools/execute/AGENT_MAIL_SEND_EMAIL']) == 1


def test_missing_app_gives_an_actionable_answer(env, tmp_path):
    hub, composio, row = operator(env, tmp_path)
    with pytest.raises(ConnectionError, match='brevo is not connected to TTROS. Connect it once in Settings'):
        perform_operator(tmp_path, 'BREVO_GET_ACCOUNT', {}, hub=hub, expected_mode='read')


def test_generic_tools_are_discoverable_and_read_write_is_enforced(env, tmp_path):
    hub, composio, row = operator(env, tmp_path)
    found = hub.execute(OP, row['id'], 'tools', {'query': 'account'})
    assert found['provider_verified'] is False and [t['tool'] for t in found['result']['tools']] == ['BREVO_GET_ACCOUNT', 'BREVO_SEND_TRANSACTIONAL_EMAIL']
    assert [t['mode'] for t in found['result']['tools']] == ['read', 'write']
    with pytest.raises(ConnectionError, match='belongs to gmail'):
        hub.execute(OP, row['id'], 'tool', {'tool': 'GMAIL_FETCH_EMAILS', 'arguments': {}})
    with pytest.raises(ConnectionError, match='allows only read tools'):
        hub.execute(OP, row['id'], 'tool', {'tool': 'AGENT_MAIL_SEND_EMAIL', 'arguments': {}}, expected_mode='read')
    read = hub.execute(OP, row['id'], 'tool', {'tool': 'AGENT_MAIL_LIST_INBOXES', 'arguments': {}})
    assert read['provider_verified'] and read['authorization']['basis'] == 'routine_business_operation'


def test_forge_agents_resolve_any_connected_app_through_generic_tools(env, tmp_path):
    hub, composio, row = operator(env, tmp_path)
    manifest = {'agent': {'name': 'ops.mail', 'version': '1.0.0'},
                'capabilities': {'grants': ['connector.read:agent_mail', 'connector.write:agent_mail', 'connector.read:brevo']}}
    resolved = hub.resolve(OP, manifest)
    assert resolved['resolved']['connector.read:agent_mail'][0]['operations'] == ['tools', 'tool']
    assert resolved['resolved']['connector.write:agent_mail'][0]['operations'] == ['tool']
    assert resolved['missing_required'] == ['connector.read:brevo']
    assert 'No account for this app' in resolved['missing_reasons']['connector.read:brevo']


def test_david_connects_apps_by_name_and_by_need_without_model_calls(env, monkeypatch):
    from aos_david import David, connect_request
    hub, composio, identity, root = env
    assert connect_request('Connect AgentMail') == 'AgentMail'
    assert connect_request('Please connect an application that can help me manage Facebook advertising.').startswith('an application')
    assert connect_request('Search Gmail for invoices') == ''
    apps = [{'slug': 'agent_mail', 'name': 'Agent Mail', 'meta': {'description': 'Email inboxes'}},
            {'slug': 'metaads', 'name': 'Meta Ads', 'meta': {'description': 'Manage Facebook and Instagram advertising'}},
            {'slug': 'facebook', 'name': 'Facebook', 'meta': {'description': 'Pages and posts'}}]
    original = hub.composio
    hub.composio = lambda m, p, k, **kw: {'items': apps} if p == '/toolkits' else original(m, p, k, **kw)
    model = lambda *a, **k: pytest.fail('connection management must not call a model')
    david = David(root, model=model, warm=False)
    reply = david.turn('dash', 'Connect AgentMail', connection_context=(hub, B))
    assert reply['model_calls'] == 0 and reply['connection_status'] == 'NEEDS_INPUT'
    assert '/settings/connections?app=agent_mail' in reply['response'] and 'Do not paste keys' in reply['response']
    reply = david.turn('dash', 'Connect an application that can help me manage Facebook advertising', connection_context=(hub, B))
    assert 'metaads' in reply['candidates'][:3] and 'Connect <app name>' in reply['response']
