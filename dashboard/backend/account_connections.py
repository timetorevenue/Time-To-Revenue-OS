"""Dashboard provider panel over the one Connections Hub (Composio Platform).

Tokens stay at Composio; there is no second credential platform or application key.
Model/browser-supplied owner/account IDs are refused.
"""
import json
import os
import time
import datetime as dt
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from dashboard_identity import private_response
from ttros_connections import Connections, ConnectionError

ROOT = Path(__file__).resolve().parents[2]


class Broker:
    """Dashboard provider panel over the one Connections Hub (Composio Platform).

    Connect, status, disconnect and the fixed reviewed reads all use the member's linked
    (or TTROS-managed) Platform project. There is no separate application key.
    """
    READS = {('vercel', 'deployment'): 'deployment.read', ('github', 'source'): 'source.read',
             ('googledrive', 'files'): 'files.read', ('gmail', 'labels'): 'mail.read'}

    def __init__(self, identity, transport=None):
        self.identity, self.transport = identity, transport
        adapter = (lambda method, path, key, query=None, body=None:
                   self.transport(method, path.lstrip('/'), body if body is not None else query)) if transport else None
        self.hub = Connections(identity, **({'composio': adapter} if adapter else {}))

    def config(self):
        return json.loads((ROOT / 'context/account_connections.json').read_text())

    def provider(self, name):
        cfg = self.config()['providers'].get(name)
        if not cfg:
            raise HTTPException(404, 'Unknown provider')
        return cfg

    def configuration(self, who, name):
        cfg = self.provider(name)
        missing = [] if cfg.get('read_only_reviewed') is True else [f'providers.{name}.read_only_reviewed after provider scope review']
        identity_blocker = 'This server is using the local preview identity. Sign in with configured Google OIDC or enter through the existing verified Access/Tailscale operator identity before authorizing a provider.' if who['method'] == 'local_preview' else None
        return {'configured': not missing, 'missing_configuration': missing, 'identity_blocker': identity_blocker,
                'authorization_available': not missing and not identity_blocker, 'setup': name}

    def binding(self, who, name):
        cfg = self.provider(name)
        rows = [r for r in self.hub.rows(who) if r['service'] == cfg['toolkit'] and r['config'].get('source') == 'project']
        rows.sort(key=lambda r: (not r['enabled'], r['status'] != 'ACTIVE'))
        if not rows:
            return None
        row = rows[0]
        granted = row['config'].get('panel_permissions', cfg['permissions'])
        return {'user': who['user'], 'workspace': who['workspace'], 'provider': name, 'account': row['account'],
                'enabled': row['enabled'], 'permissions': json.dumps(granted), 'status': row['connection_status'], 'id': row['id']}

    def connect(self, who, name, *, reconnect=False):
        cfg = self.provider(name)
        configuration = self.configuration(who, name)
        if not configuration['authorization_available']:
            reasons = configuration['missing_configuration'] + ([configuration['identity_blocker']] if configuration['identity_blocker'] else [])
            raise HTTPException(403 if configuration['identity_blocker'] else 503,
                                {**configuration, 'code': 'setup_required', 'message': cfg['label'] + ' authorization is unavailable: ' + '; '.join(reasons), 'instructions': cfg['setup']})
        try:
            current = self.binding(who, name) if reconnect else None
            result = self.hub.connect_app(who, cfg['toolkit'], replace=current['id'] if current else None)
        except (ConnectionError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from None
        self.identity.receipt(who, 'connection.start', name)
        return result

    def refresh(self, who, name):
        row = self.binding(who, name)
        if not row or not row['enabled']:
            raise HTTPException(409, 'Connect this provider first')
        try:
            status = self.hub.change(who, row['id'], 'status')['connection_status']
        except ConnectionError as exc:
            raise HTTPException(403, str(exc)) from None
        self.identity.receipt(who, 'connection.status', name + ':' + status)
        return {'status': status}

    def disconnect(self, who, name):
        cfg = self.provider(name)
        row = self.binding(who, name)
        if not row or not row['enabled']:
            return {'status': 'DISCONNECTED' if row else 'NOT_CONNECTED', 'changed': False,
                    'message': f"{cfg['label']}: " + ('this TTROS binding is already disconnected.' if row else 'no account is linked to this workspace member; nothing was disconnected.'),
                    'upstream_revocation': 'Existing provider and Composio accounts were not changed.'}
        # Disconnect means revoke TTROS/David use immediately, preserving the upstream account.
        self.hub.change(who, row['id'], 'disconnect')
        self.identity.receipt(who, 'connection.disconnect', name)
        return {'status': 'DISCONNECTED', 'changed': True, 'message': cfg['label'] + ' disconnected from this workspace member. TTROS and David can no longer use this binding.',
                'upstream_revocation': 'The upstream account is preserved. Use the provider account security page to revoke consent separately.'}

    def health(self, who, name):
        cfg = self.provider(name)
        row = self.binding(who, name)
        configuration = self.configuration(who, name)
        checked_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
        if not row or not row['enabled']:
            return {**configuration, 'status': 'DISCONNECTED' if row else 'NOT_CONNECTED', 'checked_at': checked_at, 'provider_checked': False,
                    'message': cfg['label'] + (': this TTROS binding is disconnected. Reconnect to authorize it again.' if row else ': no account is linked to this workspace member. Connect after completing the listed setup.'),
                    'health': 'not_connected', 'status_source': 'TTROS workspace binding; provider was not contacted'}
        state = self.refresh(who, name)['status']
        return {**configuration, 'status': state, 'checked_at': checked_at, 'provider_checked': True, 'status_source': 'Composio connected-account status',
                'health': 'active' if state == 'ACTIVE' else 'authorization_required',
                'message': cfg['label'] + (': Composio confirms this connection is ACTIVE.' if state == 'ACTIVE' else f': Composio reports {state}. Complete consent if pending, or reconnect if expired/revoked.')}

    def read(self, who, name, operation):
        cfg = self.provider(name)
        site = self.config()['website']
        endpoint = {('vercel', 'deployment'): '/v13/deployments/' + site['deployment'],
                    ('github', 'source'): '/repos/Time-To-Revenue/timetorevenue-site/git/commits/' + site['source_commit'],
                    ('googledrive', 'files'): '/drive/v3/files?pageSize=10&fields=files(id,name,mimeType)',
                    ('gmail', 'labels'): '/gmail/v1/users/me/labels'}.get((name, operation))
        permission = self.READS.get((name, operation))
        row = self.binding(who, name)
        if not endpoint or not row or not row['enabled'] or permission not in json.loads(row['permissions']):
            raise HTTPException(403, f"Connect {cfg['label']} and enable {permission} for this workspace member before requesting this read." if endpoint else 'This provider read operation is not supported')
        try:
            data = self.hub.proxy_read(who, row['id'], endpoint)['result']
        except ConnectionError as exc:
            raise HTTPException(409, str(exc)) from None
        self.identity.receipt(who, 'provider.read', name + ':' + operation)
        if name == 'vercel':
            project = data.get('projectId') or data.get('project', {}).get('id')
            sha = (data.get('meta') or {}).get('githubCommitSha') or (data.get('gitSource') or {}).get('sha')
            return {'deployment': data.get('id'), 'project': project, 'source_commit': sha, 'ready_state': data.get('readyState'),
                    'matches_registered': data.get('id') == site['deployment'] and project == site['project'] and sha == site['source_commit']}
        # Only return narrow, non-credential fields; never return account state.
        if name == 'github':
            return {'sha': data.get('sha'), 'tree': (data.get('tree') or {}).get('sha')}
        return {k: data[k] for k in ('files', 'labels') if k in data}


class Grant(BaseModel):
    permissions: list[str]


def router(identity, broker=None, get_runner=None):
    broker = broker or Broker(identity)
    api = APIRouter(prefix='/api/account-connections', dependencies=[Depends(private_response)])
    from connection_hub import router as hub_router
    api.include_router(hub_router(identity, get_runner=get_runner))

    @api.get('')
    def status(req: Request):
        who = identity.principal(req)
        providers = []
        for name, cfg in broker.config()['providers'].items():
            row = broker.binding(who, name)
            providers.append({'id': name, **{k: cfg[k] for k in ('label', 'permissions', 'setup')},
                              'configuration': broker.configuration(who, name),
                              'configured': broker.configuration(who, name)['configured'], 'status_source': 'Saved TTROS binding; use Check status for a fresh query',
                              'status': row['status'] if row else 'NOT_CONNECTED', 'granted': json.loads(row['permissions']) if row and row['enabled'] else []})
        return {'identity': who, 'providers': providers, 'website': broker.config()['website'], 'existing_connectors': 'Every app uses the Composio Platform connection in Settings → Connections.',
                'stripe': {'mode': 'test', 'configured': all(bool(os.environ.get(k)) for k in ('TTROS_STRIPE_TEST_KEY', 'TTROS_STRIPE_TEST_ACCOUNT', 'TTROS_STRIPE_TEST_WEBHOOK_SECRET')),
                           'proof': 'Not provider-verified until the existing product-launch checkout and a signed Stripe test event succeed.', 'customer_owned': 'Reserved owner/workspace/account binding; Stripe Connect approval is not assumed.'}}

    @api.post('/{name}/{action}')
    def change(name: str, action: str, req: Request):
        identity.csrf(req)
        who = identity.principal(req)
        if action in ('connect', 'reconnect'):
            return broker.connect(who, name, reconnect=action == 'reconnect')
        if action == 'status':
            return broker.health(who, name)
        if action == 'disconnect':
            return broker.disconnect(who, name)
        raise HTTPException(404, 'Unknown connection action')

    @api.put('/{name}/permissions')
    def permissions(name: str, body: Grant, req: Request):
        identity.csrf(req)
        who = identity.principal(req)
        cfg = broker.provider(name)
        row = broker.binding(who, name)
        if not row or not row['enabled'] or set(body.permissions) - set(cfg['permissions']):
            raise HTTPException(403, 'Only this member’s configured read permissions can be granted')
        with identity.db() as db:
            db.execute("UPDATE hub_connections SET config=json_set(config,'$.panel_permissions',json(?)) WHERE id=? AND user=? AND workspace=?",
                       (json.dumps(sorted(set(body.permissions))), row['id'], who['user'], who['workspace']))
        identity.receipt(who, 'connection.permissions', name + ':' + ','.join(body.permissions))
        return {'permissions': body.permissions}

    @api.post('/{name}/read/{operation}')
    def read(name: str, operation: str, req: Request):
        identity.csrf(req)
        return broker.read(identity.principal(req), name, operation)

    @api.get('/audit')
    def audit(req: Request):
        who = identity.principal(req)
        with identity.db() as db:
            rows = db.execute('SELECT at,action,detail FROM audit WHERE user=? AND workspace=? ORDER BY id DESC LIMIT 50', (who['user'], who['workspace'])).fetchall()
        return {'receipts': [dict(r) for r in rows]}

    @api.post('/stripe-test/connection/verify')
    def stripe_test(req: Request):
        identity.csrf(req)
        who = identity.principal(req)
        # Runtime-injected operator test keys cannot be loaned to a customer
        # workspace. Customer-owned keys require separately provisioned binding.
        if who['workspace'] not in ('ttros-operator', 'local-preview'):
            raise HTTPException(403, 'No customer-owned Stripe TEST account is provisioned for this workspace')
        from aos_stripe_test import StripeTest, StripeBlocked
        try:
            result = StripeTest().connection_status()
        except StripeBlocked as exc:
            raise HTTPException(503, str(exc)) from None
        identity.receipt(who, 'stripe.test.connection', result['account'])
        return result
    return api
