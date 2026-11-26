"""Isolated end-to-end proof of Website Studio exact-version publication and rollback.

Saved drafts → validated release build (real Builds._release_evidence, real preview health over
loopback) → immutable release → staged deployment on an isolated target → production alias →
identity/health verification → receipts. No network beyond loopback, no provider, no model.
"""
import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'dashboard/backend'))
sys.path.insert(0, str(ROOT / 'tools'))

import website_studio_artifacts as artifacts  # noqa: E402
import website_studio_builds as builds  # noqa: E402
import website_studio_edits as edits  # noqa: E402
import website_studio_release_targets as targets  # noqa: E402
import website_studio_releases as releases  # noqa: E402

SITE = 'default-site'


@pytest.fixture(autouse=True)
def _no_vercel_credential(tmp_path, monkeypatch):
    # Tests never read the operator's real Vercel token or reach Vercel.
    monkeypatch.setenv('TTROS_VERCEL_TOKEN_FILE', str(tmp_path / 'missing-vercel-token'))
OWNER = 'owner@example.test'
PAGES = ('home', 'site', 'service-ai')


class FakeSources:
    root = ROOT

    def __init__(self, state):
        self.state = Path(state)

    def site(self, site_id):
        if site_id != SITE:
            raise KeyError('Website is not registered for Studio')
        return {'id': SITE, 'repository': Path('/read-only/website')}

    def revision(self, site):
        return {'current': True, 'problems': []}


def save_draft(state, page, version, text):
    folder = state / SITE / edits.page_info(page)['draftFolder']
    folder.mkdir(parents=True, exist_ok=True)
    record = {'version': version, 'fingerprint': f'{page}-{text}', 'contrast_failures': [], 'page': page,
              'envelope': {'version': 5, 'page': page, 'data': {'text': text}}}
    (folder / f'v{version:04d}.json').write_text(json.dumps(record))
    return record


def site_fixture(folder, build_id, text):
    app = folder / '.next/server/app'
    (app / 'services').mkdir(parents=True)
    (folder / '.next/static' / build_id).mkdir(parents=True)
    (folder / '.next/static' / build_id / '_buildManifest.js').write_text('self.__BUILD_MANIFEST={}')
    (folder / '.next/BUILD_ID').write_text(build_id)
    page = '<html><body><h1>' + text + '</h1><script>self.__next_f.push([1,"0:{\\"P\\":null,\\"b\\":\\"' + build_id + '\\"}"])</script></body></html>'
    (app / 'index.html').write_text(page)
    (app / 'services/ai-implementation.html').write_text(page)
    (folder / 'package.json').write_text('{"name":"timetorevenue"}')
    (folder / 'package-lock.json').write_text('{"lockfileVersion":3}')
    (folder / 'studio-adapter-receipt.json').write_text('{}')
    # Never part of the artifact:
    (folder / 'node_modules').mkdir()
    (folder / 'node_modules/next.js').write_text('dependency')
    (folder / '.next/cache').mkdir()
    (folder / '.next/cache/runtime').write_text('cache')
    (folder / 'source.tar').write_text('intermediate')


