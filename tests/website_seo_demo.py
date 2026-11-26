"""Zero-model SEO preparation -> canonical job -> validated preview demonstration.

Only writes a unique fixture folder. Run through test_website_seo_integration.py
so the repository-wide paid-model and production-import guards stay active.
"""
import copy
import html
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from tests.command_centre_fixture import crm_environment, git, make_site_repo, write_website_registry
from fastapi.testclient import TestClient
import aos_jobs
import aos_website as web
import aos_website_seo as seo
import main as backend

RESEARCH = {
    'business': 'Fixture Co', 'category': 'workflow consulting', 'audience': 'fixture teams',
    'client_scope': 'global', 'buyer_problem': 'FIXTURE: compare self-service and assisted setup',
    'offer_difference': 'FIXTURE: assisted implementation', 'author': 'Fixture Reviewer',
    'cta_label': 'Discuss this fixture', 'cta_url': 'https://example.org/fixture/contact',
    'competitors': [{'name': 'Example Automation'}],
    'comparison_method': 'FIXTURE: compare the supplied test specifications; no real vendor research.',
    'bias_disclosure': 'FIXTURE: we offer a competing service; this is not real vendor advice.',
    'claims': [{'claim': 'FIXTURE: Example Automation has a self-service setup option.',
                'source_url': 'https://example.org/fixture/specification', 'checked_on': '2026-01-03'}],
}


def page_source(page):
    e = html.escape
    r = RESEARCH
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{e(page['title'])}</title>
<meta name="description" content="Invented fixture comparison for a zero-model TTROS work order.">
<link rel="stylesheet" href="styles.css"><style>table {{ border-collapse: collapse; width: 100%; }} th, td {{ padding: .6rem; border: 1px solid #bbb; text-align: left; }} body {{ padding: 0 1rem; box-sizing: border-box; }}</style></head><body>
<nav><a href="index.html">Home</a> <a href="contact.html">Contact</a></nav>
<main><p>TTROS SEO DEMONSTRATION — INVENTED FIXTURE DATA — NOT PUBLISHED</p>
<h1>{e(page['h1'])}</h1><p>By {r['author']}</p><p>{r['bias_disclosure']}</p>
<h2>Method</h2><p>{r['comparison_method']}</p>
<h2>Compare the fit</h2><table><caption>Invented specification comparison</caption>
<tr><th scope="col">Option</th><th scope="col">Setup</th></tr>
<tr><td>Example Automation</td><td>Self-service (fixture specification)</td></tr>
<tr><td>Fixture Co</td><td>Assisted implementation (fixture specification)</td></tr></table>
<p>Choose self-service when you can implement it yourself. Choose assistance when the team needs hands-on help.</p>
<p><a href="{r['claims'][0]['source_url']}">Fixture specification</a> · checked {r['claims'][0]['checked_on']}</p>
<p><a href="{r['cta_url']}">{r['cta_label']}</a></p>
<p>Model calls: 0. Publication held. No real claims, metrics or rankings.</p></main></body></html>'''


def run():
    folder = Path(tempfile.mkdtemp(prefix='ttros-seo-zero-model-'))
    root = folder / 'runtime'; root.mkdir()
    repo = make_site_repo(folder / 'fixture-site')
    head = git(repo, 'rev-parse', 'HEAD')
    with crm_environment(root) as paths:
        def brain_read(scope, pointer):
            return {'content': (paths['vault'] / pointer.removeprefix('business_brain:')).read_text()}
        page = seo.build_plan(RESEARCH)['pages'][0]
        write_website_registry(root, repo, fixture_edits=[{
            'name': 'seo-fixture-page', 'when': 'SEO comparison page',
            'path': 'site/index.html', 'text': page_source(page)}])
        with patch.object(backend, 'BASE_DIR', root), patch.object(backend, 'RUNNER', None), \
                patch.object(web, 'codex_worker', side_effect=AssertionError('Paid worker forbidden')):
            client = TestClient(backend.app)
            planned = client.post('/api/website/preparations/seo-plan', json=RESEARCH)
            assert planned.status_code == 200, planned.text
            assert planned.json()['pages'][0]['kind'] == 'alternatives'
            assert aos_jobs.list_jobs(root) == []
            order = seo.work_order_page(RESEARCH, page['kind'], page['slug'], project='fixture-site',
                repository=str(repo), client_scope='global', target_path='site/index.html',
                branch='feature/seo-fixture', source_references=['AGENTS.md', 'site/index.html'])
            poor = copy.deepcopy(order); poor['seo_strategy']['research']['claims'] = []
            draft = client.post('/api/website/preparations', json={**poor, 'draft': True})
            assert draft.status_code == 200 and draft.json()['state'] == 'draft'
            refused = client.post(f"/api/website/preparations/{draft.json()['id']}/queue")
            assert refused.status_code == 422 and aos_jobs.list_jobs(root) == []
            prepared = client.post('/api/website/preparations', json=order)
            assert prepared.status_code == 200, prepared.text
            assert prepared.json()['state'] == 'prepared', prepared.text
            identity = prepared.json()['id']
            queued = client.post(f'/api/website/preparations/{identity}/queue')
            assert queued.status_code == 200, queued.text
            job_id = queued.json()['job']['job_id']
            assert client.post(f'/api/website/preparations/{identity}/queue').json()['job']['job_id'] == job_id
            job = aos_jobs.execute(root, job_id, brain_read=brain_read)
            assert job['status'] == 'done', job
            shown = client.get(f'/api/website/preparations/{identity}').json()
            assert shown['state'] == 'validated', shown
            preview = client.get(shown['job']['preview']['url'])
            assert preview.status_code == 200 and page['h1'] in preview.text
            assert preview.text.count('<td>') == 4
            assert 'sandbox' in preview.headers['content-security-policy']
            assert shown['job']['usage']['model_calls'] == 0
            assert shown['job']['publish']['status'] == 'held' and not shown['released']
            assert shown['job']['artifact_problems'] == []
            assert git(repo, 'rev-parse', 'HEAD') == head and not git(repo, 'status', '--porcelain')
            assert not git(repo, 'remote') and len(aos_jobs.list_jobs(root)) == 1
            report = {'status': 'PASS', 'fixture': True, 'folder': str(folder), 'runtime_root': str(root),
                'preparation_id': identity, 'job_id': job_id, 'state': shown['state'], 'model_calls': 0,
                'publish': 'held', 'released': False, 'project_checkout_untouched': True,
                'evidence_poor_draft_queue_status': refused.status_code,
                'preview': str(web.preview_file(root, job, 'index.html')), 'preview_api': shown['job']['preview']['url'],
                'receipt': str(root / job['receipts'][0]['path']), 'artifact_problems': [],
                'api_preview_status': preview.status_code, 'sandbox_csp': preview.headers['content-security-policy']}
            (folder / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
            (folder / 'workflow-output.json').write_text(json.dumps(shown, indent=2) + '\n')
            return report
