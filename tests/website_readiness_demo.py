"""Reproduce the real preparation/API/job/preview path with an isolated fixture.

Run from the repository: python -m tests.website_readiness_demo
Only a unique temporary tree is written. No model, network, server or publication.
"""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from tests.command_centre_fixture import crm_environment, git, make_site_repo, write_website_registry
import aos_jobs
import aos_website as web
import main as backend
from fastapi.testclient import TestClient

HEADLINE = 'A bounded offline website change.'


def run() -> dict:
    folder = Path(tempfile.mkdtemp(prefix='ttros-website-readiness-'))
    root = folder / 'runtime'; root.mkdir()
    repo = make_site_repo(folder / 'fixture-site')
    (repo / '.gitignore').write_text('build/\n')
    git(repo, 'add', '.gitignore'); git(repo, 'commit', '-qm', 'Ignore local build artifacts')
    head = git(repo, 'rev-parse', 'HEAD')
    with crm_environment(root) as paths:
        write_website_registry(root, repo, required_assets=['site/styles.css'],
            preview={'dir': 'build', 'entry': 'index.html', 'command': ['{python}', '-c',
                "import shutil; shutil.copytree('site', 'build', dirs_exist_ok=True)"]},
            external_requirements=['Production hosting, deployment authentication and live-site verification require separately authorized work'])
        body = {'project': 'fixture-site', 'repository': str(repo), 'client_scope': 'global',
                'request': f"Change the homepage headline to '{HEADLINE}'", 'branch': 'feature/website-offline-demo',
                'affected_paths': ['site/index.html'], 'source_references': ['AGENTS.md', 'site/index.html'],
                'acceptance_checks': [{'path': 'site/index.html', 'contains': HEADLINE}]}
        (folder / 'request.json').write_text(json.dumps(body, indent=2) + '\n')
        def brain_read(scope, pointer):
            return {'content': (paths['vault'] / pointer.removeprefix('business_brain:')).read_text()}
        with patch.object(backend, 'BASE_DIR', root), patch.object(backend, 'RUNNER', None), \
                patch.object(web, 'codex_worker', side_effect=AssertionError('No model may run')):
            client = TestClient(backend.app)
            response = client.post('/api/website/preparations', json=body)
            assert response.status_code == 200, response.text
            prepared = response.json()
            assert prepared['state'] == 'prepared', prepared
            queued = client.post(f'/api/website/preparations/{prepared["id"]}/queue')
            assert queued.status_code == 200, queued.text
            job_id = queued.json()['job']['job_id']
            job = aos_jobs.execute(root, job_id, brain_read=brain_read)
            assert job['status'] == 'done', job
            final = client.get(f'/api/website/preparations/{prepared["id"]}').json()
            assert final['state'] == 'validated', final
            preview = client.get(final['job']['preview']['url'])
            assert HEADLINE in preview.text
            assert 'sandbox' in preview.headers['content-security-policy']
            assert final['job']['publish']['status'] == 'held' and not final['released']
            assert final['job']['usage']['model_calls'] == 0
            assert git(repo, 'rev-parse', 'HEAD') == head and not git(repo, 'status', '--porcelain')
            assert not git(repo, 'remote')
            (folder / 'workflow-output.json').write_text(json.dumps(final, indent=2) + '\n')
            report = {'status': 'PASS', 'fixture': True, 'folder': str(folder), 'runtime_root': str(root),
                      'project_checkout_untouched': True, 'preparation_id': prepared['id'], 'job_id': job_id,
                      'state': final['state'], 'published': False, 'released': False, 'publish_status': 'held',
                      'model_calls': 0, 'network_calls': 0, 'server_started': False,
                      'work_order': str(root / next(a['path'] for a in job['artifacts'] if a['ref'] == 'work_order')),
                      'preview': str(web.preview_file(root, job, 'index.html')),
                      'receipt': str(root / job['receipts'][0]['path']), 'workflow_output': str(folder / 'workflow-output.json'),
                      'artifacts_verified': len(final['job']['artifacts']), 'artifact_problems': final['job']['artifact_problems'],
                      'external_steps_unverified': final['validation']['external_steps']}
            (folder / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
            return report


if __name__ == '__main__':
    print(json.dumps(run(), indent=2))