class World:
    """A Studio state folder, a builds folder and one isolated static target."""

    def __init__(self, tmp):
        self.tmp = tmp
        self.state = tmp / 'state'
        self.sources = FakeSources(self.state)
        self.builds_root = tmp / 'builds'
        self.target = targets.IsolatedTarget('isolated-test', {'root': str(tmp / 'target'), 'server': 'static'})
        self.releases = releases.Releases(self.sources, self.builds_root, {'isolated-test': self.target}, root=ROOT)
        self.servers = []
        self.n = 0

    def build(self, text, *, versions=None, validation_ok=True):
        """Saved drafts → a release build exactly as Builds._release_evidence records one."""
        self.n += 1
        records = {}
        for page in PAGES:
            version = (versions or {}).get(page, self.n)
            path = self.state / SITE / edits.page_info(page)['draftFolder'] / f'v{version:04d}.json'
            records[page] = json.loads(path.read_text()) if path.is_file() else save_draft(self.state, page, version, f'{text}-{page}')
        ident = hashlib.sha256(f'{text}{self.n}'.encode()).hexdigest()[:32]
        folder = self.builds_root / ident
        fixture = folder / 'ttros-studio-site-fixture'
        build_id = f'build{self.n}{text}'[:21]
        site_fixture(fixture, build_id, text)
        (folder / 'build.log').write_text('next build passed')
        handler = type('H', (targets._StaticHandler,), {'folder': fixture})
        server = targets._Server(('127.0.0.1', 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.servers.append(server)
        versions = {p: {'version': r['version'], 'fingerprint': r['fingerprint'], 'schema_version': 5,
                        'record_sha256': hashlib.sha256(json.dumps(r, sort_keys=True).encode()).hexdigest()} for p, r in records.items()}
        row = {'id': ident, 'site': SITE, 'versions': versions, 'contrast': {p: [] for p in PAGES},
               'contrast_acknowledged': False, 'started_at': time.time()}
        result = {'base': builds.CANDIDATE, 'sourceUntouched': True, 'build': 'passed', 'purpose': 'release',
                  'nextBuildId': build_id, 'changes': [{'path': 'app/page.tsx'}],
                  'routes': ['/page', '/services/ai-implementation/page'] if validation_ok else ['/page'],
                  'fixture': str(fixture), 'pageDraftSha256': {p: 'sha-' + p for p in PAGES}, 'sourceDiff': 'diff'}
        evidence = builds.Builds(self.sources, self.builds_root)._release_evidence(folder, row, result, f'http://127.0.0.1:{server.server_address[1]}')
        return ident, evidence

    def release(self, text):
        ident, _ = self.build(text)
        return self.releases.create(SITE, ident, OWNER)

    def run(self, action, release_id, *, expected=None, authorize=None):
        expected = expected if expected is not None else releases.current_key(self.target.current())
        return self.releases.execute(SITE, action, release_id, 'isolated-test', expected,
                                     authorize or (lambda plan: {'authorized': True, 'basis': 'test'}), OWNER, background=False)

    def served_build(self):
        status, headers, body = targets.http_get(self.target.production_url(), '/')
        return status, headers.get('x-ttros-deployment'), body

    def close(self):
        self.target.close()
        for server in self.servers:
            server.shutdown()
            server.server_close()


@pytest.fixture
def world(tmp_path, monkeypatch):
    item = World(tmp_path)
    yield item
    item.close()


def test_saved_draft_to_validated_build_to_immutable_release_to_staged_publication(world):
    ident, evidence = world.build('alpha')
    assert evidence['validation'] == 'passed'
    record, folder = world.releases.builds.record(ident)
    assert {c['check'] for c in record['validation']['checks']} >= {
        'adapter_source_verified', 'next_build', 'website_configuration', 'routes_built', 'preview_health', 'contrast', 'artifact'}
    # The artifact excludes dependencies, caches and intermediates, and is byte-reproducible.
    names = [r['path'] for r in artifacts.manifest(Path(record['artifact'] and folder / 'ttros-studio-site-fixture'))['files']]
    assert not any(n.startswith(('node_modules', '.next/cache', 'source.tar')) for n in names)
    again = artifacts.package(folder / 'ttros-studio-site-fixture', world.tmp / 'again.tar')
    assert again['sha256'] == record['artifact']['sha256']

    release = world.releases.create(SITE, ident, OWNER)
    rid = release['release_id']
    assert release['existing'] is False and release['model_calls'] == 0
    stored = world.releases.get(SITE, rid)
    assert stored['source']['commit'] == builds.CANDIDATE
    assert stored['drafts']['home']['version'] == 1 and stored['drafts']['service-ai']['record_sha256']
    assert stored['content_fingerprints'] == {p: 'sha-' + p for p in PAGES}
    assert stored['artifact']['sha256'] == record['artifact']['sha256']
    assert stored['validation']['status'] == 'passed'
    path = world.releases.folder(SITE) / rid
    assert not os.access(path / 'release.json', os.W_OK) and not os.access(path / 'artifact.tar', os.W_OK)
    assert world.releases.verify(SITE, rid)['ok']
    # Creating a release from the same build again is idempotent.
    assert world.releases.create(SITE, ident, OWNER)['existing'] is True

    plan = world.releases.plan(SITE, 'publish', rid, 'isolated-test')
    assert plan['allowed'] and plan['expected_current'] == 'none' and plan['phrase'] == f'PUBLISH {rid} TO isolated-test'
    op = world.run('publish', rid)
    assert op['status'] == 'succeeded', op.get('error')
    assert [s['status'] for s in op['steps']] == ['staging', 'staged', 'promoting', 'succeeded']
    assert op['staged_health']['ok'] and op['production_health']['ok']
    assert op['deployments_created'] == 1 and op['model_calls'] == 0
    # Published identity is the approved build.
    identity = op['published_identity']
    assert identity['matches_release'] and identity['artifact_sha256'] == stored['artifact']['sha256']
    assert identity['manifest_sha256'] == stored['artifact']['manifest_sha256']
    assert world.target.current()['release_id'] == rid
    status, deployment, body = world.served_build()
    assert status == 200 and deployment == op['deployment']['deployment_id'] and stored['build']['next_build_id'].encode() in body
    # The deployment is read-only; dependencies and `next start` runtime caches live outside it.
    site = world.target.root / 'deployments' / op['deployment']['deployment_id'] / 'site'
    for link in ('node_modules', '.next/cache', '.next/server/route-cache'):
        assert (site / link).is_symlink() and not str((site / link).resolve()).startswith(str(site))
    with pytest.raises(PermissionError):
        (site / '.next/server/new-file').write_text('runtime write')
    # Durable, write-once receipt and an intact hash-chained ledger.
    receipt = world.releases.folder(SITE) / 'receipts' / f"{op['id']}.json"
    assert json.loads(receipt.read_text())['status'] == 'succeeded' and not os.access(receipt, os.W_OK)
    ledger = world.releases.ledger(SITE)
    assert ledger['intact'] and [e['event'] for e in ledger['entries']][-5:] == [
        'operation.requested', 'operation.staging', 'operation.staged', 'operation.promoting', 'operation.succeeded']


def test_changed_missing_stale_or_unverified_builds_and_releases_are_rejected(world, monkeypatch):
    ident, evidence = world.build('alpha', validation_ok=False)
    assert evidence['validation'] == 'failed'
    with pytest.raises(releases.ReleaseError, match='validation did not pass'):
        world.releases.create(SITE, ident, OWNER)
    with pytest.raises(KeyError):
        world.releases.create(SITE, 'f' * 32, OWNER)  # missing build record
    # Changed build artifact after validation.
    ident, _ = world.build('beta')
    artifact = world.builds_root / ident / 'artifact.tar'
    os.chmod(artifact, 0o644)
    artifact.write_bytes(artifact.read_bytes() + b'x')
    with pytest.raises(releases.ReleaseError, match='changed after validation'):
        world.releases.create(SITE, ident, OWNER)
    artifact.unlink()
    with pytest.raises(releases.ReleaseError, match='missing'):
        world.releases.create(SITE, ident, OWNER)
    # A saved draft changed after the build.
    ident, _ = world.build('gamma')
    draft = world.state / SITE / 'drafts' / 'v0003.json'
    draft.write_text(json.dumps({**json.loads(draft.read_text()), 'fingerprint': 'edited'}))
    with pytest.raises(releases.ReleaseError, match='changed after it was built'):
        world.releases.create(SITE, ident, OWNER)

    release = world.release('delta')
    rid = release['release_id']
    folder = world.releases.folder(SITE) / rid
    # Tampered artifact in the release store.
    os.chmod(folder / 'artifact.tar', 0o644)
    original = (folder / 'artifact.tar').read_bytes()
    (folder / 'artifact.tar').write_bytes(original[:-1] + b'\x01')
    plan = world.releases.plan(SITE, 'publish', rid, 'isolated-test')
    assert not plan['allowed'] and any('artifact_checksum' in p for p in plan['problems'])
    with pytest.raises(releases.ReleaseError, match='artifact_checksum'):
        world.run('publish', rid)
    (folder / 'artifact.tar').write_bytes(original)
    # Missing artifact.
    (folder / 'artifact.tar').rename(folder / 'moved.tar')
    assert any('artifact_present' in p for p in world.releases.plan(SITE, 'publish', rid, 'isolated-test')['problems'])
    (folder / 'moved.tar').rename(folder / 'artifact.tar')
    # Tampered release record.
    os.chmod(folder / 'release.json', 0o644)
    text = (folder / 'release.json').read_text()
    (folder / 'release.json').write_text(text.replace('"status": "passed"', '"status": "passed "', 1))
    assert any('record_integrity' in p for p in world.releases.plan(SITE, 'publish', rid, 'isolated-test')['problems'])
    (folder / 'release.json').write_text(text)
    assert world.releases.plan(SITE, 'publish', rid, 'isolated-test')['allowed']
    # Stale: Studio's adapter pin moved on since the release was built.
    monkeypatch.setattr(builds, 'CANDIDATE', '0' * 40)
    assert any(p.startswith('stale') for p in world.releases.plan(SITE, 'publish', rid, 'isolated-test')['problems'])
    monkeypatch.undo()
    # Stale comparison: the operator compared against a different published state.
    with pytest.raises(releases.ReleaseError, match='stale comparison'):
        world.run('publish', rid, expected='rel_' + '0' * 20)
    assert world.target.current() is None and world.target.deployments() == []


def test_duplicate_requests_do_not_deploy_twice(world):
    rid = world.release('alpha')['release_id']
    first = world.run('publish', rid, expected='none')
    replay = world.run('publish', rid, expected='none')
    assert first['status'] == 'succeeded' and replay['duplicate'] is True and replay['id'] == first['id']
    noop = world.run('publish', rid)  # compared against the now-current release
    assert noop['status'] == 'noop' and noop['deployments_created'] == 0 and noop['authorization'] is None
    assert world.run('publish', rid)['duplicate'] is True
    assert len(world.target.deployments()) == 1
    assert len((world.target.root / 'deployments.jsonl').read_text().splitlines()) == 1
    assert len((world.target.root / 'alias.jsonl').read_text().splitlines()) == 1
    # A concurrent request while one runs is refused rather than queued as a second deployment.
    rid2 = world.release('beta')['release_id']
    with world.releases._target_lock(SITE, 'isolated-test'):
        with pytest.raises(releases.ReleaseError, match='Another publication'):
            world.run('publish', rid2)
    assert len(world.target.deployments()) == 1


@pytest.mark.parametrize('step', ['stage', 'start', 'health', 'production_health'])
def test_failed_release_leaves_previous_valid_version_available(world, step):
    first = world.release('alpha')
    assert world.run('publish', first['release_id'])['status'] == 'succeeded'
    before = world.target.current()
    second = world.release('beta')
    (world.target.root / 'faults.json').write_text(json.dumps([{'step': step}]))
    op = world.run('publish', second['release_id'])
    assert op['status'] == ('reverted' if step == 'production_health' else 'failed')
    assert op['previous_available'] is True and op['previous_health']['ok']
    assert world.target.current() == before
    status, deployment, body = world.served_build()
    assert status == 200 and deployment == before['deployment_id'] and first['build']['next_build_id'].encode() in body
    receipt = json.loads((world.releases.folder(SITE) / 'receipts' / f"{op['id']}.json").read_text())
    assert receipt['status'] == op['status'] and receipt['error']
    # The failed attempt can be retried by a new authorized request; it then succeeds.
    retry = world.run('publish', second['release_id'])
    assert retry['status'] == 'succeeded' and retry['attempt'] == 2
    assert world.target.current()['release_id'] == second['release_id']


def test_rollback_restores_the_exact_previous_release(world):
    first = world.release('alpha')
    published = world.run('publish', first['release_id'])
    second = world.release('beta')
    assert world.run('publish', second['release_id'])['status'] == 'succeeded'
    never = world.release('gamma')
    assert world.releases.rollback_targets(SITE, 'isolated-test') == [first['release_id']]
    refused = world.releases.plan(SITE, 'rollback', never['release_id'], 'isolated-test')
    assert not refused['allowed'] and any('never successfully published' in p for p in refused['problems'])
    plan = world.releases.plan(SITE, 'rollback', first['release_id'], 'isolated-test')
    assert plan['phrase'] == f"ROLLBACK isolated-test TO {first['release_id']}"
    op = world.run('rollback', first['release_id'])
    assert op['status'] == 'succeeded' and op['action'] == 'rollback'
    # The same immutable deployment of the same artifact is re-aliased; nothing is rebuilt.
    assert op['deployment']['deployment_id'] == published['deployment']['deployment_id']
    assert op['deployment']['created'] is False and op['deployments_created'] == 0
    assert op['published_identity']['artifact_sha256'] == first['artifact']['sha256']
    assert op['published_identity']['manifest_sha256'] == first['artifact']['manifest_sha256']
    status, deployment, body = world.served_build()
    assert deployment == published['deployment']['deployment_id'] and first['build']['next_build_id'].encode() in body
    assert len(world.target.deployments()) == 2
    # A deployed copy that changed on disk is never re-promoted.
    world.run('publish', second['release_id'])
    site = world.target.root / 'deployments' / published['deployment']['deployment_id'] / 'site' / '.next/server/app/index.html'
    os.chmod(site, 0o644)
    site.write_text('tampered')
    failed = world.run('rollback', first['release_id'])
    assert failed['status'] == 'failed' and 'files changed' in failed['error']
    assert world.target.current()['release_id'] == second['release_id']


def test_interrupted_operation_is_reconciled(world):
    rid = world.release('alpha')['release_id']
    op = {'id': 'op_' + '1' * 16 + '_1', 'attempt': 1, 'idempotency_key': '1' * 64, 'action': 'publish', 'site': SITE,
          'release': rid, 'target': 'isolated-test', 'created_at': releases.now(), 'status': 'staging', 'steps': []}
    world.releases._save(SITE, op)
    world.run('publish', rid)
    closed = world.releases.operation(SITE, op['id'])
    assert closed['status'] == 'interrupted' and 'observed_current' in closed


def test_history_order_follows_the_ledger_not_the_wall_clock(world, monkeypatch):
    first = world.release('alpha')
    second = world.release('beta')
    world.run('publish', first['release_id'])
    monkeypatch.setattr(releases, 'now', lambda: '2000-01-01T00:00:00+00:00')  # clock stepped backwards
    world.run('publish', second['release_id'])
    ops = world.releases.operations(SITE, 'isolated-test')
    assert [o['release'] for o in ops] == [second['release_id'], first['release_id']]
    assert ops[0]['seq'] > ops[1]['seq']


def test_vercel_production_target_without_credential_reports_blockers_and_refuses(tmp_path):
    # No token file: blocked before any network call (the autouse fixture points at a missing file).
    target = targets.VercelTarget('vercel-production', {'label': 'Vercel production', 'project': 'prj_unit', 'team': 'team_unit',
                                                        'production_domains': ['www.example.test']}, ROOT)
    target.client.request = lambda *a, **k: pytest.fail('no Vercel request without a credential')
    readiness = target.readiness()
    assert readiness['available'] is False
    assert [b['id'] for b in readiness['blockers']][:1] == ['deployment_tooling']
    with pytest.raises(targets.TargetBlocked):
        target.stage({'release_id': 'rel_x', 'artifact': {'sha256': 'x'}}, tmp_path)
    # Only isolated targets can come from the test file, and none by default.
    registered = json.loads((ROOT / 'context/website_studio_sites.json').read_text())['sites'][SITE]
    assert list(targets.load_targets(registered, ROOT)) == ['vercel-production']
    bad = tmp_path / 'targets.json'
    bad.write_text(json.dumps({'sneaky': {'provider': 'vercel'}}))
    os.environ['TTROS_STUDIO_RELEASE_TARGETS'] = str(bad)
    try:
        with pytest.raises(ValueError):
            targets.load_targets(registered, ROOT)
    finally:
        del os.environ['TTROS_STUDIO_RELEASE_TARGETS']


# -- HTTP: authentication, explicit command, Dashboard status -----------------------------------

class Identity:
    origin = 'http://127.0.0.1:4340'

    def principal(self, request):
        workspace = request.headers.get('x-fixture-workspace', 'customer-a')
        return {'user': workspace, 'workspace': workspace, 'method': request.headers.get('x-fixture-method', 'fixture'), 'email': ''}

    def csrf(self, request):
        if request.headers.get('x-ttros-connections') != '1':
            raise HTTPException(403, 'Same-origin Dashboard request required')


@pytest.fixture
def client(world, monkeypatch, tmp_path):
    import remote_pc
    monkeypatch.setenv('TTROS_REMOTE_PC_IDENTITIES', OWNER)
    monkeypatch.setattr(remote_pc, 'RECEIPTS_DIR', tmp_path / 'remote-pc')
    monkeypatch.setattr(remote_pc, 'DENIED_LOG', tmp_path / 'remote-pc/denied.jsonl')
    vercel = targets.VercelTarget('vercel-production', {}, ROOT)
    world.releases._targets = {'isolated-test': world.target, 'vercel-production': vercel}
    app = FastAPI()
    app.include_router(releases.router(world.sources, Identity(), world.releases, root=ROOT))
    return TestClient(app)


READER = {'x-fixture-workspace': 'ttros-operator'}
LOCAL = {'x-fixture-workspace': 'local-preview', 'x-fixture-method': 'local_preview', 'x-ttros-connections': '1'}
OWNER_HEADERS = {'x-ttros-connections': '1', 'x-ttros-remote-pc': '1', 'tailscale-user-login': OWNER}


def wait(client, op_id):
    for _ in range(200):
        op = client.get(f'/api/website/studio/sites/{SITE}/release-operations/{op_id}', headers=READER).json()
        if op['status'] in releases.TERMINAL:
            return op
        time.sleep(.05)
    raise AssertionError('operation did not finish')


def test_http_owner_authorization_and_dashboard_status(world, client):
    base = f'/api/website/studio/sites/{SITE}'
    ident, _ = world.build('alpha')
    # Unauthorized: customer workspace, missing CSRF.
    assert client.get(base + '/releases').status_code == 403
    assert client.post(base + '/releases', json={'build_id': ident}, headers=READER).status_code == 403
    created = client.post(base + '/releases', json={'build_id': ident}, headers={**READER, 'x-ttros-connections': '1'})
    assert created.status_code == 201
    rid = created.json()['release_id']
    prepare = {'action': 'publish', 'target': 'isolated-test'}
    url = f'{base}/releases/{rid}'
    # Prepare/execute need the verified operator identity: not a workspace member, not local preview.
    for headers in (READER, LOCAL, {'x-ttros-remote-pc': '1', 'tailscale-user-login': OWNER},
                    {**OWNER_HEADERS, 'tailscale-user-login': 'someone@example.test'},
                    {**OWNER_HEADERS, 'tailscale-funnel-request': '1'}):
        assert client.post(url + '/prepare', json=prepare, headers=headers).status_code == 403
    plan = client.post(url + '/prepare', json=prepare, headers=OWNER_HEADERS).json()
    assert plan['allowed'] and plan['phrase'] == f'PUBLISH {rid} TO isolated-test' and plan['model_calls'] == 0
    token = plan['confirmation']['confirmation_token']
    body = {**prepare, 'expected_current': plan['expected_current'], 'confirmation_token': token, 'command': plan['phrase']}
    assert client.post(url + '/execute', json=body, headers=LOCAL).status_code == 403
    assert client.post(url + '/execute', json={**body, 'command': 'publish it'}, headers=OWNER_HEADERS).status_code == 403
    assert client.post(url + '/execute', json={**body, 'confirmation_token': 'a.1.b'}, headers=OWNER_HEADERS).status_code == 403
    rollback_body = {**body, 'action': 'rollback', 'command': f'ROLLBACK isolated-test TO {rid}'}
    assert client.post(url + '/execute', json=rollback_body, headers=OWNER_HEADERS).status_code == 409  # never published here
    assert world.target.deployments() == [] and world.target.current() is None
    started = client.post(url + '/execute', json=body, headers=OWNER_HEADERS)
    assert started.status_code == 202
    op = wait(client, started.json()['id'])
    assert op['status'] == 'succeeded' and op['authorization']['basis'] == 'authenticated_explicit_publication_command'
    assert op['authorization']['operator']['identity'] == OWNER and op['actor'] == OWNER
    # The same request again is a duplicate (no second deployment, no second token use needed).
    replay = client.post(url + '/execute', json=body, headers=OWNER_HEADERS).json()
    assert replay['duplicate'] is True and replay['id'] == op['id']
    # A used confirmation cannot authorize a different action.
    second = client.post(base + '/releases', json={'build_id': world.build('beta')[0]}, headers={**READER, 'x-ttros-connections': '1'}).json()
    reused = {**body, 'expected_current': rid, 'command': f"PUBLISH {second['release_id']} TO isolated-test"}
    assert client.post(f"{base}/releases/{second['release_id']}/execute", json=reused, headers=OWNER_HEADERS).status_code == 403
    assert len(world.target.deployments()) == 1
    # Production Vercel target: blockers shown, no confirmation issued, execution refused.
    blocked = client.post(url + '/prepare', json={'action': 'publish', 'target': 'vercel-production'}, headers=OWNER_HEADERS).json()
    assert blocked['allowed'] is False and 'confirmation' not in blocked
    assert any('deployment_tooling' in p for p in blocked['problems'])
    refused = client.post(url + '/execute', json={**body, 'target': 'vercel-production', 'expected_current': blocked['expected_current'],
                                                 'command': f'PUBLISH {rid} TO vercel-production'}, headers=OWNER_HEADERS)
    assert refused.status_code == 409
    # Dashboard status/evidence endpoints describe exactly what is published.
    index = client.get(base + '/releases', headers=READER).json()
    assert index['targets']['isolated-test']['current_key'] == rid and index['ledger']['intact']
    assert index['targets']['vercel-production']['available'] is False
    row = next(r for r in index['releases'] if r['release_id'] == rid)
    assert row['published_on'] == ['isolated-test'] and row['verified'] and row['validation'] == 'passed'
    compare = client.get(f"{base}/releases/{second['release_id']}/compare?target=isolated-test", headers=READER).json()
    assert compare['current_key'] == rid and compare['artifact']['published'] == world.releases.get(SITE, rid)['artifact']['sha256']
    history = client.get(base + '/release-targets/isolated-test/history', headers=READER).json()
    assert history['operations'][0]['id'] == op['id'] and history['current']['release_id'] == rid
    detail = client.get(url, headers=READER).json()
    assert detail['verification']['ok'] and detail['release']['record_sha256']


def test_authority_requires_the_exact_bound_command(monkeypatch):
    import aos_authority
    monkeypatch.setenv('TTROS_REMOTE_PC_IDENTITIES', OWNER)
    headers = {'x-ttros-remote-pc': '1', 'tailscale-user-login': OWNER}
    phrase = 'PUBLISH rel_x TO isolated-test'
    decide = lambda command, action='WEBSITE_PUBLISH', target='default-site:isolated-test', digest='d1': aos_authority.action_authorization(
        ROOT, None, action=action, target=target, website_release=(command, digest))
    assert decide(None)['authorized'] is False
    make = lambda typed=phrase, digest='d1': aos_authority.authenticated_website_release_command(
        headers, 'WEBSITE_PUBLISH', 'default-site:isolated-test', digest, phrase, typed)
    assert decide(make(typed='publish'))['authorized'] is False
    assert decide(make(digest='other'))['authorized'] is False
    assert decide(make(), target='default-site:vercel-production')['authorized'] is False
    assert decide(make(), action='WEBSITE_ROLLBACK')['authorized'] is False
    command = make()
    assert decide(command)['authorized'] is True
    assert decide(command)['authorized'] is False  # single use
    with pytest.raises(Exception):
        aos_authority.authenticated_website_release_command({}, 'WEBSITE_PUBLISH', 't', 'd', phrase, phrase)


def test_release_operations_make_zero_model_calls(world, monkeypatch):
    import aos_modelport
    calls = []
    monkeypatch.setattr(aos_modelport, 'call', lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError('model call')))
    for name in ('aos_david', 'website_studio'):
        assert name not in {m.split('.')[0] for m in releases.__dict__ if isinstance(m, str)}
    first = world.release('alpha')
    world.run('publish', first['release_id'])
    second = world.release('beta')
    world.run('publish', second['release_id'])
    world.run('rollback', first['release_id'])
    assert calls == []
    receipts = [json.loads(p.read_text()) for p in (world.releases.folder(SITE) / 'receipts').glob('*.json')]
    assert len(receipts) == 3 and all(r['model_calls'] == 0 for r in receipts)
    source = '\n'.join((ROOT / 'dashboard/backend' / f).read_text() for f in (
        'website_studio_releases.py', 'website_studio_release_targets.py', 'website_studio_artifacts.py'))
    assert 'aos_modelport' not in source and 'aos_david' not in source and 'David(' not in source
