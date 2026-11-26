"""Live website editor (website_studio_live): validation authority, immutable versions, restore,
stale protection, ledger integrity, autosave, assets with provenance, Drive approval and API gates.
Synthetic snapshot; no network, no Git writes, no model."""
import base64
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'dashboard/backend'))
sys.path.insert(0, str(ROOT / 'tools'))

import website_studio_live as live  # noqa: E402

SITE = 'default-site'
COMMIT = live.LIVE_SOURCE
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')


def el(ident, tag='div', kids=None, **props):
    p = {'id': ident, 'tag': tag, **props}
    if tag not in live.VOID:
        p['kids'] = kids or []
    return {'type': 'El', 'props': p}


def txt(ident, text):
    return {'type': 'Txt', 'props': {'id': ident, 'text': text}}


CODES = {'home~0': {'doc': 'app/page.tsx', 'text': '{items.map(i => <li>{i}</li>)}', 'kind': 'child'},
         'c-header~0': {'doc': 'components/Header.tsx', 'text': '() => setOpen(true)', 'kind': 'attr'},
         'home~1': {'doc': 'app/page.tsx', 'text': '<Header />', 'kind': 'use'},
         'home~2': {'doc': 'app/page.tsx', 'text': '{"a":1}', 'kind': 'attr', 'pure': True}}
HOME = [{'type': 'Use', 'props': {'id': 'home_0', 'name': 'Header', 'doc': 'components/Header.tsx', 'use': 'home~1'}},
        el('home_1', 'main', [el('home_2', 'h1', [txt('home_3', 'Live headline')]), {'type': 'Code', 'props': {'id': 'home_4', 'c': 'home~0'}}])]
HEADER = [el('c-header_0', 'header', [el('c-header_1', 'button', [txt('c-header_2', 'Menu')], x={'onClick': 'c-header~0'})])]


class FakeSources:
    root = ROOT

    def __init__(self, state, drive_status=None, drive_api=None):
        self.state = Path(state)
        self._drive_status = drive_status or {'files': {}, 'folders': {}, 'asset_folder': None}
        self._drive_api = drive_api

    def site(self, site_id):
        if site_id != SITE:
            raise KeyError('Unknown site')
        return {'id': SITE, 'repository': str(ROOT), 'drive': {'brand_assets': [{'id': 'registeredLogo1234', 'label': 'Logo'}]}}

    def drive_status(self, site):
        return self._drive_status

    def drive_factory(self):
        return self._drive_api


@pytest.fixture()
def studio(tmp_path):
    sources = FakeSources(tmp_path / 'state')
    store = live.LiveStudio(sources)
    snap = store.snapshot_dir(SITE)
    snap.mkdir(parents=True)
    model = {'schema': live.SCHEMA, 'commit': COMMIT, 'docs': {'app/page.tsx': {'tree': HOME}, 'components/Header.tsx': {'tree': HEADER}},
             'pages': [{'id': 'home', 'route': '/', 'doc': 'app/page.tsx', 'bind': {}}], 'codes': {}}
    (snap / 'model.json').write_text(json.dumps(model))
    (snap / 'pristine.json').write_text(json.dumps({'commit': COMMIT, 'codes': CODES, 'files': {}}))
    (snap / 'assets.json').write_text(json.dumps({'assets': []}))
    (snap / 'receipt.json').write_text(json.dumps({'commit': COMMIT}))
    return store


def edited(text='New headline'):
    tree = json.loads(json.dumps(HOME))
    tree[1]['props']['kids'][0]['props']['kids'][0]['props']['text'] = text
    return tree


def test_valid_documents_and_versions_are_immutable_timestamped_and_chained(studio):
    v1 = studio.save(SITE, {'app/page.tsx': edited('One')}, 0, 'owner:test')
    v2 = studio.save(SITE, {'app/page.tsx': edited('Two')}, 1, 'owner:test', note='second')
    assert (v1['version'], v2['version']) == (1, 2)
    assert v1['at'].endswith('Z') and v2['prev_sha256'] == v1['sha256']
    assert v2['changed'] == ['app/page.tsx'] and studio.ledger_intact(SITE)
    assert studio.documents(SITE, 1)['app/page.tsx'][1]['props']['kids'][0]['props']['kids'][0]['props']['text'] == 'One'
    # Unchanged documents resolve to the import; only edited ones are stored.
    assert set(v2['docs']) == {'app/page.tsx'}
    assert studio.documents(SITE, 2)['components/Header.tsx'] == HEADER


