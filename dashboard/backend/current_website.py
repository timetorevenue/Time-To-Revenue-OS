"""Draft editor for captured published EXCO pages; never claims source parity.

Immutable public-page captures retain the live deployment marker and their hash.
Drafts contain only validated values/section references, never HTML or executable
code. No checkout writes, network capture, models, build, publish or deploy here.
"""
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import ttros_site
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from dashboard_identity import private_response

ROOT = Path(__file__).resolve().parents[2]
CAPTURES = ROOT / 'context/current_site'
STYLE_FIELDS = {'color', 'backgroundColor', 'fontFamily', 'fontSize', 'padding', 'borderRadius', 'textAlign'}
COLOR = re.compile(r'#[0-9a-fA-F]{6}\Z')


def initial(capture):
    return {'sections': [{'source': s['id'], 'id': s['id'], 'texts': {}, 'images': {}, 'styles': {}} for s in capture['sections']]}


def validate(data, capture):
    if not isinstance(data, dict) or set(data) != {'sections'} or not isinstance(data['sections'], list) or not 1 <= len(data['sections']) <= 80:
        raise ValueError('Invalid page sections')
    known = {s['id']: s for s in capture['sections']}
    ids = set()
    for section in data['sections']:
        if not isinstance(section, dict) or set(section) != {'source', 'id', 'texts', 'images', 'styles'}:
            raise ValueError('Invalid section')
        source = known.get(section['source'])
        if not source or not re.fullmatch(r's\d+(?:-copy\d+)?', section['id']) or section['id'] in ids:
            raise ValueError('Unknown or duplicate section')
        ids.add(section['id'])
        for category in ('texts', 'images'):
            fields = {f['id'] for f in source[category]}
            values = section[category]
            if not isinstance(values, dict) or set(values) - fields:
                raise ValueError('Unknown content field')
            for value in values.values():
                if category == 'texts':
                    if not isinstance(value, str) or len(value) > 6000:
                        raise ValueError('Text exceeds edit limit')
                else:
                    if not isinstance(value, dict) or set(value) != {'src', 'alt'} or not isinstance(value['alt'], str) or len(value['alt']) > 500:
                        raise ValueError('Invalid image')
                    src = value['src']
                    if not isinstance(src, str) or len(src) > 2000:
                        raise ValueError('Invalid image URL')
                    uri = urlsplit(src)
                    local = re.fullmatch(r'/api/current-website/assets/[0-9a-f]{64}', src)
                    if not local and (uri.scheme != 'https' or uri.netloc != 'www.' + ttros_site.DOMAIN or uri.username):
                        raise ValueError('Choose an existing EXCO image or upload a local image')
        styles = section['styles']
        if not isinstance(styles, dict) or set(styles) - STYLE_FIELDS:
            raise ValueError('Unknown style')
        for key, value in styles.items():
            if key in ('color', 'backgroundColor'):
                ok = isinstance(value, str) and COLOR.fullmatch(value)
            elif key == 'fontFamily':
                ok = value in ('inherit', 'Arial, sans-serif', 'Georgia, serif', 'system-ui, sans-serif')
            elif key == 'textAlign':
                ok = value in ('left', 'center', 'right')
            else:
                ok = isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 160
            if not ok:
                raise ValueError('Invalid style value')
    return data


class Draft(BaseModel):
    data: dict
    base_version: int = Field(ge=0)
    source_hash: str


class Proposal(BaseModel):
    data: dict
    base_version: int = Field(ge=0)
    section: str
    field: str
    request: str = Field(min_length=1, max_length=6500)


