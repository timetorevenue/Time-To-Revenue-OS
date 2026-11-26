"""Deployment targets for Website Studio releases. Deterministic; no models.

Every target follows the existing provider's (Vercel's) model: immutable deployments, each staged
and health-checked on its own URL, then a production alias moved atomically onto one deployment;
rollback moves the alias back onto an earlier, unchanged deployment.

- IsolatedTarget: a local, loopback-only test target with that exact model (content-addressed
  deployment folders, an atomic production pointer and a stable loopback production endpoint).
  Configured only by an explicit test/proof file (TTROS_STUDIO_RELEASE_TARGETS), never by default.
- VercelTarget: the registered core-site.example production project (website_studio_vercel). It
  stages the release's exact Vercel Build Output as one production-target deployment that no domain
  serves, health/functional/browser-checks that immutable URL, promotes that same deployment, and
  rolls back by promoting the earlier deployment again. Without the pinned tooling and a private
  deployment token it reports its blockers and refuses every deployment call.

Revisit: when the Vercel project, its credential or the Build Output support changes.
Last touched: 2026-11-22.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import http.client
import http.server
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import threading
import time
import urllib.parse

import website_studio_artifacts as artifacts
import website_studio_release_checks as release_checks
import website_studio_vercel as vercel

ROOT = Path(__file__).resolve().parents[2]


class TargetError(RuntimeError):
    """A deployment step failed; the production alias is unchanged unless stated."""


class TargetBlocked(TargetError):
    """The target cannot deploy at all (missing access or support)."""


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def _atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name('.' + path.name + '.tmp')
    with tmp.open('w', encoding='utf-8') as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


# HTTPS hosts a registered provider target has declared (its deployment URLs and production domains).
REMOTE_HOSTS: set[str] = set()


def http_get(base: str, path: str, timeout: float = 20.0) -> tuple[int, dict, bytes]:
    parts = urllib.parse.urlsplit(base)
    if parts.scheme == 'https' and parts.hostname in REMOTE_HOSTS:
        connection = http.client.HTTPSConnection(parts.hostname, parts.port or 443, timeout=timeout)
    elif parts.hostname in ('127.0.0.1', 'localhost'):
        connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=timeout)
    else:
        raise TargetError('Health checks reach loopback targets or a registered provider deployment only')
    try:
        connection.request('GET', path, headers={'User-Agent': 'ttros-studio-release-health'})
        response = connection.getresponse()
        return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
    finally:
        connection.close()


def health(base: str, build_id: str, routes: list[str], deployment: str | None = None) -> dict:
    """Identity and route health of one served build.

    Identity: Next serves /_next/static/<BUILD_ID>/_buildManifest.js only for its own build, and every
    rendered page carries the build id in its flight data. A wrong build fails both.
    """
    checks = []
    def add(check, ok, detail):
        checks.append({'check': check, 'ok': bool(ok), 'detail': detail})
    try:
        status, headers, _ = http_get(base, f'/_next/static/{build_id}/_buildManifest.js')
        add('build_identity', status == 200, f'/_next/static/{build_id}/_buildManifest.js -> {status}')
        if deployment is not None:
            served = headers.get('x-ttros-deployment')
            add('deployment_identity', served == deployment, f'x-ttros-deployment {served}')
        for route in routes:
            status, _, body = http_get(base, route)
            marker = f'\\"b\\":\\"{build_id}\\"'.encode() in body or f'"b":"{build_id}"'.encode() in body
            add(f'route {route}', status == 200 and marker,
                f'{status}, {len(body)} bytes, build id {"present" if marker else "absent"}, sha256 {hashlib.sha256(body).hexdigest()[:16]}')
    except (OSError, http.client.HTTPException, TargetError) as exc:
        add('reachable', False, f'{type(exc).__name__}: {exc}')
    return {'ok': bool(checks) and all(c['ok'] for c in checks), 'checked_at': now(), 'base': base,
            'build_id': build_id, 'checks': checks}


class _StaticHandler(http.server.BaseHTTPRequestHandler):
    """Serves a deployed Next build's prerendered output the way `next start` maps it (test targets)."""
    folder: Path

    def do_GET(self):  # noqa: N802
        path = urllib.parse.urlsplit(self.path).path
        if path.startswith('/_next/static/'):
            file = self.folder / '.next/static' / path.removeprefix('/_next/static/')
        else:
            file = self.folder / '.next/server/app' / ((path.strip('/') or 'index') + '.html')
        try:
            resolved = file.resolve()
            resolved.relative_to(self.folder.resolve())
            body = resolved.read_bytes()
        except (OSError, ValueError):
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class IsolatedTarget:
    provider = 'isolated'
    environment = 'isolated-test'

    def __init__(self, ident: str, config: dict):
        self.id = ident
        self.label = config.get('label') or f'Isolated test target {ident}'
        self.root = Path(config['root']).resolve()
        if str(self.root).startswith('/opt/ttros-core') or not self.root.is_absolute():
            raise ValueError('Isolated release targets must live outside the production checkout')
        self.server = config.get('server', 'next')
        if self.server not in ('next', 'static'):
            raise ValueError('Unknown isolated server mode')
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.processes: dict[str, tuple[object, int]] = {}
        self.proxy = None
        self.production_port = int(config.get('production_port') or 0)

    # -- provider surface ---------------------------------------------------------------------
    def describe(self) -> dict:
        return {'id': self.id, 'provider': self.provider, 'label': self.label, 'environment': self.environment,
                'production': False, 'production_url': self.production_url(), **self.readiness()}

    def readiness(self) -> dict:
        return {'available': True, 'blockers': []}

    def current(self) -> dict | None:
        try:
            return json.loads((self.root / 'production.json').read_text())
        except FileNotFoundError:
            return None

    def deployments(self) -> list[dict]:
        rows = []
        for meta in sorted((self.root / 'deployments').glob('*/deployment.json')):
            rows.append(json.loads(meta.read_text()))
        return rows

    def deployment_id(self, release: dict) -> str:
        key = f"{self.id}|{release['release_id']}|{release['artifact']['sha256']}"
        return 'dpl_' + hashlib.sha256(key.encode()).hexdigest()[:20]

    def stage(self, release: dict, artifact: Path, *, fault: str | None = None) -> dict:
        """Create (or reuse) the one immutable deployment of this exact artifact. Alias untouched."""
        ident = self.deployment_id(release)
        folder = self.root / 'deployments' / ident
        meta_path = folder / 'deployment.json'
        if fault == 'stage':
            raise TargetError('Injected test fault: staging failed before any deployment was created')
        if meta_path.is_file():
            meta = json.loads(meta_path.read_text())
            self.verify_files(meta)
            return {**meta, 'created': False}
        incoming = self.root / 'deployments' / ('.incoming-' + ident)
        shutil.rmtree(incoming, ignore_errors=True)
        site = incoming / 'site'
        found = artifacts.extract(artifact, release['artifact']['sha256'], release['artifact']['manifest_sha256'], site)
        # Runtime writes (`next start` caches) and dependencies are links to folders outside the
        # deployment; then every file and directory of the deployment is made read-only.
        runtime = self.root / 'runtime' / ident
        links = {('node_modules',): self.root / 'dependencies' / release['source']['lock_sha256'] / 'node_modules',
                 **{parts: runtime / '-'.join(parts[1:]) for parts in artifacts.RUNTIME_LINKS}}
        for parts, destination in links.items():
            link = site.joinpath(*parts)
            if link.exists() or link.is_symlink():
                raise TargetError(f'Artifact unexpectedly contains {"/".join(parts)}')
            link.parent.mkdir(parents=True, exist_ok=True)
            destination.mkdir(parents=True, exist_ok=True)
            link.symlink_to(destination)
        for current, dirs, names in os.walk(site, topdown=False):
            for name in names:
                path = Path(current) / name
                if not path.is_symlink():
                    os.chmod(path, 0o555 if os.access(path, os.X_OK) else 0o444)
            os.chmod(current, 0o555)
        meta = {'deployment_id': ident, 'target': self.id, 'release_id': release['release_id'],
                'artifact_sha256': release['artifact']['sha256'], 'manifest_sha256': found['sha256'],
                'build_id': release['build']['next_build_id'], 'lock_sha256': release['source']['lock_sha256'],
                'created_at': now(), 'files': found['count']}
        _atomic_json(incoming / 'deployment.json', meta)
        os.replace(incoming, folder)
        with (self.root / 'deployments.jsonl').open('a') as log:
            log.write(json.dumps({'at': now(), 'event': 'deployment.created', 'deployment_id': ident,
                                  'release_id': release['release_id'], 'artifact_sha256': meta['artifact_sha256']}) + '\n')
        return {**meta, 'created': True}

    def verify_files(self, meta: dict) -> dict:
        found = artifacts.manifest(self.root / 'deployments' / meta['deployment_id'] / 'site')
        if found['sha256'] != meta['manifest_sha256']:
            raise TargetError(f"Deployment {meta['deployment_id']} files changed since staging")
        return found

    def url(self, deployment: dict, fault: str | None = None) -> str:
        """Start (once) the loopback server for one deployment and return its URL."""
        ident = deployment['deployment_id']
        if fault == 'start':
            raise TargetError('Injected test fault: the deployment server did not start')
        with self.lock:
            running = self.processes.get(ident)
            if running and (self.server == 'static' or running[0].poll() is None):
                return f'http://127.0.0.1:{running[1]}'
            folder = self.root / 'deployments' / ident / 'site'
            port = _free_port()
            if self.server == 'static':
                handler = type('Handler', (_StaticHandler,), {'folder': folder})
                server = _Server(('127.0.0.1', port), handler)
                threading.Thread(target=server.serve_forever, daemon=True).start()
                self.processes[ident] = (server, port)
                return f'http://127.0.0.1:{port}'
            self._dependencies(deployment)
            modules = folder / 'node_modules'
            runtime = self.root / 'runtime' / ident
            runtime.mkdir(parents=True, exist_ok=True)
            env = {'PATH': os.environ['PATH'], 'NEXT_TELEMETRY_DISABLED': '1', 'HOME': str(runtime),
                   **release_runtime_env()}
            log = (runtime / 'server.log').open('a')
            process = subprocess.Popen(['node', str(modules / 'next/dist/bin/next'), 'start', '--hostname', '127.0.0.1', '--port', str(port)],
                                       cwd=folder, env=env, stdout=log, stderr=log, start_new_session=True)
            for _ in range(150):
                if process.poll() is not None:
                    break
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.2):
                        self.processes[ident] = (process, port)
                        return f'http://127.0.0.1:{port}'
                except OSError:
                    time.sleep(.1)
            process.terminate()
            raise TargetError('Deployment server did not start; see the isolated runtime server.log')

    def _dependencies(self, deployment: dict) -> Path:
        """Install, once per exact lockfile, the release's dependencies (npm ci, no scripts).

        All of them: `next start` loads next.config.ts through TypeScript (a devDependency).
        """
        lock = deployment['lock_sha256']
        folder = self.root / 'dependencies' / lock
        modules = folder / 'node_modules'
        if (folder / '.installed').is_file():
            return modules
        if folder.exists():
            os.chmod(folder, 0o755)
        site = self.root / 'deployments' / deployment['deployment_id'] / 'site'
        if artifacts.sha256_file(site / 'package-lock.json') != lock:
            raise TargetError('Deployment lockfile does not match the release record')
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True, exist_ok=True)
        for name in ('package.json', 'package-lock.json'):
            shutil.copyfile(site / name, folder / name)
        (folder / 'npm-user.config').write_text('')
        (folder / 'npm-global.config').write_text('')
        env = {'PATH': os.environ['PATH'], 'npm_config_cache': str(self.root / 'npm-cache'),
               'npm_config_userconfig': str(folder / 'npm-user.config'), 'npm_config_globalconfig': str(folder / 'npm-global.config')}
        with (folder / 'install.log').open('w') as log:
            code = subprocess.run(['npm', 'ci', '--ignore-scripts', '--no-audit', '--no-fund', '--prefer-offline'],
                                  cwd=folder, env=env, stdout=log, stderr=log, timeout=600).returncode
        if code:
            raise TargetError('Dependency installation from the release lockfile failed')
        (folder / '.installed').write_text(lock)
        return modules

    def promote(self, deployment: dict, operation: str) -> dict:
        """Atomically point production at one deployment (Vercel: assign the production alias)."""
        previous = self.current()
        pointer = {'deployment_id': deployment['deployment_id'], 'release_id': deployment['release_id'],
                   'artifact_sha256': deployment['artifact_sha256'], 'build_id': deployment['build_id'],
                   'operation': operation, 'promoted_at': now()}
        _atomic_json(self.root / 'production.json', pointer)
        with (self.root / 'alias.jsonl').open('a') as log:
            log.write(json.dumps({'at': now(), 'from': (previous or {}).get('deployment_id'), 'to': pointer['deployment_id'],
                                  'operation': operation}) + '\n')
        self.production_url()
        return pointer

    def restore(self, pointer: dict | None, operation: str) -> None:
        """Put the earlier production pointer back exactly (or clear it if there was none)."""
        if pointer is None:
            (self.root / 'production.json').unlink(missing_ok=True)
        else:
            _atomic_json(self.root / 'production.json', pointer)
        with (self.root / 'alias.jsonl').open('a') as log:
            log.write(json.dumps({'at': now(), 'restored': (pointer or {}).get('deployment_id'), 'operation': operation}) + '\n')

    def production_url(self) -> str:
        """Stable loopback production endpoint that always serves the aliased deployment."""
        if self.proxy is None:
            target = self

            class Proxy(http.server.BaseHTTPRequestHandler):
                def do_GET(self):  # noqa: N802
                    pointer = target.current()
                    if not pointer:
                        self.send_error(503, 'Nothing published')
                        return
                    deployments = {d['deployment_id']: d for d in target.deployments()}
                    deployment = deployments.get(pointer['deployment_id'])
                    try:
                        base = target.url(deployment)
                        status, headers, body = http_get(base, self.path)
                    except Exception:
                        self.send_error(502, 'Published deployment unavailable')
                        return
                    self.send_response(status)
                    for key in ('content-type', 'cache-control', 'etag'):
                        if key in headers:
                            self.send_header(key, headers[key])
                    self.send_header('x-ttros-deployment', pointer['deployment_id'])
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, *args):
                    pass

            self.proxy = _Server(('127.0.0.1', self.production_port), Proxy)
            self.production_port = self.proxy.server_address[1]
            threading.Thread(target=self.proxy.serve_forever, daemon=True).start()
        return f'http://127.0.0.1:{self.production_port}'

    def close(self) -> None:
        for ident, (process, _) in list(self.processes.items()):
            if hasattr(process, 'shutdown'):
                process.shutdown()
                process.server_close()
            elif process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
            self.processes.pop(ident, None)
        if self.proxy is not None:
            self.proxy.shutdown()
            self.proxy.server_close()
            self.proxy = None


