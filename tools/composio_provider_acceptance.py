"""Internal provider acceptance using existing systemd encrypted credentials.

Run provisioning interactively on the server, never through agent command input.
No production service/configuration changes. Provider writes occur only when
explicitly starting the owner-authorized hosted test.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import pwd
import stat
import secrets
from pathlib import Path
import subprocess
import sys
import tempfile
import warnings

REF = 'TTROS_CONNECTOR_SECRET_COMPOSIO_TEST'
STORE = Path('/var/lib/ttros/acceptance')

# Read-only witness independently verified with the existing user CLI login,
# explicitly scoped by COMPOSIO_ORG_ID and COMPOSIO_PROJECT_ID (2026-11-23).
# Composio project resources are isolated: a project key that can retrieve this
# exact account and matching owner/config addresses the same project. This is
# verification evidence only; it never grants/binds the acceptance user to TTROS.
PROJECT_WITNESSES = {
    'pr_sJPPh34mNls8': {
        'account': 'ca_EXAMPLEACCT01', 'owner': 'ttros-acceptance-casey-20261120',
        'toolkit': 'github', 'auth_config': 'ac_EXAMPLEAUTH01',
    },
}


def verify_project_witness(expected, key, request):
    witness = PROJECT_WITNESSES.get(expected)
    if not witness:
        return {'result': 'UNVERIFIED', 'reason': 'no_independently_verified_project_resource'}
    account = request('GET', '/connected_accounts/' + witness['account'], key)
    matched = (account.get('id') == witness['account'] and account.get('user_id') == witness['owner']
               and (account.get('toolkit') or {}).get('slug') == witness['toolkit']
               and (account.get('auth_config') or {}).get('id') == witness['auth_config'])
    return {'result': 'PASS' if matched else 'UNVERIFIED', 'project_ids': [expected] if matched else [],
            'method': 'independently_project_scoped_account_resource',
            'exact_account_owner_config_match': matched}


def provision_runtime_project(expected):
    """Re-envelope the existing verified key for the backend user; stage service references.

    Uses systemd-creds' native host-secret path selection, not a sudo runtime
    dependency. The original root credential and provider key remain unchanged.
    No plaintext key is written, placed in argv/env, printed or logged.
    """
    from ttros_connections import MANAGED_REF, composio_http
    if os.geteuid() != 0 or expected not in PROJECT_WITNESSES:
        raise ValueError('Root and an independently verified project resource are required')
    key = test_key()
    verified = verify_project_witness(expected, key, composio_http)
    if verified['result'] != 'PASS':
        raise ValueError('Existing credential project association was not verified')
    operator = pwd.getpwnam(__import__('getpass').getuser())
    home = Path(operator.pw_dir)
    folder = home / '.config/ttros/credentials/composio'
    for directory in (home / '.config/ttros', home / '.config/ttros/credentials', folder):
        if not directory.exists():
            directory.mkdir(mode=0o700)
            os.chown(directory, operator.pw_uid, operator.pw_gid)
        meta = directory.lstat()
        if (not stat.S_ISDIR(meta.st_mode) or directory.is_symlink()
                or meta.st_uid != operator.pw_uid or meta.st_mode & 0o077):
            raise ValueError('Runtime credential directory must be private and operator-owned')
    host_secret = folder / 'host.secret'
    runtime_env = {**os.environ, 'SYSTEMD_CREDENTIAL_SECRET': str(host_secret),
                   'TTROS_CONNECTOR_ENCRYPTED_DIRECTORY': str(folder)}
    runtime_env.pop('CREDENTIALS_DIRECTORY', None)
    run_as_operator = ['/usr/sbin/runuser', '-u', operator.pw_name, '--']
    setup = subprocess.run(run_as_operator + ['/usr/bin/systemd-creds', 'setup'],
                           env=runtime_env, capture_output=True, timeout=15)
    if setup.returncode:
        raise ValueError('Native operator host-secret setup failed; original credential unchanged')
    meta = host_secret.lstat()
    if (not stat.S_ISREG(meta.st_mode) or host_secret.is_symlink()
            or meta.st_uid != operator.pw_uid or meta.st_mode & 0o077):
        raise ValueError('Native operator host secret is not private')
    target = folder / (MANAGED_REF + '.cred')
    created = False
    if not target.exists():
        encrypted = subprocess.run(run_as_operator + ['/usr/bin/systemd-creds', 'encrypt', '--with-key=host',
                                   '--name=' + MANAGED_REF, '-', '-'], input=key.encode(), env=runtime_env,
                                   capture_output=True, timeout=15)
        if encrypted.returncode or not encrypted.stdout:
            raise ValueError('Native credential encryption failed; original credential unchanged')
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            os.fchown(handle.fileno(), operator.pw_uid, operator.pw_gid)
            handle.write(encrypted.stdout)
            handle.flush(); os.fsync(handle.fileno())
        created = True
    # Verify through the unchanged TTROS reader, as casey, without sudo. The child
    # emits only sanitized project proof; plaintext never leaves process memory.
    code = (
        'import sys,json;sys.path.insert(0,sys.argv[1]);'
        'from ttros_connections import credential,MANAGED_REF,composio_http;'
        'from composio_provider_acceptance import verify_project_witness;'
        'print(json.dumps(verify_project_witness(sys.argv[2],credential(MANAGED_REF),composio_http)))'
    )
    check = subprocess.run(run_as_operator + [str(home / 'agentic-os-live/.venv/bin/python'), '-B', '-c', code,
                           str(Path(__file__).resolve().parent), expected], env=runtime_env,
                           capture_output=True, text=True, timeout=45)
    try:
        unprivileged = json.loads(check.stdout) if check.returncode == 0 else {}
    except ValueError:
        unprivileged = {}
    if unprivileged.get('result') != 'PASS':
        raise ValueError('Backend-user credential/project read failed; existing files preserved')
    dropin = home / '.config/systemd/user/aos-backend.service.d/62-composio-project-credential.conf.pending'
    content = ('[Service]\nEnvironment="TTROS_CONNECTOR_ENCRYPTED_DIRECTORY=' + str(folder) + '"\n'
               'Environment="SYSTEMD_CREDENTIAL_SECRET=' + str(host_secret) + '"\n')
    if dropin.exists():
        if dropin.is_symlink() or dropin.read_text() != content:
            raise ValueError('A different pending service configuration exists; it was preserved')
    else:
        fd = os.open(dropin, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as handle:
            os.fchown(handle.fileno(), operator.pw_uid, operator.pw_gid)
            handle.write(content)
    print(json.dumps({'result': 'PASS', 'project': expected, 'original_credential_preserved': True,
                      'same_provider_key_reenveloped': created, 'backend_user_read_verified': True,
                      'runtime_requires_sudo': False, 'service_configuration_staged': str(dropin),
                      'service_restarted': False}))


def encrypt_key(value, target, *, run=subprocess.run):
    from ttros_connections import PROJECT_KEY
    if not PROJECT_KEY.fullmatch(value):
        raise ValueError('Invalid project key format')
    result = run(['/usr/bin/systemd-creds', 'encrypt', '--with-key=host', '--name=' + REF, '-', '-'],
                 input=value.encode(), capture_output=True, timeout=15)
    if result.returncode or not result.stdout:
        raise ValueError('OS encryption failed; no key was stored')
    # Exclusive creation: an existing credential can never be replaced here.
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(result.stdout)


def provision():
    if os.geteuid() != 0 or not sys.stdin.isatty() or not sys.stderr.isatty():
        raise ValueError('Provision only from an interactive server terminal using sudo')
    STORE.mkdir(parents=True, exist_ok=True, mode=0o700)
    if STORE.is_symlink() or STORE.stat().st_uid != 0 or STORE.stat().st_mode & 0o077:
        raise ValueError('Credential directory must be private and root-owned')
    if (STORE / (REF + '.cred')).exists():
        raise ValueError('An internal test credential already exists; refusing replacement')
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        value = getpass.getpass('Existing isolated TEST project API key (hidden): ')
    encrypt_key(value, STORE / (REF + '.cred'))
    print('Encrypted internal test credential stored; production unchanged.')


def test_key():
    from ttros_connections import credential
    os.environ.pop('CREDENTIALS_DIRECTORY', None)
    os.environ['TTROS_CONNECTOR_ENCRYPTED_DIRECTORY'] = str(STORE)
    return credential(REF)


def verify_project_identity(expected):
    """Read-only staged diagnostic; never print a key, raw body or exception text."""
    from ttros_connections import composio_http
    if not re.fullmatch(r'(?:pr|proj)_[A-Za-z0-9_-]+', expected):
        raise ValueError('Invalid expected project ID')
    result = {'result': 'UNVERIFIED', 'stage': 'credential_reader', 'expected_project': expected}
    reasons = {
        'Encrypted credential must be a private regular file owned by the backend operator.': 'credential_owner_or_permissions_mismatch',
        'Encrypted credential has an invalid size.': 'encrypted_credential_size_invalid',
        'The encrypted credential could not be decrypted by this runtime. Check its name, host binding and expiry securely.': 'os_decryption_failed',
        'Encrypted API-key credential is unavailable; provision it through OS secret storage. No credential details were logged.': 'encrypted_credential_unavailable',
        'Composio rejected the project API key for this request.': 'credential_rejected_for_this_endpoint',
        'Composio could not find that project resource.': 'endpoint_or_resource_not_found',
        'Composio API unreachable; nothing was changed.': 'provider_unreachable',
        'Composio rate-limited the request; retry later.': 'provider_rate_limited',
    }
    try:
        path = STORE / (REF + '.cred')
        result['root'] = os.geteuid() == 0
        result['credential_file_exists'] = path.is_file()
        if path.exists():
            meta = path.lstat()
            result['credential_owner_uid'] = meta.st_uid
            result['credential_mode'] = oct(meta.st_mode & 0o777)
        key = test_key()
        result['credential_read'] = True
        result['stage'] = 'project_api_acceptance'
        composio_http('GET', '/connected_accounts', key, query={'limit': 1})
        result['credential_accepted'] = True
        if expected in PROJECT_WITNESSES:
            result['stage'] = 'project_resource_identity'
            result.update(verify_project_witness(expected, key, composio_http))
            print(json.dumps(result))
            return 0 if result['result'] == 'PASS' else 1
        result['stage'] = 'project_identity'
        try:
            info = composio_http('GET', '/auth/session/info', key)
        except Exception as exc:
            if str(exc) != 'Composio rejected the project API key for this request.':
                raise
            # A valid project key need not have access to this user/session
            # endpoint. Verify a known isolated project resource instead.
            result['identity_endpoint'] = 'not_available_to_this_credential'
            result['stage'] = 'project_resource_identity'
            result.update(verify_project_witness(expected, key, composio_http))
            print(json.dumps(result))
            return 0 if result['result'] == 'PASS' else 1
        ids = set()
        def inspect(value, project=False):
            if isinstance(value, dict):
                for name, item in value.items():
                    identity = name in ('project_id', 'projectId', 'project_nano_id', 'projectNanoId')
                    identity |= project and name in ('id', 'nano_id', 'nanoId')
                    if identity and isinstance(item, str) and re.fullmatch(r'(?:pr|proj)_[A-Za-z0-9_-]+', item):
                        ids.add(item)
                    if isinstance(item, (dict, list)):
                        inspect(item, project or name in ('project', 'current_project', 'project_details'))
            elif isinstance(value, list):
                for item in value:
                    inspect(item, project)
        inspect(info)
        result['project_ids'] = sorted(ids)
        result['result'] = 'PASS' if ids == {expected} else 'MISMATCH' if ids else 'UNVERIFIED'
        if not ids:
            result['reason'] = 'identity_endpoint_omitted_project_id'
    except Exception as exc:
        result['reason'] = reasons.get(str(exc), 'unexpected_failure_details_withheld')
    print(json.dumps(result))
    return 0 if result['result'] == 'PASS' else 1


def public_metadata(row):
    """Allowlist metadata; connected-account responses may include credentials."""
    result = {name: row.get(name) for name in
              ('id', 'user_id', 'status', 'is_disabled', 'auth_scheme', 'is_composio_managed')
              if name in row}
    if isinstance(row.get('toolkit'), dict):
        result['toolkit'] = {'slug': row['toolkit'].get('slug')}
    if isinstance(row.get('auth_config'), dict):
        result['auth_config'] = {name: row['auth_config'].get(name) for name in
                                 ('id', 'auth_scheme', 'is_composio_managed', 'is_disabled')}
    return result


def inspect_project():
    """Read only this explicitly provisioned test project's account metadata."""
    from ttros_connections import composio_http
    key = test_key()
    evidence = {'mode': 'test_project_metadata_only', 'provider_writes': 0, 'responses': []}
    all_configs = []
    for path in ('/connected_accounts', '/auth_configs'):
        page = composio_http('GET', path, key, query={'limit': 100})
        if path == '/auth_configs':
            all_configs = page.get('items', [])
        evidence['responses'].append({'method': 'GET', 'path': path, 'response': {
            'items': [public_metadata(row) for row in page.get('items', [])],
            'has_next_page': bool(page.get('next_cursor'))}})
    for service in ('gmail', 'github'):
        page = composio_http('GET', '/auth_configs', key, query={'toolkit_slug': service, 'limit': 100})
        rows = page.get('items', [])
        evidence['responses'].append({'method': 'GET', 'path': '/auth_configs',
                                     'query': {'toolkit_slug': service},
                                     'response': {'items': [public_metadata(row) for row in rows]},
                                     'filter_honored': all((row.get('toolkit') or {}).get('slug') == service for row in rows) if all_configs else None})
    print(json.dumps(evidence, indent=2))