def test_restore_appends_a_new_version_and_keeps_every_other(studio):
    studio.save(SITE, {'app/page.tsx': edited('One')}, 0, 'owner:test')
    studio.save(SITE, {'app/page.tsx': edited('Two')}, 1, 'owner:test')
    v3 = studio.restore(SITE, 1, 2, 'owner:test')
    assert v3['version'] == 3 and v3['origin'] == 'restore' and v3['restored_from'] == 1
    assert [r['version'] for r in studio.versions(SITE)] == [1, 2, 3]
    assert studio.documents(SITE, 3) == studio.documents(SITE, 1)
    v4 = studio.restore(SITE, 0, 3, 'owner:test')
    assert studio.documents(SITE, 4)['app/page.tsx'] == HOME and v4['docs'] == {}


def test_stale_base_is_refused(studio):
    studio.save(SITE, {'app/page.tsx': edited('One')}, 0, 'owner:test')
    with pytest.raises(live.StaleError):
        studio.save(SITE, {'app/page.tsx': edited('Other')}, 0, 'owner:other')


def test_tampered_ledger_or_blob_is_detected(studio):
    studio.save(SITE, {'app/page.tsx': edited('One')}, 0, 'owner:test')
    blob = next((studio.folder(SITE) / 'versions' / 'blobs').glob('*.json'))
    blob.write_text(blob.read_text().replace('One', 'Evil'))
    with pytest.raises(live.LiveError, match='corrupted'):
        studio.documents(SITE, 1)
    ledger = studio.folder(SITE) / 'versions' / 'ledger.jsonl'
    ledger.write_text(ledger.read_text().replace('"owner:test"', '"owner:evil"'))
    assert not studio.ledger_intact(SITE)
    assert 'live version ledger is not intact' in studio.draft_problems(SITE, {'live': {'version': 1, 'record_sha256': 'x', 'fingerprint': 'y'}})


@pytest.mark.parametrize('tree, message', [
    ([el('home_9', 'div', a={'onClick': 'alert(1)'})], 'not allowed'),
    ([el('home_9', 'div', a={'dangerouslySetInnerHTML': '<b>'})], 'not allowed'),
    ([el('home_9', 'script')], 'not allowed'),
    ([el('home_9', 'a', a={'href': 'javascript:alert(1)'})], 'Unsafe href'),
    ([el('home_9', 'a', a={'href': ' JaVa\tscript:alert(1)'})], 'Unsafe href'),
    ([el('home_9', 'img', a={'src': 'data:text/html;base64,PHNjcmlwdD4='})], 'Unsafe src'),
    ([el('home_9', 'iframe', a={'src': 'https://evil.example/frame'})], 'approved'),
    ([el('home_9', 'div', st={'d': {'backgroundImage': 'url(https://evil.example/x.png)'}})], 'asset library'),
    ([el('home_9', 'div', st={'d': {'color': 'red;}body{display:none'}})], 'unsupported value'),
    ([el('home_9', 'div', st={'x': {'color': 'red'}})], 'Invalid styles'),
    ([el('home_9', 'div', cls='a" onload="b')], 'class list'),
    ([{'type': 'Code', 'props': {'id': 'home_9', 'c': 'invented~1'}}], 'Unknown source reference'),
    ([el('home_9', 'button', x={'onClick': 'c-header~0'})], 'only stay inside'),
    ([{'type': 'Use', 'props': {'id': 'home_9', 'name': 'Header', 'doc': 'components/Header.tsx', 'use': 'home~1', 'kids': []}}], 'own document'),
    ([el('home_9', 'div'), el('home_9', 'div')], 'duplicate'),
    ([el('home_9', 'path')], None),
    ([el('home_9', 'div', [el('home_a', 'path')])], 'inside a graphic'),
    ([{'type': 'El', 'props': {'id': 'home_9', 'tag': 'img', 'kids': []}}], 'children list'),
    ([el('home_9', 'div', evil=1)], 'Unknown field'),
])
def test_validator_refuses_unsafe_or_foreign_values(tree, message):
    if message is None:
        live.validate_tree(tree, CODES, 'app/page.tsx')
        return
    with pytest.raises(live.LiveError, match=message):
        live.validate_tree(tree, CODES, 'app/page.tsx', {'app/page.tsx', 'components/Header.tsx'})


