"""Vercel production deployments for Website Studio releases. Deterministic; no models.

The registered core-site.example Vercel project receives exactly the validated release build:

- Build: the release build runs `vercel build --prod` (pinned Vercel CLI, pinned Node matching the
  project's runtime) offline, from project settings read here (no `vercel pull`, no environment
  secrets: BREVO_API_KEY stays a sensitive runtime variable that Vercel attaches to functions).
  It writes `studio-vercel-output.json`: every file `vercel deploy --prebuilt` would upload
  (`.vercel/output/**` plus the dependency files each function maps), with sha1/sha256/size/mode.
- Stage: the release artifact is extracted and re-verified, dependencies are reinstalled from its
  exact lockfile, the upload set is recomputed and must equal the recorded one file for file; the
  files are uploaded (content-addressed) and one production-target deployment is created with
  `autoAssignCustomDomains: false`, so it is live only on its own immutable URL.
- Promote / rollback: the project's production domains are moved onto an existing READY deployment
  (`/v10/projects/{id}/promote/{deployment}`); nothing is rebuilt. Rolling back promotes the earlier
  deployment again, so the exact earlier bytes serve.

The token is read from a 0600 file (TTROS_VERCEL_TOKEN_FILE) only inside requests and never logged,
recorded or returned. The Vercel CLI is used only for the offline build: the provisioned token is
project-scoped and cannot read the user/team endpoints the CLI's deploy/promote commands need.
Revisit: when the Vercel project, CLI pin, runtime or deployment API changes. Last touched: 2026-11-22.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

API = 'https://api.vercel.com'
HOME = Path(os.path.expanduser('~'))
TOKEN_FILE = HOME / '.config/ttros-vercel-deploy.token'
CLI = HOME / '.local/share/ttros/vercel-cli-53.3.2/node_modules/.bin/vercel'
NODE = HOME / '.local/share/ttros/node-24.22.2/node_modules/.bin/node'
OUTPUT_LIST = 'studio-vercel-output.json'
SETTINGS = ('createdAt', 'framework', 'devCommand', 'installCommand', 'buildCommand', 'outputDirectory', 'rootDirectory',
            'directoryListing', 'nodeVersion')


class VercelError(RuntimeError):
    """A Vercel request failed. Messages never contain the credential."""


def token_file() -> Path:
    return Path(os.environ.get('TTROS_VERCEL_TOKEN_FILE') or TOKEN_FILE)


def cli() -> Path:
    return Path(os.environ.get('TTROS_VERCEL_CLI') or CLI)


def node() -> Path:
    return Path(os.environ.get('TTROS_VERCEL_NODE') or NODE)


def tooling_problems() -> list[str]:
    problems = []
    path = token_file()
    if not path.is_file():
        problems.append(f'no Vercel deployment token file ({path})')
    elif stat.S_IMODE(path.stat().st_mode) & 0o077:
        problems.append(f'Vercel token file {path} must be private (chmod 600)')
    if not cli().is_file():
        problems.append(f'pinned Vercel CLI missing ({cli()})')
    if not node().exists():
        problems.append(f'pinned Node runtime missing ({node()})')
    return problems


def build_request(client: 'Vercel', domain: str) -> dict:
    """What a release build needs to run `vercel build --prod` offline (no secrets)."""
    entry = Path(os.path.realpath(cli()))
    return {'cli': str(entry), 'node': str(node()), 'project': client.build_project_json(),
            'env': {'VERCEL': '1', 'VERCEL_ENV': 'production', 'VERCEL_TARGET_ENV': 'production', 'VERCEL_PROJECT_PRODUCTION_URL': domain}}


def record_output(folder: Path) -> dict | None:
    """Write studio-vercel-output.json (the exact upload set) into a Vercel-built release fixture."""
    folder = Path(folder)
    if not (folder / '.vercel/output/config.json').is_file():
        return None
    rows = upload_set(folder)
    record = {'schema': 'ttros.website_studio.vercel_output/1', 'files': rows, 'digest': upload_digest(rows),
              'count': len(rows), 'bytes': sum(r['size'] for r in rows)}
    (folder / OUTPUT_LIST).write_text(json.dumps(record, indent=1, sort_keys=True))
    return record


# ------------------------------------------------------------------------------ upload set
def _walk(root: Path, base: Path) -> list[Path]:
    found = []
    for current, dirs, names in os.walk(root, followlinks=False):
        here = Path(current)
        for name in list(dirs):
            if (here / name).is_symlink():
                found.append(here / name)  # uploaded as a link entry, not descended (as the CLI does)
                dirs.remove(name)
        found.extend(here / n for n in names)
    return found


def upload_set(folder: Path) -> list[dict]:
    """What `vercel deploy --prebuilt` uploads from `folder`: .vercel/output/** + mapped function files."""
    folder = Path(folder)
    out = folder / '.vercel/output'
    if not (out / 'config.json').is_file():
        raise VercelError('No Vercel Build Output (.vercel/output/config.json); the release was not built with `vercel build`')
    paths = set(_walk(out, folder))
    configs = [Path(c) / '.vc-config.json' for c, _, names in os.walk(out, followlinks=False) if '.vc-config.json' in names]
    for config in sorted(configs):
        mapping = json.loads(config.read_text()).get('filePathMap') or {}
        for value in mapping.values():
            target = folder / value
            normal = os.path.normpath(value)
            if os.path.isabs(normal) or normal == '..' or normal.startswith('../'):
                raise VercelError(f'Function file outside the build: {value}')
            paths.add(target)
    routes = folder / '.vercel/routes.json'
    if routes.is_file():
        paths.add(routes)
    rows = []
    for path in sorted(paths, key=lambda p: p.relative_to(folder).as_posix()):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            data = os.readlink(path).encode()
        elif stat.S_ISREG(info.st_mode):
            data = path.read_bytes()
        else:
            raise VercelError(f'Unexpected special file in the deployment: {path.relative_to(folder)}')
        # Modes as the immutable artifact stores them (0644/0755 files, 0777 links), so the set computed in
        # the build and after re-extracting the artifact is identical whatever the build's umask was.
        mode = 0o120777 if stat.S_ISLNK(info.st_mode) else (0o100755 if info.st_mode & 0o111 else 0o100644)
        rows.append({'file': path.relative_to(folder).as_posix(), 'sha': hashlib.sha1(data).hexdigest(),
                     'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data), 'mode': mode})
    return rows


def upload_digest(rows: list[dict]) -> str:
    body = json.dumps([[r['file'], r['sha'], r['size'], r['mode']] for r in rows], separators=(',', ':'))
    return hashlib.sha256(body.encode()).hexdigest()


# ------------------------------------------------------------------------------ API client
class Vercel:
    def __init__(self, project: str, team: str, *, production_domains: list[str] | None = None, timeout: float = 60.0):
        self.project_id = project
        self.team_id = team
        self.domains = list(production_domains or [])
        self.timeout = timeout

    def _token(self) -> str:
        try:
            value = token_file().read_text().strip()
        except OSError:
            raise VercelError('Vercel deployment token is not provisioned') from None
        if not value:
            raise VercelError('Vercel deployment token is empty')
        return value

    def request(self, method: str, path: str, *, body=None, data: bytes | None = None, headers: dict | None = None,
                query: dict | None = None, timeout: float | None = None, ok=(200, 201, 202, 204)):
        params = {'teamId': self.team_id, **(query or {})}
        url = f'{API}{path}?{urllib.parse.urlencode(params)}'
        payload = data if data is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(url, data=payload, method=method)
        req.add_header('Authorization', 'Bearer ' + self._token())
        req.add_header('User-Agent', 'ttros-website-studio-release')
        if body is not None:
            req.add_header('Content-Type', 'application/json')
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read()
                status = resp.status
        except urllib.error.HTTPError as exc:
            raw, status = exc.read(), exc.code
        except (urllib.error.URLError, OSError) as exc:
            raise VercelError(f'{method} {path}: {type(exc).__name__}') from None
        try:
            result = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            result = {'raw': raw[:200].decode(errors='replace')}
        if status not in ok:
            err = result.get('error') if isinstance(result, dict) else None
            detail = f"{err.get('code')}: {err.get('message')}" if isinstance(err, dict) else str(result)[:300]
            error = VercelError(f'{method} {path} -> {status} {detail}')
            error.status, error.result = status, result
            raise error
        return status, result

    # -- reads ------------------------------------------------------------------------------
    def project(self) -> dict:
        return self.request('GET', f'/v9/projects/{self.project_id}')[1]

    def build_project_json(self) -> dict:
        p = self.project()
        return {'projectId': p['id'], 'orgId': p['accountId'], 'settings': {k: p.get(k) for k in SETTINGS}}

    def project_domains(self) -> list[dict]:
        return self.request('GET', f'/v9/projects/{self.project_id}/domains')[1].get('domains', [])

    def deployment(self, ident: str) -> dict:
        return self.request('GET', f'/v13/deployments/{urllib.parse.quote(ident)}')[1]

    def production(self) -> dict | None:
        return (self.project().get('targets') or {}).get('production')

    def deployments(self, limit: int = 20) -> list[dict]:
        return self.request('GET', '/v6/deployments', query={'projectId': self.project_id, 'limit': str(limit)})[1].get('deployments', [])

    def find_release(self, release_id: str, artifact_sha256: str) -> dict | None:
        """An existing READY deployment of exactly this release (stage is idempotent)."""
        for row in self.deployments(100):
            meta = row.get('meta') or {}
            if meta.get('ttrosRelease') == release_id and meta.get('ttrosArtifact') == artifact_sha256 and row.get('state') == 'READY':
                return self.deployment(row['uid'])
        return None

    # -- writes -----------------------------------------------------------------------------
    def upload(self, folder: Path, rows: list[dict]) -> int:
        unique = {}
        for r in rows:
            unique.setdefault(r['sha'], r)

        def put(r):
            path = Path(folder) / r['file']
            data = os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
            if hashlib.sha1(data).hexdigest() != r['sha']:
                raise VercelError(f"{r['file']} changed before upload")
            self.request('POST', '/v2/files', data=data, timeout=300,
                         headers={'Content-Type': 'application/octet-stream', 'x-vercel-digest': r['sha'], 'Content-Length': str(len(data))})
            return 1
        with ThreadPoolExecutor(max_workers=8) as pool:
            return sum(pool.map(put, unique.values()))

    def create(self, folder: Path, rows: list[dict], meta: dict, name: str) -> dict:
        body = {'name': name, 'project': self.project_id, 'target': 'production', 'source': 'cli',
                'files': [{'file': r['file'], 'sha': r['sha'], 'size': r['size'], 'mode': r['mode']} for r in rows],
                'meta': meta, 'autoAssignCustomDomains': False, 'env': {}, 'build': {'env': {}},
                'projectSettings': {'sourceFilesOutsideRootDirectory': True}}
        query = {'skipAutoDetectionConfirmation': '1', 'forceNew': '1'}
        try:
            return self.request('POST', '/v13/deployments', body=body, query=query, timeout=300)[1]
        except VercelError as exc:
            if getattr(exc, 'status', None) == 400 and ((getattr(exc, 'result', {}) or {}).get('error') or {}).get('code') == 'missing_files':
                missing = set(exc.result['error'].get('missing') or [])
                self.upload(folder, [r for r in rows if r['sha'] in missing])
                return self.request('POST', '/v13/deployments', body=body, query=query, timeout=300)[1]
            raise

    def wait_ready(self, ident: str, timeout: float = 900) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            row = self.deployment(ident)
            state = row.get('readyState') or row.get('status')
            if state == 'READY':
                return row
            if state in ('ERROR', 'CANCELED'):
                raise VercelError(f"Deployment {ident} ended {state}: {(row.get('errorMessage') or '')[:300]}")
            if time.monotonic() > deadline:
                raise VercelError(f'Deployment {ident} not ready after {int(timeout)} s (state {state})')
            time.sleep(4)

    def promote(self, ident: str, timeout: float = 300) -> dict:
        """Move the production domains onto an existing READY production deployment (no rebuild)."""
        self.request('POST', f'/v10/projects/{self.project_id}/promote/{urllib.parse.quote(ident)}', body={}, timeout=120)
        deadline = time.monotonic() + timeout
        while True:
            current = self.production() or {}
            if current.get('id') == ident:
                return current
            if time.monotonic() > deadline:
                raise VercelError(f'Production did not move to {ident} within {int(timeout)} s (serving {current.get("id")})')
            time.sleep(3)

    def deployment_files(self, ident: str) -> list[dict]:
        """Flattened uploaded file tree of a deployment: [{file, sha}] (directories expanded)."""
        _, tree = self.request('GET', f'/v6/deployments/{urllib.parse.quote(ident)}/files', timeout=120)
        out = []

        def walk(nodes, prefix):
            for n in nodes or []:
                path = f"{prefix}{n['name']}"
                if n.get('type') == 'directory':
                    walk(n.get('children'), path + '/')
                else:
                    out.append({'file': path, 'sha': n.get('uid'), 'mode': n.get('mode')})
        walk(tree if isinstance(tree, list) else tree.get('files') or [], '')
        # Uploaded files are listed under `src/` (the build output Vercel derived is not compared).
        return [{**f, 'file': f['file'][4:]} for f in out if f['file'].startswith('src/')]
