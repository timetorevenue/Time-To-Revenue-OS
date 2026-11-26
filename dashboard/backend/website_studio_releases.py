"""Immutable Website Studio releases, exact-build publication and rollback. Deterministic; no models.

A release binds, once and immutably: the saved draft versions (each stored record's sha256),
the pinned website source commit and lockfile, the exact release build (Next build id), the
exported content fingerprints, the validation evidence and the build checksum (artifact sha256 +
per-file manifest). Publication deploys only that artifact, after re-verifying it; anything
changed, missing, stale or unverified is refused.

Publish/rollback run through a target adapter (website_studio_release_targets) as one operation:
stage an immutable deployment → health-check it on its own URL → move the production alias →
re-check production identity and health → succeed, or put the previous alias back. Operations are
idempotent (one per exact request; a duplicate returns the first and never deploys again), keyed
to the published state the operator compared against, serialized per target, recorded step by step
in a hash-chained ledger and closed with a write-once receipt.

Authorization (rules/runtime_permission_policy.md: deployment/public publishing needs Casey's
explicit command naming the action and target): the verified Dashboard operator (Cloudflare
Access/Tailscale identity, remote_pc) prepares the exact action, receives a short-lived single-use
confirmation bound to its digest, and confirms by typing the exact command; aos_authority decides.
Revisit: when a production deployment adapter or the permission policy changes. Last touched: 2026-11-21.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import threading
from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from dashboard_identity import private_response
import website_studio_artifacts as artifacts
import website_studio_builds as studio_builds
import website_studio_edits as edits
import website_studio_release_targets as release_targets
from website_studio_release_targets import TargetError, health, now

SCHEMA = 'ttros.website_studio.release/1'
ACTIONS = {'publish': 'WEBSITE_PUBLISH', 'rollback': 'WEBSITE_ROLLBACK'}
TERMINAL = frozenset({'succeeded', 'noop', 'failed', 'reverted', 'interrupted'})
RETRYABLE = frozenset({'failed', 'reverted', 'interrupted'})  # a new authorized request may try again
RELEASE_ID = re.compile(r'rel_[0-9a-f]{20}')
# A provider deployment that served production before Studio releases (e.g. the Git-built site):
# rollback may promote it again exactly, when this target's own history shows it was replaced.
EXTERNAL_ID = re.compile(r'dpl_[A-Za-z0-9]{8,40}')
OPERATION_ID = re.compile(r'op_[0-9a-f]{16}_[0-9]{1,4}')
TARGET_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,40}')
INJECTED_ROUTE = '/__ttros_injected_failure__'


class ReleaseError(ValueError):
    """The request is refused because of release/target state (HTTP 409)."""


class Refused(PermissionError):
    """The request is not authorized (HTTP 403)."""


def sha(value) -> str:
    return hashlib.sha256(artifacts.canonical(value)).hexdigest()


def _write_once(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, 0o444)


def _record_sha(drafts_record: dict) -> str:
    # Same digest the build recorded for each stored draft (website_studio_builds.start).
    return hashlib.sha256(json.dumps(drafts_record, sort_keys=True).encode()).hexdigest()


def current_key(pointer: dict | None) -> str:
    if not pointer:
        return 'none'
    return pointer.get('release_id') or pointer.get('deployment_id') or 'none'


class Releases:
    def __init__(self, sources, builds_root: Path | None = None, targets: dict | None = None, root: Path | None = None):
        self.sources = sources
        self.builds = studio_builds.Builds(sources, builds_root)
        self.root = Path(root or getattr(sources, 'root', None) or release_targets.ROOT)
        self._targets = targets
        self._target_cache: dict[str, dict] = {}
        self._locks: dict[tuple[str, str], threading.Lock] = {}
        self._running: set[str] = set()
        self._guard = threading.Lock()

    # -- storage -----------------------------------------------------------------------------
    def folder(self, site_id: str) -> Path:
        self.sources.site(site_id)
        return Path(self.sources.state) / site_id / 'releases'

    def targets(self, site_id: str) -> dict:
        if self._targets is not None:
            return self._targets
        if site_id not in self._target_cache:
            self._target_cache[site_id] = release_targets.load_targets(self.sources.site(site_id), self.root)
        return self._target_cache[site_id]

    def target(self, site_id: str, target_id: str):
        targets = self.targets(site_id)
        if not TARGET_ID.fullmatch(target_id or '') or target_id not in targets:
            raise KeyError('Unknown release target')
        return targets[target_id]

    def _ledger(self, site_id: str, event: str, **fields) -> dict:
        path = self.folder(site_id) / 'ledger.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a+', encoding='utf-8') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            handle.seek(0)
            lines = [line for line in handle.read().splitlines() if line.strip()]
            previous = json.loads(lines[-1]) if lines else None
            entry = {'seq': (previous['seq'] + 1) if previous else 1, 'at': now(), 'event': event, **fields,
                     'prev_sha256': previous['sha256'] if previous else None}
            entry['sha256'] = sha(entry)
            handle.write(json.dumps(entry, sort_keys=True) + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        return entry

    def ledger(self, site_id: str) -> dict:
        path = self.folder(site_id) / 'ledger.jsonl'
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.is_file() else []
        previous, intact = None, True
        for row in rows:
            body = {k: v for k, v in row.items() if k != 'sha256'}
            if row.get('prev_sha256') != (previous['sha256'] if previous else None) or sha(body) != row.get('sha256'):
                intact = False
            previous = row
        return {'entries': rows, 'intact': intact, 'head': previous['sha256'] if previous else None}

    # -- releases ----------------------------------------------------------------------------
    def create(self, site_id: str, build_id: str, actor: str) -> dict:
        build, build_folder = self.builds.record(build_id)
        if build.get('site') != site_id or build.get('purpose') != 'release':
            raise ReleaseError('Only a finished release build of this site can become a release')
        if (build.get('validation') or {}).get('status') != 'passed':
            failed = [c['check'] for c in build['validation']['checks'] if not c['ok']]
            raise ReleaseError('Build validation did not pass: ' + ', '.join(failed))
        artifact = build_folder / 'artifact.tar'
        if not artifact.is_file():
            raise ReleaseError('The build artifact is missing')
        if artifacts.sha256_file(artifact) != build['artifact']['sha256']:
            raise ReleaseError('The build artifact changed after validation')
        adapter = build.get('adapter', 'candidate')
        problems = self._drafts_for(site_id, adapter, build['versions'])
        if build['base'] != self._pin(adapter):
            problems.append(f"Build is pinned to {build['base'][:12]}, Studio is now pinned to {self._pin(adapter)[:12]}")
        problems.extend(self._source_problems(site_id, adapter))
        if problems:
            raise ReleaseError('Release refused: ' + '; '.join(problems))
        core = {'site': site_id, 'source_commit': build['base'], 'drafts': build['versions'],
                'artifact_sha256': build['artifact']['sha256'], 'environment_sha256': build['environment']['sha256'],
                'next_build_id': build['next_build_id']}
        release_id = 'rel_' + sha(core)[:20]
        folder = self.folder(site_id) / release_id
        if (folder / 'release.json').is_file():
            return {**self.get(site_id, release_id), 'existing': True}
        record = {
            'schema': SCHEMA, 'release_id': release_id, 'site': site_id, 'created_at': now(), 'created_by': actor,
            'model_calls': 0, 'adapter': adapter,
            'source': {'commit': build['base'], 'repository': 'registered website repository (read from Git objects only)',
                       'lock_sha256': build['lock_sha256'], 'adapter_verified': True},
            'drafts': build['versions'],
            'content_fingerprints': build.get('page_draft_sha256', {}),
            'build': {'id': build['id'], 'next_build_id': build['next_build_id'], 'purpose': 'release',
                      'health_routes': build['health_routes'], 'routes': len(build['routes']),
                      'environment': build['environment'], 'changes': build['changes'], 'evidence': build['evidence'],
                      'build_record_sha256': artifacts.sha256_file(build_folder / 'build-record.json')},
            'artifact': {'file': 'artifact.tar', **{k: build['artifact'][k] for k in ('kind', 'sha256', 'bytes', 'manifest_sha256', 'files')}},
            'validation': {'status': build['validation']['status'], 'checks': build['validation']['checks'],
                           'preview_health': build['validation']['preview_health']},
        }
        record['record_sha256'] = sha(record)
        incoming = self.folder(site_id) / ('.incoming-' + release_id)
        shutil.rmtree(incoming, ignore_errors=True)
        (incoming / 'evidence').mkdir(parents=True)
        shutil.copyfile(artifact, incoming / 'artifact.tar')
        os.chmod(incoming / 'artifact.tar', 0o444)
        if artifacts.sha256_file(incoming / 'artifact.tar') != record['artifact']['sha256']:
            shutil.rmtree(incoming, ignore_errors=True)
            raise ReleaseError('The artifact changed while it was being recorded')
        for name in ('build-record.json', 'source.diff', 'build.log'):
            if (build_folder / name).is_file():
                shutil.copyfile(build_folder / name, incoming / 'evidence' / name)
                os.chmod(incoming / 'evidence' / name, 0o444)
        _write_once(incoming / 'release.json', record)
        os.replace(incoming, folder)
        self._ledger(site_id, 'release.created', release=release_id, artifact_sha256=record['artifact']['sha256'],
                     record_sha256=record['record_sha256'], actor=actor)
        return {**record, 'existing': False}

    # Two source lines share releases: the preserved candidate adapter and the live-source editor.
    def _live(self):
        if not hasattr(self, '_live_store'):
            import website_studio_live
            self._live_store = website_studio_live.LiveStudio(self.sources)
        return self._live_store

    def _pin(self, adapter: str | None) -> str:
        return self._live().commit if adapter == 'live' else studio_builds.CANDIDATE

    def _drafts_for(self, site_id: str, adapter: str | None, versions: dict) -> list[str]:
        return self._live().draft_problems(site_id, versions) if adapter == 'live' else self._draft_problems(site_id, versions)

    def _source_problems(self, site_id: str, adapter: str | None) -> list[str]:
        if adapter == 'live':
            problems = self._live().verify_source(site_id)
            return ['Website source no longer verifies: ' + ' '.join(problems)] if problems else []
        revision = self.sources.revision(self.sources.site(site_id))
        return [] if revision['current'] else ['Website source no longer verifies: ' + ' '.join(revision['problems'])]

    def _draft_problems(self, site_id: str, versions: dict) -> list[str]:
        store, problems = edits.Drafts(self.sources), []
        for page, saved in versions.items():
            try:
                record = store.get(site_id, saved['version'], page)
            except KeyError:
                problems.append(f'{page} v{saved["version"]} is missing')
                continue
            if _record_sha(record) != saved['record_sha256'] or record.get('fingerprint') != saved['fingerprint']:
                problems.append(f'{page} v{saved["version"]} changed after it was built')
        return problems

    def get(self, site_id: str, release_id: str) -> dict:
        if not RELEASE_ID.fullmatch(release_id or ''):
            raise KeyError('Unknown release')
        path = self.folder(site_id) / release_id / 'release.json'
        if not path.is_file():
            raise KeyError('Unknown release')
        return json.loads(path.read_text())

    def list(self, site_id: str) -> list[dict]:
        folder = self.folder(site_id)
        rows = []
        for path in sorted(folder.glob('rel_*/release.json')) if folder.is_dir() else []:
            record = json.loads(path.read_text())
            rows.append(record)
        return sorted(rows, key=lambda r: r['created_at'], reverse=True)

    def verify(self, site_id: str, release_id: str) -> dict:
        """Re-verify everything a publication relies on. Problems are reasons to refuse."""
        record = self.get(site_id, release_id)
        problems, checks = [], []
        def check(name, ok, detail):
            checks.append({'check': name, 'ok': bool(ok), 'detail': detail})
            if not ok:
                problems.append(f'{name}: {detail}')
        body = {k: v for k, v in record.items() if k != 'record_sha256'}
        check('record_integrity', record.get('schema') == SCHEMA and sha(body) == record.get('record_sha256'),
              'release record matches its sha256' if sha(body) == record.get('record_sha256') else 'release record was changed')
        artifact = self.folder(site_id) / release_id / 'artifact.tar'
        if not artifact.is_file():
            check('artifact_present', False, 'artifact.tar is missing')
        else:
            actual = artifacts.sha256_file(artifact)
            check('artifact_checksum', actual == record['artifact']['sha256'],
                  f"sha256 {actual[:16]}… {'matches' if actual == record['artifact']['sha256'] else 'does not match'} the record")
        check('validation_evidence', (record.get('validation') or {}).get('status') == 'passed' and all(
            c['ok'] for c in record['validation']['checks']), f"build validation {(record.get('validation') or {}).get('status')}")
        evidence = self.folder(site_id) / release_id / 'evidence' / 'build-record.json'
        check('build_record', evidence.is_file() and artifacts.sha256_file(evidence) == record['build']['build_record_sha256'],
              'durable build record present and unchanged' if evidence.is_file() else 'build record missing')
        drift = self._drafts_for(site_id, record.get('adapter'), record['drafts'])
        check('saved_drafts', not drift, '; '.join(drift) or f"{len(record['drafts'])} saved versions unchanged")
        current = record['source']['commit'] == self._pin(record.get('adapter'))
        return {'ok': not problems, 'problems': problems, 'checks': checks, 'pin_current': current, 'checked_at': now()}

    # -- comparison and planning -------------------------------------------------------------
    def compare(self, site_id: str, release_id: str, target_id: str) -> dict:
        release = self.get(site_id, release_id)
        target = self.target(site_id, target_id)
        pointer = target.current()
        result = {'release': release_id, 'target': target_id, 'current': pointer, 'current_key': current_key(pointer)}
        if not pointer:
            return {**result, 'summary': 'Nothing is published on this target yet.', 'pages': [
                {'page': p, 'release': v['version'], 'published': None, 'changed': True} for p, v in release['drafts'].items()]}
        if not pointer.get('release_id'):
            return {**result, 'summary': f"Currently published: external deployment {pointer.get('deployment_id')} "
                                         f"(source {str(pointer.get('source_commit') or 'unknown')[:12]}), not a Studio release.",
                    'pages': [{'page': p, 'release': v['version'], 'published': None, 'changed': True} for p, v in release['drafts'].items()]}
        published = self.get(site_id, pointer['release_id'])
        pages = []
        for page in sorted(set(release['drafts']) | set(published['drafts'])):
            a, b = release['drafts'].get(page), published['drafts'].get(page)
            pages.append({'page': page, 'release': a and a['version'], 'published': b and b['version'],
                          'changed': (a or {}).get('fingerprint') != (b or {}).get('fingerprint')})
        same = release_id == pointer['release_id']
        return {**result, 'same': same, 'pages': pages,
                'source': {'release': release['source']['commit'], 'published': published['source']['commit']},
                'build': {'release': release['build']['next_build_id'], 'published': published['build']['next_build_id']},
                'artifact': {'release': release['artifact']['sha256'], 'published': published['artifact']['sha256']},
                'summary': 'This release is the published version.' if same else
                f"Published: {pointer['release_id']} (build {published['build']['next_build_id']})."}

    def operations(self, site_id: str, target_id: str | None = None) -> list[dict]:
        folder = self.folder(site_id) / 'operations'
        rows = [json.loads(p.read_text()) for p in folder.glob('op_*.json')] if folder.is_dir() else []
        rows = [r for r in rows if target_id is None or r['target'] == target_id]
        # Newest first by ledger sequence: the wall clock can step backwards (seen on WSL).
        return sorted(rows, key=lambda r: r.get('seq', 0), reverse=True)

    def operation(self, site_id: str, op_id: str) -> dict:
        if not OPERATION_ID.fullmatch(op_id or ''):
            raise KeyError('Unknown operation')
        path = self.folder(site_id) / 'operations' / f'{op_id}.json'
        if not path.is_file():
            raise KeyError('Unknown operation')
        return json.loads(path.read_text())

    def rollback_targets(self, site_id: str, target_id: str) -> list[str]:
        """What this target has successfully served before, most recent first (not the current one):
        Studio releases, and external deployments that a Studio operation replaced (exact deployment ids)."""
        current = current_key(self.target(site_id, target_id).current())
        seen = []
        for op in self.operations(site_id, target_id):
            if op['status'] != 'succeeded':
                continue
            candidates = [op['release']]
            previous = op.get('previous') or {}
            if previous.get('deployment_id') and not previous.get('release_id') and EXTERNAL_ID.fullmatch(previous['deployment_id']):
                candidates.append(previous['deployment_id'])
            for key in candidates:
                if key not in seen and key != current:
                    seen.append(key)
        return seen

    def _external_plan(self, site_id: str, deployment_id: str, target_id: str) -> dict:
        target = self.target(site_id, target_id)
        pointer = target.current()
        key = current_key(pointer)
        problems = []
        if deployment_id != key and deployment_id not in self.rollback_targets(site_id, target_id):
            problems.append('rollback target was never replaced by a Studio operation on this target')
        readiness = target.readiness()
        if not readiness['available']:
            problems.extend(f"target blocked ({b['id']}): {b['detail']}" for b in readiness['blockers'])
        inspected = None
        if hasattr(target, 'inspect') and not problems:
            try:
                row = target.inspect(deployment_id)
                inspected = {'state': row.get('readyState'), 'target': row.get('target'), 'url': row.get('url'),
                             'source_commit': (row.get('meta') or {}).get('githubCommitSha')}
                if row.get('readyState') != 'READY' or row.get('target') != 'production':
                    problems.append(f"deployment {deployment_id} is {row.get('readyState')} ({row.get('target')}), not a READY production deployment")
            except Exception as exc:  # provider read failed: refuse
                problems.append(f'cannot inspect {deployment_id}: {type(exc).__name__}')
        digest = sha({'action': ACTIONS['rollback'], 'site': site_id, 'release': deployment_id, 'external': True,
                      'target': target_id, 'current': key})
        return {'action': 'rollback', 'authority_action': ACTIONS['rollback'], 'site': site_id, 'release': deployment_id,
                'external': True, 'deployment': inspected, 'target': target_id, 'target_label': target.label,
                'production': target.environment == 'production', 'current': pointer, 'expected_current': key,
                'noop': key == deployment_id, 'problems': problems, 'allowed': not problems, 'digest': digest,
                'phrase': f'ROLLBACK {target_id} TO {deployment_id}', 'verification': None,
                'artifact_sha256': None, 'build_id': None, 'model_calls': 0}

    def plan(self, site_id: str, action: str, release_id: str, target_id: str) -> dict:
        if action not in ACTIONS:
            raise ReleaseError('Unknown release action')
        if action == 'rollback' and EXTERNAL_ID.fullmatch(release_id or ''):
            return self._external_plan(site_id, release_id, target_id)
        release = self.get(site_id, release_id)
        target = self.target(site_id, target_id)
        verification = self.verify(site_id, release_id)
        problems = list(verification['problems'])
        if action == 'publish' and not verification['pin_current']:
            problems.append(f"stale: built from {release['source']['commit'][:12]}; Studio is pinned to {self._pin(release.get('adapter'))[:12]}")
        readiness = target.readiness()
        pointer = target.current()
        key = current_key(pointer)
        if action == 'rollback' and release_id != key and release_id not in self.rollback_targets(site_id, target_id):
            problems.append('rollback target was never successfully published on this target')
        if not readiness['available']:
            problems.extend(f"target blocked ({b['id']}): {b['detail']}" for b in readiness['blockers'])
        if target.provider == 'vercel' and release['artifact'].get('kind') != artifacts.KIND_VERCEL:
            problems.append('this release has no Vercel Build Output; build a release candidate again to publish it to Vercel')
        digest = sha({'action': ACTIONS[action], 'site': site_id, 'release': release_id,
                      'artifact_sha256': release['artifact']['sha256'], 'manifest_sha256': release['artifact']['manifest_sha256'],
                      'record_sha256': release['record_sha256'], 'target': target_id, 'current': key})
        phrase = f'PUBLISH {release_id} TO {target_id}' if action == 'publish' else f'ROLLBACK {target_id} TO {release_id}'
        return {'action': action, 'authority_action': ACTIONS[action], 'site': site_id, 'release': release_id,
                'target': target_id, 'target_label': target.label, 'production': target.environment == 'production',
                'current': pointer, 'expected_current': key, 'noop': key == release_id, 'problems': problems,
                'allowed': not problems, 'digest': digest, 'phrase': phrase, 'verification': verification,
                'artifact_sha256': release['artifact']['sha256'], 'build_id': release['build']['next_build_id'],
                'model_calls': 0}

    # -- operations --------------------------------------------------------------------------
    def _key(self, action, site_id, release_id, target_id, expected) -> str:
        return sha({'action': action, 'site': site_id, 'release': release_id, 'target': target_id, 'expected_current': expected})

    def _save(self, site_id: str, op: dict) -> None:
        release_targets._atomic_json(self.folder(site_id) / 'operations' / f"{op['id']}.json", op)

    @contextmanager
    def _target_lock(self, site_id: str, target_id: str):
        with self._guard:
            lock = self._locks.setdefault((site_id, target_id), threading.Lock())
        if not lock.acquire(blocking=False):
            raise ReleaseError('Another publication or rollback is running on this target; wait for it to finish')
        path = self.folder(site_id) / f'.{target_id}.lock'
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open('a')
        try:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ReleaseError('Another process is publishing on this target') from None
            yield
        finally:
            handle.close()
            lock.release()

    def _reconcile(self, site_id: str, target) -> None:
        """An operation left mid-way by a crash is closed as interrupted, with the observed state."""
        for op in self.operations(site_id, target.id):
            if op['status'] not in TERMINAL and op['id'] not in self._running:
                op.update(status='interrupted', finished_at=now(), observed_current=target.current(),
                          error='The service stopped during this operation; production shows the observed state.')
                self._save(site_id, op)
                self._ledger(site_id, 'operation.interrupted', operation=op['id'], target=target.id, release=op['release'],
                             observed=current_key(op['observed_current']))
                self._receipt(site_id, op)

    def _duplicate(self, site_id: str, target_id: str, key: str) -> dict | None:
        """The earlier operation this request repeats, while its outcome is still the target's state.

        Running, or finished without a later state change on the target: the repeat returns it and
        deploys nothing. After a later publish/rollback the same words are a new request.
        """
        for op in self.operations(site_id, target_id):  # newest first
            if op['idempotency_key'] == key:
                return None if op['status'] in RETRYABLE else {**op, 'duplicate': True}
            if op['status'] in ('succeeded', 'requested', 'staging', 'staged', 'promoting'):
                return None
        return None

    def execute(self, site_id: str, action: str, release_id: str, target_id: str, expected_current: str,
                authorize, actor: str, background: bool = True) -> dict:
        """Run one authorized publish/rollback. `authorize(plan)` raises Refused unless explicitly authorized."""
        target = self.target(site_id, target_id)
        key = self._key(action, site_id, release_id, target_id, expected_current)
        duplicate = self._duplicate(site_id, target_id, key)
        if duplicate:
            return duplicate
        lock = self._target_lock(site_id, target_id)
        lock.__enter__()
        try:
            self._reconcile(site_id, target)
            duplicate = self._duplicate(site_id, target_id, key)
            if duplicate:
                lock.__exit__(None, None, None)
                return duplicate
            attempts = [op for op in self.operations(site_id, target_id) if op['idempotency_key'] == key]
            plan = self.plan(site_id, action, release_id, target_id)
            if plan['expected_current'] != expected_current:
                raise ReleaseError(f"stale comparison: the target now serves {plan['expected_current']}, not {expected_current}; compare again")
            if not plan['allowed']:
                raise ReleaseError('Refused: ' + '; '.join(plan['problems']))
            # A no-op deploys nothing, so it needs no confirmation; everything else is authorized here.
            decision = None if plan['noop'] else authorize(plan)
            attempt = 1 + max((op['attempt'] for op in attempts), default=0)
            op = {'id': f"op_{key[:16]}_{attempt}", 'attempt': attempt, 'idempotency_key': key, 'action': action,
                  'site': site_id, 'release': release_id, 'target': target_id, 'provider': target.provider,
                  'expected_current': expected_current, 'previous': plan['current'], 'digest': plan['digest'],
                  'artifact_sha256': plan['artifact_sha256'], 'build_id': plan['build_id'],
                  'authorization': decision, 'actor': actor, 'created_at': now(), 'status': 'requested',
                  'steps': [], 'deployments_created': 0, 'model_calls': 0}
            if plan['noop']:
                op.update(status='noop', finished_at=now(), detail='The target already serves this exact release; nothing was deployed.')
                op['seq'] = self._ledger(site_id, 'operation.noop', operation=op['id'], target=target_id, release=release_id)['seq']
                self._save(site_id, op)
                self._receipt(site_id, op)
                lock.__exit__(None, None, None)
                return op
            op['seq'] = self._ledger(site_id, 'operation.requested', operation=op['id'], action=action, target=target_id,
                                     release=release_id, expected_current=expected_current, digest=plan['digest'])['seq']
            self._save(site_id, op)
            self._running.add(op['id'])
        except BaseException:
            lock.__exit__(None, None, None)
            raise

        def work():
            try:
                self._run(site_id, op, target)
            finally:
                self._running.discard(op['id'])
                lock.__exit__(None, None, None)
        if background:
            threading.Thread(target=work, daemon=True).start()
            return dict(op)
        work()
        return self.operation(site_id, op['id'])

    def _step(self, site_id: str, op: dict, status: str, **detail) -> None:
        op['status'] = status
        op['steps'].append({'at': now(), 'status': status, **detail})
        self._save(site_id, op)
        self._ledger(site_id, f'operation.{status}', operation=op['id'], target=op['target'], release=op['release'],
                     **{k: v for k, v in detail.items() if isinstance(v, (str, int, bool)) or v is None})

    def _fault(self, target, release_id: str) -> str | None:
        """Test-only fault injection, read from an isolated target's faults.json and consumed once."""
        if target.provider != 'isolated':
            return None
        path = target.root / 'faults.json'
        try:
            faults = json.loads(path.read_text())
        except FileNotFoundError:
            return None
        for index, fault in enumerate(faults):
            if fault.get('release') in (None, release_id):
                faults.pop(index)
                release_targets._atomic_json(path, faults)
                return fault['step']
        return None

    def _evidence(self, site_id: str, op: dict, name: str) -> Path:
        return self.folder(site_id) / 'operation-evidence' / op['id'] / name

    def _run_external(self, site_id: str, op: dict, target) -> None:
        """Rollback to an earlier non-Studio production deployment: promote it again, then verify."""
        previous, promoted = op['previous'], False
        try:
            self._step(site_id, op, 'promoting', deployment=op['release'])
            promoted = True
            target.promote({'deployment_id': op['release']}, op['id'])
            routes = (self._last_routes(site_id, target.id) or ['/'])
            checks = target.functional(target.production_url(), {'site': None}, routes, self._evidence(site_id, op, 'production')) if hasattr(target, 'functional') else {'ok': True, 'checks': []}
            served = target.current()
            op['production_functional'] = checks
            op['published_identity'] = {'deployment_id': (served or {}).get('deployment_id'), 'matches_release': (served or {}).get('deployment_id') == op['release'],
                                        'source_commit': (served or {}).get('source_commit')}
            if not checks['ok'] or not op['published_identity']['matches_release']:
                raise TargetError('Production verification failed after rollback')
            op['finished_at'] = now()
            self._step(site_id, op, 'succeeded', deployment=op['release'], identity_matches=True)
        except Exception as exc:
            op['error'] = f'{type(exc).__name__}: {exc}'
            if promoted:
                target.restore(previous, op['id'])
            op['current_after'] = target.current()
            op['previous_available'] = current_key(op['current_after']) == current_key(previous)
            op['finished_at'] = now()
            self._step(site_id, op, 'reverted' if promoted else 'failed', error=op['error'], restored=op['previous_available'])
        finally:
            self._receipt(site_id, op)

    def _last_routes(self, site_id: str, target_id: str) -> list[str]:
        for op in self.operations(site_id, target_id):
            if op['status'] == 'succeeded' and RELEASE_ID.fullmatch(op['release']):
                return self.get(site_id, op['release'])['build']['health_routes']
        return []

    def _run(self, site_id: str, op: dict, target) -> None:
        if EXTERNAL_ID.fullmatch(op['release']):
            return self._run_external(site_id, op, target)
        release = self.get(site_id, op['release'])
        routes = release['build']['health_routes']
        build_id = release['build']['next_build_id']
        previous = op['previous']
        fault = self._fault(target, op['release'])
        promoted = False
        try:
            self._step(site_id, op, 'staging', fault=fault)
            deployment = target.stage(release, self.folder(site_id) / op['release'] / 'artifact.tar', fault=fault)
            op['deployment'] = {k: deployment[k] for k in ('deployment_id', 'artifact_sha256', 'manifest_sha256', 'build_id', 'created')}
            op['deployment'].update({k: deployment[k] for k in ('url', 'upload_digest', 'files') if k in deployment})
            op['deployment_site'] = {'site': deployment['site']} if deployment.get('site') else None
            op['deployments_created'] = int(bool(deployment['created']))
            self._step(site_id, op, 'staged', deployment=deployment['deployment_id'], created=bool(deployment['created']))
            url = target.url(deployment, fault=fault)
            staged = health(url, build_id, routes + ([INJECTED_ROUTE] if fault == 'health' else []))
            op['staged_health'] = staged
            if not staged['ok']:
                raise TargetError('Staged deployment failed its health checks; production was not changed')
            if hasattr(target, 'functional'):
                op['staged_functional'] = target.functional(url, deployment, routes, self._evidence(site_id, op, 'staged'))
                self._step(site_id, op, 'staged_checked', ok=op['staged_functional']['ok'])
                if not op['staged_functional']['ok']:
                    failed = [c['check'] for c in op['staged_functional']['checks'] if not c['ok']]
                    raise TargetError('Staged deployment failed functional checks (' + ', '.join(failed) + '); production was not changed')
            self._step(site_id, op, 'promoting', deployment=deployment['deployment_id'])
            # From here production may already have moved (even if promote() then raises): any failure restores.
            promoted = True
            pointer = target.promote(deployment, op['id'])
            produced = health(target.production_url(), build_id, routes + ([INJECTED_ROUTE] if fault == 'production_health' else []),
                              deployment=deployment['deployment_id'] if target.provider == 'isolated' else None)
            files = target.verify_files(deployment)
            if hasattr(target, 'functional'):
                op['production_functional'] = target.functional(target.production_url(), deployment, routes, self._evidence(site_id, op, 'production'))
                if not op['production_functional']['ok']:
                    produced = {**produced, 'ok': False}
            identity = {'deployment_id': pointer['deployment_id'], 'artifact_sha256': pointer['artifact_sha256'],
                        'build_id': pointer['build_id'], 'manifest_sha256': files['sha256'],
                        'matches_release': pointer['artifact_sha256'] == release['artifact']['sha256']
                        and files['sha256'] == release['artifact']['manifest_sha256'] and pointer['build_id'] == build_id}
            op['production_health'], op['published_identity'] = produced, identity
            if not produced['ok'] or not identity['matches_release']:
                raise TargetError('Production verification failed after promotion')
            op['finished_at'] = now()
            self._step(site_id, op, 'succeeded', deployment=deployment['deployment_id'], identity_matches=True)
        except Exception as exc:
            op['error'] = f'{type(exc).__name__}: {exc}'
            if promoted:
                target.restore(previous, op['id'])
            op['current_after'] = target.current() if target.provider in ('isolated', 'vercel') else previous
            restored = current_key(op['current_after']) == current_key(previous)
            if previous and previous.get('release_id') and target.provider == 'isolated':
                prior = self.get(site_id, previous['release_id'])
                op['previous_health'] = health(target.production_url(), prior['build']['next_build_id'],
                                               prior['build']['health_routes'], deployment=previous['deployment_id'])
            op['previous_available'] = restored and (not previous or op.get('previous_health', {}).get('ok', True))
            op['finished_at'] = now()
            self._step(site_id, op, 'reverted' if promoted else 'failed', error=op['error'], restored=restored)
        finally:
            if hasattr(target, 'finish'):
                target.finish(op.get('deployment_site'))
            self._receipt(site_id, op)

    def _receipt(self, site_id: str, op: dict) -> None:
        ledger = self.ledger(site_id)
        receipt = {**op, 'receipt': 'ttros.website_studio.release_operation/1', 'ledger_head': ledger['head'],
                   'ledger_intact': ledger['intact'], 'token_usage': 'not applicable: zero model calls'}
        path = self.folder(site_id) / 'receipts' / f"{op['id']}.json"
        if not path.exists():
            _write_once(path, receipt)

    def history(self, site_id: str, target_id: str) -> dict:
        target = self.target(site_id, target_id)
        return {'target': target.describe(), 'current': target.current(), 'operations': self.operations(site_id, target_id),
                'deployments': target.deployments(), 'rollback_targets': self.rollback_targets(site_id, target_id)}