def test_validator_accepts_moves_within_a_file_and_pure_codes_anywhere():
    moved = [el('c-header_0', 'header', [el('c-header_1', 'button', [txt('c-header_2', 'Menu')], x={'onClick': 'c-header~0'}), el('home_5', 'div')])]
    live.validate_tree(moved, CODES, 'components/Header.tsx')
    live.validate_tree([el('home_9', 'div', x={'style': 'home~2'})], CODES, 'components/Header.tsx')
    live.validate_tree([el('home_9', 'a', a={'href': 'mailto:hello@core-site.example'}), el('home_a', 'iframe', a={'src': 'https://tally.so/embed/2Eb6ee'})], CODES, 'app/page.tsx')


def test_save_refuses_invalid_documents_and_unknown_assets(studio):
    bad = edited()
    bad[1]['props']['kids'].append(el('home_9', 'button', x={'onClick': 'c-header~0'}))
    with pytest.raises(live.LiveError, match='only stay inside'):
        studio.save(SITE, {'app/page.tsx': bad}, 0, 'owner:test')
    img = edited()
    img[1]['props']['kids'].append(el('home_9', 'img', a={'src': 'asset:' + 'a' * 64, 'alt': ''}))
    with pytest.raises(live.LiveError, match='Unknown Studio asset'):
        studio.save(SITE, {'app/page.tsx': img}, 0, 'owner:test')
    with pytest.raises(live.LiveError, match='Unknown document'):
        studio.save(SITE, {'app/evil.tsx': []}, 0, 'owner:test')
    assert studio.latest(SITE) == 0


def test_autosave_round_trip_and_cleared_by_save(studio):
    rec = studio.autosave(SITE, {'base_version': 0, 'page': 'home', 'docs': {'app/page.tsx': edited('Draft')}}, 'owner:test')
    assert rec['sha256'] and 'docs' not in rec
    assert studio.get_autosave(SITE)['docs']['app/page.tsx'][1]['props']['kids'][0]['props']['kids'][0]['props']['text'] == 'Draft'
    studio.save(SITE, {'app/page.tsx': edited('Draft')}, 0, 'owner:test')
    assert studio.get_autosave(SITE) is None
    with pytest.raises(live.LiveError):
        studio.autosave(SITE, {'docs': {'app/page.tsx': [el('home_9', 'script')]}}, 'owner:test')


