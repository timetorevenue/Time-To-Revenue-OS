#!/usr/bin/env python3
"""Bounded, read-only Drive Changes capture. Never semantic ICM or Git push."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import time
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import drive_memory_intake as drive, source_intake as source, memory_exchange_import as exchange
from tools import brain_memory, aos_indexer
from tools.business_brain import BUSINESS_BRAIN_ROOT
from tools.business_brain_scope import load_registry
from tools.aos_paths import assert_authoritative_root

DRIVE_ROOT = '1PdJ5qci_nH3KCbbAWx_xalsq8X7tNktF'
FOLDER = 'application/vnd.google-apps.folder'
DOC = 'application/vnd.google-apps.document'
FIELDS = 'id,name,mimeType,parents,trashed,modifiedTime,version,headRevisionId,size,webViewLink'
MAX_BYTES = 750_000
# Protective headroom only: 85% of Google's published standard daily thresholds
# (400M quota units, 1000 GB egress). These are NOT throughput targets, and the
# counters below are watcher-only: they say nothing about project-wide usage.
QUOTA_UNIT_HEADROOM = 340_000_000
EGRESS_HEADROOM_BYTES = 850 * 10**9
EST_UNITS_PER_CALL = 1  # conservative local estimate for Drive v3 metadata/list/changes/export/media GETs
THROTTLE_REASONS = {'ratelimitexceeded', 'userratelimitexceeded', 'dailylimitexceeded',
                    'quotaexceeded', 'sharingratelimitexceeded'}
MAX_BACKOFF = 3600
METRICS = ('api_calls', 'est_quota_units', 'egress_bytes', 'captures', 'failures', 'throttled')
ID = re.compile(r'[A-Za-z0-9_-]{1,200}\Z')
BLOCKED = {'clients', 'client', 'protected', 'confidential', '01 ready', '02 ready', '03 ready',
           '04 imported', '05 rejected', 'ttros memory exchange', 'memory exchange',
           'generated', 'mirrors', 'mirror', 'sources', 'sessions', 'queue', '.git',
           'ttros business brain', 'business brain', 'agentic os live', 'agentic os',
           'agenticosclean', 'memory', 'index', 'artifacts', 'runtime'}


def no_push():
    # Nonempty override also prevents closure code consulting credential/config files.
    os.environ['TTROS_BRAIN_AUTOPUSH'] = '0'
    os.environ['TTROS_CODEX_NO_PUSH'] = '1'
    os.environ['AOS_DISABLE_TELEMETRY'] = '1'
    # Process-local Git defence: hooks cannot push; origin has no usable push transport.
    # Repository config and the operator's normal Git closure remain unchanged.
    os.environ['GIT_CONFIG_COUNT'] = '2'
    os.environ['GIT_CONFIG_KEY_0'] = 'core.hooksPath'
    os.environ['GIT_CONFIG_VALUE_0'] = '/dev/null'
    os.environ['GIT_CONFIG_KEY_1'] = 'remote.origin.pushurl'
    os.environ['GIT_CONFIG_VALUE_1'] = 'disabled://ttros-local-only'


class Budget(Exception):
    pass


class Throttled(Exception):
    def __init__(self, reason='rateLimit', retry_after=0):
        super().__init__(reason)
        self.reason, self.retry_after = reason, retry_after


def _throttle_reason(response):
    """Bounded read of the error reason only; bodies are never logged or stored."""
    try:
        body = next(response.iter_content(16384), b'')
        errors = json.loads(body)['error'].get('errors') or [{}]
        return str(errors[0].get('reason', '')).lower()
    except Exception:
        return ''


class DriveHTTP:
    """SDK owns authentication; never log, print, or copy credentials."""
    def __init__(self, budget=128):
        import google.auth
        from google.auth.transport.requests import AuthorizedSession
        credentials, _ = google.auth.default(scopes=['https://www.googleapis.com/auth/drive.readonly'])
        self.session = AuthorizedSession(credentials)
        self.remaining = budget
        self.stats = dict.fromkeys(('calls', 'bytes'), 0)

    def get(self, path, **params):
        if self.remaining <= 0:
            raise Budget('API budget exhausted; resume next tick')
        self.remaining -= 1
        self.stats['calls'] += 1
        response = self.session.get('https://www.googleapis.com/drive/v3/' + path,
                                    params=params, timeout=20, stream=True)
        with response:
            if response.status_code == 410:
                raise CursorExpired()
            if response.status_code in (403, 429):
                reason = _throttle_reason(response)
                if response.status_code == 429 or reason in THROTTLE_REASONS:
                    try:
                        retry_after = int(response.headers.get('Retry-After', 0))
                    except ValueError:
                        retry_after = 0
                    raise Throttled(reason or 'http429', retry_after)
            if response.status_code != 200:
                raise RuntimeError(f'Drive HTTP {response.status_code}')
            raw = bytearray()
            for chunk in response.iter_content(65536):
                raw.extend(chunk)
                self.stats['bytes'] += len(chunk)
                if len(raw) > MAX_BYTES:
                    raise ValueError('Drive response exceeds capture size limit')
        return bytes(raw) if params.get('alt') == 'media' or path.endswith('/export') else json.loads(raw)

    def metadata(self, ident):
        if not ID.fullmatch(ident):
            raise ValueError('invalid Drive ID')
        return self.get('files/' + ident, fields=FIELDS, supportsAllDrives='true')

    def content(self, row):
        ident = quote(row['id'], safe='')
        if row['mimeType'] == DOC:
            return self.get(f'files/{ident}/export', mimeType='text/plain')
        return self.get(f'files/{ident}', alt='media', supportsAllDrives='true')


class CursorExpired(Exception):
    pass


def safe_path(parts):
    for part in parts:
        canonical = re.sub(r'[_-]+', ' ', part).lower().strip()
        if any(ord(c) < 32 for c in part) or '/' in part or '\\' in part or part in {'.', '..'} or canonical in BLOCKED:
            return False
        if re.search(r'north\s*shore', canonical) or aos_indexer.is_excluded(part):
            return False
    return True


class Watcher:
    def __init__(self, state: Path, api, sink, root_id=DRIVE_ROOT):
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state, self.api, self.sink, self.root_id = state, api, sink, root_id
        self.db = sqlite3.connect(state / 'state.sqlite3')
        self.db.executescript('''
            PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS nodes(id TEXT PRIMARY KEY, data TEXT, dirty INTEGER DEFAULT 1,
                captured_version TEXT, digest TEXT, pointer TEXT, attempted INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS inventory(id TEXT PRIMARY KEY, token TEXT DEFAULT '');
        ''')
        bound = self.value('root')
        if bound and bound != root_id:
            raise ValueError('state belongs to another Drive root')
        self.put('root', root_id)
        self.db.commit()

    def value(self, key):
        row = self.db.execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
        return row[0] if row else None

    def put(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO kv VALUES (?,?)', (key, value))

    def node(self, ident):
        row = self.db.execute('SELECT data FROM nodes WHERE id=?', (ident,)).fetchone()
        return json.loads(row[0]) if row else None

    def path(self, row):
        parts, seen = [], set()
        while row and row['id'] != self.root_id:
            if row['id'] in seen or row.get('trashed') or len(seen) >= 100:
                return None
            seen.add(row['id'])
            parts.insert(0, row.get('name', ''))
            parents = row.get('parents', [])
            row = self.node(parents[0]) if len(parents) == 1 else None
        if not row or row.get('trashed') or not safe_path(parts):
            return None
        return '/'.join(parts)

    def remember(self, row):
        if not ID.fullmatch(row.get('id', '')):
            raise ValueError('invalid Drive ID')
        old = self.node(row['id'])
        if old == row:
            return
        self.db.execute('INSERT INTO nodes(id,data,dirty) VALUES (?,?,1) '
                        'ON CONFLICT(id) DO UPDATE SET data=excluded.data,dirty=1',
                        (row['id'], json.dumps(row, sort_keys=True)))
        if row['mimeType'] == FOLDER and (old is None or old.get('trashed') or old.get('parents') != row.get('parents')):
            if self.path(row) is not None:
                self.db.execute('INSERT OR IGNORE INTO inventory(id) VALUES (?)', (row['id'],))
        if row['mimeType'] == FOLDER and old != row:
            # Paths and eligibility are derived from ancestry, including all known descendants.
            self.db.execute('UPDATE nodes SET dirty=1')

    def discover(self, pages, counts):
        if not self.value('cursor'):
            token = self.api.get('changes/startPageToken', supportsAllDrives='true')['startPageToken']
            self.remember(self.api.metadata(self.root_id))
            self.put('cursor', token)  # before inventory: changes during inventory cannot be lost
            self.db.commit()
        for _ in range(pages):
            task = self.db.execute('SELECT id,token FROM inventory ORDER BY rowid LIMIT 1').fetchone()
            if task:
                ident, token = task
                if self.path(self.node(ident)) is None:
                    self.db.execute('DELETE FROM inventory WHERE id=?', (ident,))
                    self.db.commit()
                    continue
                result = self.api.get('files', q=f"'{ident}' in parents and trashed = false",
                    pageToken=token or None, pageSize=100, fields=f'nextPageToken,files({FIELDS})',
                    supportsAllDrives='true', includeItemsFromAllDrives='true')
                for row in result.get('files', []):
                    self.remember(row)
                    counts['discovered'] += 1
                if result.get('nextPageToken'):
                    self.db.execute('UPDATE inventory SET token=? WHERE id=?', (result['nextPageToken'], ident))
                else:
                    self.db.execute('DELETE FROM inventory WHERE id=?', (ident,))
            else:
                try:
                    cached = self.value('changes_page')
                    result = json.loads(cached) if cached else self.api.get('changes', pageToken=self.value('cursor'), pageSize=100,
                        spaces='drive', includeRemoved='true', includeCorpusRemovals='true',
                        supportsAllDrives='true', includeItemsFromAllDrives='true',
                        fields=f'nextPageToken,newStartPageToken,changes(fileId,removed,file({FIELDS}))')
                except CursorExpired:
                    # Retain originals; invalidate ancestry until a bounded fresh inventory confirms it.
                    self.db.execute('UPDATE nodes SET data=json_set(data,\'$.trashed\',json(\'true\')),dirty=1')
                    self.db.execute('DELETE FROM kv WHERE key=\'cursor\'')
                    self.db.commit()
                    return
                if not self.value('changes_page'):
                    self.put('changes_page', json.dumps(result))
                    self.put('change_offset', '0')
                    self.db.commit()
                start = int(self.value('change_offset') or 0)
                for offset, change in enumerate(result.get('changes', [])):
                    if offset < start:
                        continue
                    ident = change['fileId']
                    row = change.get('file')
                    old = self.node(ident)
                    if change.get('removed'):
                        if old:
                            self.remember({**old, 'trashed': True})
                    elif row:
                        # Unknown ancestry is resolved lazily by metadata only, never content.
                        lineage, candidate, visited = [], row, set()
                        for _depth in range(100):
                            parents = candidate.get('parents', [])
                            if candidate['id'] == self.root_id or len(parents) != 1 or candidate['id'] in visited:
                                break
                            visited.add(candidate['id'])
                            if self.node(parents[0]):
                                break
                            candidate = self.api.metadata(parents[0])
                            lineage.append(candidate)
                        if candidate['id'] == self.root_id or any(self.node(p) for p in candidate.get('parents', [])):
                            for ancestor in reversed(lineage):
                                self.remember(ancestor)
                            self.remember(row)
                            counts['discovered'] += 1
                        elif old:
                            self.remember(row)  # moved outside: retain historical evidence only
                    self.put('change_offset', str(offset + 1))
                    self.db.commit()
                self.put('cursor', result.get('nextPageToken') or result['newStartPageToken'])
                self.db.execute("DELETE FROM kv WHERE key IN ('changes_page','change_offset')")
            self.db.commit()
            if not task and not result.get('nextPageToken'):
                break

    def live_scope(self, ident):
        """Recheck file and ancestry before content; cached inventory is never authority."""
        row = self.api.metadata(ident)
        self.remember(row)
        candidate, seen = row, set()
        for _ in range(100):
            if candidate['id'] == self.root_id:
                return row, self.path(row)
            if candidate['id'] in seen or candidate.get('trashed') or not safe_path([candidate.get('name', '')]):
                return row, None
            seen.add(candidate['id'])
            parents = candidate.get('parents', [])
            if len(parents) != 1:
                return row, None
            parent = parents[0]
            if parent not in self.live_nodes:
                self.live_nodes[parent] = self.api.metadata(parent)
            candidate = self.live_nodes[parent]
            self.remember(candidate)
        return row, None

    def metrics(self, **delta):
        """Per-UTC-day local counters (no quota subsystem); returns today's totals."""
        day = source._utc_now()[:10]
        if self.value('m_day') != day:
            self.put('m_day', day)
            for name in METRICS:
                self.put('m_' + name, '0')
        for name, amount in delta.items():
            self.put('m_' + name, str(int(self.value('m_' + name) or 0) + int(amount)))
        return {'day': day, **{n: int(self.value('m_' + n) or 0) for n in METRICS}}

    def run(self, pages=2, batch=5):
        counts = dict.fromkeys(('discovered', 'imported', 'metadata_only', 'duplicate', 'failed'), 0)
        receipt = {'last_check': source._utc_now(), 'model_invocations': 0,
                   'token_usage': 'unavailable from current CLI output', **counts}
        try:
            until = float(self.value('backoff_until') or 0)
            if time.time() < until:
                receipt['deferred'] = 'Google throttling backoff in effect; durable work remains pending'
                receipt['backoff_until'] = until
                raise Budget()
            self.metrics()  # rolls the per-day counters
            if (int(self.value('m_est_quota_units') or 0) >= QUOTA_UNIT_HEADROOM
                    or int(self.value('m_egress_bytes') or 0) >= EGRESS_HEADROOM_BYTES):
                receipt['deferred'] = 'protective 85% headroom reached (watcher-only estimate)'
                raise Budget()
            self.discover(pages, counts)
            self.live_nodes = {}
            prepared, completed = [], []
            for ident, data, version, old_digest, pointer in self.db.execute(
                    'SELECT id,data,captured_version,digest,pointer FROM nodes WHERE dirty=1 ORDER BY attempted,rowid LIMIT ?', (batch,)).fetchall():
                self.db.execute('UPDATE nodes SET attempted=(SELECT coalesce(max(attempted),0)+1 FROM nodes) WHERE id=?', (ident,))
                row = json.loads(data)
                path = self.path(row)
                suffix = '.txt' if row['mimeType'] == DOC else Path(row['name']).suffix.lower()
                if path is None or row['mimeType'] == FOLDER or suffix not in drive.SUPPORTED:
                    counts['metadata_only'] += 1
                    if pointer:
                        prepared.append((row, None, None, old_digest, pointer))
                    completed.append((ident, None, old_digest, pointer))
                    continue
                current = str(row.get('version', row.get('modifiedTime', '')))
                if current and current == version:
                    counts['metadata_only'] += 1
                    prepared.append((row, path, None, old_digest, pointer))
                    completed.append((ident, current, old_digest, pointer))
                    continue
                try:
                    row, live_path = self.live_scope(ident)
                    if live_path is None:
                        # Do not acknowledge the old snapshot. Next tick handles its tombstone.
                        continue
                    if row != json.loads(data):
                        # A newer safe snapshot is durable and will be processed on the next pass.
                        continue
                    path = live_path
                    if int(row.get('size', 0)) > MAX_BYTES:
                        raise ValueError('source exceeds capture size limit')
                    raw = self.api.content(row)
                    if len(raw) > MAX_BYTES:
                        raise ValueError('source exceeds capture size limit')
                    # Do not assign content to stale metadata if it changed during download/export.
                    after = self.api.metadata(ident)
                    if any(after.get(k) != row.get(k) for k in ('version', 'modifiedTime', 'parents', 'name', 'trashed')):
                        self.remember(after)
                        continue
                    if suffix == '.docx':
                        from io import BytesIO
                        from zipfile import ZipFile
                        with ZipFile(BytesIO(raw)) as archive:
                            if sum(info.file_size for info in archive.infolist()) > drive.MAX_BYTES:
                                raise ValueError('DOCX expanded size limit')
                    normalized = drive.normalize(raw, suffix)
                    if len(normalized) > MAX_BYTES:
                        raise ValueError('normalized content exceeds capture size limit')
                    digest = exchange.digest(raw)
                    blob = self.state / 'originals' / digest
                    blob.parent.mkdir(exist_ok=True)
                    if blob.exists():
                        if exchange.digest(blob.read_bytes()) != digest:
                            raise ValueError('original blob integrity failure')
                    else:
                        atomic_bytes(blob, raw)
                    prepared.append(CaptureRow((row, path, normalized, digest, pointer), raw))
                    completed.append((ident, current, digest, None))
                except (Budget, Throttled):
                    raise
                except Exception:
                    counts['failed'] += 1  # stays dirty; one bad source does not block the batch
            if prepared:
                results = self.sink(prepared)
                for ident, status, pointer in results:
                    counts[status] += 1
                    completed = [(i, v, d, pointer if i == ident else p) for i, v, d, p in completed]
            for ident, version, digest, pointer in completed:
                self.db.execute('UPDATE nodes SET dirty=0,captured_version=?,digest=?,pointer=? WHERE id=?',
                                (version, digest, pointer, ident))
            self.db.commit()
        except Budget:
            self.db.commit()
            receipt.setdefault('deferred', 'per-run API budget; durable work remains pending')
        except Throttled as exc:
            self.db.commit()
            streak = int(self.value('throttle_streak') or 0) + 1
            delay = max(exc.retry_after, min(MAX_BACKOFF, 60 * 2 ** (streak - 1)))
            self.put('throttle_streak', str(streak))
            self.put('backoff_until', str(time.time() + min(delay, MAX_BACKOFF)))
            self.db.commit()
            counts['throttled'] = 1
            receipt['deferred'] = f'Google throttling ({exc.reason}); backing off {min(delay, MAX_BACKOFF)}s'
        except Exception as exc:
            self.db.rollback()
            counts['failed'] += 1
            receipt['error'] = type(exc).__name__  # never credential or protected response bodies
        if 'deferred' not in receipt and not counts.get('throttled') and self.value('throttle_streak'):
            self.put('throttle_streak', '0')
        stats = getattr(self.api, 'stats', None) or {}
        calls = stats.get('calls', 0)
        self.metrics(api_calls=calls, est_quota_units=calls * EST_UNITS_PER_CALL,
                     egress_bytes=stats.get('bytes', 0), captures=counts['imported'] + counts['duplicate'],
                     failures=counts['failed'], throttled=counts.get('throttled', 0))
        self.db.commit()
        receipt['usage_today'] = {**self.metrics(), 'scope': 'this watcher only; not project-wide Google usage',
                                  'est_units_per_call': EST_UNITS_PER_CALL,
                                  'headroom_units': QUOTA_UNIT_HEADROOM, 'headroom_egress_bytes': EGRESS_HEADROOM_BYTES}
        receipt.update({k: v for k, v in counts.items() if k != 'throttled'})
        receipt['throttled'] = counts.get('throttled', 0)
        receipt['pending'] = self.db.execute('SELECT count(*) FROM nodes WHERE dirty=1').fetchone()[0]
        receipt['backlog_runs_at_current_batch'] = -(-receipt['pending'] // max(batch, 1))
        receipt['inventory_folders_pending'] = self.db.execute('SELECT count(*) FROM inventory').fetchone()[0]
        atomic_json(self.state / 'receipt.json', receipt)
        return receipt


def atomic_json(path, value):
    atomic_bytes(path, json.dumps(value, sort_keys=True, indent=2).encode('utf-8'))


def atomic_bytes(path, raw):
    fd, name = tempfile.mkstemp(dir=path.parent)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(name, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def capture_node(path):
    """The JSON of an existing Drive provenance node, or {} when absent/unreadable."""
    try:
        if path.is_symlink() or not path.is_file():
            return {}
        match = re.search(r'```json\s*(\{.*?\})\s*```', path.read_text(encoding='utf-8'), re.DOTALL)
        return json.loads(match.group(1)) if match else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


class CaptureRow(tuple):
    """A (row, path, normalized, digest, pointer) tuple that also carries the exact downloaded bytes."""
    def __new__(cls, fields, raw=None):
        self = super().__new__(cls, fields)
        self.raw = raw
        return self


ORIGINAL_KEYS = ('original_sha256', 'original_size_bytes', 'derivative_sha256', 'derivative_size_bytes', 'conversion')
ORIGINAL_HEADING = '## Exact original bytes'


def original_block(raw):
    """The Drive file's exact bytes in the Brain's existing TTROS:SOURCE-BYTES form (same extractor as records)."""
    import base64
    return (f"\n{ORIGINAL_HEADING}\n\nBase64 decodes to the exact bytes of the Drive file named by `original_sha256`. "
            "`source_sha256` names the normalized derivative TTROS interprets; the derivative is not the original.\n\n"
            f"{source.BYTES_BEGIN}\n{base64.b64encode(raw).decode('ascii')}\n{source.BYTES_END}\n")


def preserved_original(path):
    """The exact original bytes stored in an existing Drive provenance node, or None."""
    try:
        if path.is_symlink() or not path.is_file():
            return None
        return source.extract_exact_bytes(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, source.SourceIntakeError):
        return None


def capture_batch(rows, *, brain=BUSINESS_BRAIN_ROOT, root=ROOT):
    """One validated transaction per tick; evidence + deterministic provenance only.

    Each row is (row, path, normalized, digest, old_pointer) (a CaptureRow when the exact bytes are known); ``CaptureRow.raw`` is the exact Drive download. When it
    is given, the original bytes, their SHA-256 and size are stored in the Drive node next to the link to the
    normalized derivative; replays without it carry the stored original forward unchanged."""
    no_push()
    gate = load_registry(root / 'context/client_scope_registry.json')
    documents, expected, items, results = {}, {}, [], []
    for entry in rows:
        row, path, normalized, digest, old_pointer = entry
        raw = getattr(entry, 'raw', None)
        pointer = old_pointer
        if normalized is not None:
            sha = hashlib.sha256(normalized).hexdigest()
            item = source.SourceItem(Path(row['name']), normalized, sha, 'text/markdown', row['name'], None)
            pointer = item.pointer
            gate.validate_brain_pointer('global', pointer)
            target = brain / item.relative
            duplicate = target.exists() or item.relative in documents
            if target.exists() and source.extract_exact_bytes(target.read_text()) != normalized:
                raise ValueError('existing evidence hash mismatch')
            if not duplicate:
                items.append(item)
                documents[item.relative] = source.render_record(item, source._utc_now(), source_path='drive:' + path)
            results.append((row['id'], 'duplicate' if duplicate else 'imported', pointer))
        relative = f"sources/intake/drive/{row['id']}.md"
        previous = capture_node(brain / relative)
        # Replays keep the first capture time for unchanged content, so a duplicate tick writes nothing.
        captured_at = (previous.get('captured_at') if previous.get('content_sha256') == digest and previous.get('captured_at')
                       else source._utc_now())
        revision = row.get('headRevisionId') or ('drive-version:' + str(row.get('version', 'unknown')))
        record_sha = (pointer or '').rsplit('/', 1)[-1].removesuffix('.md') or None
        provenance = {'drive_id': row['id'], 'original_path': path,
                      'url': row.get('webViewLink') or f"https://drive.google.com/file/d/{row['id']}/view",
                      'revision': revision, 'drive_version': row.get('version'),
                      'modified_time': row.get('modifiedTime'), 'content_sha256': digest,
                      'source_record': pointer, 'category': 'unknown', 'interpretation': 'required',
                      'classification_basis': 'none; source capture only', 'active': path is not None,
                      # Shared provenance vocabulary (tools/memory_exchange_import.py source_provenance).
                      'external_id': row['id'], 'external_version': revision, 'original_name': row.get('name'),
                      'original_type': row.get('mimeType'), 'origin': 'google_drive', 'source_sha256': record_sha,
                      'processing_route': 'drive_watcher_capture', 'captured_at': captured_at}
        if path is None:
            # Never copy excluded names/paths into global evidence on move or permission loss.
            provenance = {'drive_id': row['id'], 'source_record': pointer, 'active': False,
                          'status': 'removed-or-out-of-scope', 'content_sha256': digest,
                          'external_id': row['id'], 'origin': 'google_drive', 'source_sha256': record_sha,
                          'processing_route': 'drive_watcher_capture', 'captured_at': captured_at}
        # Exact original bytes + the explicit derivative link. A replay without the download keeps what is stored.
        stored = preserved_original(brain / relative)
        if raw is not None:
            if exchange.digest(raw) != digest:
                raise ValueError('original bytes do not match their SHA-256')
            derived = normalized
            if derived is not None:
                provenance.update({'original_sha256': digest, 'original_size_bytes': len(raw),
                                   'derivative_sha256': hashlib.sha256(derived).hexdigest(),
                                   'derivative_size_bytes': len(derived),
                                   'conversion': 'none' if derived == raw else 'normalized'})
            block = original_block(raw)
        elif stored is not None and previous.get('original_sha256') == digest and exchange.digest(stored) == digest:
            provenance.update({k: previous[k] for k in ORIGINAL_KEYS if k in previous})
            block = original_block(stored)
        else:
            block = ''
        gate.validate_brain_pointer('global', 'business_brain:' + relative)
        documents[relative] = ('---\nid: drive-source-' + row['id'] + '\ntype: source\nstatus: evidence_only\n---\n'
                               '# Drive source provenance\n\n```json\n' + json.dumps(provenance, ensure_ascii=False, indent=2) + '\n```\n'
                               + block)
    if items:
        documents[source.INDEX_RELATIVE] = source.render_index(brain, items)
    for relative in documents:
        expected[relative] = brain_memory.file_sha256(brain / relative)
    def unstamped(text):
        return re.sub(r'(?m)^' + re.escape(brain_memory.PROVENANCE_KEY) + r':\n(?:  .*\n)*', '', text)
    documents = {rel: text for rel, text in documents.items()
                 if not (brain / rel).exists() or unstamped((brain / rel).read_text()) != text}
    previous = brain_memory.VAULT_ROOT
    brain_memory.VAULT_ROOT = brain
    try:
        if documents:
            brain_memory.write_transaction(documents, source='drive-watcher:capture',
                session_id='drive-watcher-' + source._utc_now().replace(':', ''),
                expected_hashes=expected, force_add=True, commit=True, refresh_indexes=False)
        # Retry derived refresh even after a process dies between commit and cursor acknowledgement.
        paths = [f"business_brain:sources/intake/drive/{row['id']}.md" for row, *_ in rows]
        paths += ['business_brain:' + rel for rel in documents]
        paths += [pointer for _, _, pointer in results]
        if paths:
            search, graph = exchange._refresh(list(dict.fromkeys(paths)), brain, root,
                                              records=[item.pointer for item in items])
            if search.get('status') != 'success' or graph.get('status') not in {'success', 'unchanged'}:
                raise RuntimeError('retrieval refresh pending')
    finally:
        brain_memory.VAULT_ROOT = previous
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pages', type=int, default=2)
    parser.add_argument('--batch', type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.pages <= 5 or not 1 <= args.batch <= 10:
        parser.error('pages must be 1..5; batch must be 1..10')
    no_push()
    assert_authoritative_root(ROOT)
    state = ROOT / 'logs/runtime/drive_watcher'
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with exchange.locked(state / 'watcher.lock'):
        try:
            watcher = Watcher(state, DriveHTTP(), capture_batch)
            receipt = watcher.run(args.pages, args.batch)
        except Exception as exc:
            receipt = {'last_check': source._utc_now(), 'discovered': 0, 'imported': 0,
                       'metadata_only': 0, 'duplicate': 0, 'failed': 1, 'error': type(exc).__name__,
                       'operator_action': 'Provide read-only Drive ADC authentication outside Codex',
                       'model_invocations': 0, 'token_usage': 'unavailable from current CLI output'}
            atomic_json(state / 'receipt.json', receipt)
        print(json.dumps(receipt, sort_keys=True))
        return 1 if receipt.get('failed') else 0


if __name__ == '__main__':
    raise SystemExit(main())