# -- HTTP ------------------------------------------------------------------------------------

class CreateRequest(BaseModel):
    build_id: str = Field(pattern=r'^[a-f0-9]{32}$')


class PrepareRequest(BaseModel):
    action: str = Field(pattern=r'^(publish|rollback)$')
    target: str


class ExecuteRequest(PrepareRequest):
    expected_current: str = Field(max_length=80)
    confirmation_token: str = Field(max_length=200)
    command: str = Field(max_length=200)


def router(sources, identity, releases: Releases | None = None, root: Path | None = None):
    releases = releases or Releases(sources)
    api = APIRouter(prefix='/api/website/studio/sites/{site_id}', dependencies=[Depends(private_response)])
    from aos_paths import aos_root
    authority_root = Path(root or aos_root())

    def reader(request, mutate=False):
        who = identity.principal(request)
        if who.get('workspace') != 'ttros-operator' and who.get('method') != 'local_preview':
            raise HTTPException(403, 'Website releases require the TTROS operator')
        if mutate:
            identity.csrf(request)
        return who

    def owner(request):
        """Publishing and rollback: the verified operator identity only (no local preview, no workspace member)."""
        from remote_pc import require_operator
        identity.csrf(request)
        return require_operator(request, 'Website publication')

    def handle(call):
        try:
            return call()
        except KeyError as exc:
            raise HTTPException(404, str(exc).strip("'")) from exc
        except Refused as exc:
            raise HTTPException(403, str(exc)) from exc
        except (ReleaseError, TargetError, artifacts.ArtifactError) as exc:
            raise HTTPException(409, str(exc)) from exc

    def overview(site_id):
        targets = {}
        for ident, target in releases.targets(site_id).items():
            targets[ident] = {**target.describe(), 'current': target.current(), 'current_key': current_key(target.current()),
                              'rollback_targets': releases.rollback_targets(site_id, ident)}
        rows = []
        for record in releases.list(site_id):
            verification = releases.verify(site_id, record['release_id'])
            rows.append({k: record[k] for k in ('release_id', 'created_at', 'created_by', 'drafts', 'source', 'artifact', 'model_calls')} |
                        {'build_id': record['build']['next_build_id'], 'validation': record['validation']['status'],
                         'verified': verification['ok'], 'pin_current': verification['pin_current'],
                         'published_on': [t for t, info in targets.items() if info['current_key'] == record['release_id']]})
        ledger = releases.ledger(site_id)
        return {'releases': rows, 'targets': targets, 'ledger': {'entries': len(ledger['entries']), 'intact': ledger['intact'], 'head': ledger['head']},
                'model_calls': 0}

    @api.get('/releases')
    def index(site_id: str, request: Request):
        reader(request)
        return handle(lambda: overview(site_id))

    @api.post('/releases', status_code=201)
    def create(site_id: str, body: CreateRequest, request: Request):
        who = reader(request, True)
        return handle(lambda: releases.create(site_id, body.build_id, who.get('email') or who.get('user') or 'operator'))

    @api.get('/releases/{release_id}')
    def detail(site_id: str, release_id: str, request: Request):
        reader(request)
        return handle(lambda: {'release': releases.get(site_id, release_id), 'verification': releases.verify(site_id, release_id)})

    @api.get('/releases/{release_id}/compare')
    def compare(site_id: str, release_id: str, target: str, request: Request):
        reader(request)
        return handle(lambda: releases.compare(site_id, release_id, target))

    @api.get('/release-targets/{target_id}/history')
    def history(site_id: str, target_id: str, request: Request):
        reader(request)
        return handle(lambda: releases.history(site_id, target_id))

    @api.get('/release-operations/{op_id}')
    def operation(site_id: str, op_id: str, request: Request):
        reader(request)
        return handle(lambda: releases.operation(site_id, op_id))

    @api.post('/releases/{release_id}/prepare')
    def prepare(site_id: str, release_id: str, body: PrepareRequest, request: Request):
        operator = owner(request)
        def run():
            plan = releases.plan(site_id, body.action, release_id, body.target)
            if plan['allowed'] and not plan['noop']:
                from remote_pc import issue_confirmation
                plan['confirmation'] = issue_confirmation(plan['authority_action'], plan['digest'], operator['identity'])
            plan['operator'] = operator['identity']
            return plan
        return handle(run)

    @api.post('/releases/{release_id}/execute', status_code=202)
    def execute(site_id: str, release_id: str, body: ExecuteRequest, request: Request):
        operator = owner(request)
        def authorize(plan):
            from remote_pc import consume_confirmation
            from aos_authority import action_authorization, authenticated_website_release_command
            if body.command.strip() != plan['phrase']:
                raise Refused(f"Type the exact command to authorize this action: {plan['phrase']}")
            try:
                consume_confirmation(body.confirmation_token, plan['authority_action'], plan['digest'], operator['identity'])
            except HTTPException as exc:
                raise Refused(f'Confirmation refused: {exc.detail}') from None
            command = authenticated_website_release_command(request.headers, plan['authority_action'],
                                                            f"{site_id}:{body.target}", plan['digest'], plan['phrase'], body.command.strip())
            verdict = action_authorization(authority_root, None, action=plan['authority_action'], target=f"{site_id}:{body.target}",
                                           website_release=(command, plan['digest']))
            if not verdict['authorized']:
                raise Refused(f"Not authorized ({verdict['basis']}): {verdict['detail']}")
            return verdict
        return handle(lambda: releases.execute(site_id, body.action, release_id, body.target, body.expected_current,
                                               authorize, operator['identity']))

    return api