def test_assets_are_content_addressed_with_provenance_and_unsafe_svg_is_refused(studio):
    asset = studio.add_asset(SITE, PNG, 'pixel.png', 'owner:test', {'kind': 'upload'})
    assert asset['ref'] == 'asset:' + asset['id'] and asset['type'] == 'image/png' and asset['provenance']['kind'] == 'upload'
    assert studio.asset_file(SITE, asset['id']).read_bytes() == PNG
    tree = edited()
    tree[1]['props']['kids'].append(el('home_9', 'img', a={'src': asset['ref'], 'alt': 'pixel'}))
    assert studio.save(SITE, {'app/page.tsx': tree}, 0, 'owner:test')['assets'] == [asset['id']]
    with pytest.raises(live.LiveError, match='scripts'):
        studio.add_asset(SITE, b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', 'x.svg', 'owner:test', {'kind': 'upload'})
    with pytest.raises(live.LiveError, match='external'):
        studio.add_asset(SITE, b'<svg><image href="https://evil.example/x.png"/></svg>', 'x.svg', 'owner:test', {'kind': 'upload'})
    with pytest.raises(live.LiveError, match='Unsupported file type'):
        studio.add_asset(SITE, b'MZ\x90\x00 not an image', 'x.exe', 'owner:test', {'kind': 'upload'})


def test_drive_import_requires_an_approved_file_and_records_provenance(tmp_path):
    class Api:
        calls = []

        def metadata(self, file_id):
            return {'id': file_id, 'name': 'logo.png', 'mimeType': 'image/png', 'modifiedTime': '2026-11-01T00:00:00Z', 'version': '7'}

        def get(self, path, **kw):
            self.calls.append(path)
            return PNG
    api = Api()
    sources = FakeSources(tmp_path / 's', {'files': {'registeredLogo1234': {'name': 'logo.png', 'mimeType': 'image/png'}}, 'folders': {}, 'asset_folder': None}, api)
    studio = live.LiveStudio(sources)
    with pytest.raises(live.LiveError, match='not an approved'):
        studio.import_drive(SITE, 'someOtherDriveFile99', 'owner:test')
    assert api.calls == []
    asset = studio.import_drive(SITE, 'registeredLogo1234', 'owner:test')
    assert asset['provenance']['kind'] == 'google_drive' and asset['provenance']['file_id'] == 'registeredLogo1234'
    assert asset['provenance']['approval'] == 'registered brand asset' and asset['provenance']['version'] == '7'


class Identity:
    def __init__(self, workspace='ttros-operator'):
        self.workspace = workspace

    def principal(self, request):
        return {'workspace': self.workspace, 'user': 'u', 'email': 'owner@example.test', 'method': 'test'}

    def csrf(self, request):
        if request.headers.get('x-ttros-connections') != '1':
            raise HTTPException(403, 'Same-origin Dashboard request required')


def client_for(studio, workspace='ttros-operator'):
    app = FastAPI()
    app.include_router(live.router(studio.sources, Identity(workspace), studio))
    return TestClient(app)


def test_api_gates_versions_and_negative_cases(studio):
    c = client_for(studio)
    base = '/api/website/studio/sites/default-site/live'
    assert c.post(base + '/versions', json={'base_version': 0, 'docs': {'app/page.tsx': edited()}}).status_code == 403  # no CSRF header
    h = {'X-TTROS-Connections': '1'}
    r = c.post(base + '/versions', json={'base_version': 0, 'docs': {'app/page.tsx': edited('API')}, 'note': 'api'}, headers=h)
    assert r.status_code == 200 and r.json()['version'] == 1
    assert c.post(base + '/versions', json={'base_version': 0, 'docs': {'app/page.tsx': edited('Stale')}}, headers=h).status_code == 409
    bad = edited()
    bad[1]['props']['kids'].append(el('home_9', 'a', a={'href': 'javascript:alert(1)'}))
    assert c.post(base + '/versions', json={'base_version': 1, 'docs': {'app/page.tsx': bad}}, headers=h).status_code == 422
    assert c.post(base + '/versions/1/restore', json={'base_version': 1}, headers=h).json()['version'] == 2
    listing = c.get(base + '/versions').json()
    assert listing['latest'] == 2 and listing['intact'] and 'docs' not in listing['versions'][0]
    assert c.put(base + '/autosave', json={'base_version': 2, 'docs': {'app/page.tsx': edited('auto')}}, headers=h).status_code == 200
    up = c.post(base + '/assets/upload', json={'name': 'p.png', 'data': base64.b64encode(PNG).decode()}, headers=h)
    assert up.status_code == 200 and c.get(base + f"/assets/{up.json()['id']}").content == PNG
    assert c.post(base + '/assets/upload', json={'name': 'x.svg', 'data': base64.b64encode(b'<svg onload="x()"/>').decode()}, headers=h).status_code == 422
    assert c.get(base + '/canvas/../../../etc/passwd').status_code == 404
    outsider = client_for(studio, workspace='client-portal')
    assert outsider.get(base + '/versions').status_code == 403


def test_release_pin_follows_the_adapter(tmp_path):
    import website_studio_releases as releases
    import website_studio_builds as builds
    r = releases.Releases(FakeSources(tmp_path / 's'), builds_root=tmp_path / 'b', targets={})
    assert r._pin('live') == live.LIVE_SOURCE
    assert r._pin('candidate') == builds.CANDIDATE == r._pin(None)


def test_rebase_carries_saved_work_onto_a_new_source_commit(tmp_path):
    """Real 2e521be import → saved owner edit → the website source changes → rebase carries the edit,
    keeps the source's addition, moves the pin, and an old-commit version restores onto the new pin."""
    import subprocess
    repo = Path.home() / 'ttros-projects/default-site'
    if subprocess.run(['git', '-C', str(repo), 'cat-file', '-e', f'{live.LIVE_SOURCE}^{{commit}}'], capture_output=True).returncode:
        pytest.skip('live website source not available')
    sources = FakeSources(tmp_path / 'state')
    store = live.LiveStudio(sources)
    new_commit = 'f' * 40
    script = f'''
import fs from 'node:fs'
import {{readCommit}} from '{ROOT}/dashboard/frontend/scripts/studio-live/git-source.mjs'
import {{importSource}} from '{ROOT}/dashboard/frontend/scripts/studio-live/live-source.mjs'
const s = readCommit('{repo}', '{live.LIVE_SOURCE}')
const write = (dir, files, commit) => {{ const r = importSource(files, {{commit}}); fs.mkdirSync(dir, {{recursive: true}}); fs.writeFileSync(dir + '/model.json', JSON.stringify(r.model)); fs.writeFileSync(dir + '/pristine.json', JSON.stringify(r.pristine)); fs.writeFileSync(dir + '/receipt.json', '{{}}'); fs.writeFileSync(dir + '/assets.json', '{{"assets": []}}') }}
write('{store.snapshot_dir(SITE)}', s.files, s.commit)
const f = 'app/pricing/page.tsx'
s.files.set(f, s.files.get(f).replace('<main id="main-content">', '<main id="main-content">\\n        <p className="source-added">Added by the source</p>'))
write('{store.snapshot_dir(SITE, new_commit)}', s.files, '{new_commit}')
'''
    (tmp_path / 'snap.mjs').write_text(script)
    subprocess.run(['node', str(tmp_path / 'snap.mjs')], check=True, cwd=ROOT / 'dashboard/frontend', timeout=300)
    model = store.model(SITE)
    doc = 'app/pricing/page.tsx'
    tree = json.loads(json.dumps(model['docs'][doc]['tree']))

    def find(nodes, pred):
        for n in nodes:
            if pred(n):
                return n
            for slot in ('kids', 'alt'):
                hit = find(n['props'].get(slot) or [], pred)
                if hit:
                    return hit
    h2 = find(tree, lambda n: n['props'].get('tag') == 'h2' and (n['props'].get('a') or {}).get('id') == 'pricing-how-heading')
    h2['props']['st'] = {'d': {'color': '#7a1f1f'}}
    v1 = store.save(SITE, {doc: tree}, 0, 'owner@example.test', note='restyle')
    report = store._rebase(SITE, new_commit, 'owner@example.test')
    assert store.commit == new_commit and report['version'] == v1['version'] + 1
    assert report['version_report']['applied'] == [doc] and report['version_report']['conflicts'] == []
    latest = store.version(SITE, report['version'])
    assert latest['origin'] == 'migration' and latest['commit'] == new_commit
    docs = store.documents(SITE, report['version'])
    assert find(docs[doc], lambda n: n['props'].get('cls') == 'source-added')
    assert find(docs[doc], lambda n: (n['props'].get('a') or {}).get('id') == 'pricing-how-heading')['props']['st'] == {'d': {'color': '#7a1f1f'}}
    # v1 (made from the old commit) restores onto the new pin, as a new version.
    restored = store.restore(SITE, v1['version'], report['version'], 'owner@example.test')
    assert restored['migration']['applied'] == [doc] and restored['commit'] == new_commit
    assert store.ledger_intact(SITE)
