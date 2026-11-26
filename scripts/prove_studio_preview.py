"""Opt-in local build proof. Never runs from the ordinary test suite.

Run with the integration venv and PYTHONPATH=tools:dashboard/backend. Uses isolated
fixture identity and state, committed website objects, no providers/models/secrets.
Creates and closes only its own temporary Next preview.
"""
import json
from pathlib import Path
import tempfile
import time
import urllib.request

from fastapi import FastAPI
from fastapi.testclient import TestClient
from website_studio import router as studio_router
from website_studio_builds import router as builds_router
from website_studio_sources import Sources
import website_studio_edits as edits

ROOT = Path(__file__).resolve().parents[1]


def main():
    output = ROOT / '.studio-preview'
    output.mkdir(exist_ok=True)
    state = Path(tempfile.mkdtemp(prefix='http-proof-', dir=output))
    sources = Sources(root=ROOT, state=state)
    class FixtureIdentity:
        def principal(self, _): return {'workspace': 'ttros-operator', 'method': 'fixture'}
        def csrf(self, request):
            assert request.headers.get('x-ttros-connections') == '1'
    def no_model(): raise AssertionError('Ordinary editing/building reached David')
    app = FastAPI()
    app.include_router(studio_router(no_model, sources))
    app.include_router(builds_router(sources, FixtureIdentity()))
    base = '/api/website/studio/sites/default-site'
    home = edits.apply_proposal(edits.initial_data('home'), {
        'summary': 'Duplicate Outcomes', 'edits': [],
        'sections': [{'op': 'duplicate', 'source': 'Section3', 'id': 'preview-outcomes', 'place': 'after', 'ref': 'Section3'}]}, page='home')
    home = edits.apply_proposal(home, {'summary': 'Edit duplicate', 'edits': [
        {'section': 'preview-outcomes', 'field': 'text0', 'text': 'Saved draft preview — exact version'}]}, page='home')
    shared = edits.initial_data('site')
    shared['content'][0]['props']['cta'] = 'Preview discussion'
    headers = {'x-ttros-connections': '1'}
    with TestClient(app) as client:
        for page, data in [('home', home), ('site', shared)]:
            response = client.post(base + '/drafts?page=' + page,
                                   json={'data': data, 'base_version': 0, 'actor': 'owner'})
            assert response.status_code == 200, response.text
        response = client.post(base + '/builds', json={'version': 1, 'shared_version': 1, 'acknowledge_contrast': True}, headers=headers)
        assert response.status_code == 202, response.text
        row = response.json()
        for _ in range(1100):
            row = client.get(base + '/builds/' + row['id']).json()
            if row['status'] != 'building': break
            time.sleep(1)
        assert row['status'] == 'ready', row
        try:
            http = {}
            for route in ['', 'blog', 'services/ai-implementation', 'resources', 'privacy']:
                with urllib.request.urlopen(row['url'] + route) as response:
                    html = response.read().decode()
                    assert response.status == 200
                    http['/' + route] = {'status': response.status, 'bytes': len(html)}
                    if not route:
                        assert all(text in html for text in ['Saved draft preview', 'Preview discussion',
                                   'Amber juniper amber bravo willow velve', 'hero-signature'])
                        (output / 'http-proof-home.html').write_text(html)
            row['http'] = http
            row['fixture_identity'] = True
            row['publication'] = 'held'
            (output / 'http-acceptance.json').write_text(json.dumps(row, indent=2))
            print(json.dumps({k: v for k, v in row.items() if k != 'sourceDiff'}, indent=2), flush=True)
        finally:
            stopped = client.delete(base + '/builds/' + row['id'], headers=headers)
            assert stopped.status_code == 200 and stopped.json()['status'] == 'stopped'
            print('Owned preview closed through DELETE; source checkout untouched.', flush=True)


if __name__ == '__main__': main()
