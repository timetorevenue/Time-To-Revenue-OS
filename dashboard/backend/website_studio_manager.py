"""Website Manager panel API for the live website editor.

One owner turn = one Job (aos_jobs, executor kind ``website_manager``; aos_website_manager runs it:
deterministic rules first, the Agent Forge package website.manager on its pinned model otherwise).
The Studio then validates the job's operations against the exact documents the owner is editing
(website_studio_code.apply_ops + the live store's document and code-layer validation), renders the
real source diff with the build's generator, and keeps the result as a proposal. A proposal is
applied only on accept, only if the owner's documents and the latest version are still the ones it
was made from (otherwise 409, stale); the editor applies it as one undoable history step and the
owner saves it as an ordinary version. Reject records the decision. Nothing here saves a version,
builds, publishes or writes the website checkout.
Revisit: when the op vocabulary, the Jobs executor or the live store's validation changes. Last touched: 2026-11-22.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from dashboard_identity import private_response
import ttros_site
import website_studio_code as studio_code
import website_studio_live as studio_live
import website_studio_pages as studio_pages

# The site's brand tokens (tailwind.config.ts at the pinned live source) given to the manager as context.
BRAND = {
    'colors': {'deep-ink': '#0D1418', 'exec-graphite': '#111315', 'soft-graphite': '#2B2F32', 'ivory': '#F7F3EA', 'warm-stone': '#D8D0C2',
               'mist-grey': '#ECE9E2', 'champagne': '#B89B63', 'rev-gold': '#C6A15B', 'slate-taupe': '#7A746A', 'olive-graphite': '#4E5A50',
               'signal-blue': '#52616B', 'alert-clay': '#A56C53'},
    'fonts': {'heading': 'var(--font-geist), "Helvetica Neue", sans-serif', 'body': 'var(--font-inter), "Helvetica Neue", Arial, sans-serif',
              'display': 'var(--font-inter-display), var(--font-inter), sans-serif'},
}
DOMAIN = ttros_site.DOMAIN
MAX_TURNS_SHOWN = 60
# Voice fallback for browsers without speech recognition: local faster-whisper in its own venv
# (tools/studio_transcribe.py). No network, no provider, no cost. Paths can be overridden.
STT_PYTHON = Path(os.environ.get('TTROS_STT_PYTHON') or Path.home() / '.local/share/ttros/stt-venv/bin/python')
STT_MODEL = Path(os.environ.get('TTROS_STT_MODEL') or Path.home() / '.local/share/ttros/models/whisper/base.en')
STT_SCRIPT = Path(__file__).resolve().parents[2] / 'tools' / 'studio_transcribe.py'
MAX_AUDIO_BYTES = 6_000_000
AUDIO_TYPES = {'audio/webm': '.webm', 'audio/ogg': '.ogg', 'audio/mp4': '.m4a', 'audio/mpeg': '.mp3', 'audio/wav': '.wav', 'audio/x-wav': '.wav', 'audio/aac': '.aac'}


def voice_status() -> dict:
    ok = STT_PYTHON.is_file() and (STT_MODEL / 'model.bin').is_file() and STT_SCRIPT.is_file()
    return {'browser': 'Web Speech API (Chrome, Edge, Safari) when the browser offers it',
            'server_transcription': 'available' if ok else 'unavailable',
            'engine': f'faster-whisper {STT_MODEL.name} (local CPU, no network)' if ok else None,
            'detail': None if ok else 'Local speech model not installed; type, or use your device dictation (macOS: press Fn twice; Windows: Win+H).'}


def transcribe(data: bytes, mime: str) -> dict:
    import subprocess
    import tempfile
    base = (mime or '').split(';')[0].strip().lower()
    if base not in AUDIO_TYPES:
        raise studio_live.LiveError('Unsupported audio type.')
    if not data or len(data) > MAX_AUDIO_BYTES:
        raise studio_live.LiveError('Recordings are limited to about a minute.')
    if voice_status()['server_transcription'] != 'available':
        raise studio_live.LiveError(voice_status()['detail'])
    with tempfile.TemporaryDirectory(prefix='studio-voice-') as tmp:
        path = Path(tmp) / ('clip' + AUDIO_TYPES[base])
        path.write_bytes(data)
        started = time.time()
        proc = subprocess.run([str(STT_PYTHON), '-I', str(STT_SCRIPT), str(path), str(STT_MODEL)], capture_output=True, text=True, timeout=90,
                              env={'PATH': os.environ.get('PATH', ''), 'HOME': tmp, 'HF_HUB_OFFLINE': '1', 'OMP_NUM_THREADS': '4'})
        if proc.returncode:
            raise studio_live.LiveError('Transcription failed: ' + (proc.stderr or proc.stdout)[-300:])
        out = json.loads(proc.stdout.strip().splitlines()[-1])
    return {'text': out['text'][:4000], 'engine': out['engine'], 'audio_seconds': out['duration'], 'seconds': round(time.time() - started, 2), 'model_calls': 0}


def _iso() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def state_sha(docs: dict, code: dict | None) -> str:
    """Identity of the owner's working state a proposal is made from (canonical JSON)."""
    return hashlib.sha256(json.dumps({'docs': docs, 'code': studio_code.normalize(code)}, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _walk(tree, parents=()):
    for item in tree:
        yield item, parents
        for slot in ('kids', 'alt'):
            kids = item.get('props', {}).get(slot)
            if isinstance(kids, list):
                yield from _walk(kids, parents + (item,))


def _text_of(item, limit=120) -> str:
    out = []
    for node, _ in _walk([item]):
        if node['type'] == 'Txt':
            out.append(node['props']['text'])
        if sum(len(t) for t in out) > limit:
            break
    return re.sub(r'\s+', ' ', ' '.join(out)).strip()[:limit]


def _brief(item) -> dict:
    p = item['props']
    row = {'id': p['id'], 'type': item['type'], 'tag': p.get('tag') or p.get('name') or '', 'label': p.get('label') or p.get('tag') or item['type']}
    text = _text_of(item, 80)
    if text:
        row['text'] = text
    if (p.get('a') or {}).get('href'):
        row['href'] = p['a']['href']
    return row


def page_entry(live, site_id: str, page_id: str, code: dict | None) -> dict | None:
    """An imported page, or an owner-created page/post declared by the code layer."""
    model = live.model(site_id)
    pages = model['pages'] + studio_pages.studio_pages((code or {}).get('pages') or {}, model)
    return next((p for p in pages if p['id'] == page_id), None)


def build_context(live, site_id: str, docs: dict, code: dict, page: dict, selected: str | None, bp: str, computed: dict | None, today: str | None = None) -> dict:
    """Precise, server-derived context: page, selection (with link/text/style targets), breakpoint,
    revision, brand, code layer, the routes links may use, created pages, the asset library and the
    templates new pages can start from."""
    created = (code or {}).get('pages') or {}
    routes, _ = live.link_scope(site_id, created)
    doc = page.get('post_doc') or page.get('doc')
    context = {'site': site_id, 'domain': DOMAIN, 'page': {k: page.get(k) for k in ('id', 'route', 'label', 'created')}, 'breakpoint': bp,
               'revision': {'version': live.latest(site_id), 'commit': live.commit}, 'brand': BRAND,
               'code': {k: v for k, v in (code or {}).items() if k in ('css', 'header')}, 'routes': sorted(routes)[:80],
               'style_props': sorted(studio_live.STYLE_NAMES), 'elements': [], 'today': today or time.strftime('%Y-%m-%d', time.gmtime()),
               'site_posts': live.site_scope(site_id)['posts'],
               'templates': {'landing': sorted(studio_pages.TEMPLATES['landing']), 'post': sorted(studio_pages.TEMPLATES['post'])},
               'created_pages': [{'doc': d, 'kind': s['kind'], 'slug': s['slug'], 'route': studio_pages.route_of(d, s), 'title': s['title'], 'status': s['status']}
                                 for d, s in sorted(created.items())][:40],
               'assets': [{'ref': a['ref'], 'name': a['name'], 'type': a['type']} for a in sorted(live.assets(site_id).values(), key=lambda a: a['added_at'], reverse=True)[:24]],
               'components': live.placeable_components(site_id, code)[:60]}
    if doc in created:
        context['page']['doc'] = doc
        context['page']['settings'] = created[doc]
    if page.get('created') == 'landing' or doc in docs:
        tree = docs.get(doc) or []
        main = next((n for n, _ in _walk(tree) if n['type'] == 'El' and n['props'].get('tag') == 'main'), None)
        context['page']['root'] = {'doc': doc, 'main': main['props']['id'] if main else None, 'count': len(tree)}
    if not selected:
        return context
    hit = None
    for doc_id, tree in docs.items():
        for item, parents in _walk(tree):
            if item['props'].get('id') == selected:
                hit = (doc_id, item, parents)
                break
        if hit:
            break
    if not hit:
        raise studio_live.LiveError(f'Element {selected[:60]} is not on this page.')
    doc_id, item, parents = hit
    p = item['props']
    chain = [item] + list(reversed(parents))
    link = next((n for n in chain if n['type'] == 'El' and n['props'].get('tag') in ('a', 'Link')), None)
    style_target = next((n for n in chain if n['type'] in ('El', 'Use', 'Map', 'Code')), None)
    texts = [n for n, _ in _walk([item]) if n['type'] == 'Txt'] if item['type'] != 'Txt' else [item]
    sel = {**_brief(item), 'doc': doc_id, 'classes': p.get('cls') or '', 'styles': p.get('st') or {},
           'ancestors': [_brief(n) for n in reversed(parents)][:8]}
    # Where the selection sits (inserts go after it, or inside it when it is a container).
    holder = parents[-1]['props'].get('kids') if parents else docs[doc_id]
    sel['parent'] = parents[-1]['props']['id'] if parents else None
    sel['index'] = next((i for i, n in enumerate(holder or []) if n is item), None)
    if item['type'] == 'El' and isinstance(p.get('kids'), list) and p.get('tag') not in ('a', 'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'span', 'button', 'li', 'summary'):
        sel['container'] = True
    if link is not None:
        sel['link_id'] = link['props']['id']
        sel['href'] = (link['props'].get('a') or {}).get('href')
        sel['href_from_code'] = bool((link['props'].get('x') or {}).get('href'))
    if len(texts) == 1:
        sel['text_id'] = texts[0]['props']['id']
    if style_target is not None:
        sel['style_target'] = style_target['props']['id']
    if isinstance(computed, dict):
        sel['computed'] = {str(k)[:40]: str(v)[:120] for k, v in list(computed.items())[:30] if isinstance(v, (str, int, float))}
    context['selected'] = sel
    siblings = (parents[-1]['props'].get('kids') if parents else docs[doc_id]) or []
    nearby = [n for n in siblings if n is not item][:12] + [n for n, _ in _walk([item]) if n is not item][:28]
    context['elements'] = [_brief(n) for n in nearby]
    return context


class TurnBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    # How the owner gave the instruction (receipts only: typed and spoken text take the same path).
    input: str = Field(default='typed', pattern='^(typed|voice)$')
    today: str | None = Field(default=None, pattern=r'^20\d\d-\d\d-\d\d$')
    page: str = Field(min_length=1, max_length=120)
    selected: str | None = Field(default=None, max_length=80)
    bp: str = Field(default='d', pattern='^(d|t|m)$')
    base_version: int = Field(ge=0)
    docs: dict = Field(default_factory=dict)
    code: dict | None = None
    computed: dict | None = None


class AudioBody(BaseModel):
    audio: str = Field(min_length=10, max_length=8_200_000)
    mime: str = Field(min_length=5, max_length=80)


class DecisionBody(BaseModel):
    base_version: int = Field(ge=0)
    docs: dict = Field(default_factory=dict)
    code: dict | None = None


class ManagerStore:
    """Turns and proposals for one site, next to the live editor's versions."""

    def __init__(self, live):
        self.live = live
        self._lock = threading.Lock()

    def folder(self, site_id: str) -> Path:
        return self.live.folder(site_id) / 'manager'

    def append_turn(self, site_id: str, row: dict) -> None:
        path = self.folder(site_id) / 'turns.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, path.open('a') as handle:
            handle.write(json.dumps(row, sort_keys=True) + '\n')

    def turns(self, site_id: str) -> list[dict]:
        path = self.folder(site_id) / 'turns.jsonl'
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()][-MAX_TURNS_SHOWN:]

    def save_proposal(self, site_id: str, proposal: dict) -> None:
        studio_live._atomic(self.folder(site_id) / 'proposals' / f"{proposal['id']}.json", proposal)

    def proposal(self, site_id: str, pid: str) -> dict:
        if not re.fullmatch(r'wm_[a-f0-9]{16}', pid or ''):
            raise KeyError('Unknown proposal')
        path = self.folder(site_id) / 'proposals' / f'{pid}.json'
        if not path.is_file():
            raise KeyError('Unknown proposal')
        return json.loads(path.read_text())


def router(sources, identity, live=None, *, jobs_root: Path, package_root: Path, modelport=None):
    live = live or studio_live.LiveStudio(sources)
    store = ManagerStore(live)
    api = APIRouter(prefix='/api/website/studio/sites/{site_id}/live/manager', dependencies=[Depends(private_response)])

    def who(request: Request, mutate: bool = False) -> dict:
        principal = identity.principal(request)
        if principal.get('workspace') != 'ttros-operator' and principal.get('method') != 'local_preview':
            raise HTTPException(403, 'The Website Manager requires the TTROS operator')
        if mutate:
            identity.csrf(request)
        return principal

    def guard(fn):
        try:
            return fn()
        except studio_live.StaleError as exc:
            raise HTTPException(409, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, str(exc).strip("'")) from exc
        except (studio_live.LiveError, studio_code.CodeError, ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc

    def agent_status() -> dict:
        import aos_jobs
        try:
            registry = json.loads((Path(package_root) / 'agents' / 'registry.lock.json').read_text()).get('agents') or {}
        except (OSError, ValueError):
            registry = {}
        entry = registry.get('website.manager') or {}
        package = Path(package_root) / 'agents' / 'packages' / 'website.manager' / 'agent.toml'
        problem = aos_jobs.refs_release_problem(package_root, ['website.manager'])
        return {'agent': 'website.manager', 'package': package.is_file(), 'state': entry.get('state') or 'unregistered', 'level': entry.get('level'),
                'model': ((entry.get('built') or {}).get('candidate') or {}), 'model_route': 'available' if not problem else 'held',
                'model_route_detail': problem, 'deterministic_route': 'available'}

    def check_base(site_id: str, base_version: int, docs: dict, code) -> tuple[dict, dict]:
        latest = live.latest(site_id)
        if base_version != latest:
            raise studio_live.StaleError(f'Version v{latest} is newer than your base v{base_version}. Reload the latest version first.')
        model_docs = live.model(site_id)['docs']
        if not isinstance(docs, dict) or not docs or len(docs) > 12:
            raise studio_live.LiveError('Send the documents of the page you are editing.')
        try:
            layer = studio_code.check(code or {})
        except studio_code.CodeError as exc:
            raise studio_live.LiveError(str(exc)) from exc
        created = layer.get('pages') or {}
        for doc_id in docs:
            if doc_id not in model_docs and doc_id not in created:
                raise studio_live.LiveError(f'Unknown document {str(doc_id)[:60]}.')
        full = live.documents(site_id, latest) if latest else {k: d['tree'] for k, d in model_docs.items()}
        full.update(docs)
        full = {k: v for k, v in full.items() if k in model_docs or k in created}
        layer = live.validate_code(site_id, layer, full)
        live.validate_documents(site_id, full, layer)
        return full, layer

    @api.get('')
    def status(site_id: str, request: Request):
        who(request)
        live.folder(site_id)
        return guard(lambda: {**agent_status(), 'turns': store.turns(site_id)})

    @api.post('/turns')
    def turn(site_id: str, body: TurnBody, request: Request):
        who(request, True)
        import aos_jobs

        def run():
            full, code = check_base(site_id, body.base_version, body.docs, body.code)
            page = page_entry(live, site_id, body.page, code)
            if page is None:
                raise KeyError('Unknown page')
            context = build_context(live, site_id, body.docs, code, page, body.selected, body.bp, body.computed, body.today)
            input_sha = state_sha(body.docs, code)
            job, _ = aos_jobs.create(Path(jobs_root), conversation_id=f'website-studio:{site_id}', topic='Website Manager', request=body.text,
                                     executor={'kind': 'website_manager', 'action': 'interpret', 'request': body.text, 'context': context, 'input': body.input,
                                               'site': site_id, 'package_root': str(package_root)}, requested_by='casey')
            done = aos_jobs.execute(Path(jobs_root), job['id'], modelport=modelport)
            with aos_jobs._locked(Path(jobs_root), job['id']):
                record = aos_jobs.load(Path(jobs_root), job['id'])
                if not record.get('notification'):
                    record['notification'] = {'status': 'in_app', 'at': _iso(), 'surface': 'website_studio'}
                    aos_jobs.save(Path(jobs_root), record)
            output = None
            for artifact in done.get('artifacts') or []:
                path = Path(jobs_root) / artifact['path']
                if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == artifact['sha256']:
                    output = json.loads(path.read_text())
            row = {'at': _iso(), 'job_id': job['id'], 'job_status': done['status'], 'request': body.text, 'page': body.page, 'selected': body.selected,
                   'input': body.input, 'capability': (output or {}).get('capability') or '', 'coding_job': (output or {}).get('coding_job'),
                   'bp': body.bp, 'base_version': body.base_version, 'route': (output or {}).get('route') or ('model' if done.get('usage') else 'none'),
                   'reply': (output or {}).get('reply') or done.get('detail') or '', 'usage': done.get('usage'),
                   'failure_class': done.get('failure_class') or ''}
            if done['status'] != 'done' or not output:
                row['outcome'] = 'refused' if done['status'] == 'refused' else 'failed'
                store.append_turn(site_id, row)
                return row
            if output.get('needs_input') or not output.get('ops'):
                row.update(outcome='needs_input', missing=output.get('missing') or [])
                store.append_turn(site_id, row)
                return row
            routes, files = live.link_scope(site_id, code.get('pages'))
            try:
                after, after_code = studio_code.apply_ops(body.docs, code, output['ops'], routes=routes, files=files, site=live.site_scope(site_id),
                                                          style_problem=studio_live.style_problem, unsafe=studio_live.url_problem)
                created = (after_code or {}).get('pages') or {}
                merged = {k: v for k, v in {**full, **after}.items() if k in live.model(site_id)['docs'] or k in created}
                after_code = live.validate_code(site_id, after_code, merged)
                live.validate_documents(site_id, merged, after_code)
            except (studio_code.CodeError, studio_live.LiveError) as exc:
                row.update(outcome='rejected', reason=str(exc)[:400])
                store.append_turn(site_id, row)
                return row
            changed = sorted(d for d in after if d not in body.docs or studio_code.canonical(after[d]) != studio_code.canonical(body.docs[d]))
            # Both sides carry every document of the page, so only the proposal's own change shows.
            diff = live.source_diff(site_id, {**body.docs, **after}, after_code, before={'docs': body.docs, 'code': code})
            proposal = {'id': 'wm_' + secrets.token_hex(8), 'site': site_id, 'job_id': job['id'], 'route': output.get('route'), 'reply': output.get('reply'),
                        'capability': output.get('capability'), 'input': body.input, 'opens': output.get('opens'),
                        'ops': output['ops'], 'page': body.page, 'selected': body.selected, 'bp': body.bp, 'base_version': body.base_version,
                        'commit': live.commit, 'input_sha256': input_sha, 'docs': {d: after[d] for d in changed}, 'code': after_code,
                        'code_changed': studio_code.canonical(after_code) != studio_code.canonical(code), 'changed_docs': changed,
                        'source_diff': diff, 'status': 'open', 'created_at': _iso(), 'usage': done.get('usage')}
            store.save_proposal(site_id, proposal)
            row.update(outcome='proposal', proposal=proposal['id'])
            store.append_turn(site_id, row)
            return {**row, 'proposal': proposal}
        return guard(run)

    @api.get('/voice')
    def voice(site_id: str, request: Request):
        who(request)
        live.folder(site_id)
        return voice_status()

    @api.post('/transcribe')
    def transcribe_audio(site_id: str, body: AudioBody, request: Request):
        who(request, True)
        live.folder(site_id)
        import base64
        try:
            data = base64.b64decode(body.audio, validate=True)
        except Exception as exc:
            raise HTTPException(422, 'Invalid audio encoding') from exc
        return guard(lambda: transcribe(data, body.mime))

    @api.get('/proposals/{pid}')
    def get_proposal(site_id: str, pid: str, request: Request):
        who(request)
        return guard(lambda: store.proposal(site_id, pid))

    def decide(site_id: str, pid: str, body: DecisionBody, verdict: str, principal: dict) -> dict:
        proposal = store.proposal(site_id, pid)
        if proposal['site'] != site_id:
            raise KeyError('Unknown proposal')
        if proposal['status'] != 'open':
            raise studio_live.LiveError(f'This proposal was already {proposal["status"]}.')
        if verdict == 'accepted':
            latest = live.latest(site_id)
            if body.base_version != proposal['base_version'] or latest != proposal['base_version'] or live.commit != proposal['commit']:
                raise studio_live.StaleError(f'The site moved on (v{latest}) since this proposal was made on v{proposal["base_version"]}; ask again.')
            if state_sha(body.docs, studio_code.normalize(body.code)) != proposal['input_sha256']:
                raise studio_live.StaleError('The page changed since this proposal was made; it no longer applies. Ask again.')
        proposal.update(status=verdict, decided_at=_iso(), decided_by='owner:' + str(principal.get('email') or principal.get('user') or 'local')[:80])
        store.save_proposal(site_id, proposal)
        store.append_turn(site_id, {'at': _iso(), 'job_id': proposal['job_id'], 'proposal': pid, 'outcome': verdict, 'request': '', 'reply': ''})
        return proposal

    @api.post('/proposals/{pid}/accept')
    def accept(site_id: str, pid: str, body: DecisionBody, request: Request):
        p = who(request, True)
        return guard(lambda: decide(site_id, pid, body, 'accepted', p))

    @api.post('/proposals/{pid}/reject')
    def reject(site_id: str, pid: str, body: DecisionBody, request: Request):
        p = who(request, True)
        return guard(lambda: decide(site_id, pid, body, 'rejected', p))

    api.store = store  # type: ignore[attr-defined]
    return api
