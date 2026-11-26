"""Non-executing website work orders; aos_jobs remains the only execution owner.

Prepared snapshots bind a registered repository, scope, inputs and Git baseline.
No model, connector, publish or deploy is called here. Revisit on website executor
or project registry contract changes. Last touched: 2026-11-16.
"""
from __future__ import annotations

import argparse
import fcntl
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import aos_website as web
import aos_website_seo as seo
from aos_paths import AuthorityError, assert_authoritative_root
from aos_queue_storage import durable_replace_text

ACTIONS = ['edit', 'test', 'build', 'local_preview', 'local_commit']
ORDERS = Path('results/website/preparations')
ID = re.compile(r'prep-[a-f0-9]{32}\Z')


def safe_path(root: Path, relative: str) -> Path:
    """Reject traversal, protected files and symlinks, including in output parents."""
    if not isinstance(relative, str) or not relative or '\\' in relative or ':' in relative:
        raise web.WebsiteError('unsafe path: use a repository-relative POSIX path')
    parts = relative.split('/')
    if relative.startswith('/') or any(p in ('', '.', '..') for p in parts):
        raise web.WebsiteError(f'unsafe path: {relative}')
    current = Path(root)
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise web.WebsiteError(f'unsafe symlink: {relative}')
    if not current.resolve().is_relative_to(Path(root).resolve()):
        raise web.WebsiteError(f'unsafe path: {relative}')
    if any(fnmatch.fnmatchcase(relative, p) for p in web.ALWAYS_FORBIDDEN) or '.git' in parts:
        raise web.WebsiteError(f'protected path: {relative}')
    return current


def _path(root: Path, identity: str) -> Path:
    if not ID.fullmatch(identity):
        raise web.WebsiteError('invalid preparation id')
    return safe_path(root, f'{ORDERS}/{identity}.json')


def _fingerprint(project: dict) -> str:
    return web._sha(json.dumps(project, sort_keys=True).encode())


