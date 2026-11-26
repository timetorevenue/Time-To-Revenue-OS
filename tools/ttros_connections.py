"""Scoped Connections operations shared by Dashboard, David and Jobs.

Composio owns provider credentials, scoped to each Platform project and user. Custom connectors
are a small, GET-only adapter using runtime-injected secret references.
"""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import subprocess
import time
import uuid
import secrets
import stat
import fcntl
from contextlib import contextmanager
import datetime as dt
import base64
from html.parser import HTMLParser
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib.parse import urlsplit, unquote, urlencode, quote

import jsonschema

SLUG = re.compile(r'^[a-z][a-z0-9_]{0,79}$')
SECRET_KEY = re.compile(r'authorization|cookie|secret|token|password|api.?key|credential', re.I)
# Named, reviewed reads. Discovery is not permission to execute arbitrary tools.
READS = {
    'github': {'profile': ('GITHUB_GET_THE_AUTHENTICATED_USER', {})},
    'gmail': {'profile': ('GMAIL_GET_PROFILE', {'user_id': 'me'})},
    'googledrive': {'files': ('GOOGLEDRIVE_LIST_FILES', {'page_size': 5})},
    'googlecalendar': {'calendars': ('GOOGLECALENDAR_LIST_CALENDARS', {})},
}

# Reviewed provider operations, shared by Dashboard, David, Jobs and Forge.
# Account selection is never accepted in arguments. Calendar writes are private,
# primary-calendar operations without attendees, sharing or notifications.
def operation_schema(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}

TEXT = {'type': 'string', 'minLength': 1, 'maxLength': 2000}
WINDOW = {'time_min': TEXT, 'time_max': TEXT, 'timezone': TEXT}
EVENT_FIELDS = {'summary': TEXT, 'start_datetime': TEXT, 'end_datetime': TEXT, 'timezone': TEXT,
                'location': {'type': 'string', 'maxLength': 2000}, 'description': {'type': 'string', 'maxLength': 6000}}
OPERATIONS = {
    'gmail': {
        'search': ('GMAIL_FETCH_EMAILS', operation_schema({'query': TEXT, 'page_token': TEXT, 'max_results': {'type': 'integer', 'minimum': 1, 'maximum': 25}}, ['query']), {'user_id': 'me', 'max_results': 10, 'verbose': False, 'include_payload': False}, False),
        'message': ('GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID', operation_schema({'message_id': TEXT}, ['message_id']), {'user_id': 'me', 'format': 'full'}, False),
    },
    'googlecalendar': {
        'events': ('GOOGLECALENDAR_FIND_EVENT', operation_schema({'query': {'type': 'string', 'maxLength': 500}, 'time_min': TEXT, 'time_max': TEXT,
                    'page_token': TEXT}, ['time_min', 'time_max']), {'calendar_id': 'primary', 'max_results': 25}, False),
        'event': ('GOOGLECALENDAR_EVENTS_GET', operation_schema({'event_id': TEXT}, ['event_id']), {'calendar_id': 'primary'}, False),
        'availability': ('GOOGLECALENDAR_FIND_FREE_SLOTS', operation_schema(WINDOW, ['time_min', 'time_max', 'timezone']), {'items': ['primary']}, False),
        'create_event': ('GOOGLECALENDAR_CREATE_EVENT', operation_schema(EVENT_FIELDS, ['summary', 'start_datetime', 'end_datetime', 'timezone']),
                         {'calendar_id': 'primary', 'visibility': 'private', 'attendees': [], 'send_updates': 'none', 'create_meeting_room': False}, True),
        'update_event': ('GOOGLECALENDAR_UPDATE_EVENT', operation_schema({**EVENT_FIELDS, 'event_id': TEXT}, ['event_id', 'summary', 'start_datetime', 'end_datetime', 'timezone']),
                         {'calendar_id': 'primary', 'visibility': 'private', 'attendees': [], 'send_updates': 'none', 'create_meeting_room': False}, True),
    },
}
TOOL_VERSIONS = {'gmail': '20261120_00', 'googlecalendar': '20261116_00'}

def is_write(service, operation):
    return bool(OPERATIONS.get(service, {}).get(operation, (None, None, None, False))[3])

def calendar_events(value):
    """Composio Calendar versions wrap events differently (including event_data twice)."""
    if isinstance(value, dict):
        if value.get('id') and ('start' in value or value.get('kind') == 'calendar#event'):
            return [value]
        if value.get('event_id'):
            return [{**value, 'id': value['event_id']}]
        return [event for child in value.values() for event in calendar_events(child)]
    if isinstance(value, list):
        return [event for child in value for event in calendar_events(child)]
    return []

def gmail_message(data):
    """Decode text MIME parts before bounding output; never expose QR/image attachments."""
    class VisibleText(HTMLParser):
        def __init__(self):
            super().__init__(); self.parts = []; self.hidden = 0; self.links = []
        def handle_starttag(self, tag, attrs):
            if tag in ('script', 'style'):
                self.hidden += 1
            if tag == 'a':
                url = dict(attrs).get('href', '')
                parts = urlsplit(url)
                if parts.scheme == 'https' and parts.hostname and not re.search(r'qr.?code|barcode|ticket.*download', url, re.I):
                    self.links.append(url)
        def handle_endtag(self, tag):
            if tag in ('script', 'style'):
                self.hidden = max(0, self.hidden - 1)
        def handle_data(self, value):
            if not self.hidden:
                self.parts.append(value)
    plain, html_text, links = [], [], []
    def walk(part):
        if not isinstance(part, dict):
            return
        mime = part.get('mimeType')
        encoded = (part.get('body') or {}).get('data')
        if mime in ('text/plain', 'text/html') and isinstance(encoded, str) and len(encoded) <= 400000:
            try:
                text = base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)).decode('utf-8', errors='replace')
                if mime == 'text/html':
                    parser = VisibleText(); parser.feed(text)
                    html_text.append(' '.join(parser.parts)); links.extend(parser.links)
                else:
                    plain.append(text)
            except (ValueError, UnicodeError):
                pass
        for child in part.get('parts') or []:
            walk(child)
    walk(data.get('payload'))
    text = '\n'.join(html_text + plain) or data.get('messageText') or ''
    # Encoded MIME bodies may contain images/QRs and are never a model input.
    return {**{k: v for k, v in data.items() if k not in ('payload', 'attachmentList', 'messageText')},
            'messageText': re.sub(r'\s+', ' ', text)[:8000], 'links': [u for u in dict.fromkeys(links) if len(u) <= 3000][:6],
            'content_truncated': len(text) > 8000, 'attachments': 'Private attachments withheld; no QR or barcode content returned.'}


REMOVED = ('The old Composio For You connection was removed from TTROS. Connect this app to '
           'Composio Platform in Settings → Connections.')


class ConnectionError(ValueError):
    pass


# -- Dynamic Platform tools -------------------------------------------------------------------
# Every connected app exposes two generic operations alongside the reviewed named ones:
#   tools  discover the app's tools (Composio metadata only, no account access)
#   tool   run one discovered tool: {"tool": SLUG, "arguments": {...}}
# Read/write is classified from Composio's tool hints, then the runtime permission policy
# (aos_authority.action_authorization) decides exactly as for every other action.
GENERIC_OPS = ('tools', 'tool')
TOOL_SLUG = re.compile(r'^[A-Z][A-Z0-9_]{2,127}$')
FIELD_NAME = re.compile(r'^[A-Za-z][A-Za-z0-9_.\-]{0,63}$')
OAUTH_SCHEMES = frozenset({'OAUTH1', 'OAUTH2', 'DCR_OAUTH', 'CIMD_OAUTH', 'S2S_OAUTH2'})
READ_VERBS = frozenset({'GET', 'LIST', 'FETCH', 'SEARCH', 'FIND', 'READ', 'RETRIEVE', 'QUERY', 'LOOKUP', 'CHECK',
                        'COUNT', 'DESCRIBE', 'VIEW', 'SHOW', 'EXPORT', 'DOWNLOAD', 'BROWSE', 'VALIDATE', 'VERIFY'})
SECRET_FIELD = re.compile(r'key|secret|token|password|passwd|credential|private|bearer|basic_encoded|jwt', re.I)
_CACHE = {}
SYNONYMS = {'advertising': ('ads', 'ad '), 'advert': ('ads',), 'ad': ('ads', 'advertising'), 'email': ('mail', 'inbox'),
            'mail': ('email',), 'spreadsheet': ('sheets',), 'calendar': ('schedule', 'events'), 'invoice': ('invoicing', 'billing'),
            'payment': ('payments', 'billing'), 'chat': ('messaging', 'message'), 'crm': ('contacts', 'deals'),
            'newsletter': ('campaigns', 'email marketing'), 'meeting': ('meetings', 'video'), 'files': ('drive', 'storage')}


def cached(key, ttl, compute):
    """Small process cache for Composio metadata (catalogue, toolkit auth details, tool schemas)."""
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit and hit[0] > now:
        return hit[1]
    value = compute()
    if len(_CACHE) > 4000:
        _CACHE.clear()
    _CACHE[key] = (now + ttl, value)
    return value


def tool_mode(slug, tags=()):
    """'read' only when Composio marks the tool read-only or its verb is a read; otherwise 'write'."""
    tags = {str(t).lower() for t in tags or ()}
    if 'readonlyhint' in tags:
        return 'read'
    if tags & {'destructivehint', 'mutatinghint'}:
        return 'write'
    tokens = [t for t in str(slug or '').upper().split('_') if t]
    verbs = [t for t in tokens[1:] if t in READ_VERBS or t in PROTECTED_VERBS or t in WRITE_VERBS]
    return 'read' if verbs and verbs[0] in READ_VERBS and not set(tokens) & PROTECTED_VERBS else 'write'


PROTECTED_VERBS = frozenset({'SEND', 'REPLY', 'FORWARD', 'SHARE', 'INVITE', 'PUBLISH', 'POST', 'DELETE', 'TRASH',
                             'REMOVE', 'PAY', 'CHARGE', 'REFUND', 'TRANSFER', 'PURCHASE'})
WRITE_VERBS = frozenset({'CREATE', 'ADD', 'UPDATE', 'PATCH', 'PUT', 'SET', 'MOVE', 'INSERT', 'UPLOAD', 'MODIFY',
                         'EDIT', 'ARCHIVE', 'MARK', 'APPLY', 'REVOKE', 'START', 'STOP', 'RUN', 'EXECUTE', 'TRIGGER'})


def recipients_of(arguments):
    """Outbound destinations named in tool arguments (to/cc/bcc/recipient...), lower-cased."""
    found = []
    for key, value in (arguments or {}).items():
        if re.fullmatch(r'(?i)(to|cc|bcc|recipients?|recipient_emails?|to_emails?|email_to|send_to|extra_recipients)', str(key)):
            values = value if isinstance(value, list) else [value]
            found += [str(v).strip().lower() for v in values if isinstance(v, str) and v.strip()]
    return sorted(set(found))


def _https_links(text):
    return [u.rstrip('.,);') for u in re.findall(r'https://[^\s"<>]+', str(text or ''))]


def sanitize(value, secrets=(), max_items=100):
    if isinstance(value, dict):
        if str(value.get('mimeType', '')).startswith(('image/', 'application/pdf')):
            return {'mimeType': value['mimeType'], 'content': '[private attachment withheld]'}
        return {k: '[redacted]' if (SECRET_KEY.search(k) and k not in ('nextPageToken', 'next_page_token', 'page_token')) or re.search(r'qr.?code|bar.?code', k, re.I) else sanitize(v, secrets, max_items) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(v, secrets, max_items) for v in value[:max_items]]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, '[redacted]')
        value = re.sub(r'<img\b[^>]*>', '[image withheld]', value, flags=re.I)
        value = re.sub(r'data:image/[^\s"<>]+', '[private image withheld]', value, flags=re.I)
        return re.sub(r'(?i)Bearer\s+\S+', 'Bearer [redacted]', value)[:8000]
    return value


def host_url(value):
    p = urlsplit(value)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.port not in (None, 443) or p.path not in ('', '/') or p.query or p.fragment:
        raise ConnectionError('API host must be an HTTPS origin on port 443 without a path, credentials or query.')
    host = p.hostname.encode('idna').decode('ascii').lower()
    if not re.fullmatch(r'[a-z0-9.-]+', host) or '.' not in host or host.endswith('.'):
        raise ConnectionError('Use a public DNS API hostname.')
    return 'https://' + host