class Website:
    def __init__(self, identity):
        self.identity = identity
        with identity.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS current_drafts(user TEXT,workspace TEXT,page TEXT,version INTEGER,source_hash TEXT,data TEXT,at REAL,PRIMARY KEY(user,workspace,page,version))''')
            db.execute('''CREATE TABLE IF NOT EXISTS current_assets(user TEXT,workspace TEXT,hash TEXT,mime TEXT,data BLOB,PRIMARY KEY(user,workspace,hash))''')
            db.execute('''CREATE TABLE IF NOT EXISTS current_proposals(id INTEGER PRIMARY KEY,user TEXT,workspace TEXT,page TEXT,base_version INTEGER,proposal TEXT,at REAL)''')

    def capture(self, page):
        if not re.fullmatch(r'[a-z0-9-]{1,64}', page) or not (CAPTURES / (page + '.json')).is_file():
            raise HTTPException(404, 'Unknown current website page')
        raw = (CAPTURES / (page + '.json')).read_bytes()
        return json.loads(raw), hashlib.sha256(raw).hexdigest()

    def latest(self, who, page, db=None):
        if db is None:
            with self.identity.db() as db:
                return self.latest(who, page, db)
        row = db.execute('SELECT * FROM current_drafts WHERE user=? AND workspace=? AND page=? ORDER BY version DESC LIMIT 1', (who['user'], who['workspace'], page)).fetchone()
        return dict(row) if row else None

    def save(self, who, page, body):
        capture, sha = self.capture(page)
        try:
            validate(body.data, capture)
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(422, str(exc)) from None
        if body.source_hash != sha:
            raise HTTPException(409, 'Published capture changed; review the new base before saving')
        with self.identity.db() as db:
            db.execute('BEGIN IMMEDIATE')
            latest = self.latest(who, page, db)
            version = latest['version'] if latest else 0
            if body.base_version != version:
                raise HTTPException(409, 'A newer draft exists. Reload it before saving.')
            db.execute('INSERT INTO current_drafts VALUES(?,?,?,?,?,?,?)', (who['user'], who['workspace'], page, version + 1, sha, json.dumps(body.data), time.time()))
        self.identity.receipt(who, 'website.draft.save', page + ':v' + str(version + 1))
        return {'version': version + 1, 'published': False, 'source_hash': sha}


def router(identity):
    site = Website(identity)
    api = APIRouter(prefix='/api/current-website', dependencies=[Depends(private_response)])

    @api.get('')
    def pages(req: Request):
        identity.principal(req)
        cfg = json.loads((ROOT / 'context/account_connections.json').read_text())['website']
        rows = []
        for path in sorted(CAPTURES.glob('*.json')):
            data, sha = site.capture(path.stem)
            rows.append({'id': path.stem, 'title': data['title'], 'route': data['route'], 'sections': len(data['sections']), 'source_hash': sha})
        return {'website': cfg, 'pages': rows, 'source_status': 'blocked', 'mode': 'published_page_capture',
                'source_blocker': 'Exact deployed Git object is unavailable. GitHub organization restricts the connected OAuth app. These are actual published-page captures, not a source-backed Next build.',
                'publication': 'held', 'model_calls': 0}

    @api.get('/pages/{page}')
    def page(page: str, req: Request):
        who = identity.principal(req)
        capture, sha = site.capture(page)
        latest = site.latest(who, page)
        if latest and latest['source_hash'] != sha:
            raise HTTPException(409, 'Saved draft belongs to an older capture; source reconciliation required')
        with identity.db() as db:
            versions = db.execute('SELECT version,at FROM current_drafts WHERE user=? AND workspace=? AND page=? ORDER BY version DESC', (who['user'], who['workspace'], page)).fetchall()
        return {'capture': capture, 'source_hash': sha, 'data': json.loads(latest['data']) if latest else initial(capture),
                'version': latest['version'] if latest else 0, 'versions': [dict(r) for r in versions]}

    @api.get('/pages/{page}/versions/{version}')
    def version(page: str, version: int, req: Request):
        who = identity.principal(req)
        site.capture(page)
        with identity.db() as db:
            row = db.execute('SELECT data,source_hash FROM current_drafts WHERE user=? AND workspace=? AND page=? AND version=?', (who['user'], who['workspace'], page, version)).fetchone()
        if not row:
            raise HTTPException(404, 'Draft version not found')
        return {'data': json.loads(row['data']), 'source_hash': row['source_hash']}

    @api.post('/pages/{page}/drafts')
    def save(page: str, body: Draft, req: Request):
        identity.csrf(req)
        return site.save(identity.principal(req), page, body)

    @api.post('/pages/{page}/propose')
    def propose(page: str, body: Proposal, req: Request):
        identity.csrf(req)
        who = identity.principal(req)
        capture, sha = site.capture(page)
        try:
            validate(body.data, capture)
            section = next(s for s in body.data['sections'] if s['id'] == body.section)
            source = next(s for s in capture['sections'] if s['id'] == section['source'])
            field = next(f for f in source['texts'] if f['id'] == body.field)
        except (ValueError, TypeError, KeyError, StopIteration):
            raise HTTPException(422, 'Select an actual text field on this page') from None
        latest = site.latest(who, page)
        if body.base_version != (latest['version'] if latest else 0):
            raise HTTPException(409, 'A newer draft exists; reload before requesting a proposal')
        from aos_david import David
        # Explicit bounded David capability, not a fixture/model impersonation.
        # Freeform generation remains disabled until separately authorized.
        result = David.studio_text_proposal(body.request, page=page, section=body.section, field=body.field,
                                            before=section['texts'].get(body.field, field['text']))
        if not result['ok']:
            raise HTTPException(422, result['detail'])
        with identity.db() as db:
            cur = db.execute('INSERT INTO current_proposals(user,workspace,page,base_version,proposal,at) VALUES(?,?,?,?,?,?)',
                             (who['user'], who['workspace'], page, body.base_version, json.dumps(result), time.time()))
            result['id'] = cur.lastrowid
        identity.receipt(who, 'website.david.propose', page + ':' + str(result['id']))
        return {**result, 'source_hash': sha, 'published': False, 'applied': False}

    @api.post('/assets')
    async def upload(req: Request):
        identity.csrf(req)
        who = identity.principal(req)
        raw = bytearray()
        async for chunk in req.stream():
            raw.extend(chunk)
            if len(raw) > 5_000_000:
                raise HTTPException(413, 'Image exceeds 5 MB')
        mime = 'image/png' if raw.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg' if raw.startswith(b'\xff\xd8\xff') else 'image/webp' if raw.startswith(b'RIFF') and raw[8:12] == b'WEBP' else None
        if not mime:
            raise HTTPException(422, 'Upload a PNG, JPEG or WebP image')
        sha = hashlib.sha256(raw).hexdigest()
        with identity.db() as db:
            db.execute('INSERT OR IGNORE INTO current_assets VALUES(?,?,?,?,?)', (who['user'], who['workspace'], sha, mime, bytes(raw)))
        identity.receipt(who, 'website.image.upload', sha)
        return {'url': '/api/current-website/assets/' + sha}

    @api.get('/assets/{sha}')
    def asset(sha: str, req: Request):
        who = identity.principal(req)
        with identity.db() as db:
            row = db.execute('SELECT mime,data FROM current_assets WHERE user=? AND workspace=? AND hash=?', (who['user'], who['workspace'], sha)).fetchone()
        if not row:
            raise HTTPException(404, 'Image not found')
        return Response(row['data'], media_type=row['mime'], headers={'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, no-store'})
    return api
