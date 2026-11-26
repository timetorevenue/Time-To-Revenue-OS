"""Guarded API, evidence rejection and real canonical fixture execution coverage."""
import json
from unittest.mock import patch

from fastapi.testclient import TestClient
from tests.test_command_centre_website import WebsiteCase
from tests.command_centre_fixture import write_website_registry
from tests.website_seo_demo import RESEARCH, page_source, run
import aos_jobs
import aos_website as web
import aos_website_prepare as prep
import aos_website_seo as seo
import main as backend


def test_seo_zero_model_demonstration():
    report = run()
    print('\nSEO_DEMO=' + json.dumps(report))
    assert report['model_calls'] == 0 and report['publish'] == 'held'


class SeoIntegration(WebsiteCase):
    def order(self):
        page = seo.build_plan(RESEARCH)['pages'][0]
        return seo.work_order_page(RESEARCH, page['kind'], page['slug'], project='fixture-site',
            repository=str(self.repo), client_scope='global', target_path='site/index.html',
            branch='feature/seo-negative', source_references=['AGENTS.md'])

    def test_malformed_planner_inputs_return_422_without_jobs(self):
        with patch.object(backend, 'BASE_DIR', self.root), patch.object(backend, 'RUNNER', None):
            client = TestClient(backend.app)
            for fields in [{'claims': 42}, {'claims': {}}, {'competitors': 'bad'}, {'author': []},
                           {'competitors': [None]}, {'claims': [{}] * 26}, {'business': 'x' * 16000}]:
                response = client.post('/api/website/preparations/seo-plan', json={**RESEARCH, **fields})
                self.assertEqual(response.status_code, 422, response.text)
            for fields in [{'search_metrics': {'example': None}}, {'cta_url': '//unapproved.example'},
                           {'claims': [{'claim': 'bad URL', 'source_url': 'https://[', 'checked_on': '2026-01-03'}]}]:
                response = client.post('/api/website/preparations/seo-plan', json={**RESEARCH, **fields})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['pages'][0]['readiness'], 'research_required')
            self.assertEqual(aos_jobs.list_jobs(self.root), [])

    def test_client_scope_version_and_allowed_paths_still_block(self):
        for change in [{'version': 'unknown'}, {'research': {**RESEARCH, 'client_scope': 'another-client'}},
                       {'target_path': 'tools/unsafe.py'}]:
            order = self.order(); order['seo_strategy'].update(change)
            row = prep.prepare(self.root, order)
            self.assertEqual(row['state'], 'blocked')
            with self.assertRaises(web.WebsiteError): prep.queue(self.root, row['id'])
        self.assertEqual(aos_jobs.list_jobs(self.root), [])

    def test_missing_output_evidence_fails_before_validation_even_with_passing_site_tests(self):
        page = seo.build_plan(RESEARCH)['pages'][0]
        for missing in [RESEARCH['author'], RESEARCH['bias_disclosure'], RESEARCH['cta_label'], RESEARCH['claims'][0]['source_url']]:
            with self.subTest(missing=missing):
                source = page_source(page).replace(missing, 'OMITTED')
                write_website_registry(self.root, self.repo, fixture_edits=[{'name': 'missing-evidence',
                    'when': 'SEO comparison page', 'path': 'site/index.html', 'text': source}])
                order = self.order()
                # Separate branch for each isolated job; all base-source files remain untouched.
                order['branch'] += '-' + str(len(aos_jobs.list_jobs(self.root)))
                row = prep.prepare(self.root, order)
                self.assertEqual(row['state'], 'prepared', row)
                with patch.object(web, 'codex_worker', side_effect=AssertionError('model forbidden')):
                    job = aos_jobs.execute(self.root, prep.queue(self.root, row['id'])['id'], brain_read=self.brain_read)
                self.assertEqual(job['status'], 'failed', job)
                self.assertIn('SEO source', job['detail'])
                self.assertFalse(prep.view(self.root, prep.load(self.root, row['id']))['state'] == 'validated')
                self.assertEqual(job['effects'], [])
        self.assert_checkout_untouched()
