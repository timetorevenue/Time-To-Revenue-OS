"""Explicit isolated Next previews of immutable, existing Puck server drafts.

Operator only; one local build at a time. No AI, credentials, provider adapters or
website checkout writes. Built processes bind loopback and expire after 30 minutes.
A 'release' build keeps the website's own next.config.ts, is health-checked on its loopback
server, packaged as one deterministic artifact (website_studio_artifacts) and recorded durably
in build-record.json, so website_studio_releases can turn exactly that build into a release.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import threading
import time
import uuid

import ttros_site
from fastapi import APIRouter, Depends, HTTPException, Request
from dashboard_identity import private_response
from typing import Literal
from pydantic import BaseModel, Field
import website_studio_edits as edits
import website_studio_artifacts as artifacts

CANDIDATE = '215679e81711553cf572acff71b60f42f2b2181d'
ROOT = Path(__file__).resolve().parents[2]


class BuildRequest(BaseModel):
    page: str = 'home'
    version: int = Field(gt=0)
    shared_version: int = Field(gt=0)
    # Schema v5: saved service-page versions to include ({page: version}); others keep their source.
    pages: dict[str, int] = Field(default_factory=dict)
    acknowledge_contrast: bool = False
    # 'release': the website's own configuration, packaged and validated so it can become a release.
    purpose: Literal['preview', 'release'] = 'preview'


# Non-secret build environment: every provider integration disabled, no credentials inherited.
BUILD_ENV = {'NEXT_TELEMETRY_DISABLED': '1', 'GUIDE_CAPTURE_ENABLED': 'false', 'NEWSLETTER_CAPTURE_ENABLED': 'false',
             'NEWSLETTER_RELAYS_ENABLED': 'false', 'TTROS_SUBSCRIBERS_ENABLED': 'false'}


def build_environment() -> dict:
    return {'profile': 'isolated-integrations-disabled', 'variables': dict(BUILD_ENV),
            'sha256': hashlib.sha256(json.dumps(BUILD_ENV, sort_keys=True).encode()).hexdigest()}


class Builds:
    # Live-source builds (website_studio_live.LiveBuilds) reuse this pipeline with their own script,
    # pinned commit and health routes.
    script = ROOT / 'dashboard/frontend/scripts/studio-preview-build.mjs'
    pinned = CANDIDATE

    def health_routes(self, row, result):
        return ['/' if p == 'home' else edits.section_model.PAGES[p]['route'] for p in row['versions'] if p != 'site']

    def __init__(self, sources, root=None):
        self.sources = sources
        self.root = Path(root or ROOT / '.studio-preview')
        self.lock = threading.Lock()
        self.rows = {}
        self.processes = {}

    def start(self, site, body):
        services = [p for p, info in edits.section_model.PAGES.items() if info.get('template') == 'service']
        if site != ttros_site.SITE_ID or body.page not in ('home', 'site', *services):
            raise ValueError('Unknown Studio page for a candidate preview.')
        if len(body.pages) > len(services) or any(p not in services or type(v) is not int or v < 1 for p, v in body.pages.items()):
            raise ValueError('Service-page versions must name saved versions of Studio service pages.')
        if body.page in services and body.page not in body.pages:
            raise ValueError('Save this service page and choose its saved version before building.')
        store = edits.Drafts(self.sources)
        records = {'home': store.get(site, body.version, 'home'),
                   'site': store.get(site, body.shared_version, 'site'),
                   **{page: store.get(site, version, page) for page, version in sorted(body.pages.items())}}
        if any(r.get('contrast_failures') for r in records.values()) and not body.acknowledge_contrast:
            raise ValueError('Review and acknowledge saved contrast failures before building.')
        if not self.lock.acquire(blocking=False):
            raise ValueError('Another Studio preview is building; wait for it to finish.')
        ident = uuid.uuid4().hex
        try:
            folder = self.root / ident
            folder.mkdir(parents=True)
            # record_sha256 is of the stored (immutable) record; pre-v5 envelopes migrate on read in the build.
            versions = {p: {'version': r['version'], 'fingerprint': r['fingerprint'], 'schema_version': r['envelope'].get('version'),
                            'record_sha256': hashlib.sha256(json.dumps(r, sort_keys=True).encode()).hexdigest()}
                        for p, r in records.items()}
            request = {'repository': str(self.sources.site(site)['repository']), 'output': str(folder),
                       'envelopes': [r['envelope'] for r in records.values()], 'versions': versions, 'purpose': body.purpose}
            (folder / 'request.json').write_text(json.dumps(request))
            row = {'id': ident, 'site': site, 'status': 'building', 'base': CANDIDATE, 'versions': versions,
                   'purpose': body.purpose, 'started_at': time.time(),
                   'contrast': {p: r.get('contrast_failures') or [] for p, r in records.items()},
                   'contrast_acknowledged': bool(body.acknowledge_contrast),
                   'modelCalls': 0, 'publication': 'held'}
            self.rows[ident] = row
            threading.Thread(target=self._build, args=(folder, row), daemon=True).start()
            return dict(row)
        except Exception:
            self.lock.release()
            raise

    def _build(self, folder, row):
        # Deliberately don't inherit provider secrets, activation flags, or model settings.
        (folder / 'npm-user.config').write_text('')
        (folder / 'npm-global.config').write_text('')
        env = {'PATH': os.environ['PATH'], 'npm_config_cache': str(folder / 'npm-cache'),
               'npm_config_userconfig': str(folder / 'npm-user.config'),
               'npm_config_globalconfig': str(folder / 'npm-global.config'), **BUILD_ENV}
        try:
            with (folder / 'build.log').open('w') as log:
                worker = subprocess.Popen(['node', str(self.script), str(folder / 'request.json')],
                                          env=env, stdout=log, stderr=log, start_new_session=True)
                try:
                    code = worker.wait(timeout=1000)
                except subprocess.TimeoutExpired:
                    os.killpg(worker.pid, signal.SIGTERM)
                    worker.wait()
                    raise
                if code:
                    raise ValueError('Isolated build failed')
            result = json.loads((folder / 'build.json').read_text())
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            fixture = Path(result['fixture'])
            with (folder / 'server.log').open('w') as log:
                process = subprocess.Popen(['node', str(fixture / 'node_modules/next/dist/bin/next'),
                                            'start', '--hostname', '127.0.0.1', '--port', str(port)],
                                           cwd=fixture, env=env, stdout=log, stderr=log)
            # Only the newly owned preview process is stopped on failure/expiry.
            ready = False
            for _ in range(100):
                if process.poll() is not None:
                    break
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.2):
                        ready = True
                        break
                except OSError:
                    time.sleep(.1)
            if not ready:
                process.terminate()
                raise ValueError('Built preview failed to start')
            self.processes[row['id']] = process
            row.update(status='ready', nextBuildId=result['nextBuildId'], routes=result['routes'],
                       sourceDiff=result['sourceDiff'], url=f'http://127.0.0.1:{port}/',
                       expires_at=time.time() + 1800)
            if row.get('purpose') == 'release':
                row.update(self._release_evidence(folder, row, result, f'http://127.0.0.1:{port}'))
            def expire():
                if row['status'] == 'ready':
                    self.stop(row['id'])
                    row['status'] = 'expired'
            timer = threading.Timer(1800, expire)
            timer.daemon = True
            timer.start()
        except Exception as exc:
            row.update(status='failed', error=f'{type(exc).__name__}: preview failed; inspect isolated build.log')
        finally:
            self.lock.release()

    def _release_evidence(self, folder, row, result, url):
        """Validate the served release build, package it once and record it durably."""
        from website_studio_release_targets import health
        routes = self.health_routes(row, result)
        prerendered, app = set(result.get('prerendered') or []), set(result.get('routes') or [])
        missing = [r for r in routes if r not in prerendered and ('/page' if r == '/' else r + '/page') not in app]
        served = health(url, result['nextBuildId'], routes)
        contrast = sorted({f"{p}:{f.get('id')}" for p, rows in row['contrast'].items() for f in rows})
        checks = [
            {'check': 'adapter_source_verified', 'ok': result.get('base') == self.pinned and result.get('sourceUntouched') is True,
             'detail': f"pinned {result.get('base')}, source verified before and after the build"},
            {'check': 'next_build', 'ok': result.get('build') == 'passed', 'detail': f"next build {result.get('build')}, id {result.get('nextBuildId')}"},
            {'check': 'website_configuration', 'ok': result.get('purpose') == 'release' and not any(c['path'].startswith('next.config') for c in result.get('changes', [])),
             'detail': "website's own next.config.ts (no preview CSP/iframe changes)"},
            {'check': 'routes_built', 'ok': not missing,
             'detail': (f"missing: {', '.join(missing)}" if missing else f"{len(routes)} routes built") + ': ' + ', '.join(sorted(routes))[:600]},
            {'check': 'preview_health', 'ok': served['ok'], 'detail': f"{sum(c['ok'] for c in served['checks'])}/{len(served['checks'])} loopback checks"},
            {'check': 'contrast', 'ok': not contrast or row['contrast_acknowledged'],
             'detail': ('acknowledged: ' + ', '.join(contrast)) if contrast else 'no saved contrast failures'},
        ]
        fixture = Path(result['fixture'])
        import website_studio_vercel
        vercel_output = website_studio_vercel.record_output(fixture)
        if vercel_output:
            checks.append({'check': 'vercel_build_output', 'ok': vercel_output['count'] > 0 and not (fixture / '.vercel/.env.production.local').exists(),
                           'detail': f"`vercel build --prod`: {vercel_output['count']} deployable files, upload digest {vercel_output['digest'][:16]}, no environment file"})
        packed = artifacts.package(fixture, folder / 'artifact.tar')
        checks.append({'check': 'artifact', 'ok': packed['build_id'] == result['nextBuildId'],
                       'detail': f"{packed['files']} files, sha256 {packed['sha256']}"})
        sha = lambda p: artifacts.sha256_file(p) if p.is_file() else None
        record = {'schema': 'ttros.website_studio.build/1', 'id': row['id'], 'site': row['site'], 'purpose': 'release',
                  'adapter': row.get('adapter', 'candidate'), 'base': self.pinned, 'versions': row['versions'], 'next_build_id': result['nextBuildId'],
                  'routes': result['routes'], 'health_routes': routes, 'changes': result.get('changes', []),
                  'page_draft_sha256': result.get('pageDraftSha256', {}),
                  'environment': build_environment(), 'artifact': packed,
                  'builder': result.get('builder', 'next build'),
                  'vercel_output': {k: vercel_output[k] for k in ('digest', 'count', 'bytes')} if vercel_output else None,
                  'lock_sha256': sha(fixture / 'package-lock.json'),
                  'evidence': {'source_diff_sha256': hashlib.sha256(result.get('sourceDiff', '').encode()).hexdigest(),
                               'build_log_sha256': sha(folder / 'build.log'), 'build_json_sha256': sha(folder / 'build.json'),
                               'adapter_receipt_sha256': sha(fixture / 'studio-adapter-receipt.json')},
                  'validation': {'status': 'passed' if all(c['ok'] for c in checks) else 'failed', 'checks': checks,
                                 'preview_health': served},
                  'started_at': row['started_at'], 'finished_at': time.time(), 'model_calls': 0}
        (folder / 'source.diff').write_text(result.get('sourceDiff', ''))
        tmp = folder / '.build-record.json'
        tmp.write_text(json.dumps(record, indent=2, sort_keys=True))
        os.replace(tmp, folder / 'build-record.json')
        return {'validation': record['validation']['status'], 'artifact': {k: packed[k] for k in ('sha256', 'files', 'bytes')}}

    def record(self, ident):
        """The durable record of a finished release build (survives restarts and preview expiry)."""
        if not re.fullmatch(r'[a-f0-9]{32}', ident or ''):
            raise KeyError('Unknown build')
        path = self.root / ident / 'build-record.json'
        if not path.is_file():
            raise KeyError('No release build record (only finished release builds can become releases)')
        return json.loads(path.read_text()), self.root / ident

    def stop(self, ident):
        self.get(ident)
        process = self.processes.pop(ident, None)
        if process is not None and process.poll() is None:
            process.terminate()
        self.rows[ident]['status'] = 'stopped'
        return self.get(ident)

    def get(self, ident):
        if not re.fullmatch(r'[a-f0-9]{32}', ident) or ident not in self.rows:
            raise KeyError('Unknown preview')
        return dict(self.rows[ident])


def router(sources, identity):
    api = APIRouter(prefix='/api/website/studio/sites', dependencies=[Depends(private_response)])
    builds = Builds(sources)

    def operator(request, mutate=False):
        who = identity.principal(request)
        if who.get('workspace') != 'ttros-operator' and who.get('method') != 'local_preview':
            raise HTTPException(403, 'Real-site builds require the TTROS operator')
        if mutate:
            identity.csrf(request)

    @api.post('/{site_id}/builds', status_code=202)
    def start(site_id: str, body: BuildRequest, request: Request):
        operator(request, True)
        try:
            return builds.start(site_id, body)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api.get('/{site_id}/builds/{ident}')
    def status(site_id: str, ident: str, request: Request):
        operator(request)
        if site_id != ttros_site.SITE_ID:
            raise HTTPException(404, 'Unknown site')
        try:
            return builds.get(ident)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @api.delete('/{site_id}/builds/{ident}')
    def stop(site_id: str, ident: str, request: Request):
        operator(request, True)
        if site_id != ttros_site.SITE_ID:
            raise HTTPException(404, 'Unknown site')
        try:
            row = builds.get(ident)
            if row['status'] == 'building':
                raise HTTPException(409, 'Build in progress; wait before closing preview')
            return builds.stop(ident)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    return api
