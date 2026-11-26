"""Website Studio live-source editor: the real website (pinned commit) as editable elements.

The snapshot is imported server-side from Git objects (dashboard/frontend/scripts/studio-live).
Drafts are element trees per source document (src/studio/live/model.js); they carry literal
values only and reference source code by id, so a draft can move, duplicate, restyle or delete
functional parts but never author code. This module is the authority for validating them.

Versions are immutable, timestamped and hash-chained (append-only ledger + content-addressed
document blobs). Restoring an older version appends a new version; nothing is overwritten. An
autosave slot keeps unsaved work for crash/reopen. Builds regenerate only edited elements into
the website source in a disposable fixture and run the real `next build`; release builds feed the
existing immutable releases (website_studio_releases). Nothing here publishes or calls a model.
Revisit: when the live adapter, snapshot schema or release integration changes. Last touched: 2026-11-21.
"""
from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from dashboard_identity import private_response
import ttros_site
import website_studio_builds as studio_builds
import website_studio_code as studio_code
import website_studio_pages as studio_pages

LIVE_SOURCE = '2e521be1ce7469538b0eff234860e7f97bc6c739'
SCHEMA = 'ttros.studio.live/1'
ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'dashboard/frontend/scripts/studio-live'
SITE = ttros_site.SITE_ID
MAX_DOC_BYTES = 4_000_000
MAX_VERSIONS = 5000
MAX_ASSET_BYTES = 12_000_000


class LiveError(ValueError):
    pass


class StaleError(LiveError):
    pass


def _iso() -> str:
    t = time.time()
    return time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(t)) + f'.{int(t % 1 * 1000):03d}Z'


def sha(value) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(raw).hexdigest()


