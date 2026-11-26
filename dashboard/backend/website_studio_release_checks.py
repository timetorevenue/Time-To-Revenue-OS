"""Functional checks of a deployed Website Studio release (staged Vercel URL or production). No models.

Read-only and safe for production: it never books, subscribes, submits a form or sends a payload that
reaches Brevo. The booking webhook is checked with GET (405) and an empty POST, which the website's
handler rejects (400 "No participant email found") before any Brevo request.

- protected functionality: booking webhook guard, robots.txt, sitemap.xml, static guides, 404,
  the website's security headers (HSTS, frame, content-type, referrer, permissions, CSP report-only);
- SEO parity: every health route's title, description, canonical, robots, Open Graph/Twitter and
  JSON-LD as served must equal the validated release build's prerendered page in the artifact;
- browser: scripts/studio-live/deploy-check.mjs (navigation, FAQ accordion, booking embed, blog
  navigation, static guide, mobile menu, no page errors) with the pinned Playwright.

Revisit: when the website's integrations, headers or routes change. Last touched: 2026-11-22.
"""
from __future__ import annotations

import html.parser
import json
import os
from pathlib import Path
import subprocess
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
BROWSER_SCRIPT = ROOT / 'dashboard/frontend/scripts/studio-live/deploy-check.mjs'
PLAYWRIGHT = Path(os.path.expanduser('~')) / '.local/share/ttros/playwright-1.61.1/node_modules/playwright/index.mjs'
HEADERS = {'strict-transport-security': 'max-age=31536000', 'x-frame-options': 'SAMEORIGIN', 'x-content-type-options': 'nosniff',
           'referrer-policy': 'strict-origin-when-cross-origin', 'permissions-policy': 'camera=()',
           'content-security-policy-report-only': "frame-src 'self' https://tally.so"}
GUIDES = ['/guides/4-prompt-project-workflow']


def fetch(base: str, path: str, method: str = 'GET', body: bytes | None = None, timeout: float = 30) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(base.rstrip('/') + path, data=body, method=method,
                                 headers={'User-Agent': 'ttros-website-studio-release-check', **({'Content-Type': 'application/json'} if body is not None else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read()


class _Head(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.seo, self._title, self._ld, self.title, self.jsonld = {}, False, None, '', []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'title':
            self._title = True
        elif tag == 'meta' and (a.get('name') or a.get('property')) and 'content' in a:
            key = a.get('name') or a.get('property')
            if key in ('description', 'robots', 'keywords') or key.startswith(('og:', 'twitter:')):
                self.seo.setdefault(key, []).append(a['content'])
        elif tag == 'link' and a.get('rel') == 'canonical':
            self.seo['canonical'] = [a.get('href')]
        elif tag == 'script' and a.get('type') == 'application/ld+json':
            self._ld = ''

    def handle_endtag(self, tag):
        if tag == 'title':
            self._title = False
        if tag == 'script' and self._ld is not None:
            self.jsonld.append(self._ld)
            self._ld = None

    def handle_data(self, data):
        if self._title:
            self.title += data
        if self._ld is not None:
            self._ld += data


def seo_of(markup: str) -> dict:
    parser = _Head()
    parser.feed(markup)
    jsonld = []
    for text in parser.jsonld:
        try:
            jsonld.append(json.dumps(json.loads(text), sort_keys=True))
        except json.JSONDecodeError:
            jsonld.append(text)
    return {'title': parser.title.strip(), **{k: sorted(v) for k, v in sorted(parser.seo.items())}, 'jsonld': sorted(jsonld)}


def prerendered(folder: Path, route: str) -> str | None:
    name = 'index' if route == '/' else route.strip('/')
    path = Path(folder) / '.next/server/app' / f'{name}.html'
    return path.read_text() if path.is_file() else None


def functional(base: str, folder: Path | None, routes: list[str], evidence: Path | None = None, browser: bool = True) -> dict:
    checks = []

    def add(check, ok, detail):
        checks.append({'check': check, 'ok': bool(ok), 'detail': str(detail)[:500]})
    try:
        status, headers, _ = fetch(base, '/')
        missing = [k for k, v in HEADERS.items() if v not in headers.get(k, '')]
        add('security_headers', status == 200 and not missing, f'missing/changed: {missing}' if missing else 'HSTS, frame, nosniff, referrer, permissions, CSP report-only present')
        s, _, _ = fetch(base, '/api/brevo-booking-webhook')
        p, _, body = fetch(base, '/api/brevo-booking-webhook', 'POST', b'{}')
        guarded = p == 400 and b'No participant email found' in body
        add('booking_webhook', s == 405 and guarded, f'GET {s}, empty POST {p} {body[:80]!r} (rejected before any Brevo call)')
        s, _, body = fetch(base, '/robots.txt')
        add('robots', s == 200 and b'Sitemap:' in body, f'{s}, {len(body)} bytes')
        s, _, body = fetch(base, '/sitemap.xml')
        add('sitemap', s == 200 and body.count(b'<loc>') >= 20, f'{s}, {body.count(b"<loc>")} URLs')
        for guide in GUIDES:
            s, _, body = fetch(base, guide)
            add(f'guide {guide}', s == 200 and b'<title>' in body, f'{s}, {len(body)} bytes')
        s, _, _ = fetch(base, '/__ttros-missing-page__')
        add('not_found', s == 404, f'{s}')
        if folder is not None:
            differing = []
            compared = 0
            for route in routes:
                expected = prerendered(folder, route)
                if expected is None:
                    continue
                s, _, body = fetch(base, route)
                compared += 1
                if s != 200 or seo_of(body.decode('utf-8', 'replace')) != seo_of(expected):
                    differing.append(f'{route} ({s})')
            add('seo_parity', compared > 0 and not differing,
                f'{compared} routes: title, description, canonical, robots, Open Graph/Twitter and JSON-LD equal the validated build'
                if not differing else 'differs: ' + ', '.join(differing[:12]))
    except (OSError, urllib.error.URLError) as exc:
        add('reachable', False, f'{type(exc).__name__}: {exc}')
    if browser:
        out = Path(evidence) if evidence else None
        if out is None:
            add('browser', False, 'no evidence folder')
        else:
            try:
                proc = subprocess.run(['node', str(BROWSER_SCRIPT), base, str(out), str(PLAYWRIGHT)], capture_output=True, text=True, timeout=300,
                                      env={'PATH': os.environ.get('PATH', ''), 'HOME': os.environ.get('HOME', '')})
                result = json.loads(proc.stdout.strip().splitlines()[-1])
                for c in result['checks']:
                    add(f"browser {c['check']}", c['ok'], c['detail'])
            except (subprocess.SubprocessError, OSError, ValueError, IndexError, KeyError) as exc:
                add('browser', False, f'{type(exc).__name__}: browser check did not complete')
    return {'ok': bool(checks) and all(c['ok'] for c in checks), 'base': base, 'checks': checks, 'model_calls': 0}