def probe(user_id, account_id=None):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'dashboard/backend'))
    from dashboard_identity import Identity
    from ttros_connections import Connections, SecretVault, credential, composio_http, ConnectionError
    # Use the existing credential reader, with a narrowly scoped directory.
    os.environ.pop('CREDENTIALS_DIRECTORY', None)
    os.environ['TTROS_CONNECTOR_ENCRYPTED_DIRECTORY'] = str(STORE)
    key = credential(REF)
    who = dict(user='internal-acceptance', workspace='isolated-composio-test', method='google_oidc')
    checks = {}
    with tempfile.TemporaryDirectory(prefix='ttros-composio-acceptance-') as directory:
        root = Path(directory)
        master = secrets.token_bytes(32)
        vault = SecretVault(root / 'vault', key_source=lambda: master)
        # Ephemeral vault key: only in memory, never a production credential.
        hub = Connections(Identity(root), vault=vault, managed_key=lambda: key)
        state = hub.link_project(who, key, user_id)
        checks['existing_project_verified'] = state['status'] == 'LINKED'
        checks['key_absent_from_responses'] = key not in json.dumps(state)
        checks['key_absent_from_plaintext_storage'] = all(key.encode() not in p.read_bytes() for p in root.rglob('*') if p.is_file())
        other = dict(who, workspace='isolated-other-workspace')
        try:
            vault.get(hub._project_row(who)['secret'], other['workspace'])
            checks['cross_workspace_vault_denied'] = False
        except ConnectionError:
            checks['cross_workspace_vault_denied'] = True
        checks['other_workspace_has_no_project'] = hub.project_state(other) is None
        try:
            hub.link_project(dict(who, user='other-member'), key, user_id)
            checks['other_member_cannot_replace_project'] = False
        except ConnectionError:
            checks['other_member_cannot_replace_project'] = True
        before = hub.project_state(other)
        try:
            hub.link_project(other, 'ak_deliberately_invalid_acceptance_key')
            checks['real_invalid_key_denied'] = False
        except ConnectionError:
            checks['real_invalid_key_denied'] = hub.project_state(other) == before

        page = composio_http('GET', '/connected_accounts', key, query={'user_ids': user_id, 'limit': 100})
        items = page.get('items', [])
        checks['list_items_include_user_id'] = all(bool(row.get('user_id')) for row in items) if items else None
        accounts = hub.project_accounts(who)
        verified = [hub._project_account(who, key, user_id, row['account']) for row in accounts]
        checks['owned_accounts_verified_by_id'] = len(verified)
        filtered = composio_http('GET', '/auth_configs', key, query={'toolkit_slug': 'gmail', 'limit': 100})
        rows = filtered.get('items', [])
        checks['gmail_auth_config_filter_honored'] = all((r.get('toolkit') or {}).get('slug') == 'gmail' for r in rows) if rows else None
        checks['active_account_reuse_and_read'] = None
        for account in accounts:
            if account_id and account['account'] == account_id and account['status'] == 'ACTIVE' and account['permissions']:
                row = hub.bind_project(who, account['account'])
                receipt = hub.execute(who, row['id'], account['permissions'][0])
                checks['active_account_reuse_and_read'] = receipt['provider_verified']
                break
        checks['hosted_authorization'] = 'Not checked by this read-only probe; use --verify-hosted for the saved attempt'
    # Only booleans/counts, never keys, account data or raw provider responses.
    print(json.dumps(checks, indent=2))