def inspect(root: Path, order: dict, *, job_id: str = '') -> dict:
    """Pure deterministic readiness; commands and models are never run by inspection."""
    problems, hashes = [], {}
    try:
        project_id, project = web.resolve_project(root, order.get('project', ''))
        state, why = web.project_state(project)
        if state != 'ready':
            raise web.WebsiteError(why)
        repo = Path(project['repository'])
        assert_authoritative_root(repo)
        if str(repo.resolve()) != order.get('repository'):
            raise web.WebsiteError('repository identity differs from the registered project')
        if web._git(repo, 'rev-parse', '--show-toplevel') != str(repo.resolve()):
            raise web.WebsiteError('registered repository must be the Git top level')
        if not project.get('client_scope') or order.get('client_scope') != project['client_scope']:
            raise web.WebsiteError('client-scope mismatch')
        if not project.get('allowed_paths'):
            problems.append('project has no allowed paths')
        if not isinstance(order.get('request'), str) or not 8 <= len(order['request'].strip()) <= 1000:
            problems.append('a bounded change request is required')
        if order.get('allowed_actions') != ACTIONS:
            problems.append('allowed actions must be local development only; publish/deploy is unavailable')
        branch = order.get('branch') or ''
        if not branch.startswith('feature/') or subprocess.run(
                ['git', 'check-ref-format', '--branch', branch], capture_output=True).returncode:
            problems.append('branch must be a valid feature/ branch')
        elif web._git(repo, 'branch', '--list', branch):
            own = Path(root) / web.RESULTS / web.storage_key(job_id) / 'worktree' if job_id else None
            if not own or not own.exists() or web._git(own, 'rev-parse', '--abbrev-ref', 'HEAD') != branch or \
                    web._git(own, 'rev-parse', '--path-format=absolute', '--git-common-dir') != web._git(
                        repo, 'rev-parse', '--path-format=absolute', '--git-common-dir'):
                problems.append('branch already exists or belongs to another workspace')
        web.dependency_cache(project, repo)
        pre = web.preflight(project)
        if pre['dirty']:
            problems.append('repository has conflicting uncommitted or untracked files')
        if pre['head'] != pre['base_sha']:
            problems.append('checkout HEAD differs from the registered base')
        if pre['upstream'].get('behind', 0):
            problems.append('local checkout is behind its locally known upstream')
        for key, command in [('test_command', project.get('test_command')), ('preview command', (project.get('preview') or {}).get('command'))]:
            if key == 'test_command' and not command:
                problems.append('project test_command is required')
            if command and (not isinstance(command, list) or not all(isinstance(x, str) and x for x in command)):
                problems.append(f'{key} must be a non-empty argument list')
            elif command:
                executable = sys.executable if command[0] == '{python}' else command[0]
                if not shutil.which(executable):
                    problems.append(f'{key} executable is unavailable: {command[0]}')
                if len(command) > 1 and command[1].endswith('.py') and not safe_path(repo, command[1]).is_file():
                    problems.append(f'{key} script is missing: {command[1]}')
        preview = project.get('preview') or {}
        limit = preview.get('max_file_bytes', web.PREVIEW_MAX_BYTES)
        if type(limit) is not int or not 0 < limit <= web.PREVIEW_FILE_CEILING:
            problems.append('preview max_file_bytes must be between 1 and 8000000')
        if preview.get('url_prefix') and not re.fullmatch(r'/(?:[A-Za-z0-9_-]+/)*', preview['url_prefix']):
            problems.append('preview url_prefix must be an absolute URL directory without traversal')
        if not preview.get('dir') or not preview.get('entry'):
            problems.append('a local preview directory and entry are required')
        else:
            safe_path(repo, preview['dir'])
            safe_path(repo, preview['dir'] + '/' + preview['entry'])
        affected = order.get('affected_paths') or []
        if not isinstance(affected, list) or not affected:
            problems.append('affected pages/assets are required')
            affected = []
        for path in affected:
            safe_path(repo, path)
        problems.extend(web.scope_problems(project, affected))
        problems.extend(seo.validate_submission(order.get('seo_strategy'), order))
        checks = order.get('acceptance_checks') or []
        if not isinstance(checks, list) or not checks:
            problems.append('at least one deterministic acceptance check is required')
            checks = []
        for check in checks:
            if not isinstance(check, dict) or check.get('path') not in affected or not isinstance(check.get('contains'), str) or not check['contains'].strip():
                problems.append('each acceptance check needs an affected path and non-empty expected text')
        sources = order.get('source_references') or []
        if not isinstance(sources, list) or not sources:
            problems.append('source references are required')
            sources = []
        icm_files = project.get('icm_files') or []
        if not icm_files:
            problems.append('register at least one local brand/style/ICM file')
        for relative in set(sources + icm_files + (project.get('required_assets') or [])):
            path = safe_path(repo, relative)
            if not path.is_file():
                problems.append(f'required source/asset missing: {relative}')
            else:
                hashes[relative] = web._sha(path.read_bytes())
        icm = web.gather_icm(project, repo)
        problems.extend(icm['problems'])
        for name in project.get('required_environment') or []:
            if not re.fullmatch(r'[A-Z][A-Z0-9_]*', name) or not os.environ.get(name):
                problems.append(f'required environment unavailable: {name} (values are never recorded)')
        safe_path(root, f'{web.RESULTS}/preparations')
        snapshot = {'head': pre['head'], 'base_sha': pre['base_sha'], 'project_sha256': _fingerprint(project), 'sources': hashes}
        if order.get('snapshot') and order['snapshot'] != snapshot:
            problems.append('stale Git, project configuration or source snapshot; prepare a new work order')
        return {'state': 'blocked' if problems else 'prepared', 'problems': problems, 'snapshot': snapshot,
                'preflight': pre, 'icm': {k: v for k, v in icm.items() if k != 'files'} | {
                    'files': [{'path': r['path'], 'sha256': r['sha256']} for r in icm['files']]},
                'external_steps': project.get('external_requirements') or [], 'checked_at': web._now()}
    except (AuthorityError, web.WebsiteError, OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
        return {'state': 'blocked', 'problems': problems + [str(exc)], 'checked_at': web._now()}


def prepare(root: Path, inputs: dict, *, draft: bool = False) -> dict:
    root = Path(root)
    assert_authoritative_root(root)
    identity = 'prep-' + uuid.uuid4().hex
    fields = ('project', 'repository', 'client_scope', 'request', 'branch', 'affected_paths', 'acceptance_checks', 'source_references', 'seo_strategy')
    order = {k: inputs.get(k) for k in fields}
    order.update(id=identity, version=1, created_at=web._now(), allowed_actions=inputs.get('allowed_actions', ACTIONS),
                 publish={'status': 'held', 'detail': web.PUBLISH_HOLD}, released=False)
    validation = inspect(root, order)
    if validation.get('state') == 'prepared':
        order['project'] = web.resolve_project(root, order['project'])[0]
    order.update(validation=validation, snapshot=validation.get('snapshot'), state='draft' if draft else validation['state'])
    if order['state'] == 'prepared':
        order['executor'] = web.executor_for(order['project'], order['request'])
        order['executor']['input']['preparation'] = {k: v for k, v in order.items() if k not in ('executor', 'validation')}
    durable_replace_text(_path(root, identity), json.dumps(order, indent=2) + '\n')
    return view(root, order)


def load(root: Path, identity: str) -> dict:
    try:
        return json.loads(_path(root, identity).read_text())
    except (OSError, ValueError) as exc:
        raise web.WebsiteError(f'preparation unavailable: {identity}') from exc


def view(root: Path, order: dict) -> dict:
    import aos_jobs
    result = dict(order)
    result['artifact_path'] = _path(root, order['id']).relative_to(root).as_posix()
    if order.get('job_id'):
        try:
            job = web.job_view(root, aos_jobs.load(root, order['job_id']))
        except aos_jobs.JobError as exc:
            result.update(state='blocked', validation={'problems': [f'linked job unavailable: {exc}']})
            return result
        result['job'] = job
        result['state'] = job.get('readiness') or ('blocked' if job['status'] in ('failed', 'refused', 'interrupted') else 'prepared')
    elif order['state'] != 'draft':
        result['validation'] = inspect(root, order)
        result['state'] = result['validation']['state']
    return result


def list_orders(root: Path) -> list[dict]:
    folder = safe_path(root, ORDERS.as_posix())
    rows = []
    for path in sorted(folder.glob('prep-*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:50]:
        try:
            rows.append(view(root, load(root, path.stem)))
        except (web.WebsiteError, OSError, ValueError) as exc:
            rows.append({'id': path.stem, 'state': 'blocked', 'created_at': '',
                         'artifact_path': path.relative_to(root).as_posix(),
                         'validation': {'problems': [str(exc)]}})
    return sorted(rows, key=lambda row: row['created_at'], reverse=True)[:50]


def queue(root: Path, identity: str) -> dict:
    """Allocate once through aos_jobs; do not launch a worker from this module."""
    import aos_jobs
    root = Path(root)
    assert_authoritative_root(root)
    path = _path(root, identity)
    lock_path = safe_path(root, path.with_suffix('.lock').relative_to(root).as_posix())
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        order = load(root, identity)
        if order.get('job_id'):
            return aos_jobs.load(root, order['job_id'])
        # Recover publication interrupted after aos_jobs allocated its id.
        found = next((j for j in aos_jobs.list_jobs(root, limit=None)
                      if (((j.get('executor') or {}).get('input') or {}).get('preparation') or {}).get('id') == identity), None)
        if found:
            job = found
        else:
            validation = inspect(root, order)
            if order['state'] != 'prepared' or validation['state'] != 'prepared':
                raise web.WebsiteError('; '.join(validation['problems']) or 'save a prepared work order first')
            job, _ = aos_jobs.create(root, conversation_id='dashboard-websites', topic='website', request=order['request'],
                                    executor=order['executor'], dedupe_key=f'website-preparation:{identity}',
                                    restrictions=['no_brain_writes', 'no_connector_writes'])
        order['job_id'] = job['id']
        durable_replace_text(path, json.dumps(order, indent=2) + '\n')
        return job


def acceptance(order: dict, worktree: Path, changed: list[str]) -> list[str]:
    problems = [f'unrelated change outside the request: {p}' for p in changed if p not in order['affected_paths']]
    for relative in changed:
        try:
            safe_path(worktree, relative)
        except web.WebsiteError as exc:
            problems.append(str(exc))
    for check in order['acceptance_checks']:
        try:
            path = safe_path(worktree, check['path'])
            if not path.is_file() or check['contains'] not in path.read_text(encoding='utf-8', errors='replace'):
                problems.append(f'acceptance failed: {check["path"]} must contain {check["contains"]!r}')
        except web.WebsiteError as exc:
            problems.append(str(exc))
    if order.get('seo_strategy'):
        strategy = order['seo_strategy']
        try:
            source = safe_path(worktree, strategy['target_path']).read_text(encoding='utf-8')
            problems.extend(seo.output_quality_issues(strategy, source))
        except (OSError, ValueError, TypeError, KeyError, web.WebsiteError) as exc:
            problems.append(f'SEO source check unavailable: {exc}')
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    sub = parser.add_subparsers(dest='action', required=True)
    new = sub.add_parser('prepare')
    new.add_argument('input', type=Path)
    new.add_argument('--draft', action='store_true')
    for action in ('show', 'queue'):
        sub.add_parser(action).add_argument('id')
    sub.add_parser('list')
    args = parser.parse_args()
    try:
        if args.action == 'prepare':
            result = prepare(args.root, json.loads(args.input.read_text()), draft=args.draft)
        elif args.action == 'show':
            result = view(args.root, load(args.root, args.id))
        elif args.action == 'queue':
            result = queue(args.root, args.id)
        else:
            result = list_orders(args.root)
        print(json.dumps(result, indent=2))
    except (web.WebsiteError, OSError, ValueError) as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    main()
