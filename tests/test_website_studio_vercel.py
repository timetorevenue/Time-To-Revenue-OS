"""Website Studio → Vercel production adapter: exact prebuilt upload set, artifact links, staging,
idempotent reuse, promotion, identity verification, failure revert and exact external rollback.
No network and no credential: a fake Vercel API stands in for api.vercel.com.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'dashboard/backend'), str(ROOT / 'tools'), str(ROOT / 'tests')]

import website_studio_artifacts as artifacts  # noqa: E402
import website_studio_release_targets as targets  # noqa: E402
import website_studio_releases as releases  # noqa: E402
import website_studio_vercel as vercel  # noqa: E402

from test_website_studio_releases import FakeSources, World, SITE, OWNER  # noqa: E402

CONFIG = {'label': 'Vercel production', 'project': 'prj_unit', 'team': 'team_unit', 'production_domains': ['www.example.test']}


@pytest.fixture(autouse=True)
def _private_token(tmp_path, monkeypatch):
    token = tmp_path / 'token'
    token.write_text('unit-token')
    token.chmod(0o600)
    tool = tmp_path / 'tool'
    tool.write_text('')
    monkeypatch.setenv('TTROS_VERCEL_TOKEN_FILE', str(token))
    monkeypatch.setenv('TTROS_VERCEL_CLI', str(tool))
    monkeypatch.setenv('TTROS_VERCEL_NODE', str(tool))
    monkeypatch.setenv('TTROS_STUDIO_VERCEL_WORK', str(tmp_path / 'deploy-work'))
    monkeypatch.setattr(targets, 'subprocess', _NpmStub(subprocess))


def vercel_output(folder: Path, dep: str = 'dependency v1'):
    out = folder / '.vercel/output'
    (out / 'static').mkdir(parents=True)
    (out / 'config.json').write_text('{"version":3}')
    (out / 'static/index.html').write_text('<h1>static</h1>')
    func = out / 'functions/index.func'
    func.mkdir(parents=True)
    (func / '___next_launcher.cjs').write_text('launcher')
    (func / '.vc-config.json').write_text(json.dumps({'runtime': 'nodejs24.x', 'filePathMap': {'node_modules/dep/index.js': 'node_modules/dep/index.js'}}))
    os.symlink('index.func', out / 'functions/index.rsc.func')
    (folder / 'node_modules/dep').mkdir(parents=True, exist_ok=True)
    (folder / 'node_modules/dep/index.js').write_text(dep)
    (folder / '.vercel/project.json').write_text('{"projectId":"prj_unit"}')


class FakeVercel:
    def __init__(self):
        self.deployments_by_id = {'dpl_ORIGINALgit1': {'id': 'dpl_ORIGINALgit1', 'url': 'original.example.test', 'target': 'production',
                                                       'readyState': 'READY', 'meta': {'githubCommitSha': '2e521be'}}}
        self.production_id = 'dpl_ORIGINALgit1'
        self.uploaded, self.created, self.promotions = {}, [], []

    def project(self):
        return {'id': 'prj_unit', 'framework': 'nextjs', 'accountId': 'team_unit',
                'targets': {'production': {**self.deployments_by_id[self.production_id], 'alias': ['www.example.test']}}}

    def project_domains(self):
        return [{'name': 'www.example.test', 'verified': True}, {'name': 'example.test', 'verified': True, 'redirect': 'www.example.test'}]

    def production(self):
        return self.deployments_by_id[self.production_id]

    def deployments(self, limit=20):
        return [{'uid': d['id'], 'state': d['readyState'], 'meta': d['meta'], 'url': d['url'], 'target': d['target']} for d in self.deployments_by_id.values()]

    def find_release(self, release_id, sha):
        return next((d for d in self.deployments_by_id.values() if d['meta'].get('ttrosRelease') == release_id and d['meta'].get('ttrosArtifact') == sha), None)

    def upload(self, folder, rows):
        for r in rows:
            self.uploaded[r['sha']] = r['file']
        return len(rows)

    def create(self, folder, rows, meta, name):
        assert all(r['sha'] in self.uploaded for r in rows)
        ident = f'dpl_STUDIO{len(self.created)}'
        self.created.append({'rows': rows, 'meta': meta})
        self.deployments_by_id[ident] = {'id': ident, 'url': f'staged{len(self.created)}.example.test', 'target': 'production',
                                         'readyState': 'READY', 'meta': meta, 'files': {r['file']: r['sha'] for r in rows}}
        return {'id': ident}

    def wait_ready(self, ident):
        return self.deployments_by_id[ident]

    def deployment(self, ident):
        return self.deployments_by_id[ident]

    def promote(self, ident):
        assert self.deployments_by_id[ident]['readyState'] == 'READY'
        self.promotions.append(ident)
        self.production_id = ident
        return self.deployments_by_id[ident]

    def deployment_files(self, ident):
        return [{'file': f, 'sha': s} for f, s in self.deployments_by_id[ident].get('files', {}).items()]


def make_target(fake):
    target = targets.VercelTarget('vercel-production', CONFIG, ROOT)
    target.client = fake
    target._await_serving = lambda row, timeout=240: None  # edge propagation (tested separately)
    return target


class _NpmStub:
    """`npm ci` in the extracted release restores the mapped dependency exactly (from the lockfile)."""
    content = 'dependency v1'

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def run(self, cmd, cwd=None, **kw):
        if cmd[:2] == ['npm', 'ci']:
            (Path(cwd) / 'node_modules/dep').mkdir(parents=True, exist_ok=True)
            (Path(cwd) / 'node_modules/dep/index.js').write_text(_NpmStub.content)
            return self._real.CompletedProcess(cmd, 0)
        return self._real.run(cmd, cwd=cwd, **kw)


def vercel_release(tmp_path, world=None):
    folder = tmp_path / 'fixture'
    folder.mkdir()
    (folder / '.next').mkdir()
    (folder / '.next/BUILD_ID').write_text('build-unit')
    vercel_output(folder)
    (folder / '.vercel/output/config.json').chmod(0o664)
    record = vercel.record_output(folder)
    packed = artifacts.package(folder, tmp_path / 'artifact.tar')
    release = {'release_id': 'rel_' + '1' * 20, 'artifact': {'sha256': packed['sha256'], 'manifest_sha256': packed['manifest_sha256'], 'kind': packed['kind']},
               'build': {'next_build_id': 'build-unit'}, 'source': {'commit': '2e521be'}}
    return release, tmp_path / 'artifact.tar', record


def test_upload_set_matches_the_cli_rules_and_artifacts_keep_internal_links(tmp_path):
    release, artifact, record = vercel_release(tmp_path)
    files = [r['file'] for r in record['files']]
    # Group-writable build files (umask 002) record the artifact's normalized modes.
    assert {r['mode'] for r in record['files']} <= {0o100644, 0o100755, 0o120777}
    assert '.vercel/output/functions/index.rsc.func' in files            # symlinked function = one link entry
    assert '.vercel/output/functions/index.rsc.func/.vc-config.json' not in files
    assert 'node_modules/dep/index.js' in files                          # mapped dependency file
    assert '.vercel/project.json' not in files and 'studio-vercel-output.json' not in files
    assert release['artifact']['kind'] == artifacts.KIND_VERCEL
    extracted = artifacts.extract(artifact, release['artifact']['sha256'], release['artifact']['manifest_sha256'], tmp_path / 'x')
    assert any(r.get('link') == 'index.func' for r in extracted['files'])
    assert not (tmp_path / 'x/node_modules').exists()                    # dependencies are never in the artifact
    # A link that leaves the build output, or an environment file, is refused.
    bad = tmp_path / 'bad'
    (bad / '.vercel/output').mkdir(parents=True)
    (bad / '.next').mkdir()
    (bad / '.next/BUILD_ID').write_text('b')
    os.symlink('/etc/passwd', bad / '.vercel/output/leak')
    with pytest.raises(artifacts.ArtifactError):
        artifacts.manifest(bad)
    (bad / '.vercel/output/leak').unlink()
    (bad / '.vercel/.env.production.local').write_text('X=1')
    with pytest.raises(artifacts.ArtifactError):
        artifacts.manifest(bad)


def test_stage_creates_one_unassigned_production_deployment_and_reuses_it(tmp_path):
    release, artifact, record = vercel_release(tmp_path)
    fake = FakeVercel()
    target = make_target(fake)
    assert target.readiness()['available'] is True
    staged = target.stage(release, artifact)
    assert staged['created'] is True and staged['upload_digest'] == record['digest']
    meta = fake.created[0]['meta']
    assert meta['ttrosRelease'] == release['release_id'] and meta['ttrosArtifact'] == release['artifact']['sha256']
    assert fake.production_id == 'dpl_ORIGINALgit1'                       # staging never touches production
    assert not (Path(staged['site']) / 'node_modules').exists()
    again = target.stage(release, artifact)
    assert again['created'] is False and again['deployment_id'] == staged['deployment_id'] and len(fake.created) == 1
    pointer = target.promote(staged, 'op')
    assert pointer['release_id'] == release['release_id'] and fake.production_id == staged['deployment_id']
    assert target.verify_files(staged)['sha256'] == release['artifact']['manifest_sha256']
    target.restore({'deployment_id': 'dpl_ORIGINALgit1'}, 'op')
    assert fake.production_id == 'dpl_ORIGINALgit1'


def test_stage_refuses_when_reinstalled_files_differ_from_the_validated_build(tmp_path, monkeypatch):
    release, artifact, _ = vercel_release(tmp_path)
    fake = FakeVercel()
    target = make_target(fake)
    monkeypatch.setattr(_NpmStub, 'content', 'dependency v2 (different)')
    with pytest.raises(targets.TargetError, match='differ from the validated build'):
        target.stage(release, artifact)
    assert fake.created == [] and fake.uploaded == {}


def test_publish_checks_staged_deployment_promotes_it_and_rolls_back_exactly(tmp_path, monkeypatch):
    world = World(tmp_path)
    fake = FakeVercel()
    target = make_target(fake)
    rel = world.releases
    rel._targets = {'vercel-production': target}
    ident, _ = world.build('alpha')
    fixture = world.builds_root / ident / 'ttros-studio-site-fixture'
    # Re-record the build as a Vercel release build (as Builds._release_evidence does after `vercel build`).
    vercel_output(fixture)
    vercel.record_output(fixture)
    (world.builds_root / ident / 'artifact.tar').chmod(0o644)
    (world.builds_root / ident / 'artifact.tar').unlink()
    packed = artifacts.package(fixture, world.builds_root / ident / 'artifact.tar')
    record_path = world.builds_root / ident / 'build-record.json'
    record = json.loads(record_path.read_text())
    record['artifact'] = packed
    record_path.write_text(json.dumps(record))
    created = rel.create(SITE, ident, OWNER)
    rid = created['release_id']
    checks = []
    monkeypatch.setattr(releases, 'health', lambda base, build_id, routes, deployment=None: {'ok': True, 'checks': [], 'base': base})
    monkeypatch.setattr(target, 'functional', lambda base, d, routes, evidence: checks.append(base) or {'ok': True, 'checks': [{'check': 'unit', 'ok': True, 'detail': ''}]})
    authorize = lambda plan: {'authorized': True, 'basis': 'test'}
    plan = rel.plan(SITE, 'publish', rid, 'vercel-production')
    assert plan['allowed'], plan['problems']
    op = rel.execute(SITE, 'publish', rid, 'vercel-production', 'dpl_ORIGINALgit1', authorize, OWNER, background=False)
    assert op['status'] == 'succeeded', op.get('error')
    assert op['published_identity']['matches_release'] and fake.production_id == op['deployment']['deployment_id']
    assert checks[0].startswith('https://staged') and checks[1] == 'https://www.example.test'   # staged first, then production
    # The pre-Studio Git deployment is now an exact rollback target; rolling back promotes it again unchanged.
    assert 'dpl_ORIGINALgit1' in rel.rollback_targets(SITE, 'vercel-production')
    back_plan = rel.plan(SITE, 'rollback', 'dpl_ORIGINALgit1', 'vercel-production')
    assert back_plan['allowed'] and back_plan['external'] and back_plan['phrase'] == 'ROLLBACK vercel-production TO dpl_ORIGINALgit1'
    back = rel.execute(SITE, 'rollback', 'dpl_ORIGINALgit1', 'vercel-production', rid, authorize, OWNER, background=False)
    assert back['status'] == 'succeeded' and fake.production_id == 'dpl_ORIGINALgit1' and len(fake.created) == 1
    # Forward again to the same release reuses the same deployment (no rebuild, no new deployment).
    again = rel.execute(SITE, 'publish', rid, 'vercel-production', 'dpl_ORIGINALgit1', authorize, OWNER, background=False)
    assert again['status'] == 'succeeded' and again['deployments_created'] == 0 and len(fake.created) == 1
    # Unknown deployments are never rollback targets.
    assert not rel.plan(SITE, 'rollback', 'dpl_NEVERSEEN99', 'vercel-production')['allowed']
    # A failing production check reverts to the previous deployment.
    rel.execute(SITE, 'rollback', 'dpl_ORIGINALgit1', 'vercel-production', rid, authorize, OWNER, background=False)
    monkeypatch.setattr(target, 'functional', lambda base, d, routes, evidence: {'ok': not base.endswith('example.test') or base.startswith('https://staged'), 'checks': [{'check': 'unit', 'ok': False, 'detail': 'x'}]})
    failed = rel.execute(SITE, 'publish', rid, 'vercel-production', 'dpl_ORIGINALgit1', authorize, OWNER, background=False)
    assert failed['status'] == 'reverted' and fake.production_id == 'dpl_ORIGINALgit1' and failed['previous_available']
    # The alias moved, then confirming the edge failed inside promote(): production is still restored.
    monkeypatch.setattr(target, 'functional', lambda base, d, routes, evidence: {'ok': True, 'checks': []})
    def stuck(row, timeout=240):
        if row['id'] != 'dpl_ORIGINALgit1':
            raise targets.TargetError('edge did not switch')
    target._await_serving = stuck
    edge = rel.execute(SITE, 'publish', rid, 'vercel-production', 'dpl_ORIGINALgit1', authorize, OWNER, background=False)
    assert edge['status'] == 'reverted' and fake.production_id == 'dpl_ORIGINALgit1' and edge['current_after']['deployment_id'] == 'dpl_ORIGINALgit1'
    assert rel.ledger(SITE)['intact']
    world.close()


def test_promotion_waits_until_every_production_domain_serves_the_new_build(monkeypatch):
    target = targets.VercelTarget('vercel-production', {**CONFIG, 'production_domains': ['www.example.test', 'example.test']}, ROOT)
    served = {'new.example.test': b'<!DOCTYPE html><!--newBuildId123456789--><html>', 'www.example.test': [b'<!DOCTYPE html><!--oldBuildId123456789-->', b'<!DOCTYPE html><!--newBuildId123456789-->'],
              'example.test': [b'<!DOCTYPE html><!--newBuildId123456789-->']}
    calls = []

    def fake_get(base, path, timeout=20.0):
        host = base.split('//')[1]
        calls.append(host)
        v = served[host]
        return 200, {}, v.pop(0) if isinstance(v, list) and len(v) > 1 else (v[0] if isinstance(v, list) else v)
    monkeypatch.setattr(targets, 'http_get', fake_get)
    monkeypatch.setattr(targets.time, 'sleep', lambda s: None)
    target._await_serving({'id': 'dpl_new', 'url': 'new.example.test'})
    assert calls.count('www.example.test') == 2  # waited for the edge to switch
    # An apex that redirects to a production domain counts as serving through that domain.
    redirect = {'example.test': (308, {'location': 'https://www.example.test/'}, b'')}
    monkeypatch.setattr(targets, 'http_get', lambda base, path, timeout=20.0: redirect.get(base.split('//')[1]) or (200, {}, b'<!DOCTYPE html><!--newBuildId123456789-->'))
    target._await_serving({'id': 'dpl_new', 'url': 'new.example.test'})
    monkeypatch.setattr(targets, 'http_get', fake_get)
    served['www.example.test'] = [b'<!DOCTYPE html><!--oldBuildId123456789-->'] * 3
    with pytest.raises(targets.TargetError, match='did not serve'):
        target._await_serving({'id': 'dpl_new', 'url': 'new.example.test'}, timeout=-1)
