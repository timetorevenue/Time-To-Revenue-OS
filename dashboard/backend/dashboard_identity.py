"""Dashboard OIDC alongside the existing verified-operator authentication.

Google proves identity only. Provider grants remain in Composio. Email is never
used to merge users; an existing operator must explicitly start a link flow.
Session/state secrets are random, hashed at rest, expiring and single-use.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request as URLRequest, urlopen

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from jwt import PyJWKClient
from aos_paths import assert_authoritative_root

COOKIE = 'ttros_session'
FLOW_COOKIE = 'ttros_oidc_flow'
GOOGLE_ISSUER = 'https://accounts.google.com'
GOOGLE_KEYS = PyJWKClient('https://www.googleapis.com/oauth2/v3/certs')


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def private_response(response: Response):
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'


class Identity:
    def __init__(self, root: Path, *, local=False):
        self.local = local
        folder = assert_authoritative_root(root) / 'queue/dashboard_identity'
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(folder, 0o700)
        self.path = folder / 'identity.sqlite3'
        with self.db() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, email TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS memberships(user TEXT, workspace TEXT, PRIMARY KEY(user,workspace));
              CREATE TABLE IF NOT EXISTS identities(issuer TEXT, subject TEXT, user TEXT, PRIMARY KEY(issuer,subject));
              CREATE TABLE IF NOT EXISTS sessions(hash TEXT PRIMARY KEY,user TEXT,workspace TEXT,expires REAL);
              CREATE TABLE IF NOT EXISTS flows(hash TEXT PRIMARY KEY,browser TEXT,nonce TEXT,verifier TEXT,target TEXT,expires REAL);
              CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,at REAL,user TEXT,workspace TEXT,action TEXT,detail TEXT);
            ''')
        os.chmod(self.path, 0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @property
    def origin(self):
        default = 'http://127.0.0.1:4390' if self.local else 'https://ttros.core-site.example'
        return os.environ.get('TTROS_DASHBOARD_ORIGIN', default).rstrip('/')

    def connection_return_url(self):
        origin = urlsplit(self.origin)
        loopback = origin.hostname in ('localhost', '127.0.0.1', '::1')
        secure = origin.scheme == 'https' and bool(origin.hostname) and (self.local or not loopback)
        local = self.local and loopback and origin.scheme == 'http'
        if (not (secure or local) or origin.username or origin.password or origin.query
                or origin.fragment or origin.path not in ('', '/')):
            raise ValueError('Configure an exact hosted HTTPS Dashboard origin; loopback is for explicit local preview only.')
        return self.origin + '/settings/connections?composio=returned'

    @staticmethod
    def google_client(name):
        # Reuse existing environment injection or the Connections OS credential
        # reader. Never read OAuth values from arbitrary files or browser input.
        value = os.environ.get('TTROS_GOOGLE_CLIENT_' + name)
        if value:
            return value
        from ttros_connections import credential, ConnectionError
        try:
            return credential('TTROS_CONNECTOR_SECRET_GOOGLE_CLIENT_' + name)
        except ConnectionError:
            return ''

    def configured(self):
        origin = urlsplit(self.origin)
        secure = origin.scheme == 'https' or (origin.scheme == 'http' and origin.hostname in ('localhost', '127.0.0.1'))
        return bool(secure and not origin.username and not origin.query and not origin.fragment and origin.path in ('', '/')
                    and self.google_client('ID') and self.google_client('SECRET'))

    def google_setup(self):
        missing = [name for name in ('TTROS_GOOGLE_CLIENT_ID', 'TTROS_GOOGLE_CLIENT_SECRET') if not self.google_client(name.removeprefix('TTROS_GOOGLE_CLIENT_'))]
        origin = urlsplit(self.origin)
        valid_origin = (origin.scheme == 'https' or (origin.scheme == 'http' and origin.hostname in ('localhost', '127.0.0.1'))) and not origin.username and not origin.query and not origin.fragment and origin.path in ('', '/')
        if not valid_origin:
            missing.append('TTROS_DASHBOARD_ORIGIN must be an exact HTTPS origin (HTTP is allowed only on localhost)')
        return {'configured': self.configured() and bool(valid_origin), 'missing_configuration': missing,
                'callback_url': self.origin + '/api/auth/google/callback' if valid_origin else None,
                'setup': 'google', 'console_url': 'https://console.cloud.google.com/apis/credentials'}

    def csrf(self, req):
        # Mutations require an exact origin, including the port. The explicit
        # header permits same-origin scripts; cross-site forms cannot supply it.
        if req.headers.get('x-ttros-connections') != '1' or req.headers.get('origin') not in (None, self.origin):
            raise HTTPException(403, 'Same-origin Dashboard request required')
        if req.headers.get('sec-fetch-site', 'same-origin') not in ('same-origin', 'none'):
            raise HTTPException(403, 'Cross-site request refused')

    def principal(self, req, *, allow_local=True):
        token = req.cookies.get(COOKIE, '')
        if token:
            with self.db() as db:
                row = db.execute('SELECT s.*,u.email FROM sessions s JOIN users u ON u.id=s.user JOIN memberships m ON m.user=s.user AND m.workspace=s.workspace WHERE s.hash=? AND s.expires>?', (digest(token), time.time())).fetchone()
            if row:
                return dict(user=row['user'], workspace=row['workspace'], email=row['email'], method='google_oidc')
            raise HTTPException(401, 'Session expired; sign in again')
        if allow_local and self.local and req.client.host in ('127.0.0.1', '::1', 'testclient') and req.url.hostname in ('localhost', '127.0.0.1', 'testserver'):
            return dict(user='local-operator', workspace='local-preview', email='', method='local_preview')
        from remote_pc import require_operator
        # Reuse the existing verified Access/Tailscale operator gate. Its
        # Remote-PC-specific intention header is supplied by this authenticated
        # surface, never by a model; identity headers/JWT are still verified.
        scope = dict(req.scope)
        scope['headers'] = [(k, v) for k, v in req.scope['headers'] if k.lower() != b'x-ttros-remote-pc'] + [(b'x-ttros-remote-pc', b'1')]
        operator = require_operator(Request(scope), 'Dashboard connections')
        user = 'operator-' + digest(operator['identity'].lower())[:24]
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO users VALUES(?,?)', (user, operator['identity'].lower()))
            db.execute('INSERT OR IGNORE INTO memberships VALUES(?,?)', (user, 'ttros-operator'))
        return dict(user=user, workspace='ttros-operator', email=operator['identity'], method=operator['access_path'])

    def receipt(self, who, action, detail=''):
        with self.db() as db:
            db.execute('INSERT INTO audit(at,user,workspace,action,detail) VALUES(?,?,?,?,?)',
                       (time.time(), who['user'], who['workspace'], action, detail))

    def telegram_operator(self, chat):
        """Only after david_ingress verifies the actual bridge delivery.

        Map the configured operator chat to the already pinned Composio member's
        existing Dashboard membership. Never link users by an incoming label or
        by an unverified email, and never borrow a customer workspace.
        """
        from aos_authority import load_notifications
        from ttros_connections import ConnectionError
        if not chat or chat not in load_notifications(self.path.parents[2])['telegram']:
            raise ConnectionError('This Telegram chat is not the configured TTROS operator destination.')
        email = os.environ.get('TTROS_CONNECTIONS_COMPOSIO_MEMBER', '').strip().casefold()
        if not email:
            raise ConnectionError('The server operator identity is not configured for Telegram Connections.')
        with self.db() as db:
            rows = db.execute("SELECT u.id,u.email FROM users u JOIN memberships m ON m.user=u.id WHERE lower(u.email)=? AND m.workspace='ttros-operator'", (email,)).fetchall()
        if len(rows) != 1:
            raise ConnectionError('The configured Telegram operator has no unique existing Dashboard membership.')
        return dict(user=rows[0]['id'], workspace='ttros-operator', email=rows[0]['email'], method='telegram_bridge')

    def verify_google(self, encoded, nonce):
        key = GOOGLE_KEYS.get_signing_key_from_jwt(encoded)
        claims = jwt.decode(encoded, key.key, algorithms=['RS256'], audience=self.google_client('ID'),
                            issuer=GOOGLE_ISSUER, leeway=30,
                            options={'require': ['iss', 'aud', 'exp', 'iat', 'sub', 'nonce', 'email', 'email_verified']})
        if claims['nonce'] != nonce or claims['email_verified'] is not True or not claims['sub']:
            raise ValueError('Invalid Google identity')
        if claims.get('azp', self.google_client('ID')) != self.google_client('ID'):
            raise ValueError('Invalid authorized party')
        return claims

    def resolve(self, claims, target=''):
        """Atomic subject binding; verified email alone never links an account."""
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT user FROM identities WHERE issuer=? AND subject=?', (GOOGLE_ISSUER, claims['sub'])).fetchone()
            if target:
                existing = db.execute('SELECT email FROM users WHERE id=?', (target,)).fetchone()
                if not existing or existing['email'].lower() != claims['email'].lower() or (old and old['user'] != target):
                    raise ValueError('Link refused: identity does not match the authenticated existing user')
            user = target or (old['user'] if old else 'user-' + uuid.uuid4().hex)
            if not old:
                db.execute('INSERT OR IGNORE INTO users VALUES(?,?)', (user, claims['email']))
                db.execute('INSERT INTO identities VALUES(?,?,?)', (GOOGLE_ISSUER, claims['sub'], user))
                if not target:
                    db.execute('INSERT INTO memberships VALUES(?,?)', (user, 'workspace-' + uuid.uuid4().hex))
            workspace = db.execute('SELECT workspace FROM memberships WHERE user=? ORDER BY workspace LIMIT 1', (user,)).fetchone()['workspace']
        return dict(user=user, workspace=workspace, email=claims['email'], method='google_oidc')


def install_session_boundary(app, identity):
    """New customer sessions cannot reach legacy global operator surfaces.

    Non-cookie service clients retain their existing authentication contracts
    (Telegram, signed webhooks, Shared Brain, etc.). The backend remains behind
    the existing protected ingress; this is not authorization to expose it.
    """
    @app.middleware('http')
    async def google_session_boundary(req, call_next):
        path = req.url.path
        scoped = any(path == prefix or path.startswith(prefix + '/') for prefix in
                     ('/api/auth', '/api/account-connections', '/api/current-website'))
        if COOKIE in req.cookies and path.startswith('/api/') and not scoped:
            try:
                who = identity.principal(req, allow_local=False)
                if who['workspace'] != 'ttros-operator':
                    raise HTTPException(403, 'This surface requires a linked existing TTROS operator. Your workspace connections and website drafts remain isolated.')
                if req.method not in ('GET', 'HEAD', 'OPTIONS') and req.headers.get('origin') not in (None, identity.origin):
                    raise HTTPException(403, 'Cross-origin operator mutation refused')
            except HTTPException as exc:
                from fastapi.responses import JSONResponse
                return JSONResponse({'detail': exc.detail}, status_code=exc.status_code, headers={'Cache-Control': 'no-store'})
        return await call_next(req)


def router(identity):
    api = APIRouter(prefix='/api/auth', dependencies=[Depends(private_response)])

    @api.get('/session')
    def session(req: Request):
        try:
            who = identity.principal(req)
        except HTTPException:
            who = None
        return {'identity': who, 'google_configured': identity.configured(), 'google_scopes': ['openid', 'email', 'profile'],
                'provider_data_authorized': False, 'local_preview': identity.local, 'google_setup': identity.google_setup()}

    @api.post('/google/start')
    def start(req: Request, link: bool = False):
        identity.csrf(req)
        setup = identity.google_setup()
        if not setup['configured']:
            raise HTTPException(503, {**setup, 'code': 'setup_required', 'message': 'Google sign-in cannot start: ' + '; '.join(setup['missing_configuration']) + '. Open Google setup, provision these values securely, then check configuration again.'})
        target = identity.principal(req, allow_local=False)['user'] if link else ''
        state, browser, nonce, verifier = [secrets.token_urlsafe(32) for _ in range(4)]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        with identity.db() as db:
            db.execute('DELETE FROM flows WHERE expires<?', (time.time(),))
            db.execute('INSERT INTO flows VALUES(?,?,?,?,?,?)', (digest(state), digest(browser), nonce, verifier, target, time.time() + 600))
        query = urlencode(dict(client_id=identity.google_client('ID'), redirect_uri=identity.origin + '/api/auth/google/callback',
                               response_type='code', scope='openid email profile', state=state, nonce=nonce,
                               code_challenge=challenge, code_challenge_method='S256', prompt='select_account'))
        from fastapi.responses import JSONResponse
        res = JSONResponse({'url': GOOGLE_ISSUER + '/o/oauth2/v2/auth?' + query})
        res.set_cookie(FLOW_COOKIE, browser, httponly=True, secure=identity.origin.startswith('https:'), samesite='lax', max_age=600, path='/api/auth')
        res.headers['Cache-Control'] = 'no-store'
        return res

    @api.get('/google/callback')
    def callback(req: Request, state: str = '', code: str = '', error: str = ''):
        with identity.db() as db:
            db.execute('BEGIN IMMEDIATE')
            flow = db.execute('SELECT * FROM flows WHERE hash=?', (digest(state),)).fetchone()
            if not flow or flow['expires'] < time.time() or flow['browser'] != digest(req.cookies.get(FLOW_COOKIE, '')):
                raise HTTPException(400, 'Invalid or expired sign-in state')
            db.execute('DELETE FROM flows WHERE hash=?', (digest(state),))
        if error or not code or len(code) > 4096:
            raise HTTPException(400, 'Google sign-in was cancelled or refused')
        try:
            body = urlencode(dict(code=code, client_id=identity.google_client('ID'), client_secret=identity.google_client('SECRET'),
                                  redirect_uri=identity.origin + '/api/auth/google/callback', grant_type='authorization_code', code_verifier=flow['verifier'])).encode()
            with urlopen(URLRequest('https://oauth2.googleapis.com/token', data=body), timeout=15) as response:
                tokens = json.load(response)
            who = identity.resolve(identity.verify_google(tokens['id_token'], flow['nonce']), flow['target'])
        except Exception:
            raise HTTPException(400, 'Google identity verification failed; no account was linked') from None
        token = secrets.token_urlsafe(48)
        with identity.db() as db:
            db.execute('INSERT INTO sessions VALUES(?,?,?,?)', (digest(token), who['user'], who['workspace'], time.time() + 8 * 3600))
        identity.receipt(who, 'google.link' if flow['target'] else 'google.sign_in')
        res = RedirectResponse('/settings/connections', status_code=303)
        res.set_cookie(COOKIE, token, httponly=True, secure=identity.origin.startswith('https:'), samesite='lax', max_age=8 * 3600, path='/')
        res.delete_cookie(FLOW_COOKIE, path='/api/auth')
        res.headers['Cache-Control'] = 'no-store'
        return res

    @api.post('/logout')
    def logout(req: Request):
        identity.csrf(req)
        with identity.db() as db:
            db.execute('DELETE FROM sessions WHERE hash=?', (digest(req.cookies.get(COOKIE, '')),))
        from fastapi.responses import JSONResponse
        res = JSONResponse({'signed_out': True})
        res.delete_cookie(COOKIE, path='/')
        return res
    return api