def public_addresses(host):
    try:
        addresses = sorted({entry[4][0] for entry in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
    except OSError:
        raise ConnectionError('API hostname could not be resolved.') from None
    def unsafe(value):
        ip = ipaddress.ip_address(value)
        return (not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_loopback or ip.is_link_local
                or getattr(ip, 'is_site_local', False) or (ip.version == 6 and (ip in ipaddress.ip_network('64:ff9b::/96') or ip in ipaddress.ip_network('64:ff9b:1::/48'))))
    if not addresses or any(unsafe(ip) for ip in addresses):
        raise ConnectionError('API DNS must resolve exclusively to public IP addresses. Private, loopback and metadata addresses are refused.')
    return addresses


def endpoint(value):
    if not isinstance(value, str) or len(value) > 500 or not value.startswith('/') or value.startswith('//'):
        raise ConnectionError('Operation path must begin with a single slash.')
    decoded = value
    for _ in range(4):
        decoded = unquote(decoded)
    p = urlsplit(decoded)
    if p.scheme or p.netloc or p.query or p.fragment or any(c in decoded for c in ('\\', '\r', '\n', '{', '}')) or any(part in ('.', '..') for part in decoded.split('/')) or decoded.startswith('//'):
        raise ConnectionError('Use a fixed endpoint path without traversal, query, template or redirect URL.')
    return value


def get_json(origin, path, headers):
    """Pin the validated IP for the TLS connection: no DNS rebinding or redirect."""
    host = urlsplit(origin).hostname
    ip = public_addresses(host)[0]
    connection = http.client.HTTPSConnection(host, 443, timeout=12, context=ssl.create_default_context())
    try:
        raw = socket.create_connection((ip, 443), timeout=12)
        try:
            connection.sock = connection._context.wrap_socket(raw, server_hostname=host)
        except Exception:
            raw.close()
            raise
        connection.request('GET', path, headers={'Accept': 'application/json', 'User-Agent': 'TTROS-Connections/1', **headers})
        response = connection.getresponse()
        if 300 <= response.status < 400:
            raise ConnectionError('API redirects are refused. Configure the final HTTPS API host and path.')
        if response.status != 200:
            raise ConnectionError(f'API returned HTTP {response.status}; check the endpoint and read permissions.')
        if 'json' not in response.getheader('Content-Type', '').lower():
            raise ConnectionError('The read operation must return JSON.')
        data = response.read(65537)
        if len(data) > 65536:
            raise ConnectionError('Response exceeds the 64 KiB limit. Reduce the endpoint page size.')
        return json.loads(data)
    except ConnectionError:
        raise
    except Exception:
        raise ConnectionError('API read failed TLS, timeout or JSON validation; sensitive response details were withheld.') from None
    finally:
        connection.close()


def credential(ref):
    """Decrypt an existing OS credential in memory; never persist plaintext.

    The operator provisions named systemd-creds blobs outside this repository.
    No plaintext environment-variable fallback is accepted for custom API keys.
    """
    if not re.fullmatch(r'TTROS_CONNECTOR_SECRET_[A-Z0-9_]{1,64}', ref):
        raise ConnectionError('Invalid encrypted credential reference.')
    delivered = os.environ.get('CREDENTIALS_DIRECTORY')
    if delivered:
        # systemd decrypts LoadCredentialEncrypted before dropping to User=casey.
        # Its credential mount is volatile and private; plaintext is never read
        # from an arbitrary caller-selected path or persisted by this adapter.
        directory_path = Path(delivered).resolve()
        roots = (Path('/run/credentials'), Path('/run/user') / str(os.geteuid()) / 'credentials')
        if not any(directory_path.is_relative_to(root) for root in roots):
            raise ConnectionError('OS-delivered credentials must use the private systemd runtime credential mount.')
        try:
            fd = os.open(directory_path / ref, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, 'rb') as handle:
                meta = os.fstat(handle.fileno())
                if not stat.S_ISREG(meta.st_mode) or meta.st_mode & 0o077 or meta.st_uid != os.geteuid():
                    raise ConnectionError('OS credential is not private to this backend operator.')
                data = handle.read(8193)
            value = data.decode('utf-8').rstrip('\n')
            if not value or len(data) > 8192 or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ConnectionError('OS credential is not a valid single-line API key.')
            return value
        except ConnectionError:
            raise
        except Exception:
            raise ConnectionError('Named OS credential was not delivered. Provision it with LoadCredentialEncrypted in the existing service secret configuration.') from None
    directory = os.environ.get('TTROS_CONNECTOR_ENCRYPTED_DIRECTORY')
    if not directory:
        raise ConnectionError('Provision this API key as an encrypted systemd credential and set TTROS_CONNECTOR_ENCRYPTED_DIRECTORY on the backend. Plaintext keys are not accepted by Connections.')
    path = Path(directory) / (ref + '.cred')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as handle:
            meta = os.fstat(handle.fileno())
            if not stat.S_ISREG(meta.st_mode) or meta.st_mode & 0o077 or meta.st_uid != os.geteuid():
                raise ConnectionError('Encrypted credential must be a private regular file owned by the backend operator.')
            blob = handle.read(65537)
        if not blob or len(blob) > 65536:
            raise ConnectionError('Encrypted credential has an invalid size.')
        result = subprocess.run(['/usr/bin/systemd-creds', 'decrypt', '--name=' + ref, '-', '-'], input=blob, capture_output=True, timeout=5)
        if result.returncode or not result.stdout or len(result.stdout) > 8192:
            raise ConnectionError('The encrypted credential could not be decrypted by this runtime. Check its name, host binding and expiry securely.')
        value = result.stdout.decode('utf-8').rstrip('\n')
        if not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ConnectionError('The encrypted credential is not a valid single-line API key.')
        return value
    except ConnectionError:
        raise
    except Exception:
        raise ConnectionError('Encrypted API-key credential is unavailable; provision it through OS secret storage. No credential details were logged.') from None



# -- Project-scoped Composio (tenant-owned or TTROS-managed project API keys) ----------------
# Documented v3.1 REST surface only (x-api-key; connected_accounts, connected_accounts/link,
# auth_configs, tools/execute). The operator CLI login is never used for a tenant project.
COMPOSIO_HOST = 'backend.composio.dev'
COMPOSIO_BASE = '/api/v3.1'
PROJECT_KEY = re.compile(r'^[A-Za-z0-9_\-]{16,256}$')
COMPOSIO_USER = re.compile(r'^[A-Za-z0-9_.@:\-]{1,128}$')
ACCOUNT_ID = re.compile(r'^ca_[A-Za-z0-9_\-]{4,64}$')
OPERATOR_WORKSPACE = 'ttros-operator'
AUTH_CONFIG_ID = re.compile(r'^ac_[A-Za-z0-9_\-]{4,64}$')
VAULT_REF = 'TTROS_CONNECTOR_SECRET_COMPOSIO_VAULT'
MANAGED_REF = 'TTROS_CONNECTOR_SECRET_COMPOSIO_MANAGED'


class ProviderRefused(ConnectionError):
    """Composio answered with a definitive refusal (nothing was performed)."""
    def __init__(self, message, status=0, detail=''):
        super().__init__(message)
        self.status, self.detail = status, detail


class ProviderUnreachable(ConnectionError):
    """Transport failure: the provider may or may not have acted."""


class ToolFailed(ConnectionError):
    """The tool ran and the provider reported failure; provider_error is scrubbed, bounded text."""
    def __init__(self, message, provider_error=''):
        super().__init__(message)
        self.provider_error = provider_error


def _strings(value):
    """String leaves of a request body (credential values to scrub from error text)."""
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return [value] if isinstance(value, str) and len(value) >= 6 else []


def composio_error(raw, secrets=()):
    """Composio's error message and slug, scrubbed of submitted values, URLs with tokens and long tokens."""
    try:
        data = json.loads(raw or b'{}')
    except ValueError:
        return ''
    error = data.get('error') if isinstance(data, dict) else None
    if isinstance(error, dict):
        text = ' '.join(str(error.get(k) or '') for k in ('message', 'suggested_fix') if error.get(k))
        slug = str(error.get('slug') or error.get('code') or '')
    else:
        text, slug = str(error or (data.get('message') if isinstance(data, dict) else '') or ''), ''
    for secret in secrets:
        if isinstance(secret, str) and secret:
            text = text.replace(secret, '[redacted]')
    text = re.sub(r'(?i)(bearer|token|key|secret|password)\s*[:=]?\s*\S+', r'\1 [redacted]', text)
    text = re.sub(r'\b[A-Za-z0-9_\-]{32,}\b', '[redacted]', text)
    text = ' '.join(text.split())[:300]
    return (text + (f' ({slug})' if slug and slug not in text else '')).strip()


def composio_http(method, path, key, *, query=None, body=None):
    """One HTTPS request to the pinned Composio API host. Never echoes keys or provider bodies."""
    if not path.startswith('/') or '..' in path or '//' in path:
        raise ConnectionError('Invalid Composio API path.')
    url = COMPOSIO_BASE + path + ('?' + urlencode(query, doseq=True) if query else '')
    payload = json.dumps(body).encode() if body is not None else None
    connection = http.client.HTTPSConnection(COMPOSIO_HOST, 443, timeout=30, context=ssl.create_default_context())
    try:
        connection.request(method, url, body=payload, headers={'x-api-key': key, 'Accept': 'application/json',
                                                               **({'Content-Type': 'application/json'} if payload else {})})
        response = connection.getresponse()
        raw = response.read(2_000_001)
        status = response.status
    except (OSError, http.client.HTTPException):
        raise ProviderUnreachable('Composio API unreachable; nothing was changed.') from None
    finally:
        connection.close()
    # Composio's own error text names the actual problem (missing auth config, invalid
    # field, rejected credential). It is scrubbed and bounded; request bodies never are.
    detail = composio_error(raw, (key, *_strings(body))) if not 200 <= status < 300 else ''
    suffix = (': ' + detail) if detail else ''
    if status in (401, 403):
        raise ProviderRefused('Composio rejected the project API key for this request' + (suffix or '.'), status, detail)
    if status == 404:
        raise ProviderRefused('Composio could not find that project resource' + (suffix or '.'), status, detail)
    if status == 429:
        raise ProviderRefused('Composio rate-limited the request; retry later.', status, detail)
    if not 200 <= status < 300 or len(raw) > 2_000_000:
        raise ProviderRefused(f'Composio refused the request (HTTP {status}){suffix or "; no success recorded."}', status, detail)
    try:
        return json.loads(raw or b'{}')
    except ValueError:
        raise ConnectionError('Composio returned an unreadable response; no success recorded.') from None


def vault_master_key():
    """32-byte vault key delivered through the existing OS credential path; never a plaintext env var."""
    import base64
    try:
        value = credential(VAULT_REF)
    except ConnectionError:
        raise ConnectionError('Project secret storage is not provisioned: deliver the 32-byte '
                              f'{VAULT_REF} credential to the backend. Nothing was stored.') from None
    try:
        key = base64.b64decode(value, validate=True)
    except ValueError:
        key = b''
    if len(key) != 32:
        raise ConnectionError(f'{VAULT_REF} must be 32 bytes, base64-encoded. Nothing was stored.')
    return key


class SecretVault:
    """AES-256-GCM envelope store for tenant project keys, separate from connection records.

    Each ciphertext is bound (AAD) to its reference and workspace, so a reference copied into
    another workspace cannot be decrypted there. Plaintext exists only in memory.
    """
    def __init__(self, folder, key_source=vault_master_key):
        self.folder, self.key_source = Path(folder), key_source
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.folder, 0o700)
        self.path = self.folder / 'vault.sqlite3'
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS secrets(ref TEXT PRIMARY KEY, workspace TEXT, nonce BLOB, ciphertext BLOB, created REAL)')
        os.chmod(self.path, 0o600)

    def _db(self):
        import sqlite3
        db = sqlite3.connect(self.path, timeout=15)
        return db

    def _cipher(self):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM(self.key_source())

    def put(self, workspace, value):
        cipher = self._cipher()
        ref = 'vault:' + uuid.uuid4().hex
        nonce = secrets.token_bytes(12)
        sealed = cipher.encrypt(nonce, value.encode(), (ref + '|' + workspace).encode())
        with self._db() as db:
            db.execute('INSERT INTO secrets VALUES(?,?,?,?,?)', (ref, workspace, nonce, sealed, time.time()))
        return ref

    def get(self, ref, workspace):
        with self._db() as db:
            row = db.execute('SELECT nonce,ciphertext FROM secrets WHERE ref=? AND workspace=?', (ref, workspace)).fetchone()
        if not row:
            raise ConnectionError('Project credential is not available to this workspace.')
        from cryptography.exceptions import InvalidTag
        try:
            return self._cipher().decrypt(row[0], row[1], (ref + '|' + workspace).encode()).decode()
        except InvalidTag:
            raise ConnectionError('Project credential failed integrity verification; nothing was sent.') from None

    def delete(self, ref, workspace):
        with self._db() as db:
            db.execute('DELETE FROM secrets WHERE ref=? AND workspace=?', (ref, workspace))


class Connections:
    def __init__(self, identity, *, fetch=get_json, composio=composio_http, vault=None, managed_key=None):
        self.identity, self.fetch, self.composio = identity, fetch, composio
        self.vault = vault or SecretVault(identity.path.parent / 'composio_vault')
        self.managed_key = managed_key or (lambda: credential(MANAGED_REF))
        with identity.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS hub_projects(workspace TEXT PRIMARY KEY,mode TEXT,secret TEXT,fingerprint TEXT,owner TEXT,linked REAL,verified REAL,status TEXT);
                CREATE TABLE IF NOT EXISTS hub_project_users(workspace TEXT,user TEXT,composio_user TEXT,PRIMARY KEY(workspace,user),UNIQUE(workspace,composio_user));
                CREATE TABLE IF NOT EXISTS hub_auth_configs(project TEXT,toolkit TEXT,auth_config TEXT,PRIMARY KEY(project,toolkit));
                CREATE TABLE IF NOT EXISTS hub_connections(id TEXT PRIMARY KEY,user TEXT,workspace TEXT,kind TEXT,service TEXT,name TEXT,account TEXT,enabled INTEGER,config TEXT,verified REAL,status TEXT);
                CREATE TABLE IF NOT EXISTS hub_receipts(id TEXT PRIMARY KEY,user TEXT,workspace TEXT,at REAL,connection TEXT,operation TEXT,result TEXT);
                CREATE TABLE IF NOT EXISTS hub_job_grants(id TEXT PRIMARY KEY,job TEXT UNIQUE,principal TEXT,connection TEXT,operation TEXT,arguments TEXT,expires REAL);
                CREATE TABLE IF NOT EXISTS hub_effects(key TEXT PRIMARY KEY,user TEXT,workspace TEXT,connection TEXT,operation TEXT,arguments TEXT,status TEXT,receipt TEXT);
            ''')
            if 'scheme' not in {r[1] for r in db.execute('PRAGMA table_info(hub_auth_configs)')}:
                db.execute('ALTER TABLE hub_auth_configs ADD COLUMN scheme TEXT')   # NULL = OAuth (pre-existing rows)

    @contextmanager
    def _lock(self, key):
        path = self.identity.path.parent / ('hub-' + hashlib.sha256(key.encode()).hexdigest() + '.lock')
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def _catalogue_key(self, who):
        """(cache scope, API key) for the member's linked project, or the TTROS-managed project."""
        project = self._project_row(who) if who else None
        if project and project['status'] == 'LINKED':
            return project['fingerprint'], self._project(who)[1]
        return 'managed', self._managed_key()

    def catalogue(self, query='', who=None, cursor=None, limit=48):
        """One page of the complete Composio catalogue, newest provider data, zero model calls.

        Composio search and cursor pagination run server-side, so every app is reachable
        whatever its position. A page is cached briefly; the UI loads further pages lazily.
        """
        scope, key = self._catalogue_key(who)
        query = str(query or '').strip()[:100]
        limit = max(1, min(int(limit or 48), 200))
        cursor = str(cursor or '')[:500]
        if cursor.startswith('idx:'):
            return self._index_page(who, scope, key, query, int(cursor[4:] or 0), limit)
        params = {'limit': limit, 'sort_by': 'usage', **({'search': query} if query else {}),
                  **({'cursor': cursor} if cursor else {})}
        try:
            page = cached(('toolkits', self.composio, scope, json.dumps(params, sort_keys=True)), 300,
                          lambda: self.composio('GET', '/toolkits', key, query=params))
        except ConnectionError as exc:
            if '(HTTP 400)' not in str(exc):
                raise
            # Some search/sort combinations are refused; filter the full index here instead.
            return self._index_page(who, scope, key, query, 0, limit)
        rows = [self.app(row) for row in page.get('items') or [] if isinstance(row, dict) and SLUG.fullmatch(str(row.get('slug', '')))]
        return {'source': 'App catalogue', 'checked_at': time.time(), 'items': self._with_status(who, rows),
                'next_cursor': page.get('next_cursor') or None, 'total': page.get('total_items')}

    def catalogue_index(self, who=None):
        """Every toolkit in the catalogue (cursor-paged, cached 6 h). New apps appear on refresh."""
        scope, key = self._catalogue_key(who)

        def load():
            items, cursor = [], None
            for _ in range(40):
                page = self.composio('GET', '/toolkits', key, query={'limit': 1000, **({'cursor': cursor} if cursor else {})})
                items += [self.app(r) for r in page.get('items') or [] if isinstance(r, dict) and SLUG.fullmatch(str(r.get('slug', '')))]
                cursor = page.get('next_cursor')
                if not cursor:
                    break
            return list({app['slug']: app for app in items}.values())
        return cached(('index', self.composio, scope), 6 * 3600, load)

    def _index_page(self, who, scope, key, query, offset, limit):
        rows = self.find_apps(who, query, limit=None) if query else self.catalogue_index(who)
        page = rows[offset:offset + limit]
        more = offset + limit < len(rows)
        return {'source': 'App catalogue', 'checked_at': time.time(), 'items': self._with_status(who, page),
                'next_cursor': f'idx:{offset + limit}' if more else None, 'total': len(rows)}

    def find_apps(self, who, text, limit=5):
        """Deterministic app match over the full catalogue (name, slug, description, categories)."""
        words = [w for w in re.findall(r'[a-z0-9]+', str(text or '').casefold())
                 if w not in ('a', 'an', 'the', 'my', 'me', 'to', 'for', 'and', 'or', 'of', 'that', 'can', 'help', 'with',
                              'app', 'application', 'apps', 'tool', 'tools', 'account', 'manage', 'connect', 'please', 'i', 'want')]
        if not words:
            return []
        joined = ''.join(words)
        # Common business wording vs. how app listings phrase it (generic, not per app).
        variants = {w: {w, *SYNONYMS.get(w, ()), *(SYNONYMS.get(w[:-1], ()) if w.endswith('s') else ())} for w in words}
        scored = []
        for app in self.catalogue_index(who):
            name = re.sub(r'[^a-z0-9]', '', str(app['name']).casefold())
            slug = app['slug'].replace('_', '')
            hay = ' '.join([str(app['name']), app['slug'].replace('_', ' '), app.get('description') or '',
                            ' '.join(app.get('categories') or [])]).casefold()
            score = 100 if joined in (name, slug) else 0
            score += sum(12 if w in (name, slug) else 6 if any(v in name or v in slug for v in variants[w])
                         else 2 if any(v in hay for v in variants[w]) else 0 for w in words)
            score += 10 if len(words) > 1 and all(any(v in hay for v in variants[w]) for w in words) else 0   # the whole need
            if score:
                scored.append((-score, -(app.get('tools_count') or 0), app['slug'], app))
        scored.sort(key=lambda r: r[:3])
        found = [r[3] for r in scored]
        return found if limit is None else found[:limit]

    def _with_status(self, who, apps):
        if not who:
            return apps
        try:
            rows = self.rows(who)
        except ConnectionError:
            rows = []
        out = []
        for app in apps:
            mine = [r for r in rows if r['service'] == app['slug'] and r['enabled'] and r['config'].get('source') == 'project']
            active = [r for r in mine if r['connection_status'] == 'ACTIVE']
            pending = [r for r in mine if r['status'] in ('PENDING', 'INITIATED', 'INITIALIZING')]
            out.append({**app, 'connection': ({'status': 'ACTIVE', 'accounts': [r['label'] for r in active]} if active else
                                              {'status': 'PENDING'} if pending else None)})
        return out

    @staticmethod
    def app(row):
        slug = str(row.get('slug') or '')
        meta = row.get('meta') or {}
        logo = meta.get('logo') if isinstance(meta.get('logo'), str) and meta['logo'].startswith('https://') else None
        managed = list(row.get('composio_managed_auth_schemes') or [])
        return {'slug': slug, 'name': row.get('name') or slug, 'enabled': row.get('enabled'),
                'is_no_auth': bool(row.get('no_auth') or row.get('is_no_auth')),
                'description': str(meta.get('description') or '')[:240],
                'categories': [c.get('name') if isinstance(c, dict) else str(c) for c in meta.get('categories') or []][:4],
                'auth_schemes': list(row.get('auth_schemes') or [])[:8], 'managed_auth': bool(managed),
                'tools_count': meta.get('tools_count'),
                # Composio's official logo service when the listing omits meta.logo.
                'logo': logo or ('https://logos.composio.dev/api/' + slug if SLUG.fullmatch(slug) else None)}

    # -- Authentication requirements (from Composio's toolkit metadata) -----------------------
    def app_detail(self, who, service):
        """The app's real auth schemes and fields, for the Connect form. No provider account access."""
        if not SLUG.fullmatch(str(service or '')):
            raise ConnectionError('Invalid app.')
        scope, key = self._catalogue_key(who)
        detail = self._toolkit_detail(scope, key, service)
        rows = [r for r in self.rows(who) if r['service'] == service] if who else []
        return {**detail, 'accounts': [{'id': r['id'], 'label': r['label'], 'status': r['status']} for r in rows if r['enabled']]}

    def _toolkit_detail(self, scope, key, service):
        def load():
            raw = self.composio('GET', '/toolkits/' + service, key)
            managed = set(raw.get('composio_managed_auth_schemes') or [])
            meta = raw.get('meta') or {}
            general = [u for u in (raw.get('auth_guide_url'), raw.get('docs_url'), meta.get('app_url'))
                       if isinstance(u, str) and u.startswith('https://')]
            schemes = []
            for item in raw.get('auth_config_details') or []:
                mode = str(item.get('mode') or '').upper()
                if not re.fullmatch(r'[A-Z0-9_]{2,40}', mode):
                    continue
                fields = item.get('fields') or {}
                def norm(group, required):
                    out = []
                    for f in (fields.get(group) or {}).get('required' if required else 'optional') or []:
                        name = str(f.get('name') or '')
                        if not FIELD_NAME.fullmatch(name):
                            continue
                        secret = bool(f.get('is_secret')) or bool(SECRET_FIELD.search(name))
                        options = f.get('enum') or f.get('options') or []
                        if not isinstance(options, list):
                            options = []
                        choices = []
                        for option in options[:50] if not secret else []:
                            value = option.get('value') if isinstance(option, dict) else option
                            if isinstance(value, (str, int, float, bool)):
                                label = option.get('label') if isinstance(option, dict) else value
                                choices.append({'value': str(value), 'label': str(label or value)[:100]})
                        default = f.get('default')
                        out.append({'name': name, 'label': str(f.get('displayName') or name)[:80],
                                    'description': str(f.get('description') or '')[:700],
                                    'required': bool(required), 'type': str(f.get('type') or 'string')[:20],
                                    'default': default if not secret and isinstance(default, (str, int, float, bool)) else None,
                                    'secret': secret, 'choices': choices})
                    return out
                config = norm('auth_config_creation', True) + norm('auth_config_creation', False)
                account = norm('connected_account_initiation', True) + norm('connected_account_initiation', False)
                hint = item.get('auth_hint_url')
                links = ([hint] if isinstance(hint, str) and hint.startswith('https://') else []) + [
                    u for f in config + account for u in _https_links(f['description'])]
                schemes.append({'mode': mode, 'name': str(item.get('name') or mode)[:80], 'managed': mode in managed,
                                'oauth': mode in OAUTH_SCHEMES, 'config_fields': config, 'account_fields': account,
                                'help_urls': list(dict.fromkeys(links + general))[:4]})
            for mode in managed - {s['mode'] for s in schemes}:
                schemes.append({'mode': mode, 'name': mode, 'managed': True, 'oauth': mode in OAUTH_SCHEMES,
                                'config_fields': [], 'account_fields': [], 'help_urls': general[:2]})
            # Simplest first: Composio-managed sign-in, then schemes that need nothing from a
            # developer (API key, token, basic), then bring-your-own OAuth app.
            schemes.sort(key=lambda s: (not s['managed'], any(f['required'] for f in s['config_fields']), s['oauth']))
            return {'slug': service, 'name': raw.get('name') or service, 'no_auth': bool(raw.get('no_auth')),
                    'logo': meta.get('logo') if str(meta.get('logo') or '').startswith('https://') else 'https://logos.composio.dev/api/' + service,
                    'description': str(meta.get('description') or '')[:400], 'help_urls': general[:3],
                    'get_current_user_endpoint': raw.get('get_current_user_endpoint') if isinstance(raw.get('get_current_user_endpoint'), str) else '',
                    'schemes': schemes, 'resource': self._resource_metadata(service)}
        return cached(('toolkit', self.composio, scope, service), 3600, load)

    def rows(self, who):
        with self.identity.db() as db:
            rows = db.execute('SELECT * FROM hub_connections WHERE user=? AND workspace=? ORDER BY name,id', (who['user'], who['workspace'])).fetchall()
        rows = [self.public(dict(row)) for row in rows if not self.removed(row)]
        project = self._project_row(who)
        current = project['fingerprint'] if project and project['status'] == 'LINKED' else None
        for row in rows:
            # Only the currently linked project serves David, Forge, Jobs and agents.
            if row['config'].get('source') == 'project' and row['config'].get('project') != current and row['status'] == 'ACTIVE':
                row['status'] = row['connection_status'] = 'PROJECT_CHANGED'
        return rows

    def public(self, row):
        row['config'] = json.loads(row['config']) if isinstance(row['config'], str) else row['config']
        # The hosted link is returned only from an explicit scoped Connect call,
        # never in account listings, Factory evidence or routine receipts.
        row['config'].pop('authorization_url', None)
        if not row['config'].get('provider_validated'):
            row['config'].pop('provider_identity', None)
        account = row['config'].get('provider_account') or {}
        # The account's real name or email when known; otherwise Composio's alias.
        identity = row['config'].get('provider_identity') if row['config'].get('provider_validated') else ''
        row['identity'] = self._identity_value(identity)
        row['label'] = row['identity'] or self._identity_value(account.get('alias')) or row['name']
        row['connection_status'] = row['status']
        if row['enabled'] and row['config'].get('verification_error'):
            row['connection_status'] = 'UNVERIFIED'
        if row['status'] == 'ACTIVE' and row['kind'] == 'composio' and not (
                row['config'].get('provider_validated') or row['config'].get('no_auth')):
            row['connection_status'] = 'UNVERIFIED'
        row['access'] = row['config'].get('access', {'mode': 'Automatic', 'agents': {}})
        if not row['enabled']:
            row['access'] = {**row['access'], 'mode': 'Disabled'}
        row['operations'] = list(READS.get(row['service'], {})) if row['kind'] == 'composio' else [op['name'] for op in row['config']['operations']]
        if row['kind'] == 'composio':
            row['operations'] += list(row['config'].get('tools', {}))
        row['permissions'] = [name + ':read' for name in row['operations']]
        if row['kind'] == 'composio':
            row['operations'] = list(dict.fromkeys(row['operations'] + list(OPERATIONS.get(row['service'], {}))))
            row['permissions'] = [name + (':write' if is_write(row['service'], name) else ':read') for name in row['operations']]
            if row['config'].get('source') == 'project':
                # Every discovered tool of a connected app, read or write per policy.
                row['operations'] += list(GENERIC_OPS)
                row['permissions'] += ['tools:read', 'tool:policy']
        return row

    def row(self, who, ident):
        with self.identity.db() as db:
            row = db.execute('SELECT * FROM hub_connections WHERE id=? AND user=? AND workspace=?', (ident, who['user'], who['workspace'])).fetchone()
        if not row:
            raise ConnectionError('Connection not found in this workspace member’s scope.')
        if self.removed(row):
            raise ConnectionError(REMOVED)
        return self.public(dict(row))

    @staticmethod
    def removed(row):
        """Old For You (CLI) and application-key bindings: kept as history, never listed or used."""
        config = row['config'] if isinstance(row['config'], dict) else json.loads(row['config'] or '{}')
        # Also rows the member removed with X: hidden everywhere, kept as history.
        return bool(config.get('dismissed')) or (row['kind'] == 'composio' and config.get('source') != 'project')

    # -- Project-scoped Composio --------------------------------------------------------------
    @staticmethod
    def managed_user(who):
        """Stable, opaque Composio user ID for one TTROS member; never an email or a guessable name."""
        return 'ttros_' + hashlib.sha256((who['workspace'] + ':' + who['user']).encode()).hexdigest()[:32]

    def _project_row(self, who):
        with self.identity.db() as db:
            return db.execute('SELECT * FROM hub_projects WHERE workspace=?', (who['workspace'],)).fetchone()

    def project_state(self, who):
        row = self._project_row(who)
        if not row:
            return None
        return {'mode': row['mode'], 'status': row['status'], 'key_fingerprint': row['fingerprint'],
                'owner': self._may_replace(who, row), 'composio_user_id': self._composio_user(who, row),
                'linked': row['linked'], 'verified': row['verified']}

    @staticmethod
    def operator_user():
        """One stable Platform user for every verified operator login and Telegram.

        Derived from the operator workspace alone, never from a login, Composio
        administrator or connected-app email. Membership of OPERATOR_WORKSPACE is
        granted only by the verified operator gate; customers keep one user each.
        """
        return 'ttros_' + hashlib.sha256(b'ttros-operator:operator-principal').hexdigest()[:32]

    def _composio_user(self, who, project=None):
        with self.identity.db() as db:
            if who['workspace'] == OPERATOR_WORKSPACE:
                mapped = db.execute('SELECT DISTINCT composio_user FROM hub_project_users WHERE workspace=?',
                                    (who['workspace'],)).fetchall()
                return mapped[0]['composio_user'] if len(mapped) == 1 else self.operator_user()
            mapped = db.execute('SELECT composio_user FROM hub_project_users WHERE workspace=? AND user=?',
                                (who['workspace'], who['user'])).fetchone()
        return mapped['composio_user'] if mapped else self.managed_user(who)

    @staticmethod
    def _may_replace(who, project):
        # The operator workspace is one principal; elsewhere only the linking member.
        return not project or project['owner'] == who['user'] or who['workspace'] == OPERATOR_WORKSPACE

    def _operator_members(self, who):
        """Other verified operator logins that share this member's Platform user."""
        if who['workspace'] != OPERATOR_WORKSPACE:
            return []
        with self.identity.db() as db:
            rows = db.execute('SELECT u.id,u.email FROM users u JOIN memberships m ON m.user=u.id WHERE m.workspace=? AND u.id<>?',
                              (OPERATOR_WORKSPACE, who['user'])).fetchall()
        return [dict(user=r['id'], workspace=OPERATOR_WORKSPACE, email=r['email'], method='operator_shared') for r in rows]

    @staticmethod
    def _project_ident(who, account_id):
        return 'cx_' + hashlib.sha256((who['workspace'] + ':' + who['user'] + ':project:' + account_id).encode()).hexdigest()[:24]

    def _project(self, who):
        """(project row, project API key, this member's Composio user ID). Key stays in memory."""
        row = self._project_row(who)
        if not row or row['status'] != 'LINKED':
            raise ConnectionError('Link a Composio project for this workspace first.')
        key = self.vault.get(row['secret'], who['workspace']) if row['mode'] == 'customer' else self._managed_key()
        if row['fingerprint'] != 'managed' and row['fingerprint'] != hashlib.sha256(key.encode()).hexdigest()[:12]:
            raise ConnectionError('The server project credential changed. Existing bindings are preserved and cannot execute until the project is verified.')
        return row, key, self._composio_user(who, row)

    def link_project(self, who, api_key, composio_user_id=''):
        """Link an existing customer-owned Composio project by its project API key."""
        api_key = str(api_key or '').strip()
        if not PROJECT_KEY.fullmatch(api_key):
            raise ConnectionError('Enter a Composio project API key (letters, digits, - or _). Nothing was stored.')
        current = self._project_row(who)
        if not self._may_replace(who, current):
            raise ConnectionError('Only the member who linked this workspace project can replace it.')
        operator = who['workspace'] == OPERATOR_WORKSPACE
        user_id = str(composio_user_id or '').strip() or (self.operator_user() if operator else self.managed_user(who))
        if not COMPOSIO_USER.fullmatch(user_id):
            raise ConnectionError('Composio user ID contains unsupported characters. Nothing was stored.')
        # Verify server-side before anything is stored; the key never reaches a response or log.
        listed = self.composio('GET', '/connected_accounts', api_key, query={'user_ids': user_id, 'limit': 50})
        if not isinstance(listed, dict) or not isinstance(listed.get('items'), list):
            raise ConnectionError('Composio did not confirm this project key. Nothing was stored.')
        ref = self.vault.put(who['workspace'], api_key)
        fingerprint = hashlib.sha256(api_key.encode()).hexdigest()[:12]
        now = time.time()
        with self.identity.db() as db:
            if operator:
                # Operator logins share one mapping; replacing the project replaces it.
                db.execute('DELETE FROM hub_project_users WHERE workspace=?', (who['workspace'],))
            elif db.execute('SELECT 1 FROM hub_project_users WHERE workspace=? AND composio_user=? AND user<>?',
                            (who['workspace'], user_id, who['user'])).fetchone():
                self.vault.delete(ref, who['workspace'])
                raise ConnectionError('That Composio user ID already belongs to another member of this workspace.')
            db.execute('INSERT OR REPLACE INTO hub_projects VALUES(?,?,?,?,?,?,?,?)',
                       (who['workspace'], 'customer', ref, fingerprint, who['user'], now, now, 'LINKED'))
            db.execute('INSERT OR REPLACE INTO hub_project_users VALUES(?,?,?)', (who['workspace'], who['user'], user_id))
        if current and current['mode'] == 'customer' and current['secret'] != ref:
            self.vault.delete(current['secret'], who['workspace'])
        self.identity.receipt(who, 'hub.project.link', 'customer:' + fingerprint)
        return {**self.project_state(who), 'accounts_found': len(listed['items']), 'accounts_connected': 0,
                'message': 'Project key verified with Composio and sealed in server-side storage. Existing accounts were not changed.'}

    def _managed_key(self):
        try:
            return self.managed_key()
        except ConnectionError:
            raise ConnectionError('App connections are temporarily unavailable. Please contact TTROS support.') from None

    def use_managed_project(self, who):
        """New customer: connect apps through the TTROS-managed Composio project, one opaque user ID per member."""
        current = self._project_row(who)
        if current and current['mode'] == 'managed' and current['status'] == 'LINKED':
            self._project(who)
            return self.project_state(who)
        if not self._may_replace(who, current):
            raise ConnectionError('Only the member who linked this workspace project can replace it.')
        key = self._managed_key()
        self.composio('GET', '/connected_accounts', key, query={'user_ids': self._composio_user(who), 'limit': 1})
        now = time.time()
        with self.identity.db() as db:
            db.execute('INSERT OR REPLACE INTO hub_projects VALUES(?,?,?,?,?,?,?,?)',
                       (who['workspace'], 'managed', '', hashlib.sha256(key.encode()).hexdigest()[:12], who['user'], now, now, 'LINKED'))
        if current and current['mode'] == 'customer':
            self.vault.delete(current['secret'], who['workspace'])
        self.identity.receipt(who, 'hub.project.link', 'managed')
        return {**self.project_state(who), 'message': 'TTROS-managed Composio enabled for this workspace. Each member connects with their own isolated Composio user.'}

    def unlink_project(self, who):
        row = self._project_row(who)
        if not row:
            raise ConnectionError('No Composio project is linked to this workspace.')
        if not self._may_replace(who, row):
            raise ConnectionError('Only the member who linked this workspace project can unlink it.')
        if row['mode'] == 'customer':
            self.vault.delete(row['secret'], who['workspace'])
        with self.identity.db() as db:
            db.execute('DELETE FROM hub_projects WHERE workspace=?', (who['workspace'],))
            db.execute('DELETE FROM hub_project_users WHERE workspace=?', (who['workspace'],))
            db.execute("UPDATE hub_connections SET enabled=0,status='DISCONNECTED' WHERE workspace=? AND json_extract(config,'$.source')='project'",
                       (who['workspace'],))
        self.identity.receipt(who, 'hub.project.unlink', row['mode'])
        return {'status': 'UNLINKED', 'message': 'TTROS access to the project was removed and its key deleted from TTROS storage. Accounts in Composio were not changed.'}

    def project_accounts(self, who):
        """This member's connected accounts in the linked project. Ownership is re-checked on bind."""
        project, key, user_id = self._project(who)
        with self.identity.db() as db:
            saved = db.execute(
                "SELECT * FROM hub_connections WHERE workspace=? AND (user=? OR ?=?) AND json_extract(config,'$.project')=?",
                (who['workspace'], who['user'], who['workspace'], OPERATOR_WORKSPACE, project['fingerprint'])).fetchall()
        unavailable = {r['account'] for r in saved if r['user'] == who['user'] or not r['enabled']}
        items, cursor = [], None
        for _ in range(10):
            page = self.composio('GET', '/connected_accounts', key,
                                 query={'user_ids': user_id, 'limit': 100, **({'cursor': cursor} if cursor else {})})
            items += [row for row in page.get('items') or [] if isinstance(row, dict)]
            cursor = page.get('next_cursor')
            if not cursor:
                break
        accounts = []
        for row in items:
            # The list endpoint may omit user_id; when present it must be this member's.
            if row.get('user_id') not in (None, '', user_id) or not ACCOUNT_ID.fullmatch(str(row.get('id') or '')):
                continue
            if row['id'] in unavailable:
                continue
            if not row.get('user_id'):
                # Do not disclose accounts on the strength of a provider list
                # filter alone when its response omits ownership.
                try:
                    self._project_account(who, key, user_id, row['id'])
                except ConnectionError:
                    continue
            service = (row.get('toolkit') or {}).get('slug') or ''
            accounts.append({'service': service, 'account': row['id'], 'status': 'DISABLED' if row.get('is_disabled') else row.get('status'),
                             'identity': self._identity_value(row.get('alias')) or self._identity_value(row.get('word_id')) or '', 'source': 'project',
                             'permissions': list(READS.get(service, {}))})
        return accounts

    def _project_account(self, who, key, user_id, account_id):
        if not ACCOUNT_ID.fullmatch(str(account_id or '')):
            raise ConnectionError('Use an exact Composio connected-account ID (ca_…).')
        account = self.composio('GET', '/connected_accounts/' + account_id, key)
        if account.get('id') != account_id or account.get('user_id') != user_id:
            raise ConnectionError('This connected account does not belong to your Composio user in this project.')
        reason = account.get('status_reason')
        reason = reason.strip()[:240] if isinstance(reason, str) else ''
        # Do not expose credentials, links or email addresses from provider diagnostics.
        if re.search(r'(?i)\b(token|secret|password|bearer|api.?key|client.?id)\b|https?://|\S+@\S+', reason):
            reason = 'Composio reported an authorization failure. Check its project logs for details.'
        return {'id': account['id'], 'user_id': account['user_id'], 'status': account.get('status'),
                'status_reason': reason, 'is_disabled': bool(account.get('is_disabled')),
                'alias': self._identity_value(account.get('alias')),
                'toolkit': (account.get('toolkit') or {}).get('slug'),
                'auth_config': {k: (account.get('auth_config') or {}).get(k) for k in ('id', 'auth_scheme', 'is_composio_managed')},
                'created_at': account.get('created_at'), 'updated_at': account.get('updated_at')}

    def _lifecycle_lock(self, who, service):
        member = '' if who['workspace'] == OPERATOR_WORKSPACE else who['user']
        return self._lock(f'lifecycle:{who["workspace"]}:{member}:{service}')

    def _saved_account(self, who, project, account_id):
        with self.identity.db() as db:
            return db.execute(
                """SELECT 1 FROM hub_connections WHERE workspace=? AND account=? AND
                   (user=? OR (?=? AND enabled=0)) AND json_extract(config,'$.project')=?""",
                (who['workspace'], account_id, who['user'], who['workspace'], OPERATOR_WORKSPACE,
                 project['fingerprint'])).fetchone()

    def bind_project(self, who, account_id, *, service=None, replace=None, resource=None):
        """Attach the exact account the member selected in the linked Platform project."""
        project, key, user_id = self._project(who)
        account = self._project_account(who, key, user_id, account_id)
        toolkit = account['toolkit'] or ''
        if not SLUG.fullmatch(toolkit) or (service and service != toolkit):
            raise ConnectionError('This Platform account is for a different app.')
        with self._lifecycle_lock(who, toolkit):
            self._replacement(who, toolkit, replace)
            resource = self._resource(toolkit, resource)
            if not replace:
                try:
                    current = self.row(who, self._project_ident(who, account_id))
                except ConnectionError:
                    current = None
                if (current and current['enabled'] and current['config'].get('project') == project['fingerprint']
                        and current['config'].get('resource') == resource):
                    return self.project_status(who, current['id'], current)
            return self._bind_new(who, project, key, user_id, toolkit, account, replace=replace, resource=resource)

    def _replacement(self, who, service, replace):
        if replace:
            row = self.row(who, replace)
            if row['service'] != service or row['kind'] != 'composio':
                raise ConnectionError('Choose a connection for this app to replace.')
            return row

    def _bind_new(self, who, project, key, user_id, service, account, *, replace=None, detail=None, resource=None, fresh=False):
        if account['toolkit'] != service or self._saved_account(who, project, account['id']):
            raise ConnectionError('This Platform account was already used. Connect again with new credentials.')
        status = 'DISABLED' if account['is_disabled'] else account['status'] or 'UNVERIFIED'
        if not fresh and status != 'ACTIVE':
            raise ConnectionError('This Platform account is unavailable. Reconnect with new credentials.')
        identity, no_probe = '', False
        if status == 'ACTIVE':
            status, identity, no_probe = self._validate_provider(project, key, user_id, service, account, resource, detail)
        config = {'source': 'project', 'project': project['fingerprint'], 'provider_account': account,
                  'app_name': (detail or {}).get('name'), 'replace': replace, 'resource': resource}
        if status in ('INVALID_CREDENTIALS', 'EXPIRED', 'SCOPE_MISMATCH', 'FAILED', 'DISABLED'):
            if fresh:
                self.save(who, self._project_ident(who, account['id']), 'composio', service, service, account['id'],
                          {**config, 'dismissed': time.time()}, status, None, enabled=0)
            return {'status': status, 'connection_status': status, 'message': self._connection_message(status)}
        return self._store_binding(who, service, account, config, status, identity, no_probe)

    @staticmethod
    def _connection_message(status):
        return {
            'ACTIVE': 'Connected. Your app is ready to use.',
            'INVALID_CREDENTIALS': 'The provider rejected these credentials. Check them and try again.',
            'EXPIRED': 'This authorization expired. Reconnect to continue.',
            'SCOPE_MISMATCH': 'Use a credential scoped only to the resource you selected, with permission to read it.',
            'UNVERIFIED': 'TTROS could not verify the new authorization. Check the account before relying on it.',
            'FAILED': 'The provider could not complete authorization. Try again.',
            'DISABLED': 'This account is disabled at the provider.',
        }.get(status, 'Waiting for provider approval.')

    # -- Connect: one generic, metadata-driven flow for every app --------------------------------
    def _existing_auth_configs(self, key, service):
        listed = self.composio('GET', '/auth_configs', key, query={'toolkit_slug': service, 'limit': 100})
        out = []
        for row in listed.get('items') or []:
            toolkit = (row.get('toolkit') or {}).get('slug')
            if (toolkit not in (None, service) or row.get('is_disabled') or str(row.get('status') or 'ENABLED') != 'ENABLED'
                    or not AUTH_CONFIG_ID.fullmatch(str(row.get('id') or ''))):
                continue
            if toolkit is None and row.get('name') and service not in str(row['name']):
                continue                                   # list filtered server-side; be strict when unlabeled
            out.append({'id': row['id'], 'scheme': (str(row.get('auth_scheme') or '').upper() or None),
                        'managed': bool(row.get('is_composio_managed')), 'uses': row.get('no_of_connections') or 0})
        # Deterministic choice among several: Composio-managed, then the most used, then id.
        return sorted(out, key=lambda c: (not c['managed'], -int(c['uses'] or 0), c['id']))

    def _cache_auth_config(self, project, service, auth_config, scheme):
        with self.identity.db() as db:
            db.execute('INSERT OR REPLACE INTO hub_auth_configs(project,toolkit,auth_config,scheme) VALUES(?,?,?,?)',
                       (project['fingerprint'], service, auth_config, scheme))

    def _auth_plan(self, who, project, key, service, scheme=None, config_values=None, account_values=None):
        """Choose the auth scheme and auth config from Composio's real metadata.

        Reuse an enabled auth config (cached or listed) when one fits; otherwise create
        exactly what the scheme supports: Composio-managed auth, or a custom auth config
        (API key / bearer / basic need no developer fields; custom OAuth needs the
        customer's own client fields, collected securely). Never assumes managed auth.
        """
        scheme = str(scheme or '').upper() or None
        with self.identity.db() as db:
            saved = db.execute('SELECT auth_config,scheme FROM hub_auth_configs WHERE project=? AND toolkit=?',
                               (project['fingerprint'], service)).fetchone()
        try:
            detail = self._toolkit_detail(project['fingerprint'], key, service)
        except ConnectionError:
            if not saved or scheme:
                raise
            detail = None                                  # metadata outage: an existing OAuth config still works
        if detail and detail['no_auth']:
            return {'no_auth': True, 'detail': detail}
        schemes = (detail or {}).get('schemes') or []
        by_mode = {s['mode']: s for s in schemes}
        if scheme and detail and scheme not in by_mode:
            raise ConnectionError(f'{detail["name"]} does not support {scheme} authentication.')
        if saved and not config_values and (not scheme or (saved['scheme'] or 'OAUTH2') == scheme):
            mode = saved['scheme'] or (scheme or 'OAUTH2')
            if not detail or mode in by_mode:
                chosen = by_mode.get(mode) or {'mode': mode, 'oauth': mode in OAUTH_SCHEMES, 'account_fields': [],
                                                'config_fields': [], 'managed': True, 'help_urls': [], 'name': mode}
                return {'auth_config': saved['auth_config'], 'scheme': chosen, 'detail': detail}
        if not schemes:
            raise ConnectionError(f'Composio reports no supported authentication for {service}.')
        existing = self._existing_auth_configs(key, service)
        if scheme:
            chosen = by_mode[scheme]
        else:
            # Prefer a scheme the project already has an auth config for, else the simplest.
            reusable = [s for s in schemes if any(c['scheme'] in (s['mode'], None) for c in existing)]
            chosen = (reusable or schemes)[0]
        match = [c for c in existing if c['scheme'] in (chosen['mode'], None)]
        if match and not config_values:
            self._cache_auth_config(project, service, match[0]['id'], chosen['mode'])
            return {'auth_config': match[0]['id'], 'scheme': chosen, 'detail': detail}
        if not chosen['oauth'] and not account_values and not config_values and any(f['required'] for f in chosen['account_fields']):
            # Ask first; create nothing in the project until the member submits the form.
            return {'form': self._form(detail, chosen, 'account', chosen['account_fields']), 'scheme': chosen, 'detail': detail}
        if chosen['managed'] and not config_values:
            body = {'toolkit': {'slug': service}, 'auth_config': {'type': 'use_composio_managed_auth'}}
        else:
            values = self._form_values(chosen['config_fields'], config_values or {})
            missing = [f for f in chosen['config_fields'] if f['required'] and f['name'] not in values]
            if missing:
                return {'form': self._form(detail, chosen, 'auth_config', chosen['config_fields']), 'scheme': chosen, 'detail': detail}
            body = {'toolkit': {'slug': service}, 'auth_config': {'type': 'use_custom_auth', 'authScheme': chosen['mode'],
                    'name': f'TTROS {detail["name"]} {chosen["mode"].lower()}'[:60], 'credentials': values}}
        created = self.composio('POST', '/auth_configs', key, body=body)
        auth_config = (created.get('auth_config') or {}).get('id')
        if not AUTH_CONFIG_ID.fullmatch(str(auth_config or '')):
            raise ConnectionError(f'Composio did not create an auth configuration for {service}.')
        self._cache_auth_config(project, service, auth_config, chosen['mode'])
        self.identity.receipt(who, 'hub.auth_config.create', f'{service}:{chosen["mode"]}:{"managed" if chosen["managed"] and not config_values else "custom"}')
        return {'auth_config': auth_config, 'scheme': chosen, 'detail': detail}

    @staticmethod
    def _form_values(fields, submitted):
        """Only declared fields, as bounded single-line strings. Values are never logged or stored."""
        if not isinstance(submitted, dict):
            raise ConnectionError('Credential fields must be a JSON object.')
        declared = {f['name']: f for f in fields}
        values = {}
        for name, value in submitted.items():
            if name not in declared:
                raise ConnectionError(f'Unexpected field {str(name)[:40]!r}; only the app’s declared fields are accepted.')
            if value is None or value == '':
                continue
            value = str(value).strip()
            if len(value) > 8192 or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ConnectionError(f'{declared[name]["label"]} must be a single line of at most 8192 characters.')
            choices = declared[name].get('choices') or []
            if choices and value not in {choice['value'] for choice in choices}:
                raise ConnectionError(f'Choose a valid {declared[name]["label"]}.')
            if value:
                values[name] = value
        for field in fields:
            if field['name'] not in values and field['default'] not in (None, '') and field['required']:
                values[field['name']] = str(field['default'])
        return values

    @staticmethod
    def _form(detail, scheme, stage, fields):
        name = (detail or {}).get('name') or 'This app'
        kind = {'API_KEY': 'an API key', 'BEARER_TOKEN': 'an access token', 'BASIC': 'a username and password',
                'BASIC_WITH_JWT': 'a username and password'}.get(scheme['mode'], 'a few details')
        message = (f'{name} needs your own OAuth app (client ID and secret) for this sign-in method.' if stage == 'auth_config'
                   else f'{name} connects with {kind}.')
        others = [s for s in (detail or {}).get('schemes') or [] if s['mode'] != scheme['mode']]
        return {'status': 'NEEDS_INPUT', 'service': (detail or {}).get('slug'), 'app': {'name': name, 'logo': (detail or {}).get('logo')},
                'scheme': scheme['mode'], 'stage': stage, 'fields': fields, 'help_urls': scheme.get('help_urls') or [],
                'alternatives': [{'scheme': s['mode'], 'managed': s['managed'], 'oauth': s['oauth'],
                                  'needs_developer_app': any(f['required'] for f in s['config_fields'])} for s in others],
                'message': message + ' Enter it below. TTROS passes it straight to Composio over HTTPS and never stores, '
                                     'logs or shows it, and David never asks for it in chat.'}

    def _connect_project(self, who, service, *, scheme=None, fields=None, config_fields=None, replace=None, resource=None):
        """Start or complete one connection for this member in the linked project."""
        if not SLUG.fullmatch(str(service or '')):
            raise ConnectionError('Invalid toolkit slug.')
        callback = self.identity.connection_return_url()   # refuse a bad origin before any provider change
        project, key, user_id = self._project(who)
        try:
            plan = self._auth_plan(who, project, key, service, scheme, config_fields, fields)
        except ProviderRefused:
            if config_fields:
                raise ConnectionError('The provider app settings could not be accepted. Check them and try again.') from None
            raise
        if plan.get('no_auth'):
            return self._bind_no_auth(who, project, service, plan['detail'])
        if plan.get('form'):
            return plan['form']
        chosen, auth_config = plan['scheme'], plan['auth_config']
        try:
            values = self._form_values(chosen['account_fields'], fields or {})
            missing = [f for f in chosen['account_fields'] if f['required'] and f['name'] not in values]
            if chosen['oauth']:
                if missing:
                    return self._form(plan['detail'], chosen, 'account', chosen['account_fields'])
                return self._hosted_link(who, project, key, user_id, service, auth_config, values, chosen['mode'], callback, replace, resource)
            if missing or not values:
                return self._form(plan['detail'], chosen, 'account', chosen['account_fields'])
            resource = self._resource(service, resource)
            created = self.composio('POST', '/connected_accounts', key, body={
                'auth_config': {'id': auth_config},
                'connection': {'user_id': user_id, 'state': {'authScheme': chosen['mode'], 'val': {**values, 'status': 'ACTIVE'}}}})
        except ProviderRefused as exc:
            if exc.status in (400, 422) or 'CredentialValidation' in exc.detail:
                return {'status': 'INVALID_CREDENTIALS', 'message': self._connection_message('INVALID_CREDENTIALS')}
            raise ConnectionError('Authorization could not start. Try again in a moment.') from None
        account_id = created.get('id') or (created.get('connected_account') or {}).get('id')
        account = self._project_account(who, key, user_id, account_id)
        bound = self._bind_new(who, project, key, user_id, service, account, replace=replace, detail=plan['detail'],
                               resource=resource, fresh=True)
        status = bound['connection_status']
        return {'id': bound.get('id'), 'status': status, 'message': self._connection_message(status)}

    def _hosted_link(self, who, project, key, user_id, service, auth_config, values, mode, callback, replace=None, resource=None):
        resource = self._resource(service, resource)
        body = {'auth_config_id': auth_config, 'user_id': user_id, 'callback_url': callback}
        if values:
            body['connection_data'] = values
        with self.identity.db() as db:
            attempts = db.execute(
                "SELECT config,id FROM hub_connections WHERE user=? AND workspace=? AND service=? AND enabled=1 "
                "AND status IN ('PENDING','INITIATED','INITIALIZING') ORDER BY rowid DESC",
                (who['user'], who['workspace'], service)).fetchall()
        if not values:
            for attempt in attempts:
                config = json.loads(attempt['config'])
                if (config.get('project'), config.get('scheme'), config.get('replace'), config.get('resource')) != (
                        project['fingerprint'], mode, replace, resource):
                    continue
                if self._link_expiry(config) > time.time() and config.get('authorization_url'):
                    return {'id': attempt['id'], 'url': config['authorization_url'], 'status': 'PENDING'}
        link = self.composio('POST', '/connected_accounts/link', key, body=body)
        url, account_id = link.get('redirect_url'), link.get('connected_account_id')
        parts = urlsplit(url if isinstance(url, str) else '')
        host = (parts.hostname or '').lower()
        if parts.scheme != 'https' or not (host == 'composio.dev' or host.endswith('.composio.dev')):
            raise ConnectionError('Composio did not issue a valid hosted Connect Link.')
        if not ACCOUNT_ID.fullmatch(str(account_id or '')):
            raise ConnectionError('Composio did not return an exact connected-account attempt ID.')
        if self._saved_account(who, project, account_id):
            raise ConnectionError('Composio did not issue a fresh authorization. Try again.')
        ident = self._project_ident(who, account_id)
        self.save(who, ident, 'composio', service, service + ' authorization', account_id,
                  {'source': 'project', 'project': project['fingerprint'], 'issued_account_id': account_id,
                   'authorization_url': url, 'expires_at': link.get('expires_at'), 'scheme': mode,
                   'started_at': time.time(), 'replace': replace, 'resource': resource}, 'PENDING', None)
        return {'id': ident, 'url': url, 'status': 'PENDING', 'expires_at': link.get('expires_at'),
                'message': 'Complete the provider approval in Composio, then return and Check status. TTROS verifies the exact account server-side.'}

    @staticmethod
    def _link_expiry(config):
        try:
            return dt.datetime.fromisoformat((config.get('expires_at') or '').replace('Z', '+00:00')).timestamp()
        except (TypeError, ValueError, AttributeError):
            return 0

    def _bind_no_auth(self, who, project, service, detail):
        """Apps that need no sign-in are available at once; nothing is stored upstream."""
        ident = 'cx_' + hashlib.sha256((who['workspace'] + ':' + who['user'] + ':noauth:' + service).encode()).hexdigest()[:24]
        self.save(who, ident, 'composio', service, detail['name'], '',
                  {'source': 'project', 'project': project['fingerprint'], 'app_name': detail['name'], 'no_auth': True}, 'ACTIVE', time.time())
        return {'id': ident, 'status': 'ACTIVE', 'message': f'{detail["name"]} needs no sign-in. It is available to David, Agent Forge and Jobs now.'}

    def connect_app(self, who, service, *, scheme=None, fields=None, config_fields=None, replace=None, resource=None):
        """Start fresh authentication. Existing accounts are available through explicit binding."""
        if not SLUG.fullmatch(str(service or '')):
            raise ConnectionError('Invalid app.')
        with self._lifecycle_lock(who, service):
            self._replacement(who, service, replace)
            if not self._project_row(who):
                self.use_managed_project(who)
            return self._connect_project(who, service, scheme=scheme, fields=fields,
                                        config_fields=config_fields, replace=replace, resource=resource)

    connect_project = connect_app

    def _detach(self, who, row, status):
        config = row['config']
        members = [who, *self._operator_members(who)] if row['account'] and config.get('source') == 'project' else [who]
        with self.identity.db() as db:
            for member in members:
                ident = self._project_ident(member, row['account']) if member != who else row['id']
                db.execute("""UPDATE hub_connections SET enabled=0,status=?,
                              config=CASE WHEN ?='REMOVED' THEN json_set(config,'$.dismissed',?) ELSE config END
                              WHERE id=? AND user=? AND workspace=? AND status<>'REMOVED'""",
                           (status, status, time.time(), ident, member['user'], who['workspace']))

    @staticmethod
    def _identity_value(value):
        if not isinstance(value, str) or not 0 < len(value.strip()) <= 120 or re.search(r'[\x00-\x1f]', value):
            return ''
        if re.search(r'(?:error|exception)$|unauthorized|forbidden|invalid[ _-]*(?:credentials|api[ _-]*key|token)', value, re.I):
            return ''
        return value.strip()

    @classmethod
    def _identity_from(cls, data):
        # Only identity objects and known wrappers, never arbitrary nested rows or an error.
        if not isinstance(data, dict) or cls._provider_error(data):
            return ''
        for key in ('email', 'emailAddress', 'email_address', 'mail', 'userPrincipalName', 'login', 'username',
                    'user_name', 'display_name', 'displayName', 'name'):
            identity = cls._identity_value(data.get(key))
            if identity:
                return identity
        for key in ('user', 'profile', 'account', 'data'):
            value = data.get(key)
            if isinstance(value, dict) and not cls._provider_error(value):
                for field in ('email', 'emailAddress', 'email_address', 'login', 'username', 'display_name', 'name'):
                    if identity := cls._identity_value(value.get(field)):
                        return identity
        return ''

    @staticmethod
    def _provider_error(data):
        return (data.get('success') is False or data.get('successful') is False
                or any(data.get(key) for key in ('error', 'errors', 'error_description', 'error_code'))
                or any(isinstance(data.get(key), str) and data[key].lower().endswith(('error', 'exception'))
                       for key in ('name', 'code', 'type'))
                or any(isinstance(data.get(key), dict) and Connections._provider_error(data[key])
                       for key in ('user', 'profile', 'account', 'data')))

    @staticmethod
    def _auth_error(data):
        for key in ('name', 'code', 'type', 'error', 'error_description', 'data'):
            value = data.get(key)
            if isinstance(value, str) and re.search(
                    r'unknown[ _-]*api[ _-]*key|invalid[ _-]*(?:api[ _-]*key|token|credential)|unauthenticated|unauthorized',
                    value, re.I):
                return True
            if isinstance(value, dict) and Connections._auth_error(value):
                return True
        return False

    @staticmethod
    def _resource_metadata(service):
        if service == 'agent_mail':
            return {'label': 'Inbox address', 'type': 'email',
                    'instruction': 'Use an API key scoped to this inbox.',
                    'help_url': 'https://docs.agentmail.to/knowledge-base/pods-multi-tenant'}
        return None

    def _resource(self, service, value):
        if not self._resource_metadata(service):
            if value:
                raise ConnectionError('This app does not support selecting a resource.')
            return None
        value = str(value or '').strip().lower()
        if not re.fullmatch(r'[^\s@/]{1,100}@[^\s@/]{1,100}', value) or len(value) > 120:
            raise ConnectionError('Enter the address of the inbox you want to connect.')
        return value

    def _validate_provider(self, project, key, user_id, service, account, resource=None, detail=None):
        """Platform ACTIVE permits a probe; only the provider can confirm authentication."""
        try:
            if service == 'cloudflare':
                # Composio's current-user proxy uses the legacy key header for Cloudflare.
                result = self.composio('POST', '/tools/execute/CLOUDFLARE_LIST_ZONES', key, body={
                    'connected_account_id': account['id'], 'user_id': user_id,
                    'version': 'latest', 'arguments': {'status': 'active', 'per_page': 5}})
                if result.get('successful') is not True or self._provider_error(result):
                    return 'UNVERIFIED', '', False
                data = result.get('data')
                if isinstance(data, dict) and isinstance(data.get('data'), dict) and 'result' not in data:
                    data = data['data']
                if not isinstance(data, dict) or data.get('success') is not True:
                    return 'UNVERIFIED', '', False
                zones = data.get('result')
                page = data.get('result_info') or {}
                if (not isinstance(zones, list) or len(zones) != 1 or not isinstance(page, dict)
                        or page.get('total_count', 1) != 1 or page.get('total_pages', 1) != 1):
                    return 'UNVERIFIED', '', False
                zone = zones[0]
                if (not isinstance(zone, dict) or zone.get('status') != 'active'
                        or not re.fullmatch(r'[0-9a-f]{32}', str(zone.get('id') or ''))):
                    return 'UNVERIFIED', '', False
                name = self._identity_value(zone.get('name'))
                if not re.fullmatch(r'(?i)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', name):
                    return 'UNVERIFIED', '', False
                return 'ACTIVE', name.lower(), False
            if service == 'agent_mail':
                endpoint = '/auth/me'  # relative to the connected account’s regional /v0 base
            else:
                detail = detail or self._toolkit_detail(project['fingerprint'], key, service)
                endpoint = detail.get('get_current_user_endpoint')
            if not endpoint:
                return 'UNVERIFIED', '', True

            def probe(path):
                return self.composio('POST', '/tools/execute/proxy', key, body={
                    'connected_account_id': account['id'], 'user_id': user_id, 'endpoint': path, 'method': 'GET'})

            result = probe(endpoint)
            data = result.get('data')
            code = result.get('status')
            if code in (401, 403):
                oauth = account['auth_config'].get('auth_scheme') in OAUTH_SCHEMES
                return ('EXPIRED' if code == 401 and oauth else 'INVALID_CREDENTIALS'), '', False
            if self._auth_error(result):
                return 'INVALID_CREDENTIALS', '', False
            if (type(code) is not int or not 200 <= code < 300 or result.get('successful') is False
                    or result.get('error') or not isinstance(data, dict) or self._provider_error(data)):
                return 'UNVERIFIED', '', False
            if service != 'agent_mail':
                return 'ACTIVE', self._identity_from(data), False
            inbox = self._identity_value(data.get('inbox_id')) if data.get('scope_type') == 'inbox' else ''
            if not resource or inbox != resource or data.get('scope_id') != resource:
                return 'SCOPE_MISMATCH', '', False
            metadata = probe('/inboxes/' + quote(inbox, safe=''))
            value = metadata.get('data')
            if metadata.get('status') in (403, 404):
                return 'SCOPE_MISMATCH', '', False
            if (metadata.get('status') == 200 and metadata.get('successful') is not False and not metadata.get('error')
                    and isinstance(value, dict) and not self._provider_error(value)
                    and value.get('inbox_id') == inbox and value.get('status') != 'deactivated'):
                return 'ACTIVE', inbox, False
            return 'UNVERIFIED', '', False
        except (ConnectionError, OSError, KeyError, ValueError, TypeError, AttributeError):
            return 'UNVERIFIED', '', False

    def _store_binding(self, who, service, account, config, status, identity='', no_probe=False, legacy_active=False):
        ident = self._project_ident(who, account['id'])
        previous = self._replacement(who, service, config.get('replace'))
        activated = status == 'ACTIVE'
        config.pop('verification_error', None)
        if activated:
            config.pop('replace', None)
            config.pop('authorization_url', None)
        runtime_status = 'ACTIVE' if no_probe and legacy_active and not previous else status
        config['manual_verification'] = bool(no_probe and not activated)
        config = {**config, 'provider_validated': activated, 'provider_identity': identity}
        members = [who, *self._operator_members(who)] if runtime_status == 'ACTIVE' else [who]
        now = time.time()
        with self.identity.db() as db:
            linked = db.execute('SELECT fingerprint FROM hub_projects WHERE workspace=? AND status=?',
                                (who['workspace'], 'LINKED')).fetchone()
            if not linked or linked['fingerprint'] != config['project']:
                raise ConnectionError('The linked project changed. Start a new authorization.')
            for member in members:
                member_ident = self._project_ident(member, account['id'])
                # Preserve each member's own access settings across credential rotation.
                saved = db.execute('SELECT config,enabled FROM hub_connections WHERE id=?', (member_ident,)).fetchone()
                saved_config = json.loads(saved['config']) if saved else {}
                if saved and saved_config.get('project') != config['project']:
                    saved, saved_config = None, {}
                if saved and not saved['enabled']:
                    continue
                member_config = {**saved_config, **config}
                member_config.pop('verification_error', None)
                if saved_config.get('access'):
                    member_config['access'] = saved_config['access']
                if activated:
                    member_config.pop('replace', None)
                    member_config.pop('authorization_url', None)
                if activated and previous:
                    old_id = (self._project_ident(member, previous['account']) if previous['account']
                              else previous['id'])
                    old = db.execute('SELECT config FROM hub_connections WHERE id=?', (old_id,)).fetchone()
                    if old:
                        old_config = json.loads(old['config'])
                        for field in ('access', 'panel_permissions'):
                            if field in old_config:
                                member_config[field] = old_config[field]
                    db.execute("UPDATE hub_connections SET enabled=0,status='REMOVED',config=json_set(config,'$.dismissed',?) WHERE id=?",
                               (now, old_id))
                db.execute('INSERT OR REPLACE INTO hub_connections VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                           (member_ident, member['user'], who['workspace'], 'composio', service,
                            config.get('app_name') or service, account['id'], 1, json.dumps(member_config),
                            now if activated else None, runtime_status))
        return self.row(who, ident)

    def refresh(self, who):
        """Check only accounts already attached to this workspace member."""
        checked = []
        for row in self.rows(who):
            if row['kind'] != 'composio' or not row['enabled']:
                continue
            try:
                current = self.change(who, row['id'], 'status')
                checked.append({'id': row['id'], 'service': row['service'], 'status': current['connection_status']})
            except ConnectionError as exc:
                checked.append({'id': row['id'], 'service': row['service'], 'status': 'UNVERIFIED', 'error': str(exc)})
        return {'connections': checked, 'message': 'Account status updated.'}

    def project_status(self, who, ident, row, *, verify_identity=True):
        project, key, user_id = self._project(who)
        if row['config'].get('project') != project['fingerprint']:
            raise ConnectionError('The linked project changed. Start a new authorization.')
        try:
            account = self._project_account(who, key, user_id, row['account'])
        except ProviderRefused as exc:
            if exc.status != 404:
                raise
            account = {**(row['config'].get('provider_account') or {}), 'id': row['account'],
                       'toolkit': row['service'], 'user_id': user_id, 'status': 'EXPIRED', 'is_disabled': False}
        if account['toolkit'] != row['service']:
            raise ConnectionError('The Platform account toolkit changed. Start a new authorization.')
        previous = row['config'].get('provider_account')
        if previous and previous['user_id'] != account['user_id']:
            raise ConnectionError('The Platform account owner changed. Start a new authorization.')
        status = 'DISABLED' if account['is_disabled'] else account['status'] or 'UNVERIFIED'
        identity, no_probe = '', False
        if status in ('PENDING', 'INITIATED', 'INITIALIZING'):
            expiry = self._link_expiry(row['config'])
            if expiry and expiry <= time.time():
                status = 'EXPIRED'
        if status == 'ACTIVE' and not verify_identity and row['config'].get('provider_validated'):
            identity = row['identity']  # The authorized read itself will exercise the provider.
        elif status == 'ACTIVE':
            status, identity, no_probe = self._validate_provider(project, key, user_id, row['service'], account, row['config'].get('resource'))
            if no_probe and row['config'].get('provider_validated'):
                status, identity = 'ACTIVE', row['identity']
        config = {**row['config'], 'provider_account': account}
        return self._store_binding(who, row['service'], account, config, status, identity, no_probe,
                                   legacy_active=row['status'] == 'ACTIVE')

    def _project_execute(self, who, ident, row, operation, arguments, guard):
        read = READS.get(row['service'], {}).get(operation)
        if not read or arguments:
            raise ConnectionError('Only enabled named Composio reads are approved here.')
        _, key, user_id = self._project(who)
        self._before_request(who, ident, guard)
        result = self.composio('POST', '/tools/execute/' + read[0], key,
                               body={'connected_account_id': row['account'], 'user_id': user_id, 'arguments': read[1]})
        if result.get('successful') is not True or result.get('error'):
            raise ConnectionError('Provider refused the read. Check the provider permissions; no successful receipt recorded.')
        data = sanitize(result.get('data'), (key,))
        if not isinstance(data, dict) or self._provider_error(data):
            raise ConnectionError('Provider returned an invalid read response. No successful receipt was recorded.')
        if read[0] == 'GITHUB_GET_THE_AUTHENTICATED_USER':
            data = {k: data.get(k) for k in ('login', 'id', 'html_url', 'name', 'type')}
        receipt = {'id': 'cr_' + uuid.uuid4().hex, 'at': time.time(), 'connection': ident, 'operation': operation,
                   'provider_verified': True, 'source': 'project', 'result': data, 'token_usage': 'unavailable', 'model_calls': 0}
        self._agent_receipt(who, receipt)
        self._provider_read_confirmed(who, row, self._identity_from(data))
        return receipt

    def _provider_tool(self, who, row, slug, arguments, guard):
        self._before_request(who, row['id'], guard)
        if row['config'].get('source') == 'project':
            _, key, user_id = self._project(who)
            result = self.composio('POST', '/tools/execute/' + slug, key,
                                   body={'connected_account_id': row['account'], 'user_id': user_id,
                                         'version': TOOL_VERSIONS[row['service']], 'arguments': arguments})
            result = sanitize(result, (key,))
        else:
            raise ConnectionError(REMOVED)
        if result.get('successful') is not True or result.get('error') or 'data' not in result:
            raise ConnectionError('Provider refused ' + slug + '. No successful receipt was recorded; check account access and provider permissions.')
        data = result.get('data') or {}
        if isinstance(data, dict) and self._provider_error(data):
            raise ConnectionError('Provider returned an error payload. No successful receipt was recorded.')
        if slug == 'GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID' and isinstance(data, dict):
            data = gmail_message(data)
        return sanitize(data)

    def _named_payload(self, row, operation, arguments):
        _, schema, defaults, write = OPERATIONS[row['service']][operation]
        try:
            jsonschema.validate(arguments, schema)
        except jsonschema.ValidationError:
            raise ConnectionError('Provide the required arguments for ' + operation + '; account IDs, attendees, notifications and unreviewed fields are not accepted.') from None
        payload = {**defaults, **arguments}
        if row['service'] == 'googlecalendar' and operation not in ('event',):
            try:
                lower = dt.datetime.fromisoformat(arguments['start_datetime' if write else 'time_min'].replace('Z', '+00:00'))
                upper = dt.datetime.fromisoformat(arguments['end_datetime' if write else 'time_max'].replace('Z', '+00:00'))
                if lower.tzinfo is None or upper.tzinfo is None or upper <= lower or upper - lower > dt.timedelta(days=90):
                    raise ValueError()
                if 'timezone' in arguments:
                    zone = ZoneInfo(arguments['timezone'])
                    if write and any(value.utcoffset() != value.astimezone(zone).utcoffset() for value in (lower, upper)):
                        raise ValueError()
            except (ValueError, KeyError):
                raise ConnectionError('Exact start/end times with matching timezone offsets are required; end must follow start and the window must be at most 90 days.') from None
        return payload

    def _named_execute(self, who, row, operation, arguments, payload, guard, item):
        from aos_authority import action_authorization
        slug, _, _, write = OPERATIONS[row['service']][operation]
        decision = action_authorization(self.identity.path.parents[2], item, action=slug if write else 'CONNECTION_READ', target=row['id'] + ':' + operation)
        if not decision['authorized']:
            raise ConnectionError('Runtime policy denied operation: ' + decision['detail'])
        if not write:
            data = self._provider_tool(who, row, slug, payload, guard)
            return self._named_receipt(who, row, operation, data, decision)
        # Durable intent survives crashes and concurrent deliveries. Identical
        # writes share a key across Dashboard, Telegram, Jobs and agent calls.
        encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'))
        semantic = json.dumps(arguments, sort_keys=True, separators=(',', ':'))
        key = hashlib.sha256((who['workspace'] + ':' + who['user'] + ':' + row['id'] + ':' + operation + ':' + semantic).encode()).hexdigest()
        with self._lock('effect:' + key):
            with self.identity.db() as db:
                prior = db.execute('SELECT * FROM hub_effects WHERE key=?', (key,)).fetchone()
                if not prior:
                    # Preserve attempts recorded before the semantic key fix.
                    prior = next((r for r in db.execute('SELECT * FROM hub_effects WHERE user=? AND workspace=? AND connection=? AND operation=?',
                                 (who['user'], who['workspace'], row['id'], operation))
                                 if all(json.loads(r['arguments']).get(k) == v for k, v in arguments.items())), None)
            if prior:
                if prior['status'] == 'performed':
                    return {**json.loads(prior['receipt']), 'replayed': True}
                raise ConnectionError('A previous attempt may have reached the provider. It will not be repeated; inspect the saved receipt and Calendar before recovery.')
            if operation == 'update_event':
                existing = self._provider_tool(who, row, 'GOOGLECALENDAR_EVENTS_GET', {'calendar_id': 'primary', 'event_id': arguments['event_id']}, guard)
                events = calendar_events(existing)
                existing = events[0] if len(events) == 1 else {}
                if not existing or any(not a.get('self') for a in existing.get('attendees', [])) or existing.get('visibility') not in ('private', 'confidential'):
                    raise ConnectionError('Only private events without attendees can be updated through this operation.')
            with self.identity.db() as db:
                db.execute('INSERT INTO hub_effects VALUES(?,?,?,?,?,?,?,?)',
                           (key, who['user'], who['workspace'], row['id'], operation, encoded, 'intent', None))
            if operation == 'create_event':
                payload = {**payload, 'exclude_organizer': True, 'extended_properties': {'private': {'ttros_effect': key}}}
            data = self._provider_tool(who, row, slug, payload, guard)
            events = calendar_events(data)
            if len(events) != 1:
                raise ConnectionError('The provider did not return an event ID. The write is uncertain and will not be retried automatically.')
            event = events[0]
            readback = self._provider_tool(who, row, 'GOOGLECALENDAR_EVENTS_GET', {'calendar_id': 'primary', 'event_id': event['id']}, guard)
            verified = calendar_events(readback)
            if len(verified) != 1 or not self._event_matches(verified[0], arguments):
                raise ConnectionError('Calendar readback did not confirm the requested private event. The write is uncertain and will not be repeated.')
            event = verified[0]
            receipt = self._named_receipt(who, row, operation, {'event': event, 'readback': readback}, decision)
            with self.identity.db() as db:
                db.execute("UPDATE hub_effects SET status='performed',receipt=? WHERE key=?", (json.dumps(receipt), key))
            return receipt

    @staticmethod
    def _event_matches(event, arguments):
        try:
            return (event.get('status') != 'cancelled' and event.get('visibility') in ('private', 'confidential')
                    and event.get('summary') == arguments['summary']
                    and all(not a.get('email') or a.get('self') for a in event.get('attendees', []))
                    and all(dt.datetime.fromisoformat(event[field]['dateTime'].replace('Z', '+00:00')) ==
                            dt.datetime.fromisoformat(arguments[arg].replace('Z', '+00:00'))
                            for field, arg in [('start', 'start_datetime'), ('end', 'end_datetime')])
                    and ('location' not in arguments or event.get('location') == arguments['location']))
        except (ValueError, KeyError, TypeError):
            return False

    def recover_event(self, who, ident, operation, arguments, event_id):
        """Reconcile an uncertain saved intent by exact owned provider readback; never write/retry."""
        if operation not in ('create_event', 'update_event'):
            raise ConnectionError('Only a saved Calendar write can be reconciled here.')
        with self.identity.db() as db:
            matches = [r for r in db.execute('SELECT * FROM hub_effects WHERE user=? AND workspace=? AND connection=? AND operation=?',
                       (who['user'], who['workspace'], ident, operation)) if all(json.loads(r['arguments']).get(k) == v for k, v in arguments.items())]
        if len(matches) != 1:
            raise ConnectionError('No unique saved effect matches these exact arguments.')
        prior = matches[0]
        readback = self.execute(who, ident, 'event', {'event_id': event_id})
        events = calendar_events(readback['result'])
        if len(events) != 1 or events[0]['id'] != event_id or not self._event_matches(events[0], arguments):
            raise ConnectionError('The exact event readback did not confirm the saved private effect.')
        receipt = {**readback, 'id': 'cr_' + uuid.uuid4().hex, 'operation': operation, 'reconciled': True,
                   'result': {'event': events[0], 'readback_receipt': readback['id']}}
        with self._lock('effect:' + prior['key']):
            with self.identity.db() as db:
                db.execute("UPDATE hub_effects SET status='performed',receipt=? WHERE key=?", (json.dumps(receipt), prior['key']))
        self._agent_receipt(who, receipt)
        return receipt

    # -- Dynamic tools: discovered from Composio, governed by the runtime permission policy ---------
    def tool_definition(self, who, slug):
        """One tool's input schema and version, loaded only when it is about to be used (cached 1 h)."""
        if not TOOL_SLUG.fullmatch(str(slug or '')):
            raise ConnectionError('Use an exact Composio tool slug, for example GMAIL_FETCH_EMAILS.')
        project, key, _ = self._project(who)

        def load():
            raw = self.composio('GET', '/tools/' + slug, key)
            toolkit = (raw.get('toolkit') or {}).get('slug') or ''
            if raw.get('slug') != slug or not SLUG.fullmatch(toolkit):
                raise ConnectionError(f'Composio has no tool {slug}.')
            tags = [str(t) for t in raw.get('tags') or [] if isinstance(t, str)][:20]
            schema = raw.get('input_parameters') if isinstance(raw.get('input_parameters'), dict) else {'type': 'object'}
            return {'slug': slug, 'name': str(raw.get('name') or slug)[:120], 'toolkit': toolkit,
                    'description': str(raw.get('description') or '')[:600], 'version': raw.get('version') if isinstance(raw.get('version'), str) else None,
                    'mode': tool_mode(slug, tags), 'tags': tags, 'input': schema}
        return cached(('tool', self.composio, project['fingerprint'], slug), 3600, load)

    def discover_tools(self, who, service, query='', *, connection='', limit=8):
        """Tools of one app matching a task, with compact input schemas. Metadata only."""
        if not SLUG.fullmatch(str(service or '')):
            raise ConnectionError('Invalid app.')
        project, key, _ = self._project(who)
        query = ' '.join(str(query or '').split())[:200]
        params = {'toolkit_slug': service, 'limit': limit, **({'query': query} if query else {})}
        page = cached(('tools', self.composio, project['fingerprint'], json.dumps(params, sort_keys=True)), 600,
                      lambda: self.composio('GET', '/tools', key, query=params))
        tools = []
        for raw in (page.get('items') or [])[:limit]:
            slug = str(raw.get('slug') or '')
            if not TOOL_SLUG.fullmatch(slug):
                continue
            schema = raw.get('input_parameters') if isinstance(raw.get('input_parameters'), dict) else {}
            props = schema.get('properties') if isinstance(schema.get('properties'), dict) else {}
            tools.append({'tool': slug, 'mode': tool_mode(slug, raw.get('tags') or []),
                          'description': ' '.join(str(raw.get('description') or raw.get('name') or '').split())[:220],
                          'required': [r for r in schema.get('required') or [] if isinstance(r, str)][:12],
                          'arguments': {k: {'type': (v or {}).get('type'), 'description': str((v or {}).get('description') or '')[:120]}
                                        for k, v in list(props.items())[:14] if isinstance(v, dict)}})
        return {'id': 'cd_' + uuid.uuid4().hex, 'at': time.time(), 'connection': connection, 'operation': 'tools',
                'provider_verified': False, 'result': {'service': service, 'query': query, 'tools': tools,
                'usage': 'Run one with operation "tool" and arguments {"tool": SLUG, "arguments": {...}}.'},
                'token_usage': 'unavailable', 'model_calls': 0}

    def _generic_execute(self, who, row, arguments, guard, item, *, expected_mode=None, notification_kind=None,
                         target='', idempotency_key=''):
        from aos_authority import action_authorization
        slug, args = arguments.get('tool'), arguments.get('arguments', {})
        if not isinstance(args, dict) or set(arguments) - {'tool', 'arguments'}:
            raise ConnectionError('tool takes {"tool": SLUG, "arguments": {...}}; account selection is never accepted in arguments.')
        if len(json.dumps(args)) > 20000:
            raise ConnectionError('Tool arguments exceed 20 KB.')
        definition = self.tool_definition(who, slug)
        if definition['toolkit'] != row['service']:
            raise ConnectionError(f'{slug} belongs to {definition["toolkit"]}, not this {row["service"]} account.')
        mode = definition['mode']
        if expected_mode and expected_mode != mode:
            raise ConnectionError(f'{slug} is a {mode} tool; this capability allows only {expected_mode} tools.')
        try:
            validator = jsonschema.Draft7Validator(definition['input'])
            problems = sorted({'/'.join(str(p) for p in e.absolute_path) or str(e.validator_value if e.validator == 'required' else e.validator)
                               for e in validator.iter_errors(args)})[:6]
        except jsonschema.SchemaError:
            problems = []                          # an unusual provider schema is validated by Composio itself
        if problems:
            raise ConnectionError(f'Arguments for {slug} do not match its schema (check: {", ".join(problems)}).')
        recipients = recipients_of(args)
        if target and recipients and recipients != [str(target).strip().lower()]:
            raise ConnectionError('The tool’s recipients do not match the authorized target. Nothing was sent.')
        exact_target = target or (','.join(recipients) if recipients else row['id'] + ':' + slug)
        decision = action_authorization(self.identity.path.parents[2], item, action=slug if mode == 'write' else 'CONNECTION_READ',
                                        target=exact_target, notification_kind=notification_kind)
        if not decision['authorized']:
            raise ConnectionError(f'Runtime policy held {slug}: {decision["detail"] or decision["basis"]}. Nothing was performed.')
        if mode == 'read':
            data = self._run_tool(who, row, definition, args, guard)
            return self._tool_receipt(who, row, slug, data, decision, read=True)
        # Writes: a durable intent survives crashes and duplicate deliveries; the same
        # effect is never performed twice (replayed instead), an uncertain one is never retried.
        semantic = json.dumps(args, sort_keys=True, separators=(',', ':'))
        key = hashlib.sha256(':'.join([who['workspace'], who['user'], row['id'], slug, semantic,
                                       str(idempotency_key or (item or {}).get('id') or '')]).encode()).hexdigest()
        with self._lock('effect:' + key):
            with self.identity.db() as db:
                prior = db.execute('SELECT * FROM hub_effects WHERE key=?', (key,)).fetchone()
            if prior and prior['status'] == 'performed':
                return {**json.loads(prior['receipt']), 'replayed': True}
            if prior:
                raise ConnectionError(f'A previous {slug} attempt may have reached the provider. It will not be repeated automatically.')
            with self.identity.db() as db:
                db.execute('INSERT INTO hub_effects VALUES(?,?,?,?,?,?,?,?)',
                           (key, who['user'], who['workspace'], row['id'], slug, semantic, 'intent', None))
            try:
                data = self._run_tool(who, row, definition, args, guard)
            except ProviderUnreachable:
                raise                               # outcome unknown: the intent stays, no retry
            except ConnectionError:
                with self.identity.db() as db:      # definitive refusal: nothing happened, retry allowed
                    db.execute("DELETE FROM hub_effects WHERE key=? AND status='intent'", (key,))
                raise
            receipt = self._tool_receipt(who, row, slug, data, decision)
            with self.identity.db() as db:
                db.execute("UPDATE hub_effects SET status='performed',receipt=? WHERE key=?", (json.dumps(receipt), key))
            return receipt

    def _run_tool(self, who, row, definition, args, guard):
        self._before_request(who, row['id'], guard)
        _, key, user_id = self._project(who)
        body = {'user_id': user_id, 'arguments': args, **({'version': definition['version']} if definition['version'] else {})}
        if not row['config'].get('no_auth'):
            body['connected_account_id'] = row['account']
        try:
            result = self.composio('POST', '/tools/execute/' + definition['slug'], key, body=body)
        except ProviderRefused as exc:
            raise ToolFailed(f'{definition["slug"]} was refused: {exc.detail or exc}', str(exc.detail or exc)) from None
        result = sanitize(result, (key,), max_items=1000)
        if result.get('successful') is not True or result.get('error'):
            error = ' '.join(str(result.get('error') or 'unsuccessful').split())[:600]
            raise ToolFailed(f'{definition["slug"]} did not succeed: {error}', error)
        return result.get('data') if isinstance(result.get('data'), (dict, list)) else {'value': result.get('data')}

    def proxy_read(self, who, ident, endpoint):
        """One fixed, reviewed GET through Composio's proxy on this member's Platform account."""
        if not isinstance(endpoint, str) or not endpoint.startswith('/') or '..' in endpoint or '://' in endpoint:
            raise ConnectionError('Invalid provider endpoint.')
        row = self.change(who, ident, 'status')
        if row['status'] != 'ACTIVE' or not row['enabled'] or row['access']['mode'] == 'Disabled':
            raise ConnectionError('Provider account is not ACTIVE; reconnect it in Settings → Connections.')
        from aos_authority import action_authorization
        decision = action_authorization(self.identity.path.parents[2], None, action='CONNECTION_READ', target=ident + ':proxy')
        if not decision['authorized']:
            raise ConnectionError('Runtime policy denied the read: ' + decision['detail'])
        self._before_request(who, ident, None)
        _, key, user_id = self._project(who)
        result = sanitize(self.composio('POST', '/tools/execute/proxy', key, body={
            'connected_account_id': row['account'], 'user_id': user_id, 'endpoint': endpoint, 'method': 'GET'}), (key,))
        if (not 200 <= int(result.get('status') or 0) < 300 or result.get('error')
                or (isinstance(result.get('data'), dict) and self._provider_error(result['data']))):
            raise ConnectionError('Provider refused the reviewed read.')
        receipt = {'id': 'cr_' + uuid.uuid4().hex, 'at': time.time(), 'connection': ident, 'operation': 'proxy',
                   'provider_verified': True, 'source': 'project', 'result': result.get('data') or {},
                   'token_usage': 'unavailable', 'model_calls': 0}
        self._agent_receipt(who, receipt)
        self._provider_read_confirmed(who, row)
        return receipt

    def _tool_receipt(self, who, row, slug, data, decision, *, read=False):
        receipt = {'id': 'cr_' + uuid.uuid4().hex, 'at': time.time(), 'connection': row['id'], 'operation': 'tool', 'tool': slug,
                   'provider_verified': True, 'source': 'project', 'provider_account': row['account'] or 'no_auth',
                   'authorization': decision, 'result': data, 'token_usage': 'unavailable', 'model_calls': 0}
        stored = receipt if len(json.dumps(data, default=str)) <= 65536 else {**receipt, 'result': {'withheld_from_receipt': 'result over 64 KB'}}
        self._agent_receipt(who, stored)
        with self.identity.db() as db:
            db.execute("UPDATE hub_connections SET verified=?,config=json_set(config,'$.provider_validated',1) "
                       "WHERE enabled=1 AND id=? AND user=? AND workspace=?",
                       (receipt['at'], row['id'], who['user'], who['workspace']))
        return receipt

    def _named_receipt(self, who, row, operation, data, decision):
        receipt = {'id': 'cr_' + uuid.uuid4().hex, 'at': time.time(), 'connection': row['id'], 'operation': operation,
                   'provider_verified': True, 'provider_account': row['config']['provider_account']['id'],
                   'authorization': decision, 'result': data, 'token_usage': 'unavailable', 'model_calls': 0}
        self._agent_receipt(who, receipt)
        if not is_write(row['service'], operation):
            self._provider_read_confirmed(who, row)
        return receipt

    def _provider_read_confirmed(self, who, row, identity=''):
        if not row['config'].get('manual_verification'):
            return
        with self._lifecycle_lock(who, row['service']):
            try:
                current = self.row(who, row['id'])
                config = current['config']
                if current['enabled'] and config.get('manual_verification'):
                    self._store_binding(who, row['service'], config['provider_account'], config, 'ACTIVE', identity)
            except ConnectionError:
                pass  # The read completed before another request removed or replaced the binding.

    def save(self, who, ident, kind, service, name, account, config, status, verified, *, enabled=1):
        with self.identity.db() as db:
            db.execute('INSERT OR REPLACE INTO hub_connections VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (ident, who['user'], who['workspace'], kind, service, name, account, enabled, json.dumps(config), verified, status))
        self.identity.receipt(who, 'hub.save', ident)

    def custom(self, who, body, ident=None):
        previous_access = None
        enabled = 1
        if ident:
            previous_row = self.row(who, ident)
            enabled = previous_row['enabled']
            previous_access = previous_row['config'].get('access')
            if previous_row['kind'] != 'custom':
                raise ConnectionError('Only custom connections can be edited here.')
        service = body.get('service', '')
        if not SLUG.fullmatch(service) or not 1 <= len(body.get('name', '')) <= 100:
            raise ConnectionError('Provide a connection name and a service slug.')
        # Check the real catalogue first. A custom adapter is an explicit choice only.
        try:
            reuse = [r for r in self.catalogue(service, who)['items'] if r['slug'] == service]
        except ConnectionError:
            reuse = []                             # catalogue outage never blocks a custom API
        if reuse and not body.get('custom_required'):
            raise ConnectionError('Composio already supports this service. Use its toolkit or explicitly choose a restricted custom read for an unsupported endpoint.')
        origin = host_url(body.get('host', ''))
        public_addresses(urlsplit(origin).hostname)
        ref = body.get('secret_ref', '')
        if ref and not re.fullmatch(r'TTROS_CONNECTOR_SECRET_[A-Z0-9_]{1,64}', ref):
            raise ConnectionError('Select an existing server secret reference named TTROS_CONNECTOR_SECRET_*. Never enter an API key here.')
        if ref:
            self.secret_binding(who, ref, origin)
            credential(ref)  # fail before saving an unusable or plaintext secret reference
        auth = body.get('auth', 'none')
        if auth not in ('none', 'bearer', 'x-api-key') or (auth != 'none' and not ref) or (auth == 'none' and ref):
            raise ConnectionError('Choose no authentication, Bearer API key, or X-API-Key and its provisioned secret reference.')
        operations = body.get('operations', [])
        spec = body.get('openapi')
        if spec:
            if not isinstance(spec, dict) or len(json.dumps(spec)) > 100000 or not str(spec.get('openapi', '')).startswith('3.'):
                raise ConnectionError('Import an OpenAPI 3 JSON document under 100 KB. Remote references are not fetched.')
            if '$ref' in json.dumps(spec):
                raise ConnectionError('Resolve OpenAPI references before import; references are not fetched.')
            servers = spec.get('servers', [])
            if servers and any(host_url(s.get('url', '')) != origin for s in servers):
                raise ConnectionError('OpenAPI servers must match the API host.')
            operations = [{'name': operation.get('operationId', ''), 'path': path, 'method': 'GET', 'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False}, 'response_schema': {'type': 'object'}}
                          for path, methods in spec.get('paths', {}).items() for method, operation in methods.items() if method.lower() == 'get' and not operation.get('parameters')]
        if not isinstance(operations, list) or not 1 <= len(operations) <= 20:
            raise ConnectionError('Define 1–20 named fixed GET operations. OpenAPI operations with parameters require manual schema review.')
        names = set()
        for op in operations:
            if not isinstance(op, dict) or not SLUG.fullmatch(op.get('name', '')) or op.get('name') in names or op.get('method', 'GET') != 'GET':
                raise ConnectionError('Operations must have unique lowercase names and method GET.')
            names.add(op['name']); endpoint(op.get('path'))
            op['method'] = 'GET'
            op.setdefault('input_schema', {'type': 'object', 'properties': {}, 'additionalProperties': False})
            op.setdefault('response_schema', {'type': 'object'})
            for schema in (op['input_schema'], op['response_schema']):
                if len(json.dumps(schema)) > 10000 or '$ref' in json.dumps(schema) or any(key in json.dumps(schema) for key in ('pattern', 'format')):
                    raise ConnectionError('Use bounded JSON schemas without references, regex patterns or formats.')
                jsonschema.Draft202012Validator.check_schema(schema)
            if op['input_schema'].get('type') != 'object' or op['input_schema'].get('additionalProperties') is not False:
                raise ConnectionError('Input schema must be an object with additionalProperties=false.')
            for key, schema in op['input_schema'].get('properties', {}).items():
                if not SLUG.fullmatch(key) or schema.get('type') not in ('string', 'integer', 'number', 'boolean') or SECRET_KEY.search(key):
                    raise ConnectionError('Only named scalar, non-secret query parameters are permitted.')
        ident = ident or 'cx_' + uuid.uuid4().hex
        config = {'host': origin, 'auth': auth, 'secret_ref': ref, 'operations': operations}
        if previous_access is not None:
            config['access'] = previous_access
        self.save(who, ident, 'custom', service, body['name'], '', config, 'UNVERIFIED', None, enabled=enabled)
        return self.row(who, ident)

    def secret_binding(self, who, ref, origin):
        try:
            bindings = json.loads(os.environ.get('TTROS_CONNECTOR_SECRET_BINDINGS', '{}'))
            permitted_host = bindings.get(who['workspace'] + ':' + who['user'], {}).get(ref)
        except (ValueError, AttributeError):
            permitted_host = None
        if permitted_host != origin:
            raise ConnectionError('Secret reference is not assigned to this workspace member and exact API host. Ask the runtime operator to provision the scoped binding in protected storage.')

    def dashboard_link(self, slug=''):
        """Where the member finishes a connection securely (Settings → Connections, app preselected)."""
        origin = str(getattr(self.identity, 'origin', '') or os.environ.get('TTROS_DASHBOARD_ORIGIN', '')).rstrip('/')
        return origin + '/settings/connections' + ('?app=' + slug if slug and SLUG.fullmatch(slug) else '')

    def remove(self, who, ident):
        return self.change(who, ident, 'remove')

    def change(self, who, ident, action, *, verify_identity=True):
        row = self.row(who, ident)
        with self._lifecycle_lock(who, row['service']):
            row = self.row(who, ident)
            if action in ('remove', 'disconnect', 'disable'):
                status = 'REMOVED' if action == 'remove' else 'DISCONNECTED'
                self._detach(who, row, status)
                self.identity.receipt(who, 'hub.' + action, ident)
                return {'status': status, 'message': 'Account removed from TTROS.' if action == 'remove'
                        else 'Account disconnected. Reconnect to restore access.'}
            if action == 'enable' and row['kind'] == 'custom':
                with self.identity.db() as db:
                    db.execute('UPDATE hub_connections SET enabled=1,status=? WHERE id=?', ('UNVERIFIED', ident))
                return self.row(who, ident)
            if action != 'status' or row['kind'] != 'composio' or not row['enabled']:
                raise ConnectionError('Unsupported action or disabled connection.')
            if row['config'].get('no_auth'):
                return row
            try:
                return self.project_status(who, ident, row, verify_identity=verify_identity)
            except ConnectionError as exc:
                with self.identity.db() as db:
                    db.execute("UPDATE hub_connections SET config=json_set(config,'$.verification_error',?) "
                               "WHERE id=? AND user=? AND workspace=? AND enabled=1",
                               (str(exc), ident, who['user'], who['workspace']))
                raise

    def access(self, who, ident, mode, agents):
        row = self.row(who, ident)
        if mode not in ('Automatic', 'Restricted', 'Disabled') or not isinstance(agents, dict):
            raise ConnectionError('Choose Automatic, Restricted or Disabled access.')
        if any(not re.fullmatch(r'[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*@[0-9]+\.[0-9]+\.[0-9]+', ref)
               or not isinstance(ops, list) or not ops or any(op not in row['operations'] for op in ops)
               for ref, ops in agents.items()):
            raise ConnectionError('Restricted access needs exact agent versions and existing named reads.')
        # Changing access never restores a disconnected binding or provider consent.
        if mode != 'Disabled' and not row['enabled']:
            raise ConnectionError('Verify the connection binding before restoring access.')
        row['config']['access'] = {'mode': mode, 'agents': agents}
        with self.identity.db() as db:
            db.execute("UPDATE hub_connections SET config=json_set(config,'$.access',json(?)) WHERE id=? AND user=? AND workspace=?",
                       (json.dumps(row['config']['access']), ident, who['user'], who['workspace']))
        self.identity.receipt(who, 'hub.access.' + mode.lower(), ident)
        return self.row(who, ident)

    def resolve(self, who, manifest):
        """Advisory discovery only. No authority or credentials enter a TAP package."""
        ref = manifest['agent']['name'] + '@' + manifest['agent']['version']
        rows = self.rows(who)
        resolved, missing, reasons = {}, [], {}
        for requirement in manifest['capabilities'].get('grants', []):
            if not requirement.startswith(('connector.read:', 'connector.write:')):
                continue
            parts = requirement.split(':')
            write = parts[0] == 'connector.write'
            service, operation = parts[1], parts[2] if len(parts) == 3 else None
            matches = []
            for row in rows:
                if row['service'] != service or not row['enabled'] or row['access']['mode'] == 'Disabled':
                    continue
                if row['kind'] == 'composio' and row['status'] != 'ACTIVE':
                    continue
                ops = [operation] if operation in row['operations'] else ([] if operation else row['operations'])
                ops = [op for op in ops if op == 'tool' or (op == 'tools' and not write)
                       or (op not in GENERIC_OPS and is_write(service, op) == write)]
                if row['access']['mode'] == 'Restricted':
                    ops = [op for op in ops if op in row['access'].get('agents', {}).get(ref, [])]
                if ops:
                    matches.append((row, {'connection': row['id'], 'operations': ops, 'account': row['account']}))
            # Like David: once the linked Platform project serves an app, older For You
            # grants for it are not offered. Never an arbitrary choice between them.
            if any(r['kind'] == 'composio' and r['config'].get('source') == 'project' for r, _ in matches):
                matches = [(r, m) for r, m in matches if r['kind'] != 'composio' or r['config'].get('source') == 'project']
            matches = [m for _, m in matches]
            resolved[requirement] = matches
            if not matches:
                missing.append(requirement)
                relevant = [r for r in rows if r['service'] == service]
                if not relevant:
                    reason = 'No account for this app is connected to this workspace member.'
                elif all(not r['enabled'] or r['access']['mode'] == 'Disabled' for r in relevant):
                    reason = 'Account access is disabled or disconnected.'
                elif all(r['status'] != 'ACTIVE' for r in relevant):
                    reason = 'Provider account is not ACTIVE; check consent or reconnect.'
                elif operation and all(operation not in r['operations'] for r in relevant):
                    reason = 'The named operation is not supported or enabled on this account.'
                else:
                    reason = 'This exact agent version is not allowed by the account restriction.'
                reasons[requirement] = reason
        return {'resolved': resolved, 'missing_required': missing, 'authority': 'rules/runtime_permission_policy.md',
                'missing_reasons': reasons, 'advisory': True}

    def execute_agent(self, who, package, service, operation, arguments, *, permissions, item=None, ident=None, mode='read'):
        """Trusted runtime context supplies principal/task scope, never handler input."""
        from aos_authority import action_authorization
        ident = ident or ''
        try:
            generic = operation in GENERIC_OPS
            if mode not in ('read', 'write') or (operation == 'tools' and mode != 'read') or (
                    not generic and is_write(service, operation) != (mode == 'write')):
                raise ConnectionError('Use the matching named read/write capability for this operation.')
            required = {'connector.' + mode + ':' + service, 'connector.' + mode + ':' + service + ':' + operation}
            if not required.intersection(package.grants):
                raise ConnectionError('Agent manifest does not declare this named connector read.')
            candidates = [r for r in self.rows(who) if r['service'] == service and operation in r['operations']
                          and (r['id'], operation) in permissions and (not ident or ident == r['id'])]
            candidates = [r for r in candidates if r['enabled'] and r['access']['mode'] != 'Disabled'
                          and (r['access']['mode'] != 'Restricted' or operation in r['access'].get('agents', {}).get(package.ref, []))]
            if len(candidates) != 1:
                raise ConnectionError('No unique allowed connection for this agent, member and task.')
            row = candidates[0]; ident = row['id']
            if who.get('method') != 'local_preview':
                with self.identity.db() as db:
                    if not db.execute('SELECT 1 FROM memberships WHERE user=? AND workspace=?', (who['user'], who['workspace'])).fetchone():
                        raise ConnectionError('Connection owner is no longer a workspace member.')
            # A dynamic tool is authorized by its exact slug inside execution.
            slug = OPERATIONS[service][operation][0] if mode == 'write' and not generic else 'CONNECTION_READ'
            decision = action_authorization(self.identity.path.parents[2], item, action=slug, target=ident + ':' + operation)
            if not decision['authorized']:
                raise ConnectionError('Runtime policy denied connection read: ' + decision['basis'])
            def guard():
                current = self.row(who, ident)
                if (not current['enabled'] or current['access']['mode'] == 'Disabled'
                        or (current['access']['mode'] == 'Restricted'
                            and operation not in current['access'].get('agents', {}).get(package.ref, []))):
                    raise ConnectionError('Connection access was restricted or revoked before provider execution.')
                if who.get('method') != 'local_preview':
                    with self.identity.db() as db:
                        if not db.execute('SELECT 1 FROM memberships WHERE user=? AND workspace=?',
                                          (who['user'], who['workspace'])).fetchone():
                            raise ConnectionError('Workspace membership was revoked before provider execution.')
            result = self.execute(who, ident, operation, arguments, guard=guard, item=item,
                                  expected_mode=mode if operation == 'tool' else None)
            audit = {**result, 'agent': package.ref, 'package_sha256': package.content_sha256,
                     'job': (item or {}).get('id'), 'authorization': decision, 'status': 'ok'}
        except (ConnectionError, jsonschema.ValidationError, jsonschema.SchemaError) as exc:
            error = exc if isinstance(exc, ConnectionError) else ConnectionError('Named read arguments or provider schema failed validation.')
            audit = {'id': 'cr_' + uuid.uuid4().hex, 'at': time.time(), 'connection': ident, 'operation': operation,
                     'agent': package.ref, 'package_sha256': package.content_sha256, 'status': 'denied',
                     'job': (item or {}).get('id'), 'detail': str(error), 'token_usage': 'unavailable'}
            self._agent_receipt(who, audit)
            raise error from None
        self._agent_receipt(who, audit)
        return audit

    def _agent_receipt(self, who, receipt):
        with self.identity.db() as db:
            db.execute('INSERT OR REPLACE INTO hub_receipts VALUES(?,?,?,?,?,?,?)',
                       (receipt['id'], who['user'], who['workspace'], receipt['at'], receipt['connection'], receipt['operation'], json.dumps(receipt)))

    def execute(self, who, ident, operation, arguments=None, *, guard=None, item=None, expected_mode=None,
                notification_kind=None, target='', idempotency_key=''):
        try:
            return self._execute(who, ident, operation, arguments, guard=guard, item=item, expected_mode=expected_mode,
                                 notification_kind=notification_kind, target=target, idempotency_key=idempotency_key)
        except ConnectionError as exc:
            self._agent_receipt(who, {'id': 'cr_' + uuid.uuid4().hex, 'at': time.time(), 'connection': ident,
                'operation': operation, 'status': 'denied', 'detail': str(exc), 'token_usage': 'unavailable', 'model_calls': 0})
            raise

    def _execute(self, who, ident, operation, arguments=None, *, guard=None, item=None, expected_mode=None,
                 notification_kind=None, target='', idempotency_key=''):
        row = self.row(who, ident)
        from aos_authority import action_authorization
        slug = OPERATIONS[row['service']][operation][0] if is_write(row['service'], operation) else 'CONNECTION_READ'
        decision = action_authorization(self.identity.path.parents[2], item, action=slug, target=ident + ':' + operation)
        if not decision['authorized']:
            raise ConnectionError('Runtime policy denied operation: ' + decision['detail'])
        if not row['enabled'] or row['access']['mode'] == 'Disabled':
            raise ConnectionError('Connection is disabled. No provider request was made.')
        if row['access']['mode'] == 'Restricted' and guard is None:
            raise ConnectionError('This account is restricted to named agent versions; direct reads are unavailable.')
        arguments = arguments or {}
        if not isinstance(arguments, dict):
            raise ConnectionError('Operation arguments must be a JSON object.')
        if row['kind'] == 'composio':
            if operation == 'tools' and row['config'].get('source') == 'project':
                if not isinstance(arguments.get('query', ''), str):
                    raise ConnectionError('tools takes {"query": "what you want to do"}.')
                return self.discover_tools(who, row['service'], arguments.get('query', ''), connection=ident)
            named = operation in OPERATIONS.get(row['service'], {})
            named_read = operation in READS.get(row['service'], {})
            generic = operation == 'tool' and row['config'].get('source') == 'project'
            if not (named or generic or (named_read and not arguments)):
                raise ConnectionError('Only enabled named Composio reads or approved operations are available here.')
            payload = self._named_payload(row, operation, arguments) if named else None
            row = self.change(who, ident, 'status',
                              verify_identity=not named_read and (not named or is_write(row['service'], operation)))
            manual_check = row['status'] == 'UNVERIFIED' and row['config'].get('manual_verification')
            if row['status'] != 'ACTIVE' and not manual_check:
                raise ConnectionError(f'Account access is unavailable ({row["connection_status"]}). Check Connections.')
            if manual_check and (is_write(row['service'], operation) or expected_mode == 'write'):
                raise ConnectionError('Verify this account with a read before using it for writes.')
            if operation == 'tool' and row['config'].get('source') == 'project':
                return self._generic_execute(who, row, arguments, guard, item, expected_mode='read' if manual_check else expected_mode,
                                             notification_kind=notification_kind, target=target, idempotency_key=idempotency_key)
            if named:
                return self._named_execute(who, row, operation, arguments, payload, guard, item)
            if row['config'].get('source') == 'project':
                return self._project_execute(who, ident, row, operation, arguments, guard)
            raise ConnectionError(REMOVED)
        else:
            cfg = row['config']
            op = next((op for op in cfg['operations'] if op['name'] == operation), None)
            if not op:
                raise ConnectionError('Operation is not approved for this connection.')
            jsonschema.validate(arguments, op['input_schema'])
            if len(json.dumps(arguments)) > 4000:
                raise ConnectionError('Operation arguments exceed 4 KB.')
            headers, secret = {}, ''
            if cfg['auth'] != 'none':
                self.secret_binding(who, cfg['secret_ref'], cfg['host'])
                secret = credential(cfg['secret_ref'])
                if not secret or '\r' in secret or '\n' in secret:
                    raise ConnectionError('Provision the configured secret in protected runtime storage.')
                headers = {'Authorization': 'Bearer ' + secret} if cfg['auth'] == 'bearer' else {'X-API-Key': secret}
            path = endpoint(op['path']) + ('?' + urlencode(arguments) if arguments else '')
            self._before_request(who, ident, guard)
            data = self.fetch(host_url(cfg['host']), path, headers)
            try:
                jsonschema.validate(data, op['response_schema'])
            except jsonschema.ValidationError:
                raise ConnectionError('Provider response did not match the approved response schema; response withheld.') from None
            data = sanitize(data, (secret,))
        receipt = {'id': 'cr_' + uuid.uuid4().hex, 'at': time.time(), 'connection': ident, 'operation': operation,
                   'provider_verified': True, 'result': data, 'token_usage': 'unavailable', 'model_calls': 0}
        with self.identity.db() as db:
            db.execute('INSERT INTO hub_receipts VALUES(?,?,?,?,?,?,?)', (receipt['id'], who['user'], who['workspace'], receipt['at'], ident, operation, json.dumps(receipt)))
            db.execute('UPDATE hub_connections SET verified=?,status=? WHERE id=?', (receipt['at'], 'ACTIVE', ident))
            if row['service'] == 'github' and row['kind'] == 'composio' and data.get('login'):
                row['config']['provider_identity'] = data['login']
                db.execute("UPDATE hub_connections SET config=json_set(config,'$.provider_identity',?) WHERE id=?", (data['login'], ident))
        return receipt

    def _before_request(self, who, ident, guard):
        # Metadata/schema calls can take time. Re-read access immediately before I/O.
        current = self.row(who, ident)
        if not current['enabled'] or current['access']['mode'] == 'Disabled':
            raise ConnectionError('Connection access was disabled before provider execution.')
        if current['kind'] == 'composio' and not (current['status'] == 'ACTIVE' or (
                current['status'] == 'UNVERIFIED' and current['config'].get('manual_verification'))):
            raise ConnectionError('Connection verification changed before provider execution.')
        if current['access']['mode'] == 'Restricted' and guard is None:
            raise ConnectionError('Connection became restricted before provider execution.')
        if guard:
            guard()

    def receipts(self, who):
        with self.identity.db() as db:
            rows = db.execute('SELECT result FROM hub_receipts WHERE user=? AND workspace=? ORDER BY at DESC LIMIT 20', (who['user'], who['workspace'])).fetchall()
        return [json.loads(row['result']) for row in rows]

    def submit_job(self, who, ident, operation, arguments):
        import aos_jobs
        row = self.row(who, ident)
        if not row['enabled'] or operation not in row['operations'] or is_write(row['service'], operation) or row['access']['mode'] in ('Disabled', 'Restricted'):
            raise ConnectionError('Enable this named read before creating a job.')
        # Persist scope only from the authenticated ingress, outside model input.
        grant = secrets.token_hex(24)
        root = self.identity.path.parents[2]
        job, _ = aos_jobs.create(root, conversation_id='connections-' + hashlib.sha256((who['workspace'] + ':' + who['user']).encode()).hexdigest()[:20],
                                topic='connection-read', request=f'Read {row["name"]}: {operation}', requested_by=who['user'],
                                executor={'kind': 'connection_read', 'action': 'read', 'grant': grant},
                                restrictions=['no_brain_writes', 'no_connector_writes'])
        with self.identity.db() as db:
            db.execute('INSERT INTO hub_job_grants VALUES(?,?,?,?,?,?,?)', (grant, job['id'], json.dumps(who), ident, operation, json.dumps(arguments), time.time() + 8 * 3600))
        self.identity.receipt(who, 'hub.job.submit', job['id'])
        return job

    def run_job(self, job):
        spec = job['executor']
        with self.identity.db() as db:
            grant = db.execute('SELECT * FROM hub_job_grants WHERE id=? AND job=? AND expires>?', (spec.get('grant'), job['id'], time.time())).fetchone()
        if not grant:
            raise ConnectionError('This job has no valid authenticated Connections scope grant.')
        who = json.loads(grant['principal'])
        if who.get('method') != 'local_preview':
            with self.identity.db() as db:
                membership = db.execute('SELECT 1 FROM memberships WHERE user=? AND workspace=?', (who['user'], who['workspace'])).fetchone()
            if not membership:
                raise ConnectionError('Job connection owner is no longer a workspace member.')
        if grant['operation'] == '__agent__':
            from aos_agents.package import load_package
            from aos_agents.runtime import RunEnv, run_agent
            payload = json.loads(grant['arguments'])
            root = self.identity.path.parents[2]
            package = load_package(payload['agent'], root)
            self._agent_release(who, package)
            if package.content_sha256 != payload['package_sha256']:
                raise ConnectionError('Agent package changed after authenticated task admission.')
            result = run_agent(package.ref, payload['input'], RunEnv(root=root, job=job['id'], item=job,
                connection_context=(self, who), connection_permissions=frozenset(tuple(p) for p in payload['permissions'])))
            result['steps'] = [{'id': 'connection-agent', 'agent': package.ref, 'status': result['status'],
                                'failure_class': result.get('failure_class'), 'execution_identity': result.get('execution_identity')}]
            return result
        receipt = self.execute(who, grant['connection'], grant['operation'], json.loads(grant['arguments']), item=job,
                               expected_mode='read' if grant['operation'] == 'tool' else None)
        return {'status': 'ok', 'agent': 'connections.read', 'output': {'summary': f'Provider read verified; receipt {receipt["id"]}', 'receipt': receipt},
                'artifacts': [], 'steps': [{'id': 'provider-read', 'status': 'ok', 'agent': 'connections.read'}],
                'usage': {'model_calls': 0, 'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0, 'exact': True}}

    def submit_agent_job(self, who, agent_ref, input, permissions):
        """Admit exact task reads at authenticated ingress, outside model/job input."""
        import aos_jobs
        from aos_agents.package import load_package
        package = load_package(agent_ref, self.identity.path.parents[2])
        self._agent_release(who, package)
        for ident, operation in permissions:
            row = self.row(who, ident)
            if not row['enabled'] or operation not in row['operations'] or row['access']['mode'] == 'Disabled':
                raise ConnectionError('Task read is unavailable in this workspace.')
            if row['access']['mode'] == 'Restricted' and operation not in row['access'].get('agents', {}).get(package.ref, []):
                raise ConnectionError('This exact agent version is not allowed to use the named account read.')
            modes = ('read', 'write') if operation == 'tool' else ('write',) if is_write(row['service'], operation) else ('read',)
            if not {'connector.' + m + ':' + row['service'] + suffix for m in modes for suffix in ('', ':' + operation)}.intersection(package.grants):
                raise ConnectionError('Task read exceeds the agent manifest.')
        grant = secrets.token_hex(24)
        job, _ = aos_jobs.create(self.identity.path.parents[2], conversation_id='agent-connections-' + who['workspace'][:60],
            topic='connection-agent', request='Run ' + package.ref + ' with scoped connection reads', requested_by=who['user'],
            executor={'kind': 'connection_read', 'action': 'read', 'grant': grant},
            restrictions=['no_brain_writes'] + ([] if any(is_write(self.row(who, ident)['service'], operation) or (
                operation == 'tool' and 'connector.write:' + self.row(who, ident)['service'] in package.grants)
                for ident, operation in permissions) else ['no_connector_writes']))
        payload = {'agent': package.ref, 'package_sha256': package.content_sha256, 'input': input, 'permissions': list(permissions)}
        with self.identity.db() as db:
            db.execute('INSERT INTO hub_job_grants VALUES(?,?,?,?,?,?,?)',
                (grant, job['id'], json.dumps(who), '', '__agent__', json.dumps(payload), time.time() + 8 * 3600))
        self.identity.receipt(who, 'hub.agent.submit', job['id'])
        return job

    def _agent_release(self, who, package):
        # Candidate evaluation stays in the explicit isolated preview. Production
        # keeps the existing Jobs release gate, including declared composition.
        if self.identity.local and who.get('method') == 'local_preview':
            return
        import aos_jobs
        refs = [package.ref] + list((package.manifest.get('compose') or {}).get('calls') or [])
        problem = aos_jobs.refs_release_problem(self.identity.path.parents[2], refs)
        if problem:
            raise ConnectionError(problem)


def run_connection_job(root, job):
    # Use the same Identity store as Dashboard; no new queue or account registry.
    import sys
    from pathlib import Path
    backend = str(Path(__file__).resolve().parents[1] / 'dashboard/backend')
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from dashboard_identity import Identity
    hub = Connections(Identity(root, local=os.environ.get('TTROS_CONNECTIONS_LOCAL_OPERATOR') == '1'))
    return hub.run_job(job)


# -- Services (timers, Jobs effects, notifications) use the same Hub, project and policy --------
class ServiceIdentity:
    """The Dashboard identity store opened by a background service: no sessions, no web login.

    Same database, receipts and operator bindings as the Dashboard; it can only use
    connections the operator already made there. It never starts a new authorization.
    """
    local = False

    def __init__(self, root):
        self.path = Path(root) / 'queue/dashboard_identity/identity.sqlite3'
        if not self.path.is_file():
            raise ConnectionError('TTROS Connections has not been set up on this installation. Connect apps in Settings → Connections.')

    @contextmanager
    def db(self):
        import sqlite3
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def receipt(self, who, action, detail=''):
        with self.db() as db:
            db.execute('INSERT INTO audit(at,user,workspace,action,detail) VALUES(?,?,?,?,?)',
                       (time.time(), who['user'], who['workspace'], action, detail))

    def connection_return_url(self):
        raise ConnectionError('New connections start in Settings → Connections, not from a background service.')


def operator_binding(hub, service):
    """(operator principal, connection id) for the operator's ACTIVE Platform account of an app."""
    with hub.identity.db() as db:
        members = [r['user'] for r in db.execute('SELECT user FROM memberships WHERE workspace=? ORDER BY user', (OPERATOR_WORKSPACE,))]
    for user in members:
        who = {'user': user, 'workspace': OPERATOR_WORKSPACE, 'email': '', 'method': 'operator_service'}
        rows = [r for r in hub.rows(who) if r['service'] == service and r['enabled'] and (
                    r['status'] == 'ACTIVE' or (r['status'] == 'UNVERIFIED' and not r['config'].get('replace')))
                and r['config'].get('source') == 'project' and r['access']['mode'] != 'Disabled']
        active = [r for r in rows if r['status'] == 'ACTIVE']
        rows = active or rows
        if len(rows) == 1:
            return who, rows[0]['id']
        if len(rows) > 1:
            raise ConnectionError(f'Several {service} accounts are connected; keep one in Settings → Connections.')
    raise ConnectionError(f'{service} is not connected to TTROS. Connect it once in Settings → Connections; '
                          'scheduled services and Jobs use it automatically.')


def perform_operator(root, tool, arguments, *, item=None, target='', notification_kind=None, idempotency_key='',
                     expected_mode=None, hub=None):
    """Run one Composio Platform tool for the TTROS operator from a service or Job.

    The tool's app is taken from Composio's own tool metadata, the account from the
    operator's Platform connection, and authority from rules/runtime_permission_policy.md.
    """
    hub = hub or Connections(ServiceIdentity(root))
    if not TOOL_SLUG.fullmatch(str(tool or '')):
        raise ConnectionError('Invalid tool slug.')
    with hub.identity.db() as db:
        member = db.execute('SELECT user FROM memberships WHERE workspace=? ORDER BY user LIMIT 1', (OPERATOR_WORKSPACE,)).fetchone()
    if not member:
        raise ConnectionError('No TTROS operator is set up. Sign in to the Dashboard once and connect apps in Settings → Connections.')
    probe = {'user': member['user'], 'workspace': OPERATOR_WORKSPACE, 'email': '', 'method': 'operator_service'}
    service = hub.tool_definition(probe, tool)['toolkit']
    who, ident = operator_binding(hub, service)
    return hub.execute(who, ident, 'tool', {'tool': tool, 'arguments': arguments or {}}, item=item, target=target,
                       notification_kind=notification_kind, idempotency_key=idempotency_key, expected_mode=expected_mode)