def release_runtime_env() -> dict:
    """Integration switches for isolated runtime: never reach email, CRM or subscriber providers."""
    return {'GUIDE_CAPTURE_ENABLED': 'false', 'NEWSLETTER_CAPTURE_ENABLED': 'false',
            'NEWSLETTER_RELAYS_ENABLED': 'false', 'TTROS_SUBSCRIBERS_ENABLED': 'false'}


class VercelTarget:
    """The registered core-site.example Vercel project: exact prebuilt stage, promote and rollback."""
    provider = 'vercel'
    environment = 'production'
    name = 'timetorevenue-site'

    def __init__(self, ident: str, config: dict, root: Path = ROOT):
        self.id = ident
        self.label = config.get('label') or 'Vercel production'
        self.root = Path(root)
        self.config = config
        self.project = config.get('project')
        self.team = config.get('team')
        self.domains = list(config.get('production_domains') or [])
        self.client = vercel.Vercel(self.project, self.team, production_domains=self.domains)
        self.work = Path(os.environ.get('TTROS_STUDIO_VERCEL_WORK') or self.root / '.studio-preview' / 'vercel-deploy')
        self._ready_cache: tuple[float, dict] | None = None
        self._staged: dict[str, list[dict]] = {}
        REMOTE_HOSTS.update(self.domains)

    def _connections(self) -> dict:
        try:
            return json.loads((self.root / 'context/account_connections.json').read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    # -- state -------------------------------------------------------------------------------
    def _pointer(self, row: dict | None) -> dict | None:
        if not row or not row.get('id'):
            return None
        meta = row.get('meta') or {}
        return {'deployment_id': row['id'], 'release_id': meta.get('ttrosRelease'), 'artifact_sha256': meta.get('ttrosArtifact'),
                'build_id': meta.get('ttrosBuildId'), 'manifest_sha256': meta.get('ttrosManifest'),
                'url': 'https://' + row['url'] if row.get('url') else None, 'project': self.project,
                'source_commit': meta.get('ttrosSource') or meta.get('githubCommitSha'), 'studio_release': bool(meta.get('ttrosRelease'))}

    def current(self) -> dict | None:
        try:
            return self._pointer(self.client.production())
        except vercel.VercelError as exc:
            return {'deployment_id': 'unknown', 'release_id': None, 'error': str(exc)}

    def readiness(self) -> dict:
        if self._ready_cache and time.monotonic() - self._ready_cache[0] < 60:
            return self._ready_cache[1]
        blockers = [{'id': 'deployment_tooling', 'detail': p} for p in vercel.tooling_problems()]
        if not self.project or not self.team or not self.domains:
            blockers.append({'id': 'target_registration', 'detail': 'The Vercel target needs project, team and production_domains in context/website_studio_sites.json'})
        if not blockers:
            try:
                project = self.client.project()
                if project.get('id') != self.project or project.get('framework') != 'nextjs':
                    blockers.append({'id': 'project_identity', 'detail': f"Vercel returned project {project.get('id')} ({project.get('framework')})"})
                # The production domains must be verified domains of this project (a promoted deployment's
                # own alias list is empty, so the project's domain list is the authority).
                verified = {d['name'] for d in self.client.project_domains() if d.get('verified')}
                missing = [d for d in self.domains if d not in verified]
                if missing:
                    blockers.append({'id': 'production_domains', 'detail': 'Not verified domains of this Vercel project: ' + ', '.join(missing)})
            except vercel.VercelError as exc:
                blockers.append({'id': 'deployment_authorization', 'detail': f'The Vercel deployment credential cannot read the project: {exc}'})
        result = {'available': not blockers, 'blockers': blockers}
        self._ready_cache = (time.monotonic(), result)
        return result

    def describe(self) -> dict:
        return {'id': self.id, 'provider': self.provider, 'label': self.label, 'environment': self.environment,
                'production': True, 'production_url': self.production_url(), 'project': self.project,
                'model': 'vercel-prebuilt: stage on an unassigned production deployment, promote that deployment, roll back by promoting the earlier one',
                **self.readiness()}

    def deployments(self) -> list[dict]:
        try:
            rows = self.client.deployments(20)
        except vercel.VercelError:
            return []
        return [{'deployment_id': r['uid'], 'state': r.get('state'), 'target': r.get('target'), 'created': r.get('created'),
                 'url': 'https://' + r['url'], 'release_id': (r.get('meta') or {}).get('ttrosRelease'),
                 'source_commit': (r.get('meta') or {}).get('ttrosSource') or (r.get('meta') or {}).get('githubCommitSha')} for r in rows]

    def production_url(self) -> str:
        return 'https://' + (self.domains[0] if self.domains else 'invalid.invalid')

    def deployment_id(self, release: dict) -> str:
        return release['release_id']

    # -- operations --------------------------------------------------------------------------
    def stage(self, release: dict, artifact: Path, *, fault: str | None = None) -> dict:
        if not self.readiness()['available']:
            raise TargetBlocked('Vercel production deployment is blocked: ' + '; '.join(b['id'] for b in self.readiness()['blockers']))
        release_id, sha = release['release_id'], release['artifact']['sha256']
        folder = self.work / release_id
        shutil.rmtree(folder, ignore_errors=True)
        site = folder / 'site'
        artifacts.extract(artifact, sha, release['artifact']['manifest_sha256'], site)
        recorded_path = site / vercel.OUTPUT_LIST
        if not recorded_path.is_file():
            raise TargetError('This release was not built for Vercel (no recorded Build Output); build a new release candidate')
        recorded = json.loads(recorded_path.read_text())
        # Function dependencies come from the release's exact lockfile (integrity-checked by npm).
        env = {'PATH': f"{vercel.node().parent}:{os.environ.get('PATH', '')}", 'HOME': str(folder / 'home'),
               'npm_config_cache': str(self.work / 'npm-cache'), 'npm_config_userconfig': str(folder / 'npmrc'),
               'npm_config_globalconfig': str(folder / 'npmrc-global'), 'npm_config_update_notifier': 'false', 'NEXT_TELEMETRY_DISABLED': '1'}
        (folder / 'npmrc').write_text('')
        (folder / 'npmrc-global').write_text('')
        with (folder / 'npm-ci.log').open('w') as log:
            code = subprocess.run(['npm', 'ci', '--include=dev', '--no-audit', '--no-fund', '--prefer-offline'],
                                  cwd=site, env=env, stdout=log, stderr=log, timeout=900).returncode
        if code:
            raise TargetError('Reinstalling the release dependencies from its lockfile failed')
        rows = vercel.upload_set(site)
        strip = lambda rs: [[r['file'], r['sha'], r['size'], r['mode']] for r in rs]
        if strip(rows) != strip(recorded['files']) or vercel.upload_digest(rows) != recorded['digest']:
            raise TargetError('The deployment files differ from the validated build; nothing was deployed')
        existing = self.client.find_release(release_id, sha)
        if existing and (existing.get('meta') or {}).get('ttrosUpload') == recorded['digest']:
            deployment, created = existing, False
        else:
            meta = {'ttrosRelease': release_id, 'ttrosArtifact': sha, 'ttrosManifest': release['artifact']['manifest_sha256'],
                    'ttrosBuildId': release['build']['next_build_id'], 'ttrosSource': release['source']['commit'],
                    'ttrosUpload': recorded['digest'], 'ttrosStudio': 'website-studio-release'}
            self.client.upload(site, rows)
            made = self.client.create(site, rows, meta, self.name)
            deployment, created = self.client.wait_ready(made['id']), True
        if deployment.get('target') != 'production':
            raise TargetError(f"Deployment {deployment.get('id')} is not a production-target deployment")
        REMOTE_HOSTS.add(deployment['url'])
        self._staged[deployment['id']] = rows
        shutil.rmtree(site / 'node_modules', ignore_errors=True)
        return {'deployment_id': deployment['id'], 'url': 'https://' + deployment['url'], 'artifact_sha256': sha,
                'manifest_sha256': release['artifact']['manifest_sha256'], 'build_id': release['build']['next_build_id'],
                'created': created, 'upload_digest': recorded['digest'], 'files': len(rows), 'site': str(site)}

    def url(self, deployment: dict, fault: str | None = None) -> str:
        return deployment['url']

    def functional(self, base: str, deployment: dict, routes: list[str], evidence: Path) -> dict:
        REMOTE_HOSTS.add(urllib.parse.urlsplit(base).hostname)
        return release_checks.functional(base, Path(deployment['site']) if deployment.get('site') else None, routes, evidence)

    def promote(self, deployment: dict, operation: str) -> dict:
        self.client.promote(deployment['deployment_id'])
        row = self.client.deployment(deployment['deployment_id'])
        self._await_serving(row)
        return self._pointer({**row, 'id': row.get('id') or deployment['deployment_id']})

    def _await_serving(self, row: dict, timeout: float = 240) -> None:
        """The alias moves at once in the API; the edge follows within seconds. Wait until every production
        domain serves this deployment's own build (Next marks each page with its build id)."""
        import re as _re
        url = 'https://' + row['url']
        REMOTE_HOSTS.add(row['url'])
        _, _, body = http_get(url, '/')
        found = _re.search(rb'<!DOCTYPE html><!--([A-Za-z0-9_-]{8,64})-->', body)
        if not found:
            raise TargetError(f"Deployment {row.get('id')} serves no build marker; cannot confirm production")
        marker = found.group(0)
        deadline = time.monotonic() + timeout
        waiting = list(self.domains)
        def serves(domain):
            status, headers, body = http_get('https://' + domain, '/')
            if status in (301, 302, 307, 308):  # e.g. the apex redirecting to www: served by the domain it names
                target = urllib.parse.urlsplit(headers.get('location', '')).hostname
                return target in self.domains and target != domain
            return marker in body
        while waiting:
            waiting = [d for d in waiting if not serves(d)]
            if not waiting:
                return
            if time.monotonic() > deadline:
                raise TargetError(f"Production domains {', '.join(waiting)} did not serve {row.get('id')} within {int(timeout)} s")
            time.sleep(3)

    def verify_files(self, deployment: dict) -> dict:
        """The served deployment is the staged one, carries this release's identity and its exact uploaded files."""
        row = self.client.deployment(deployment['deployment_id'])
        meta = row.get('meta') or {}
        current = (self.client.production() or {}).get('id')
        ok = (current == deployment['deployment_id'] and meta.get('ttrosArtifact') == deployment['artifact_sha256']
              and meta.get('ttrosUpload') == deployment['upload_digest'])
        detail = f'production {current}, release meta {"matches" if ok else "differs"}'
        rows = self._staged.get(deployment['deployment_id'])
        if ok and rows is not None:
            try:
                served = {f['file']: f['sha'] for f in self.client.deployment_files(deployment['deployment_id'])}
                expected = {r['file']: r['sha'] for r in rows}
                ok = all(served.get(k) == v for k, v in expected.items())
                detail += f', {len(expected)} uploaded files {"match" if ok else "differ"}'
            except vercel.VercelError as exc:
                detail += f', file listing unavailable ({str(exc)[:80]}); upload digest recorded in deployment meta'
        return {'sha256': deployment['manifest_sha256'] if ok else 'mismatch', 'detail': detail}

    def restore(self, pointer: dict | None, operation: str) -> None:
        if pointer and pointer.get('deployment_id') and pointer['deployment_id'] != 'unknown':
            self.client.promote(pointer['deployment_id'])
            self._await_serving(self.client.deployment(pointer['deployment_id']))

    def inspect(self, deployment_id: str) -> dict:
        return self.client.deployment(deployment_id)

    def finish(self, deployment: dict | None) -> None:
        if deployment and deployment.get('site'):
            shutil.rmtree(Path(deployment['site']).parent, ignore_errors=True)

    def close(self) -> None:
        pass


def load_targets(site: dict, root: Path = ROOT) -> dict:
    """Registered production targets plus explicitly configured isolated test targets."""
    targets = {}
    for ident, config in (site.get('release_targets') or {}).items():
        if config.get('provider') != 'vercel':
            raise ValueError('Registered release targets are provider deployments; isolated targets come from a test file')
        targets[ident] = VercelTarget(ident, config, root)
    path = os.environ.get('TTROS_STUDIO_RELEASE_TARGETS')
    if path:
        for ident, config in json.loads(Path(path).read_text()).items():
            if config.get('provider') != 'isolated' or ident in targets:
                raise ValueError('The test target file may only add isolated targets')
            targets[ident] = IsolatedTarget(ident, config)
    return targets
