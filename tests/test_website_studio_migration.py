"""Schema v5 (website 215679e) draft migration: Python/JS twin agreement, immutable history, zero models.

Fixtures under dashboard/frontend/tests/fixtures/studio-v4 were produced by the c58e641-pinned Studio code
itself; proofs/ holds every draft earlier stages committed as evidence. No model is called anywhere here.
"""
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_website_studio_stage15 import SITE, app_for, needs_site, poisoned_model, src  # noqa: E402,F401
import website_studio_edits as edits  # noqa: E402
import website_studio_migrate as migration  # noqa: E402
import website_studio_sections as sections  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / 'dashboard/frontend'
FIXTURES = FRONTEND / 'tests/fixtures/studio-v4'


def corpus():
    out = [(f'fixture:{p.stem}', json.loads(p.read_text())) for p in sorted(FIXTURES.glob('*.json'))]
    for path in sorted((ROOT / 'proofs').glob('*/*.json')):
        try:
            value = json.loads(path.read_text())
        except ValueError:
            continue
        env = value.get('envelope', value) if isinstance(value, dict) else None
        if isinstance(env, dict) and 'version' in env and isinstance((env.get('data') or {}).get('content'), list):
            out.append((str(path.relative_to(ROOT)), env))
    return out


def test_python_and_browser_migrations_agree_on_every_genuine_draft():
    drafts = corpus()
    assert len(drafts) >= 30
    script = ("import fs from 'node:fs';import {migrateEnvelope} from './src/studio/migrate.js';"
              "const input=JSON.parse(fs.readFileSync(0,'utf8'));"
              "process.stdout.write(JSON.stringify(input.map(e=>{try{return migrateEnvelope(e)}catch(x){return {error:x.message}}})))")
    browser = json.loads(subprocess.run(['node', '--input-type=module', '-e', script], cwd=FRONTEND, input=json.dumps([e for _, e in drafts]),
                                        capture_output=True, text=True, check=True, timeout=120).stdout)
    for (name, env), js in zip(drafts, browser):
        before = json.dumps(env, sort_keys=True)
        envelope, report = migration.migrate_envelope(env)
        assert 'error' not in js, (name, js)
        assert json.dumps({'envelope': envelope, 'report': report}, sort_keys=True) == json.dumps(js, sort_keys=True), name
        assert json.dumps(env, sort_keys=True) == before, f'{name}: input mutated'
        assert migration.migrate_envelope(envelope) == (envelope, None)
        page = envelope.get('page', 'home')
        assert edits.canonical(envelope['data'], page) == edits.canonical(edits.canonical(envelope['data'], page), page)


def test_unchanged_types_are_byte_identical_to_the_candidate_snapshot():
    snapshots = [json.loads((FRONTEND / 'src/studio/homepage.json').read_text())['sections'],
                 *[p['sections'] for p in json.loads((FRONTEND / 'src/studio/services.json').read_text())['pages'].values()]]
    current = {s['type']: s for group in snapshots for s in group}
    shared = {s['type']: s for s in json.loads((FRONTEND / 'src/studio/shared.json').read_text())['sections']}
    for kind, digest in migration.SPEC['unchanged'].items():
        if kind in shared:
            assert hashlib.sha256(json.dumps(shared[kind], sort_keys=True, ensure_ascii=False).encode()).hexdigest() == digest, kind
        else:
            s = current[kind]
            assert hashlib.sha256(json.dumps({'html': s['html'], 'texts': s['texts']}, sort_keys=True, ensure_ascii=False).encode()).hexdigest() == digest, kind
    assert set(migration.SPEC['unchanged']) | set(migration.SPEC['types']) | {'HeroSignature', *[r['type'] for r in migration.SPEC['insert']]} >= \
        {k for k, v in sections.section_types().items() if not v.get('nested')}


def test_fail_closed_migrations_name_the_reason():
    base = json.loads((FIXTURES / 'home-untouched.json').read_text())
    for mutate, message in [
        (lambda e: e.update(version=0), 'version 0 is not supported'),
        (lambda e: e['source'].update(commit='stale'), 'does not match'),
        (lambda e: e['data']['content'].append({'type': 'Section99', 'props': {'id': 'x1'}}), 'Section99 is not part of'),
        (lambda e: e['data']['content'][0]['props'].update(text77='x'), 'text77 has no destination'),
        (lambda e: e['data']['content'][2]['props'].update(id='HeroSignature'), 'reserved for the new source section'),
    ]:
        env = copy.deepcopy(base)
        mutate(env)
        with pytest.raises(migration.MigrationError, match=message):
            migration.migrate_envelope(env)
    with pytest.raises(ValueError, match='retained'):
        migration.validate_unsupported([{'path': '', 'value': 1, 'reason': 'x'}])


@needs_site
def test_stored_c58e641_history_migrates_on_read_saves_v5_and_is_never_rewritten(src, poisoned_model):
    calls, model = poisoned_model
    store = edits.Drafts(src)
    folder = store._dir(SITE)
    folder.mkdir(parents=True)
    legacy = json.loads((FIXTURES / 'home-edited.json').read_text())
    record = {'version': 1, 'base_version': 0, 'saved_at': '2026-11-19T12:00:00+00:00', 'actor': 'owner', 'note': 'c58e641 edits',
              'origin': 'manual', 'fingerprint': 'legacy', 'revision': 'c58e641', 'brand_overrides': [], 'contrast_failures': [],
              'operations': None, 'envelope': legacy}
    (folder / 'v0001.json').write_text(json.dumps(record))
    before = (folder / 'v0001.json').read_bytes()
    c = app_for(src, model)
    base = f'/api/website/studio/sites/{SITE}'
    # The stored record is served exactly as saved; the editor migrates it on read (migrate.js).
    assert c.get(f'{base}/drafts/1').json()['envelope'] == legacy
    migrated = store.base(SITE)
    assert migrated == migration.migrate_envelope(legacy)[0]['data']
    hero = migrated['content'][0]
    assert hero['props']['text2'] == 'Not your overtime.' and hero['props']['workflow'][0]['props']['titleLine1'] == 'From first call to signed quote'
    # Manual re-edit on the migrated base, then a validated worker operation on top of it.
    edited = edits.apply_proposal(migrated, {'summary': 'owner', 'edits': [{'section': 'Section0-workflow', 'field': 'step3Title', 'text': 'Follow up the same day'}]})
    response = c.post(f'{base}/drafts', json={'data': edited, 'base_version': 1})
    assert response.status_code == 200, response.text
    saved = store.get(SITE, 2)
    assert saved['envelope']['version'] == 5 and saved['envelope']['source'] == edits.manifest()
    assert saved['envelope']['data']['root']['props']['unsupported'][0]['path'] == 'Section0-workflow.graphic.signal'
    ops = {'summary': 'worker', 'edits': [{'section': 'HeroSignature', 'field': 'text0', 'text': 'Clear processes. Connected systems.'}]}
    assert store.save(SITE, edits.apply_proposal(store.base(SITE), ops), 2, 'worker:test', origin='worker', operations=ops)['version'] == 3
    assert store.get(SITE, 3)['envelope']['data']['content'][0]['props']['workflow'][0]['props']['step3Title'] == 'Follow up the same day'
    assert (folder / 'v0001.json').read_bytes() == before, 'history is never rewritten'
    assert calls == []