def hosted_runtime():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'dashboard/backend'))
    from dashboard_identity import Identity
    from ttros_connections import Connections, ConnectionError
    key = test_key()
    root = STORE / 'runtime'
    root.mkdir(mode=0o700, exist_ok=True)
    if root.is_symlink() or root.stat().st_uid != os.geteuid() or root.stat().st_mode & 0o077:
        raise ValueError('Acceptance runtime is not private')
    hub = Connections(Identity(root), managed_key=lambda: key)
    who = dict(user='internal-acceptance', workspace='isolated-composio-hosted', method='google_oidc')
    return hub, who, key, root


def start_hosted(service, expected_identity, user_id, owner_selects=False):
    """Only call after the owner names this exact test-provider identity."""
    from ttros_connections import COMPOSIO_USER, AUTH_CONFIG_ID
    if (service not in ('gmail', 'github') or not COMPOSIO_USER.fullmatch(str(user_id or ''))
            or not (expected_identity or owner_selects)):
        raise ValueError('Name the dedicated user and test-provider identity or explicit owner selection')
    hub, who, _, root = hosted_runtime()
    attempt_file = root / 'attempt.json'
    if attempt_file.exists():
        raise ValueError('An authorization attempt already exists; verify it instead')
    if not hub.project_state(who):
        hub.use_managed_project(who)
    with hub.identity.db() as db:
        db.execute('INSERT OR REPLACE INTO hub_project_users VALUES(?,?,?)',
                   (who['workspace'], who['user'], user_id))
        cached = db.execute('SELECT auth_config FROM hub_auth_configs WHERE project=? AND toolkit=?', ('managed', service)).fetchone()
    if not cached:
        # Acceptance config only: scope the hosted grant to profile reads.
        # The customer runtime remains unchanged.
        scopes = 'read:user' if service == 'github' else 'https://www.googleapis.com/auth/gmail.metadata'
        tool = 'GITHUB_GET_THE_AUTHENTICATED_USER' if service == 'github' else 'GMAIL_GET_PROFILE'
        created = hub.composio('POST', '/auth_configs', hub.managed_key(), body={
            'toolkit': {'slug': service}, 'auth_config': {'type': 'use_composio_managed_auth',
            'name': 'TTROS internal acceptance profile read', 'credentials': {'scopes': scopes},
            'restrict_to_following_tools': [tool]}})
        config_id = (created.get('auth_config') or {}).get('id')
        if not AUTH_CONFIG_ID.fullmatch(str(config_id or '')):
            raise ValueError('Provider returned an invalid auth config ID')
        with hub.identity.db() as db:
            db.execute('INSERT INTO hub_auth_configs(project,toolkit,auth_config) VALUES(?,?,?)', ('managed', service, config_id))
    started = hub.connect_app(who, service)
    attempt = {'id': started['id'], 'account': hub.row(who, started['id'])['account'],
               'service': service, 'expected_identity': expected_identity}
    fd = os.open(attempt_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(attempt, handle)
    # Explicit owner-requested URL only; never put it in a committed receipt.
    print(json.dumps({'authorization_url': started['url'], 'account': attempt['account'],
                      'status': started['status'], 'composio_user_id': hub.project_state(who)['composio_user_id'],
                      'manual_action': 'Open the URL and authorize only the named test account; then run --verify-hosted.'}, indent=2))


def verify_hosted():
    from ttros_connections import ConnectionError, SecretVault, Connections
    hub, who, key, root = hosted_runtime()
    attempt = json.loads((root / 'attempt.json').read_text())
    row = hub.change(who, attempt['id'], 'status')
    checks = {'issued_account': attempt['account'], 'status': row['status'], 'active': row['status'] == 'ACTIVE',
              'exact_account_owner_and_toolkit_verified_by_id': True}
    if not checks['active']:
        print(json.dumps(checks, indent=2))
        return
    if row['account'] != attempt['account'] or row['service'] != attempt['service']:
        raise ValueError('Issued account or toolkit mismatch')
    # Verify this exact account can be reused through existing-project linking.
    with tempfile.TemporaryDirectory(prefix='ttros-existing-test-') as directory:
        from dashboard_identity import Identity
        master = secrets.token_bytes(32)
        reuse = Connections(Identity(Path(directory)), composio=hub.composio,
                            vault=SecretVault(Path(directory) / 'vault', key_source=lambda: master))
        reuse.link_project(who, key, hub.project_state(who)['composio_user_id'])
        bound = reuse.bind_project(who, attempt['account'])
        checks['existing_account_reused'] = bound['account'] == row['account']
    other = dict(who, workspace='isolated-other-workspace')
    hub.use_managed_project(other)
    for label, principal in [('wrong_workspace', other), ('wrong_member', dict(who, user='other-member'))]:
        try:
            hub.bind_project(principal, attempt['account'])
            checks[label + '_ownership_denied'] = False
        except ConnectionError:
            checks[label + '_ownership_denied'] = True
        try:
            hub.execute(principal, row['id'], 'profile')
            checks[label + '_execution_denied'] = False
        except ConnectionError:
            checks[label + '_execution_denied'] = True
    manifest = {'agent': {'name': 'shared.acceptance', 'version': '0.1.0'},
                'capabilities': {'grants': ['connector.read:' + row['service'] + ':profile']}}
    checks['forge_discovers_connection'] = not hub.resolve(who, manifest)['missing_required']
    hub.access(who, row['id'], 'Restricted', {})
    checks['restricted_agent_not_discovered'] = bool(hub.resolve(who, manifest)['missing_required'])
    hub.access(who, row['id'], 'Disabled', {})
    try:
        hub.execute(who, row['id'], 'profile')
        checks['disabled_execution_denied'] = False
    except ConnectionError:
        checks['disabled_execution_denied'] = True
    hub.access(who, row['id'], 'Automatic', {})
    receipt = hub.execute(who, row['id'], 'profile')
    data = receipt['result']
    actual = data.get('emailAddress') if row['service'] == 'gmail' else data.get('login')
    checks['permitted_read'] = receipt['provider_verified']
    checks['provider_identity_verified'] = bool(actual)
    checks['provider_identity'] = actual
    checks['named_identity_matches'] = (str(actual or '').casefold() == attempt['expected_identity'].casefold()) if attempt['expected_identity'] else 'owner selected account in hosted consent'
    print(json.dumps(checks, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start-hosted', choices=('gmail', 'github'), help='Create one hosted test authorization after the owner names the account')
    parser.add_argument('--owner-selects-account', action='store_true', help='Owner explicitly chooses the account during hosted consent')
    parser.add_argument('--expected-identity', help='Exact named Gmail address or GitHub login authorized for the hosted test')
    parser.add_argument('--verify-hosted', action='store_true', help='Check the exact saved attempt, isolation and one permitted profile read')
    parser.add_argument('--inspect', action='store_true', help='Read-only test-project account/config metadata; no account selected or authorized')
    parser.add_argument('--provision', action='store_true', help='Hidden terminal input -> new encrypted test credential; refuses overwrite')
    parser.add_argument('--account-id', help='Explicit existing test account to reuse/read; never choose an account automatically')
    parser.add_argument('--test-user-id', help='Existing isolated project user ID; enables read-only acceptance after provisioning')
    parser.add_argument('--verify-project-id', help='Read-only credential/API/project-identity diagnostic; outputs allowlisted metadata only')
    parser.add_argument('--provision-runtime-project', help='Root: re-envelope the existing verified key for the backend user and stage service references; never restart')
    args = parser.parse_args()
    try:
        if args.provision_runtime_project:
            if any((args.provision, args.start_hosted, args.inspect, args.verify_hosted, args.test_user_id, args.account_id, args.verify_project_id)):
                parser.error('--provision-runtime-project must be used alone')
            provision_runtime_project(args.provision_runtime_project)
            return 0
        if args.verify_project_id:
            if any((args.provision, args.start_hosted, args.inspect, args.verify_hosted, args.test_user_id, args.account_id)):
                parser.error('--verify-project-id must be used alone')
            return verify_project_identity(args.verify_project_id)
        if args.start_hosted and (not args.test_user_id or not (args.expected_identity or args.owner_selects_account)):
            parser.error('--start-hosted requires --test-user-id and --expected-identity or --owner-selects-account')
        if args.provision:
            provision()
        if args.start_hosted:
            start_hosted(args.start_hosted, args.expected_identity, args.test_user_id, args.owner_selects_account)
        if args.verify_hosted:
            verify_hosted()
        if args.inspect:
            inspect_project()
        if args.test_user_id and not args.start_hosted:
            probe(args.test_user_id, args.account_id)
        elif not any((args.provision, args.inspect, args.start_hosted, args.verify_hosted)):
            parser.error('use --provision or --test-user-id')
    except Exception:
        # Subprocess/provider exceptions may carry sensitive context. Never echo
        # their text, arguments, environment or traceback.
        print('Acceptance stopped safely. Check credential availability, ownership and test-user metadata on the server.', file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