def _atomic(path: Path, value, raw: bytes | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name('.' + path.name + f'.{os.getpid()}.{threading.get_ident()}')
    tmp.write_bytes(raw if raw is not None else json.dumps(value, sort_keys=True).encode())
    os.replace(tmp, path)


# ------------------------------------------------------------------------------ validation
# Python twin of src/studio/live/model.js validateTree. Kept deliberately strict.
ID = re.compile(r'[a-z0-9-]{1,48}_[a-z0-9]{1,12}\Z')
SVG_TAGS = {'svg', 'g', 'path', 'circle', 'ellipse', 'rect', 'line', 'polyline', 'polygon', 'text', 'tspan', 'defs', 'linearGradient',
            'radialGradient', 'stop', 'clipPath', 'mask', 'pattern', 'title', 'desc', 'use', 'symbol', 'marker', 'foreignObject', 'filter',
            'feGaussianBlur', 'feOffset', 'feBlend', 'feColorMatrix', 'feMerge', 'feMergeNode', 'feFlood', 'feComposite', 'feDropShadow'}
HTML_TAGS = {'div', 'section', 'header', 'footer', 'nav', 'main', 'article', 'aside', 'address', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'span',
             'a', 'ul', 'ol', 'li', 'dl', 'dt', 'dd', 'img', 'picture', 'source', 'figure', 'figcaption', 'video', 'audio', 'track', 'button', 'strong',
             'em', 'b', 'i', 'u', 's', 'small', 'sub', 'sup', 'mark', 'abbr', 'cite', 'code', 'pre', 'kbd', 'q', 'time', 'br', 'hr', 'wbr', 'blockquote',
             'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption', 'colgroup', 'col', 'details', 'summary', 'label', 'form', 'fieldset',
             'legend', 'input', 'textarea', 'select', 'option', 'iframe', 'noscript', 'del', 'ins'}
TAGS = HTML_TAGS | SVG_TAGS | {'Link', 'Image'}
VOID = {'img', 'br', 'hr', 'wbr', 'input', 'source', 'track', 'col', 'Image'}
EMBED_HOSTS = {'www.youtube-nocookie.com', 'www.youtube.com', 'player.vimeo.com', 'tally.so', 'www.loom.com'}
ATTRS = set('''id href src alt title width height target rel role loading decoding type name value placeholder tabIndex lang dir hidden download
srcSet sizes poster preload controls autoPlay muted loop playsInline allow allowFullScreen frameBorder scrolling colSpan rowSpan scope headers dateTime cite
open start reversed htmlFor priority unoptimized quality fill prefetch scroll replace disabled required readOnly checked selected multiple maxLength
minLength pattern autoComplete inputMode action method encType noValidate referrerPolicy crossOrigin itemProp itemScope itemType key
suppressHydrationWarning viewBox xmlns d cx cy r rx ry x y x1 y1 x2 y2 points stroke strokeWidth strokeLinecap strokeLinejoin strokeDasharray
strokeDashoffset strokeOpacity fillOpacity fillRule clipRule opacity transform preserveAspectRatio gradientUnits gradientTransform offset stopColor
stopOpacity textAnchor dominantBaseline fontSize fontWeight fontFamily letterSpacing dx dy pathLength markerEnd markerStart clipPath mask filter
focusable vectorEffect paintOrder stdDeviation in in2 result mode values floodColor floodOpacity operator xlinkHref shapeRendering textRendering
imageRendering patternUnits maskUnits clipPathUnits refX refY markerWidth markerHeight orient'''.split())
URL_ATTRS = {'href', 'src', 'poster', 'action', 'cite', 'xlinkHref'}
ALT_TAGS = {'img', 'Image', 'area', 'input'}  # `alt` is only valid (and type-checks in the site) here
STYLE_NAMES = set('''display position top right bottom left zIndex width height minWidth maxWidth minHeight maxHeight margin marginTop marginRight
marginBottom marginLeft padding paddingTop paddingRight paddingBottom paddingLeft boxSizing overflow float aspectRatio objectFit objectPosition
transform order visibility flexDirection flexWrap justifyContent alignItems alignContent alignSelf justifySelf gap rowGap columnGap flex flexGrow
flexShrink flexBasis gridTemplateColumns gridTemplateRows gridColumn gridRow gridAutoFlow placeItems color fontFamily fontSize fontWeight fontStyle
lineHeight letterSpacing textAlign textTransform textDecoration whiteSpace wordBreak textWrap textShadow textUnderlineOffset fontVariant backgroundColor backgroundImage backgroundSize
backgroundPosition backgroundRepeat border borderTop borderRight borderBottom borderLeft borderColor borderWidth borderStyle borderRadius boxShadow
opacity outline cursor filter mixBlendMode backdropFilter transition'''.split())
STYLE_VALUE = re.compile(r'''[#a-zA-Z0-9 .,%()\-+/*"'!:]{0,240}\Z''')
STYLE_BAD = re.compile(r'url\s*\(|expression\s*\(|@import|\\|javascript:', re.I)
BG_ASSET = re.compile(r'asset:[a-f0-9]{64}\Z')
BG_GRADIENT = re.compile(r'(repeating-)?(linear|radial|conic)-gradient\([#a-zA-Z0-9 .,%()\-+/]{1,400}\)\Z')
CLASS_BAD = re.compile(r'''[<>"'`{}\\]''')
ALLOWED = {'El': {'id', 'tag', 'a', 'x', 'cls', 'st', 'kids', 'from', 'label'}, 'Txt': {'id', 'text', 'x', 'from'},
           'Code': {'id', 'c', 'st', 'from'}, 'Cond': {'id', 'c', 'kids', 'alt', 'show', 'from'},
           'Use': {'id', 'name', 'use', 'a', 'd', 'x', 'st', 'kids', 'doc', 'tpl', 'from', 'label'},
           # Map: one editable template repeated per entry; Rec: one data-module record (string fields).
           'Map': {'id', 'c', 'kids', 'from', 'label'}, 'Rec': {'id', 'key', 'f'},
           # Comp: a React component file of the site (or one written in Code mode) placed visually.
           'Comp': {'id', 'name', 'file', 'st', 'from', 'label'}}
COMP_FILE = re.compile(r'components/[A-Za-z0-9_\-]+(?:/[A-Za-z0-9_\-]+)*\.(?:tsx|jsx)\Z')
FIELD = re.compile(r'[A-Za-z][A-Za-z0-9]{0,40}\Z')
BREAKPOINTS = {'d', 't', 'm'}


def attr_allowed(name: str) -> bool:
    return name in ATTRS or bool(re.fullmatch(r'aria-[a-z]{2,30}', name)) or bool(re.fullmatch(r'data-(?!sid\Z|studio)[a-z][a-z0-9-]{0,40}', name))


def url_problem(name: str, value, tag: str | None) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    v = value.strip()
    squashed = re.sub(r'[\s\x00-\x1f]', '', v)
    if re.match(r'(javascript|vbscript|data|file):', squashed, re.I):
        if name == 'src' and re.fullmatch(r'data:image/(png|jpeg|gif|webp);base64,[A-Za-z0-9+/=]{1,400000}', v):
            return None
        return f'Unsafe {name}.'
    if tag == 'iframe' and name == 'src':
        m = re.match(r'https://([^/?#]+)', v)
        return None if m and m.group(1) in EMBED_HOSTS else 'Frames may only embed approved hosts.'
    if re.match(r'(https?:|mailto:|tel:|#|/(?!/)|\./|\?)', v, re.I) or BG_ASSET.fullmatch(v):
        return None
    if re.fullmatch(r'[a-z0-9][a-z0-9._~%/-]*', v, re.I):
        return None
    return f'Unsupported {name} address.'


def style_problem(name: str, value) -> str | None:
    if name not in STYLE_NAMES:
        return f'Unknown style property {name}.'
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return f'Style {name} must be text.'
    text = str(value)
    if name == 'backgroundImage':
        ok = text == 'none' or BG_ASSET.fullmatch(text) or (BG_GRADIENT.fullmatch(text) and not re.search(r'url\s*\(', text, re.I))
        return None if ok else 'Background images come from the asset library (or a CSS gradient).'
    if not STYLE_VALUE.fullmatch(text) or STYLE_BAD.search(text) or re.search(r'[;{}<>]', text):
        return f'Style {name} has an unsupported value.'
    return None


def _json_data(value, depth=0) -> bool:
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if depth > 12:
        return False
    if isinstance(value, list):
        return all(_json_data(v, depth + 1) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _json_data(v, depth + 1) for k, v in value.items())
    return False


def validate_tree(tree, codes: dict, doc: str, docs: set[str] | None = None, max_nodes: int = 20000) -> dict:
    """Validate one document's tree. Returns stats. Raises LiveError with the first problem."""
    if not isinstance(tree, list):
        raise LiveError('A document is a list of elements.')
    seen: set[str] = set()
    count = 0
    assets: set[str] = set()
    maps: list[str] = []  # enclosing repeated templates

    def ref(r):
        if not isinstance(r, str) or r not in codes:
            raise LiveError(f'Unknown source reference {str(r)[:40]}.')
        c = codes[r]
        if c['doc'] != doc and not c.get('pure'):
            raise LiveError(f'An element uses code from {c["doc"]} and can only stay inside that file ({doc}).')
        if c.get('scope') and c['scope'] not in maps:
            raise LiveError('An element bound to a repeated item was moved out of its template.')

    def check(item, depth, parent_tag):
        nonlocal count
        count += 1
        if count > max_nodes:
            raise LiveError('Too many elements in one document.')
        if depth > 80:
            raise LiveError('Elements are nested too deeply.')
        if not isinstance(item, dict) or item.get('type') not in ALLOWED or not isinstance(item.get('props'), dict) or set(item) - {'type', 'props'}:
            raise LiveError('Invalid element.')
        kind, p = item['type'], item['props']
        unknown = set(p) - ALLOWED[kind]
        if unknown:
            raise LiveError(f'Unknown field {sorted(unknown)[0]} on {kind}.')
        ident = p.get('id')
        if not isinstance(ident, str) or not ID.fullmatch(ident) or ident in seen:
            raise LiveError(f'Invalid or duplicate element id {str(ident)[:60]}.')
        seen.add(ident)
        if 'from' in p and (not isinstance(p['from'], str) or not ID.fullmatch(p['from'])):
            raise LiveError('Invalid origin id.')
        if 'label' in p and (not isinstance(p['label'], str) or len(p['label']) > 80):
            raise LiveError('Invalid label.')
        x = p.get('x')
        if x is not None:
            if not isinstance(x, dict):
                raise LiveError('Invalid code attributes.')
            for key, r in x.items():
                if not isinstance(key, str) or len(key) > 60:
                    raise LiveError('Invalid code attribute.')
                ref(r)
                if isinstance(p.get('a'), dict) and key in p['a']:
                    raise LiveError(f'Attribute {key} is both literal and code.')
        a = p.get('a')
        if a is not None:
            if not isinstance(a, dict):
                raise LiveError('Invalid attributes.')
            for key, value in a.items():
                if not isinstance(key, str) or not attr_allowed(key):
                    raise LiveError(f'Attribute {str(key)[:40]} is not allowed.')
                if key == 'alt' and kind == 'El' and p.get('tag') not in ALT_TAGS:
                    raise LiveError(f'Alternative text belongs to images, not <{p.get("tag")}> (use title or aria-label).')
                if not isinstance(value, (str, int, float, bool)) or len(str(value)) > 4000:
                    raise LiveError(f'Attribute {key} has an unsupported value.')
                if key in URL_ATTRS or key == 'srcSet':
                    first = str(value).split(',')[0].strip().split(' ')[0] if key == 'srcSet' else value
                    problem = url_problem('src' if key == 'srcSet' else key, first, p.get('tag'))
                    if problem:
                        raise LiveError(problem)
                    if isinstance(value, str) and BG_ASSET.fullmatch(value.strip()):
                        assets.add(value.strip()[6:])
        st = p.get('st')
        if st is not None:
            if not isinstance(st, dict) or set(st) - BREAKPOINTS:
                raise LiveError('Invalid styles.')
            for layer in st.values():
                if not isinstance(layer, dict):
                    raise LiveError('Invalid breakpoint styles.')
                for k, v in layer.items():
                    problem = style_problem(k, v)
                    if problem:
                        raise LiveError(problem)
                    if k == 'backgroundImage' and str(v).startswith('asset:'):
                        assets.add(str(v)[6:])
        if 'cls' in p and (not isinstance(p['cls'], str) or len(p['cls']) > 2000 or CLASS_BAD.search(p['cls'])):
            raise LiveError('Invalid class list.')
        if kind == 'El':
            tag = p.get('tag')
            if tag not in TAGS:
                raise LiveError(f'Tag {str(tag)[:30]} is not allowed.')
            if tag == 'iframe' and not (x or {}).get('src'):
                src = (a or {}).get('src')
                if not src or url_problem('src', src, 'iframe'):
                    raise LiveError('A frame needs an approved embed address.')
            if (tag in VOID) != ('kids' not in p) or ('kids' in p and not isinstance(p['kids'], list)):
                raise LiveError(f'{tag} has an invalid children list.')
            if tag in SVG_TAGS and tag != 'svg' and parent_tag and parent_tag not in SVG_TAGS:
                raise LiveError('SVG shapes must stay inside a graphic.')
        elif kind == 'Txt':
            if not isinstance(p.get('text'), str) or len(p['text']) > 40000:
                raise LiveError('Invalid text.')
        elif kind == 'Code':
            ref(p.get('c'))
        elif kind == 'Cond':
            ref(p.get('c'))
            if not isinstance(p.get('kids'), list) or not isinstance(p.get('alt'), list):
                raise LiveError('Invalid condition.')
            if p.get('show') not in (None, 'kids', 'alt', 'none'):
                raise LiveError('Invalid condition branch.')
        elif kind == 'Use':
            if not isinstance(p.get('name'), str) or not re.fullmatch(r'[A-Z][A-Za-z0-9]{0,60}', p['name']):
                raise LiveError('Invalid component.')
            if 'use' not in p:
                raise LiveError('A component must reference its source usage.')
            ref(p['use'])
            if 'doc' in p:
                if docs is not None and p['doc'] not in docs:
                    raise LiveError('Unknown shared document.')
                if 'kids' in p:
                    raise LiveError('Shared document contents are saved in their own document.')
            if 'kids' in p and not isinstance(p['kids'], list):
                raise LiveError('Invalid component children.')
            if 'tpl' in p and (not isinstance(p['tpl'], str) or (docs is not None and p['tpl'] not in docs) or 'kids' in p):
                raise LiveError('A component template is saved in its own document.')
            if 'd' in p:
                d = p['d']
                if not isinstance(d, dict) or not _json_data(d) or len(json.dumps(d)) > 200_000:
                    raise LiveError('Component data must be plain text, numbers and lists.')
                for key in d:
                    if key not in (x or {}):
                        raise LiveError(f'Component input {key} does not exist.')
        elif kind == 'Map':
            ref(p.get('c'))
            if not isinstance(p.get('kids'), list):
                raise LiveError('Invalid repeated template.')
        elif kind == 'Comp':
            if not isinstance(p.get('name'), str) or not re.fullmatch(r'[A-Z][A-Za-z0-9]{0,60}', p['name']):
                raise LiveError('A component is named in PascalCase (for example PricingCalculator).')
            if not isinstance(p.get('file'), str) or not COMP_FILE.fullmatch(p['file']) or p['file'] == 'components/StudioSiteBehavior.tsx':
                raise LiveError('A placed component is a file under components/ (.tsx or .jsx).')
        elif kind == 'Rec':
            if not isinstance(p.get('key'), str) or not isinstance(p.get('f'), dict):
                raise LiveError('Invalid content record.')
            for k, v in p['f'].items():
                if not isinstance(k, str) or not FIELD.fullmatch(k) or not isinstance(v, str) or len(v) > 4000:
                    raise LiveError(f'Invalid content field {str(k)[:40]}.')
        if kind == 'Map':
            maps.append(ident)
        for slot in ('kids', 'alt'):
            if isinstance(p.get(slot), list):
                for child in p[slot]:
                    check(child, depth + 1, p.get('tag') if kind == 'El' else parent_tag)
        if kind == 'Map':
            maps.pop()

    for item in tree:
        check(item, 0, None)
    return {'nodes': count, 'ids': seen, 'assets': assets}


def _walk(tree):
    for item in tree:
        yield item
        for slot in ('kids', 'alt'):
            kids = item.get('props', {}).get(slot)
            if isinstance(kids, list):
                yield from _walk(kids)


# ------------------------------------------------------------------------------ store
class LiveStudio:
    def __init__(self, sources, commit: str = LIVE_SOURCE, node: str = 'node'):
        self.sources = sources
        self.default_commit = commit
        self.node = node
        self._lock = threading.Lock()
        self._cache: dict = {}
        self.importing: dict | None = None

    # -- paths ------------------------------------------------------------------------------
    def folder(self, site_id: str = SITE) -> Path:
        if site_id != SITE:
            raise KeyError('The live editor supports the Example Company website only')
        self.sources.site(site_id)
        return Path(self.sources.state) / site_id / 'live'

    # The pinned live source moves forward when the website changes (rebase); pin.json records it.
    @property
    def commit(self) -> str:
        try:
            pin = json.loads((Path(self.sources.state) / SITE / 'live' / 'pin.json').read_text())
            if re.fullmatch(r'[0-9a-f]{40}', pin.get('commit', '')):
                return pin['commit']
        except (OSError, ValueError, AttributeError, TypeError):
            pass
        return self.default_commit

    def snapshot_dir(self, site_id: str = SITE, commit: str | None = None) -> Path:
        return self.folder(site_id) / 'imports' / (commit or self.commit)

    @contextmanager
    def _guard(self, site_id: str):
        path = self.folder(site_id) / '.lock'
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, path.open('a+') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    # -- snapshot ---------------------------------------------------------------------------
    def repository(self, site_id: str = SITE) -> Path:
        return Path(self.sources.site(site_id)['repository'])

    def status(self, site_id: str = SITE) -> dict:
        folder = self.snapshot_dir(site_id)
        receipt = folder / 'receipt.json'
        repo = self.repository(site_id)
        present = subprocess.run(['git', '-C', str(repo), 'cat-file', '-e', f'{self.commit}^{{commit}}'], capture_output=True, timeout=20).returncode == 0
        base = {'commit': self.commit, 'commit_present': present, 'importing': self.importing, 'schema': SCHEMA}
        if not receipt.is_file():
            return {**base, 'ready': False, 'detail': 'Not imported yet.' if present else f'Live source {self.commit[:12]} is not in the registered repository; fetch it first.'}
        return {**base, 'ready': True, 'receipt': json.loads(receipt.read_text())}

    def _run_import(self, site_id: str, commit: str) -> dict:
        out = self.snapshot_dir(site_id, commit)
        cache = self.folder(site_id) / 'npm-cache'
        proc = subprocess.run([self.node, str(SCRIPTS / 'import-live.mjs'), str(self.repository(site_id)), commit, str(out), '--npm-cache', str(cache)],
                              capture_output=True, text=True, timeout=1800, env={'PATH': os.environ['PATH'], 'HOME': str(self.folder(site_id))})
        if proc.returncode:
            raise LiveError((proc.stderr or proc.stdout)[-600:])
        self._cache.clear()
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def start_import(self, site_id: str = SITE, rebase_to: str | None = None, actor: str = 'operator') -> dict:
        """Import the pinned commit, or (rebase_to) a newer live commit: import it, carry the latest saved
        version and the autosave onto it (migrate-live.mjs), then move the pin. Nothing is published."""
        if rebase_to is not None:
            if not re.fullmatch(r'[0-9a-f]{40}', rebase_to or ''):
                raise LiveError('Give the full 40-character website commit.')
            if subprocess.run(['git', '-C', str(self.repository(site_id)), 'cat-file', '-e', f'{rebase_to}^{{commit}}'], capture_output=True, timeout=20).returncode:
                raise LiveError(f'Commit {rebase_to[:12]} is not in the registered website repository; fetch it first.')
            if rebase_to == self.commit:
                raise LiveError('The Studio is already pinned to that commit.')
        with self._lock:
            if self.importing and self.importing.get('state') == 'running':
                raise LiveError('An import is already running.')
            self.importing = {'state': 'running', 'started_at': _iso(), **({'rebase_to': rebase_to} if rebase_to else {})}

        def run():
            try:
                commit = rebase_to or self.commit
                result = self._run_import(site_id, commit)
                if rebase_to:
                    result['migration'] = self._rebase(site_id, rebase_to, actor)
                self.importing = {'state': 'finished', 'finished_at': _iso(), 'result': result}
            except Exception as exc:  # recorded for the operator; the previous snapshot and pin stay
                self.importing = {'state': 'failed', 'finished_at': _iso(), 'error': f'{type(exc).__name__}: {str(exc)[-400:]}'}
        threading.Thread(target=run, daemon=True).start()
        return dict(self.importing)

    def migrate_docs(self, site_id: str, from_commit: str, docs: dict, to_commit: str | None = None) -> dict:
        """Carry documents edited on one imported commit onto another (default: the pin)."""
        to_commit = to_commit or self.commit
        folder = self.folder(site_id) / 'migrations'
        folder.mkdir(parents=True, exist_ok=True)
        stamp = hashlib.sha256(f'{from_commit}{to_commit}{time.time()}'.encode()).hexdigest()[:16]
        request = {'from': str(self.snapshot_dir(site_id, from_commit)), 'to': str(self.snapshot_dir(site_id, to_commit)), 'docs': docs,
                   'output': str(folder / f'{stamp}.json')}
        (folder / f'{stamp}.request.json').write_text(json.dumps(request))
        proc = subprocess.run([self.node, str(SCRIPTS / 'migrate-live.mjs'), str(folder / f'{stamp}.request.json')], capture_output=True, text=True, timeout=600,
                              env={'PATH': os.environ['PATH']})
        if proc.returncode:
            raise LiveError('Migration failed: ' + (proc.stderr or proc.stdout)[-400:])
        (folder / f'{stamp}.request.json').unlink()
        return json.loads((folder / f'{stamp}.json').read_text())

    def _rebase(self, site_id: str, commit: str, actor: str) -> dict:
        old = self.commit
        latest = self.latest(site_id)
        report = {'from': old, 'to': commit, 'version': None, 'autosave': None}
        if latest:
            docs = self.documents(site_id, latest)
            imported_ids = set(self.model(site_id)['docs'])
            created = {d: t for d, t in docs.items() if d not in imported_ids}  # owner-created pages carry over as they are
            moved = self.migrate_docs(site_id, old, {d: t for d, t in docs.items() if d in imported_ids}, commit)
            moved['docs'].update(created)
            report['version_report'] = moved['report']
        auto = self.get_autosave(site_id)
        if auto:
            moved_auto = self.migrate_docs(site_id, old, auto['docs'], commit)
            report['autosave_report'] = moved_auto['report']
        _atomic(self.folder(site_id) / 'pin.json', {'commit': commit, 'previous': old, 'at': _iso(), 'actor': actor})
        self._cache.clear()
        if latest:
            entry = self.save(site_id, moved['docs'], latest, actor, note=f'Migrated v{latest} from {old[:8]} to {commit[:8]} '
                              f"({len(moved['report']['conflicts'])} conflicts)", origin='migration', restored_from=latest, code=self.code(site_id, latest))
            report['version'] = entry['version']
        if auto:
            _atomic(self.folder(site_id) / 'autosave.json', {**auto, 'docs': moved_auto['docs'], 'commit': commit, 'sha256': sha(moved_auto['docs'])})
            report['autosave'] = 'migrated'
        return report
        threading.Thread(target=run, daemon=True).start()
        return dict(self.importing)

    def _load(self, site_id: str, name: str):
        path = self.snapshot_dir(site_id) / name
        if not path.is_file():
            raise KeyError('The live website has not been imported yet')
        key = (str(path), path.stat().st_mtime_ns)
        if key not in self._cache:
            value = json.loads(path.read_text())
            if name in ('model.json', 'pristine.json') and isinstance(value.get('codes'), dict):
                # Kit references (Header/Footer placed by owner-created pages) resolve like source codes.
                value['codes'] = {**value['codes'], **studio_pages.KIT_CODES}
            self._cache[key] = value
        return self._cache[key]

    def model(self, site_id: str = SITE) -> dict:
        return self._load(site_id, 'model.json')

    def pristine(self, site_id: str = SITE) -> dict:
        return self._load(site_id, 'pristine.json')

    def capture(self, site_id: str, route: str) -> dict:
        data = self._load(site_id, 'capture.json')
        if route not in data:
            raise KeyError('Unknown route')
        return data[route]

    def verify_source(self, site_id: str = SITE) -> list[str]:
        """Re-verify every imported file from Git objects (the build does this again itself)."""
        pristine = self.pristine(site_id)
        repo = self.repository(site_id)
        problems = []
        listing = subprocess.run(['git', '-C', str(repo), 'ls-tree', '-r', self.commit], capture_output=True, text=True, timeout=30)
        if listing.returncode:
            return [f'Pinned live source {self.commit[:12]} is not in the registered repository.']
        objects = {line.split('\t')[1]: line.split()[2] for line in listing.stdout.strip().splitlines()}
        batch = subprocess.run(['git', '-C', str(repo), 'cat-file', '--batch'], input='\n'.join(objects[f] for f in pristine['files'] if f in objects).encode() + b'\n',
                               capture_output=True, timeout=60).stdout
        offset, files = 0, [f for f in pristine['files'] if f in objects]
        for name in files:
            nl = batch.index(b'\n', offset)
            size = int(batch[offset:nl].split()[2])
            if sha(batch[nl + 1:nl + 1 + size]) != pristine['files'][name]:
                problems.append(f'{name} changed at {self.commit[:12]}')
            offset = nl + 1 + size + 1
        missing = set(pristine['files']) - set(files)
        problems.extend(f'{m} is missing' for m in sorted(missing))
        return problems

    # -- versions ---------------------------------------------------------------------------
    def _ledger(self, site_id):
        return self.folder(site_id) / 'versions' / 'ledger.jsonl'

    def versions(self, site_id: str = SITE) -> list[dict]:
        path = self._ledger(site_id)
        if not path.is_file():
            return []
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        return rows

    def ledger_intact(self, site_id: str = SITE) -> bool:
        previous = None
        for row in self.versions(site_id):
            body = {k: v for k, v in row.items() if k != 'sha256'}
            if row.get('prev_sha256') != (previous['sha256'] if previous else None) or sha(body) != row.get('sha256'):
                return False
            previous = row
        return True

    def latest(self, site_id: str = SITE) -> int:
        rows = self.versions(site_id)
        return rows[-1]['version'] if rows else 0

    def _blob(self, site_id, digest):
        path = self.folder(site_id) / 'versions' / 'blobs' / f'{digest}.json'
        raw = path.read_bytes()
        if sha(raw) != digest:
            raise LiveError(f'Stored document {digest[:12]} is corrupted.')
        return json.loads(raw)

    def _put_blob(self, site_id, tree) -> str:
        raw = json.dumps(tree, sort_keys=True, separators=(',', ':')).encode()
        digest = sha(raw)
        path = self.folder(site_id) / 'versions' / 'blobs' / f'{digest}.json'
        if not path.is_file():
            _atomic(path, None, raw)
        return digest

    def version(self, site_id: str, number: int) -> dict:
        row = next((r for r in self.versions(site_id) if r['version'] == number), None)
        if not row:
            raise KeyError('Unknown version')
        return row

    def documents(self, site_id: str, number: int, migrate: bool = False) -> dict:
        """The full document set of a version (0 = the imported live source). A version made from an
        earlier pinned commit is refused, or with migrate=True carried onto the current pin."""
        model = self.model(site_id)
        docs = {doc_id: d['tree'] for doc_id, d in model['docs'].items()}
        if number:
            row = self.version(site_id, number)
            if row['commit'] != self.commit:
                if not migrate:
                    raise LiveError(f'Version {number} was made from {row["commit"][:12]}; the Studio is pinned to {self.commit[:12]}. Restore it to carry it over.')
                old = json.loads((self.snapshot_dir(site_id, row['commit']) / 'model.json').read_text())
                at = {doc_id: d['tree'] for doc_id, d in old['docs'].items()}
                created = {}
                for doc_id, digest in row['docs'].items():
                    (at if doc_id in old['docs'] else created)[doc_id] = self._blob(site_id, digest)
                moved = self.migrate_docs(site_id, row['commit'], at)
                docs.update(moved['docs'])
                docs.update(created)
                self.last_migration = moved['report']
                return docs
            for doc_id, digest in row['docs'].items():
                docs[doc_id] = self._blob(site_id, digest)
        return docs

    def code(self, site_id: str, number: int) -> dict:
        """The code layer (website_studio_code) of a version; {} for the import and older versions."""
        if not number:
            return {}
        digest = self.version(site_id, number).get('code')
        return self._blob(site_id, digest) if digest else {}

    def link_scope(self, site_id: str, pages: dict | None = None) -> tuple[set, set]:
        """The routes and public files internal links may point at (owner-created pages included)."""
        routes = {p['route'] for p in self.model(site_id)['pages'] if p['route'] != '/_not-found'} | studio_pages.routes(pages)
        try:
            files = {a['path'] for a in self.site_assets(site_id)}
        except (KeyError, OSError):
            files = set()
        return routes, files

    def validate_code(self, site_id: str, code, docs: dict) -> dict:
        """Check a code layer against the full document set it will be built with."""
        try:
            code = studio_code.check(code)
        except studio_code.CodeError as exc:
            raise LiveError(str(exc)) from exc
        imported = self.model(site_id)['docs']
        if code.get('pages'):
            problem = self.pages_problem(site_id, code['pages'])
            if problem:
                raise LiveError(problem)
        for path in (code.get('files') or {}):
            if path in imported and sha(docs.get(path, imported[path]['tree'])) != sha(imported[path]['tree']):
                raise LiveError(f'{path} has visual edits; a source edit of the same file would discard them. Remove one of the two.')
        missing = [a for a in studio_code.css_assets(code.get('css')) if a not in self.assets(site_id)]
        if missing:
            raise LiveError(f'Unknown Studio asset {missing[0][:12]} in CSS; add it to the asset library first.')
        return code

    def site_scope(self, site_id: str) -> dict:
        """Routes and blog posts of the imported site (new pages and posts must not collide)."""
        model = self.model(site_id)
        blog = (model['docs'].get('content/blog.ts') or {}).get('tree') or []
        return {'routes': sorted(p['route'] for p in model['pages']), 'posts': sorted(r['props']['key'] for r in blog if r.get('type') == 'Rec')}

    def pages_problem(self, site_id: str, pages: dict) -> str | None:
        scope = self.site_scope(site_id)
        try:
            site_files = {a['path'] for a in self.site_assets(site_id)}
        except (KeyError, OSError):
            site_files = set()
        return studio_pages.context_problem(pages, routes=set(scope['routes']), data_keys=set(scope['posts']), post_keys=set(scope['posts']),
                                            asset_ids=set(self.assets(site_id)), site_files=site_files,
                                            doc_keys={d.get('key') or studio_pages.doc_key(i) for i, d in self.model(site_id)['docs'].items()})

    def component_files(self, site_id: str) -> set:
        """Component files a Comp element may place (pinned commit; cached per commit)."""
        key = ('components', self.commit)
        if key not in self._cache:
            listing = subprocess.run(['git', '-C', str(self.repository(site_id)), 'ls-tree', '-r', '--name-only', self.commit, 'components/'],
                                     capture_output=True, text=True, timeout=30)
            self._cache[key] = set(listing.stdout.split()) if listing.returncode == 0 else set()
        return self._cache[key]

    PROPLESS = re.compile(r'export\s+default\s+function\s+[A-Z][A-Za-z0-9]*\s*\(\s*\)')

    def placeable_components(self, site_id: str, code: dict | None = None) -> list[str]:
        """Components that can be placed without props: the default export takes none (a component that
        needs props, such as JsonLd, would fail the site's type check). Pinned files are read from Git
        objects (cached per commit); Code-mode files from the code layer."""
        key = ('placeable', self.commit)
        if key not in self._cache:
            out = []
            for path in sorted(self.component_files(site_id)):
                if not COMP_FILE.fullmatch(path) or path in self.model(site_id)['docs']:
                    continue
                blob = subprocess.run(['git', '-C', str(self.repository(site_id)), 'show', f'{self.commit}:{path}'], capture_output=True, timeout=20)
                if blob.returncode == 0 and self.PROPLESS.search(blob.stdout.decode('utf-8', 'replace')):
                    out.append(path)
            self._cache[key] = out
        extra = [f for f, text in ((code or {}).get('files') or {}).items() if COMP_FILE.fullmatch(f) and isinstance(text, str) and self.PROPLESS.search(text)]
        return sorted(set(self._cache[key]) | set(extra))

    def validate_documents(self, site_id: str, docs: dict, code: dict | None = None) -> dict:
        """Validate the full document set. ``code`` (the version's code layer) declares owner-created
        documents (pages) and source files a placed component may come from."""
        model, pristine = self.model(site_id), self.pristine(site_id)
        pages = (code or {}).get('pages') or {}
        known = set(model['docs']) | set(pages)
        routes, site_files = self.link_scope(site_id, pages)
        components = self.component_files(site_id) | {f for f in ((code or {}).get('files') or {}) if f.startswith('components/')}
        stats, ids, assets = {}, set(), set()
        for doc_id, tree in docs.items():
            if doc_id not in known:
                raise LiveError(f'Unknown document {str(doc_id)[:60]}.')
            if len(json.dumps(tree)) > MAX_DOC_BYTES:
                raise LiveError(f'{doc_id} exceeds the document size limit.')
            result = validate_tree(tree, pristine['codes'], doc_id, known)
            for item in _walk(tree):
                p = item['props']
                if item['type'] == 'Comp' and p['file'] not in components:
                    raise LiveError(f'Component file {p["file"]} does not exist (write it in Code mode first).')
                if item['type'] == 'Comp' and model['docs'].get(doc_id, {}).get('kind') == 'html':
                    raise LiveError('React components cannot be placed in a static HTML page.')
                if item['type'] == 'El' and p.get('tag') in ('a', 'Link') and isinstance((p.get('a') or {}).get('href'), str):
                    problem = studio_code.link_problem(p['a']['href'], routes, site_files, url_problem)
                    if problem:
                        raise LiveError(f'Link {p["id"]}: {problem}')
            meta = model['docs'].get(doc_id) or {'kind': 'created'}
            if meta.get('kind') == 'data':
                # Records keep their identity and order; only declared string fields change.
                imported = [(r['props']['id'], r['props']['key']) for r in meta['tree']]
                if [(r.get('type'), r['props'].get('id'), r['props'].get('key')) for r in tree] != [('Rec', i, k) for i, k in imported]:
                    raise LiveError(f'{doc_id}: content records cannot be added, removed or reordered in the editor.')
                allowed = set(meta['data']['fields'])
                for r in tree:
                    extra = set(r['props']['f']) - allowed
                    if extra:
                        raise LiveError(f'{doc_id}: {sorted(extra)[0]} is not an editable field.')
            elif any(item.get('type') == 'Rec' for item in tree):
                raise LiveError('Content records belong to their data module.')
            clash = ids & result['ids']
            if clash:
                raise LiveError(f'Element {sorted(clash)[0]} appears in two documents.')
            ids |= result['ids']
            assets |= result['assets']
            stats[doc_id] = result['nodes']
        assets |= set(studio_pages.assets(pages))
        library = self.assets(site_id)
        missing = [a for a in assets if a not in library]
        if missing:
            raise LiveError(f'Unknown Studio asset {missing[0][:12]}; add it to the asset library first.')
        return {'nodes': stats, 'assets': sorted(assets)}

    def save(self, site_id: str, docs: dict, base_version: int, actor: str, note: str = '', origin: str = 'manual',
             restored_from: int | None = None, code: dict | None = None, proposals: list | None = None) -> dict:
        """Append a version. ``code`` None keeps the latest version's code layer; {} clears it."""
        if origin not in ('manual', 'restore', 'checkpoint', 'migration'):
            raise LiveError('Unknown save origin.')
        if not isinstance(docs, dict) or len(docs) > 200:
            raise LiveError('Invalid documents.')
        with self._guard(site_id):
            latest = self.latest(site_id)
            if base_version != latest:
                raise StaleError(f'Version v{latest} is newer than your base v{base_version}. Reload the latest version (your autosaved work is kept) before saving.')
            if len(self.versions(site_id)) >= MAX_VERSIONS:
                raise LiveError('Version history is full.')
            # After the pin moved, the latest version belongs to the previous commit: start from the new import.
            full = self.documents(site_id, latest if latest and self.version(site_id, latest)['commit'] == self.commit else 0)
            full.update(docs)
            previous_code = self.code(site_id, latest) if latest else {}
            wanted = studio_code.normalize(previous_code if code is None else code)
            imported = {doc_id: d['tree'] for doc_id, d in self.model(site_id)['docs'].items()}
            for doc_id in docs:
                if doc_id not in imported and doc_id not in (wanted.get('pages') or {}):
                    raise LiveError(f'Unknown document {str(doc_id)[:60]}.')
            # Owner-created documents exist only while their page does (a removed page leaves nothing).
            full = {doc_id: tree for doc_id, tree in full.items() if doc_id in imported or doc_id in (wanted.get('pages') or {})}
            missing = [doc_id for doc_id in (wanted.get('pages') or {}) if doc_id not in full]
            if missing:
                raise LiveError(f'{missing[0]} has no content; create the page again.')
            layer = self.validate_code(site_id, wanted, full)
            checked = self.validate_documents(site_id, full, layer)
            code_digest = self._put_blob(site_id, layer) if layer else None
            # Store only documents that differ from the import; unchanged ones resolve to the import.
            stored = {doc_id: self._put_blob(site_id, tree) for doc_id, tree in sorted(full.items()) if doc_id not in imported or sha(tree) != sha(imported[doc_id])}
            before = self.version(site_id, latest)['docs'] if latest else {}
            changed = sorted((set(stored) ^ set(before)) | {d for d in stored if before.get(d) != stored[d]})
            if (self.version(site_id, latest).get('code') if latest else None) != code_digest:
                changed.append('code-layer')
            rows = self.versions(site_id)
            previous = rows[-1] if rows else None
            entry = {'version': latest + 1, 'at': _iso(), 'actor': actor, 'origin': origin, 'note': str(note)[:500], 'base_version': base_version,
                     'restored_from': restored_from, 'commit': self.commit, 'schema': SCHEMA, 'docs': stored, 'changed': changed,
                     'nodes': sum(checked['nodes'].values()), 'assets': sorted(set(checked['assets']) | set(studio_code.css_assets(layer.get('css')))),
                     'model_calls': 0, 'code': code_digest, 'prev_sha256': previous['sha256'] if previous else None}
            if proposals:
                entry['proposals'] = [str(p)[:40] for p in proposals[:50]]
            entry['sha256'] = sha(entry)
            path = self._ledger(site_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('a') as handle:
                handle.write(json.dumps(entry, sort_keys=True) + '\n')
                handle.flush()
                os.fsync(handle.fileno())
            self.clear_autosave(site_id, upto=entry['version'])
            return entry

    def restore(self, site_id: str, number: int, base_version: int, actor: str) -> dict:
        self.last_migration = None
        docs = self.documents(site_id, number, migrate=True)
        note = f'Restored v{number}' if number else 'Restored the imported live website'
        if self.last_migration is not None:
            note += f" (carried onto {self.commit[:8]}: {len(self.last_migration['conflicts'])} conflicts)"
        entry = self.save(site_id, docs, base_version, actor, note=note, origin='restore', restored_from=number, code=self.code(site_id, number))
        return {**entry, 'migration': self.last_migration} if self.last_migration is not None else entry

    # -- autosave (crash/reopen) ------------------------------------------------------------
    def autosave(self, site_id: str, body: dict, actor: str) -> dict:
        docs = body.get('docs') or {}
        if not isinstance(docs, dict) or len(json.dumps(docs)) > 12_000_000:
            raise LiveError('Autosave too large.')
        code = body.get('code')
        if code is not None:
            try:
                code = studio_code.check(code)
            except studio_code.CodeError as exc:
                raise LiveError(str(exc)) from exc
        pristine, known = self.pristine(site_id), set(self.model(site_id)['docs']) | set((code or {}).get('pages') or {})
        for doc_id, tree in docs.items():
            if doc_id not in known:
                raise LiveError('Unknown document.')
            validate_tree(tree, pristine['codes'], doc_id, known)
        record = {'at': _iso(), 'actor': actor, 'base_version': int(body.get('base_version') or 0), 'page': str(body.get('page') or '')[:120],
                  'docs': docs, 'commit': self.commit, 'sha256': sha(docs), **({'code': code} if code is not None else {})}
        _atomic(self.folder(site_id) / 'autosave.json', record)
        return {k: v for k, v in record.items() if k != 'docs'}

    def get_autosave(self, site_id: str = SITE) -> dict | None:
        path = self.folder(site_id) / 'autosave.json'
        if not path.is_file():
            return None
        record = json.loads(path.read_text())
        return record if record.get('commit') == self.commit else None

    def clear_autosave(self, site_id: str, upto: int | None = None) -> None:
        path = self.folder(site_id) / 'autosave.json'
        if path.is_file():
            path.unlink()

    # -- assets -----------------------------------------------------------------------------
    TYPES = {b'\x89PNG\r\n\x1a\n': ('.png', 'image/png'), b'\xff\xd8\xff': ('.jpg', 'image/jpeg'), b'GIF87a': ('.gif', 'image/gif'), b'GIF89a': ('.gif', 'image/gif')}

    def _sniff(self, data: bytes) -> tuple[str, str]:
        for magic, kind in self.TYPES.items():
            if data.startswith(magic):
                return kind
        if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
            return '.webp', 'image/webp'
        if data[4:12] in (b'ftypavif', b'ftypavis'):
            return '.avif', 'image/avif'
        if data[4:8] == b'ftyp' and data[8:12] in (b'isom', b'mp42', b'mp41', b'avc1'):
            return '.mp4', 'video/mp4'
        if data[:4] == b'\x1aE\xdf\xa3':
            return '.webm', 'video/webm'
        head = data[:2048].decode('utf-8', 'ignore').lstrip().lower()
        if head.startswith('<svg') or (head.startswith('<?xml') and '<svg' in head):
            text = data.decode('utf-8', 'strict').lower()
            if re.search(r'<script|<foreignobject|\son[a-z]+\s*=|javascript:|<iframe|<embed|<object|(href|src)\s*=\s*["\']\s*(https?:|//|data:)', text):
                raise LiveError('SVG contains scripts, event handlers or external references.')
            return '.svg', 'image/svg+xml'
        raise LiveError('Unsupported file type (PNG, JPEG, GIF, WebP, AVIF, SVG, MP4 or WebM only).')

    def _asset_index(self, site_id):
        path = self.folder(site_id) / 'assets' / 'index.json'
        return json.loads(path.read_text()) if path.is_file() else {}

    def assets(self, site_id: str = SITE) -> dict:
        return self._asset_index(site_id)

    def asset_file(self, site_id: str, digest: str) -> Path:
        index = self._asset_index(site_id)
        if not re.fullmatch(r'[a-f0-9]{64}', digest or '') or digest not in index:
            raise KeyError('Unknown asset')
        path = self.folder(site_id) / 'assets' / (digest + index[digest]['ext'])
        if sha(path.read_bytes()) != digest:
            raise LiveError('Stored asset is corrupted.')
        return path

    def add_asset(self, site_id: str, data: bytes, name: str, actor: str, provenance: dict) -> dict:
        if not data or len(data) > MAX_ASSET_BYTES:
            raise LiveError('Files must be 1 byte to 12 MB.')
        ext, mime = self._sniff(data)
        digest = sha(data)
        with self._guard(site_id):
            index = self._asset_index(site_id)
            if digest not in index:
                _atomic(self.folder(site_id) / 'assets' / (digest + ext), None, data)
                index[digest] = {'id': digest, 'ref': f'asset:{digest}', 'name': re.sub(r'[^\w .()-]', '_', name)[:120] or 'file' + ext, 'ext': ext,
                                 'type': mime, 'bytes': len(data), 'added_at': _iso(), 'added_by': actor, 'provenance': provenance}
                _atomic(self.folder(site_id) / 'assets' / 'index.json', index)
            return index[digest]

    def derive_asset(self, site_id: str, data: bytes, name: str, actor: str, parent: str, edits: dict) -> dict:
        """An image edited in the Studio (crop, resize, rotate, format; done in the owner's browser) stored
        as a new asset whose provenance names the asset it was made from. The original is kept."""
        index = self._asset_index(site_id)
        if parent not in index or not index[parent]['type'].startswith('image/'):
            raise LiveError('Edit an image from the Studio library.')
        if not isinstance(edits, dict) or len(json.dumps(edits)) > 2000:
            raise LiveError('Invalid edit description.')
        ext, mime = self._sniff(data)
        if not mime.startswith('image/') or mime == 'image/svg+xml':
            raise LiveError('An edited image is PNG, JPEG or WebP.')
        return self.add_asset(site_id, data, name, actor, {'kind': 'studio_edit', 'from': parent, 'from_name': index[parent]['name'], 'edits': edits,
                                                          'edited_at': _iso(), 'by': actor, 'tool': 'Studio image editor (browser canvas)'})

    # -- reusable sections ---------------------------------------------------------------------
    def sections(self, site_id: str = SITE) -> list[dict]:
        path = self.folder(site_id) / 'sections.json'
        return json.loads(path.read_text()) if path.is_file() else []

    def save_section(self, site_id: str, name: str, node: dict, actor: str) -> dict:
        """A reusable section: literal elements only (no source references), inserted with fresh ids."""
        if not isinstance(name, str) or not name.strip() or len(name) > 80 or re.search(r'[<>]', name):
            raise LiveError('Name the section (up to 80 characters).')
        if not isinstance(node, dict):
            raise LiveError('Save one element (with its children).')
        for item in _walk([node]):
            if item.get('type') not in ('El', 'Txt', 'Comp') or (item.get('props') or {}).get('x'):
                raise LiveError('Only elements with their own content can be reused; elements that use page code stay on their page.')
        validate_tree([node], {}, 'sections', None, max_nodes=4000)
        with self._guard(site_id):
            rows = self.sections(site_id)
            if len(rows) >= 200:
                raise LiveError('The section library is full.')
            row = {'id': 'sec_' + sha({'n': name, 't': node, 'at': _iso()})[:12], 'name': name.strip(), 'node': node, 'saved_at': _iso(), 'by': actor}
            _atomic(self.folder(site_id) / 'sections.json', rows + [row])
            return row

    def drive_candidates(self, site_id: str = SITE) -> list[dict]:
        """Drive files the owner has approved for the website: registered brand assets and the selected asset folder."""
        site = self.sources.site(site_id)
        status = self.sources.drive_status(site)
        out = []
        for item in site['drive'].get('brand_assets', []):
            meta = status.get('files', {}).get(item['id'], {})
            out.append({'id': item['id'], 'name': meta.get('name') or item['label'], 'label': item['label'], 'mimeType': meta.get('mimeType'),
                        'modifiedTime': meta.get('modifiedTime'), 'approval': 'registered brand asset'})
        selection = (status.get('asset_folder') or {}).get('id')
        for folder_id, folder in status.get('folders', {}).items():
            if folder_id != selection:
                continue
            for child in folder.get('children', []):
                if (child.get('mimeType') or '').startswith(('image/', 'video/')):
                    out.append({**child, 'label': child.get('name'), 'approval': f"selected website asset folder ({folder.get('name')})"})
        return out

    def import_drive(self, site_id: str, file_id: str, actor: str) -> dict:
        candidate = next((c for c in self.drive_candidates(site_id) if c['id'] == file_id), None)
        if not candidate:
            raise LiveError('That Drive file is not an approved website asset (register it, or select its folder in Sources & Branding).')
        api = self.sources.drive_factory()
        meta = api.metadata(file_id)
        data = api.get('files/' + file_id, alt='media', supportsAllDrives='true')
        if not isinstance(data, (bytes, bytearray)):
            raise LiveError('Drive returned no file content.')
        provenance = {'kind': 'google_drive', 'file_id': file_id, 'name': meta.get('name'), 'mimeType': meta.get('mimeType'),
                      'modifiedTime': meta.get('modifiedTime'), 'version': meta.get('version'), 'webViewLink': meta.get('webViewLink'),
                      'approval': candidate['approval'], 'imported_at': _iso(), 'reader': 'TTROS Drive reader (drive.readonly)'}
        return self.add_asset(site_id, bytes(data), meta.get('name') or file_id, actor, provenance)

    def site_assets(self, site_id: str = SITE) -> list[dict]:
        return self._load(site_id, 'assets.json')['assets']

    def site_asset_bytes(self, site_id: str, path: str) -> tuple[bytes, str]:
        entry = next((a for a in self.site_assets(site_id) if a['path'] == path), None)
        if not entry:
            raise KeyError('Unknown site file')
        blob = subprocess.run(['git', '-C', str(self.repository(site_id)), 'cat-file', 'blob', entry['object']], capture_output=True, timeout=20)
        if blob.returncode:
            raise KeyError('Site file missing from Git objects')
        ext = Path(path).suffix.lower()
        mime = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp', '.avif': 'image/avif',
                '.svg': 'image/svg+xml', '.ico': 'image/x-icon', '.pdf': 'application/pdf', '.mp4': 'video/mp4', '.webm': 'video/webm'}.get(ext, 'application/octet-stream')
        return blob.stdout, mime

    # -- source files (Code mode) -----------------------------------------------------------
    def source_files(self, site_id: str = SITE) -> list[dict]:
        """Editable code files at the pinned commit (studio_code rules), visual documents marked."""
        listing = subprocess.run(['git', '-C', str(self.repository(site_id)), 'ls-tree', '-r', '-l', self.commit], capture_output=True, text=True, timeout=30)
        if listing.returncode:
            raise LiveError(f'Pinned live source {self.commit[:12]} is not in the registered repository.')
        docs = self.model(site_id)['docs']
        out = []
        for line in listing.stdout.splitlines():
            meta, path = line.split('\t', 1)
            if studio_code.file_problem(path, None):
                continue
            out.append({'path': path, 'bytes': int(meta.split()[3]), 'visual': path in docs,
                        'kind': (docs.get(path) or {}).get('kind') or 'code'})
        placeable = set(self.placeable_components(site_id))
        for row in out:
            if row['path'].startswith('components/'):
                row['placeable'] = row['path'] in placeable
        return out

    def source_file(self, site_id: str, path: str) -> dict:
        problem = studio_code.file_problem(path, None)
        if problem:
            raise LiveError(problem)
        blob = subprocess.run(['git', '-C', str(self.repository(site_id)), 'show', f'{self.commit}:{path}'], capture_output=True, timeout=20)
        if blob.returncode:
            return {'path': path, 'text': None, 'exists': False, 'commit': self.commit}
        return {'path': path, 'text': blob.stdout.decode('utf-8', 'replace'), 'exists': True, 'commit': self.commit}

    def source_diff(self, site_id: str, docs: dict, code: dict, before: dict | None = None) -> dict:
        """Generate the real website source for docs + code layer (and optionally a 'before' state) and
        return the changed files with a unified diff. Runs the same generator as the build; no build."""
        full = {doc_id: d['tree'] for doc_id, d in self.model(site_id)['docs'].items()}
        pages = (code or {}).get('pages') or {}
        after_docs = {k: v for k, v in {**full, **docs}.items() if k in full or k in pages}
        self.validate_documents(site_id, after_docs, code)
        layer = self.validate_code(site_id, code, after_docs)
        folder = self.folder(site_id) / 'diffs'
        folder.mkdir(parents=True, exist_ok=True)
        stamp = hashlib.sha256(f'{time.time()}{threading.get_ident()}'.encode()).hexdigest()[:16]
        request = {'repository': str(self.repository(site_id)), 'commit': self.commit, 'snapshot': str(self.snapshot_dir(site_id)),
                   'after': {'docs': docs, 'code': layer}, 'before': before, 'output': str(folder / f'{stamp}.json')}
        (folder / f'{stamp}.request.json').write_text(json.dumps(request))
        try:
            proc = subprocess.run([self.node, str(SCRIPTS / 'source-diff.mjs'), str(folder / f'{stamp}.request.json')], capture_output=True, text=True,
                                  timeout=180, env={'PATH': os.environ['PATH']})
            if proc.returncode:
                raise LiveError('Source generation failed: ' + (proc.stderr or proc.stdout)[-500:])
            return json.loads((folder / f'{stamp}.json').read_text())
        finally:
            for f in (folder / f'{stamp}.request.json', folder / f'{stamp}.json'):
                f.unlink(missing_ok=True)

    # -- release integration -----------------------------------------------------------------
    def record_sha(self, site_id: str, number: int) -> str:
        return sha(self.version(site_id, number))

    def draft_problems(self, site_id: str, versions: dict) -> list[str]:
        problems = []
        saved = versions.get('live')
        if not saved:
            return ['Build carries no live-editor version']
        try:
            row = self.version(site_id, saved['version'])
        except KeyError:
            return [f'live v{saved["version"]} is missing']
        if sha(row) != saved['record_sha256'] or row['sha256'] != saved['fingerprint']:
            problems.append(f'live v{saved["version"]} changed after it was built')
        if not self.ledger_intact(site_id):
            problems.append('live version ledger is not intact')
        return problems


# ------------------------------------------------------------------------------ builds
class LiveBuilds(studio_builds.Builds):
    """The existing isolated build/serve/expiry/release-evidence pipeline, fed by the live adapter."""
    script = SCRIPTS / 'build-live.mjs'

    def __init__(self, sources, live: LiveStudio, root=None):
        super().__init__(sources, root)
        self.live = live

    @property
    def pinned(self) -> str:
        return self.live.commit

    def start_live(self, site_id: str, number: int, purpose: str, frame_ancestors: list[str] | None = None) -> dict:
        if purpose not in ('preview', 'release'):
            raise ValueError('Unknown build purpose.')
        if number < 1:
            raise ValueError('Save a version before building it.')
        row = self.live.version(site_id, number)
        docs = self.live.documents(site_id, number)
        library = self.live.assets(site_id)
        if not self.lock.acquire(blocking=False):
            raise ValueError('Another Studio build is running; wait for it to finish.')
        import uuid
        ident = uuid.uuid4().hex
        try:
            folder = self.root / ident
            folder.mkdir(parents=True)
            versions = {'live': {'version': number, 'fingerprint': row['sha256'], 'schema_version': SCHEMA, 'record_sha256': sha(row), 'at': row['at']}}
            assets = {a: str(self.live.asset_file(site_id, a)) for a in row.get('assets', []) if a in library}
            code = self.live.code(site_id, number)
            request = {'vercel': self._vercel_build(site_id) if purpose == 'release' else None,
                       'repository': str(self.live.repository(site_id)), 'commit': self.live.commit, 'snapshot': str(self.live.snapshot_dir(site_id)),
                       'version': number, 'docs': docs, 'code': code, 'assets': assets, 'purpose': purpose, 'output': str(folder),
                       'npmCache': str(self.live.folder(site_id) / 'npm-cache'), 'frameAncestors': frame_ancestors or ['http://127.0.0.1:3010', 'http://localhost:3010']}
            (folder / 'request.json').write_text(json.dumps(request))
            row_out = {'id': ident, 'site': site_id, 'adapter': 'live', 'status': 'building', 'base': self.live.commit, 'versions': versions, 'purpose': purpose,
                       'started_at': time.time(), 'contrast': {}, 'contrast_acknowledged': True, 'modelCalls': 0, 'publication': 'held'}
            self.rows[ident] = row_out
            threading.Thread(target=self._build, args=(folder, row_out), daemon=True).start()
            return dict(row_out)
        except Exception:
            self.lock.release()
            raise

    def _vercel_build(self, site_id: str) -> dict | None:
        """Release builds are Vercel production builds when the site's Vercel target is ready."""
        import website_studio_release_targets as targets
        import website_studio_vercel
        for target in targets.load_targets(self.sources.site(site_id)).values():
            if target.provider == 'vercel' and target.readiness()['available']:
                return website_studio_vercel.build_request(target.client, target.domains[0])
        return None

    def health_routes(self, row: dict, result: dict) -> list[str]:
        # Static HTML guides carry no Next build id; they are checked by the functional checks instead.
        pages = [p['route'] for p in self.live.model(row['site'])['pages'] if p['route'] != '/_not-found' and not p.get('css')]
        try:
            created = self.live.code(row['site'], row['versions']['live']['version']).get('pages') or {}
        except (KeyError, LiveError, TypeError):
            created = {}
        return pages + sorted(studio_pages.routes(created))


# Request bodies (module level so FastAPI resolves them under postponed annotations).
class SaveBody(BaseModel):
    base_version: int = Field(ge=0)
    docs: dict = Field(default_factory=dict)
    note: str = Field(default='', max_length=500)
    code: dict | None = None
    proposals: list[str] = Field(default_factory=list, max_length=50)

class RebaseBody(BaseModel):
    commit: str


class RestoreBody(BaseModel):
    base_version: int = Field(ge=0)

class AutosaveBody(BaseModel):
    base_version: int = Field(ge=0)
    docs: dict = Field(default_factory=dict)
    page: str = Field(default='', max_length=120)
    code: dict | None = None

class UploadBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    data: str = Field(min_length=1, max_length=17_000_000)

class DeriveBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    data: str = Field(min_length=1, max_length=17_000_000)
    parent: str = Field(pattern=r'^[a-f0-9]{64}$')
    edits: dict = Field(default_factory=dict)

class SectionBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    node: dict

class DriveBody(BaseModel):
    file_id: str = Field(min_length=10, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')

class DiffBody(BaseModel):
    docs: dict = Field(default_factory=dict)
    code: dict | None = None
    before: dict | None = None


class BuildBody(BaseModel):
    version: int = Field(gt=0)
    purpose: str = Field(default='preview', pattern='^(preview|release)$')


def router(sources, identity, live: LiveStudio | None = None):
    live = live or LiveStudio(sources)
    builds = LiveBuilds(sources, live)
    api = APIRouter(prefix='/api/website/studio/sites/{site_id}/live', dependencies=[Depends(private_response)])

    def who(request: Request, mutate: bool = False) -> dict:
        principal = identity.principal(request)
        if principal.get('workspace') != 'ttros-operator' and principal.get('method') != 'local_preview':
            raise HTTPException(403, 'The live website editor requires the TTROS operator')
        if mutate:
            identity.csrf(request)
        return principal

    def actor(principal):
        return 'owner:' + (principal.get('email') or principal.get('user') or 'local')[:80]

    def guard(fn):
        try:
            return fn()
        except StaleError as exc:
            raise HTTPException(409, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, str(exc).strip("'")) from exc
        except (LiveError, ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @api.get('')
    def status(site_id: str, request: Request):
        who(request)
        return guard(lambda: {**live.status(site_id), 'latest': live.latest(site_id) if live.status(site_id)['ready'] else 0})

    @api.post('/import', status_code=202)
    def run_import(site_id: str, request: Request):
        who(request, True)
        return guard(lambda: live.start_import(site_id))

    @api.post('/rebase')
    def rebase(site_id: str, body: RebaseBody, request: Request):
        p = who(request, True)
        return guard(lambda: live.start_import(site_id, rebase_to=body.commit, actor=actor(p)))

    @api.get('/model')
    def model(site_id: str, request: Request):
        who(request)
        return guard(lambda: live.model(site_id))

    @api.get('/coverage')
    def coverage(site_id: str, request: Request):
        who(request)
        return guard(lambda: {'routes': live._load(site_id, 'coverage.json'), 'parity': live._load(site_id, 'parity.json')})

    @api.get('/capture')
    def capture(site_id: str, route: str, request: Request):
        who(request)
        return guard(lambda: live.capture(site_id, route))

    @api.get('/canvas/{name:path}')
    def canvas(site_id: str, name: str, request: Request):
        who(request)
        base = (live.snapshot_dir(site_id) / 'canvas').resolve()
        path = (base / name).resolve()
        if not str(path).startswith(str(base) + os.sep) or not path.is_file():
            raise HTTPException(404, 'Unknown canvas file')
        return FileResponse(path, headers={'Cache-Control': 'private, max-age=3600'})

    @api.get('/site-file')
    def site_file(site_id: str, path: str, request: Request):
        who(request)
        data, mime = guard(lambda: live.site_asset_bytes(site_id, path))
        headers = {'Cache-Control': 'private, max-age=3600', 'X-Content-Type-Options': 'nosniff'}
        if mime == 'image/svg+xml':
            headers['Content-Security-Policy'] = "default-src 'none'; style-src 'unsafe-inline'"
        return Response(data, media_type=mime, headers=headers)

    @api.get('/versions')
    def versions(site_id: str, request: Request):
        who(request)
        return guard(lambda: {'latest': live.latest(site_id), 'intact': live.ledger_intact(site_id),
                              'versions': [{k: v for k, v in r.items() if k != 'docs'} | {'documents': sorted(r['docs'])} for r in live.versions(site_id)]})

    @api.get('/versions/{number}')
    def version(site_id: str, number: int, request: Request):
        who(request)
        # Versions made from an earlier pinned commit are shown carried onto the current one.
        return guard(lambda: {'version': number, 'meta': live.version(site_id, number) if number else None, 'docs': live.documents(site_id, number, migrate=True),
                              'code': live.code(site_id, number), 'commit': live.commit})

    @api.post('/versions')
    def save(site_id: str, body: SaveBody, request: Request):
        p = who(request, True)
        return guard(lambda: live.save(site_id, body.docs, body.base_version, actor(p), body.note, code=body.code, proposals=body.proposals))

    @api.post('/versions/{number}/restore')
    def restore(site_id: str, number: int, body: RestoreBody, request: Request):
        p = who(request, True)
        return guard(lambda: live.restore(site_id, number, body.base_version, actor(p)))

    @api.get('/source/files')
    def source_files(site_id: str, request: Request):
        who(request)
        return guard(lambda: {'commit': live.commit, 'files': live.source_files(site_id)})

    @api.get('/source/file')
    def source_file(site_id: str, path: str, request: Request):
        who(request)
        return guard(lambda: live.source_file(site_id, path))

    @api.post('/source/diff')
    def source_diff(site_id: str, body: DiffBody, request: Request):
        who(request, True)
        return guard(lambda: live.source_diff(site_id, body.docs, body.code or {}, body.before))

    @api.get('/autosave')
    def get_autosave(site_id: str, request: Request):
        who(request)
        return guard(lambda: {'autosave': live.get_autosave(site_id)})

    @api.put('/autosave')
    def put_autosave(site_id: str, body: AutosaveBody, request: Request):
        p = who(request, True)
        return guard(lambda: live.autosave(site_id, body.model_dump(), actor(p)))

    @api.delete('/autosave')
    def drop_autosave(site_id: str, request: Request):
        who(request, True)
        guard(lambda: live.clear_autosave(site_id))
        return {'cleared': True}

    @api.get('/assets')
    def assets(site_id: str, request: Request):
        who(request)
        return guard(lambda: {'library': sorted(live.assets(site_id).values(), key=lambda a: a['added_at'], reverse=True),
                              'site': live.site_assets(site_id), 'drive': live.drive_candidates(site_id)})

    @api.get('/assets/{digest}')
    def asset(site_id: str, digest: str, request: Request):
        who(request)
        path = guard(lambda: live.asset_file(site_id, digest))
        index = live.assets(site_id)[digest]
        headers = {'Cache-Control': 'private, max-age=86400, immutable', 'X-Content-Type-Options': 'nosniff'}
        if index['type'] == 'image/svg+xml':
            headers['Content-Security-Policy'] = "default-src 'none'; style-src 'unsafe-inline'"
        return FileResponse(path, media_type=index['type'], headers=headers)

    @api.post('/assets/upload')
    def upload(site_id: str, body: UploadBody, request: Request):
        p = who(request, True)
        try:
            data = base64.b64decode(body.data, validate=True)
        except Exception as exc:
            raise HTTPException(422, 'Invalid file encoding') from exc
        return guard(lambda: live.add_asset(site_id, data, body.name, actor(p), {'kind': 'upload', 'filename': body.name[:200], 'uploaded_at': _iso(), 'by': actor(p)}))

    @api.post('/assets/from-site')
    def from_site(site_id: str, request: Request, path: str):
        p = who(request, True)
        data, _ = guard(lambda: live.site_asset_bytes(site_id, path))
        entry = next(a for a in live.site_assets(site_id) if a['path'] == path)
        return guard(lambda: live.add_asset(site_id, data, Path(path).name, actor(p), {'kind': 'website_source', 'path': 'public' + path, 'commit': live.commit, 'git_object': entry['object']}))

    @api.post('/assets/derive')
    def derive(site_id: str, body: DeriveBody, request: Request):
        p = who(request, True)
        try:
            data = base64.b64decode(body.data, validate=True)
        except Exception as exc:
            raise HTTPException(422, 'Invalid file encoding') from exc
        return guard(lambda: live.derive_asset(site_id, data, body.name, actor(p), body.parent, body.edits))

    @api.get('/media/providers')
    def media_providers(site_id: str, request: Request):
        who(request)
        live.folder(site_id)
        # No image-generation provider route exists in TTROS: ModelPort is text-only on the existing
        # subscriptions and refuses separately billed APIs; ChatGPT consumer image generation is not
        # callable by TTROS. Manual media tools never depend on a provider.
        return {'image_generation': {'available': False, 'provider': None,
                                     'detail': 'No authorized image-generation route is configured in TTROS. Generating images needs a provider '
                                               '(for example an image API) with credentials and a spending limit approved by Casey. Upload, Drive '
                                               'import, the website\'s own images and the Studio image editor work without it.'},
                'image_editing': {'available': True, 'detail': 'Crop, resize, rotate, flip and convert in the browser; saved as a new asset with provenance.'},
                'video': {'upload': ['video/mp4', 'video/webm'], 'embed_hosts': sorted(EMBED_HOSTS)}}

    @api.get('/sections')
    def list_sections(site_id: str, request: Request):
        who(request)
        return guard(lambda: {'sections': live.sections(site_id)})

    @api.post('/sections')
    def add_section(site_id: str, body: SectionBody, request: Request):
        p = who(request, True)
        return guard(lambda: live.save_section(site_id, body.name, body.node, actor(p)))

    @api.post('/assets/from-drive')
    def from_drive(site_id: str, body: DriveBody, request: Request):
        p = who(request, True)
        return guard(lambda: live.import_drive(site_id, body.file_id, actor(p)))

    @api.post('/builds', status_code=202)
    def build(site_id: str, body: BuildBody, request: Request):
        who(request, True)
        origin = request.headers.get('origin')
        ancestors = sorted({o for o in ['http://127.0.0.1:3010', 'http://localhost:3010', origin] if o and re.fullmatch(r'https?://[a-z0-9.:-]+', o)})
        return guard(lambda: builds.start_live(site_id, body.version, body.purpose, ancestors))

    @api.get('/builds/{ident}')
    def build_status(site_id: str, ident: str, request: Request):
        who(request)
        return guard(lambda: builds.get(ident))

    @api.delete('/builds/{ident}')
    def build_stop(site_id: str, ident: str, request: Request):
        who(request, True)
        row = guard(lambda: builds.get(ident))
        if row['status'] == 'building':
            raise HTTPException(409, 'Build in progress; wait before closing the preview')
        return guard(lambda: builds.stop(ident))

    api.live = live  # type: ignore[attr-defined]
    api.builds = builds  # type: ignore[attr-defined]
    return api
