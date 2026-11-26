"""Integration acceptance across the combined CRM, websites and canonical jobs APIs."""
import unittest

from tests.company_ops_fixture import CONTACT, HEADLINE, company_fixture
import aos_jobs
import aos_website as web


class CompanyOpsIntegration(unittest.TestCase):
    def setUp(self):
        context = company_fixture()
        self.fixture = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.client = self.fixture.client
        self.counter = 0

    def crm(self, kind, record_id, payload=None, **extra):
        self.counter += 1
        return self.client.post('/api/crm/events', json={'kind': kind, 'record_id': record_id,
            'payload': payload or {}, 'source_refs': ['casey:dashboard-entry'], 'request_key': f'integration-{self.counter}', **extra})

    def prepare(self, **overrides):
        response = self.client.post('/api/website/preparations', json={**self.fixture.inputs, **overrides})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def queued(self, order):
        response = self.client.post(f'/api/website/preparations/{order["id"]}/queue')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['job']['job_id']

    def test_sales_website_job_preview_and_handoff_share_one_backend(self):
        rid = 'opp:company-integration'
        record = self.crm('opportunity.create', rid, {'name': 'Synthetic Company Ops sale', 'contact': CONTACT, 'workflow': 'sales'}).json()['record']
        self.assertEqual(record['contact_record']['id'], CONTACT)
        for stage in ('qualified', 'proposal'):
            response = self.crm('stage.change', rid, {'stage': stage, 'reason': f'Synthetic {stage} reviewed'}, expected_revision=record['revision'])
            self.assertEqual(response.status_code, 200, response.text)
            record = response.json()['record']
        record = self.crm('interaction.log', rid, {'channel': 'note', 'direction': 'internal', 'summary': 'Synthetic scope review', 'occurred_on': '2026-11-16'}).json()['record']
        record = self.crm('next_action.set', rid, {'text': 'Review local website evidence', 'due': '2026-11-15'}).json()['record']
        attention = self.client.get('/api/crm/home').json()['attention']
        self.assertIn(rid, [r['record_id'] for r in attention if r['rule'] == 'overdue_follow_up'])
        order = self.prepare()
        self.assertEqual(order['state'], 'prepared')
        self.assertEqual(aos_jobs.list_jobs(self.fixture.root), [])
        job_id = self.queued(order)
        self.assertEqual(self.queued(order), job_id)
        self.assertEqual(self.client.get(f'/api/david/jobs/{job_id}').json()['status'], 'queued')
        job = self.fixture.execute(job_id)
        self.assertEqual(job['status'], 'done', job)
        shown = self.client.get(f'/api/website/jobs/{job_id}').json()
        self.assertEqual((shown['readiness'], shown['released'], shown['publish']['status']), ('validated', False, 'held'))
        preview = self.client.get(shown['preview']['url'])
        self.assertIn(HEADLINE, preview.text)
        self.assertIn('sandbox', preview.headers['content-security-policy'])
        self.assertEqual(shown['usage']['model_calls'], 0)
        self.assertTrue(shown['receipts'])
        listed = self.client.get('/api/website/projects').json()['jobs'][0]
        self.assertEqual((listed['readiness'], listed['released'], listed['artifact_problems']), ('validated', False, []))
        record = self.crm('link.add', rid, {'note': 'Synthetic website evidence'}, source_refs=[job_id]).json()['record']
        self.assertEqual(record['jobs'][0]['id'], job_id)
        self.assertEqual(record['jobs'][0]['status'], 'done')
        self.assertEqual(self.client.get('/api/david/jobs').json()['jobs'][0]['id'], job_id)
        record = self.crm('next_action.set', rid, {'text': 'Review next step', 'due': '2026-11-18'}).json()['record']
        self.assertFalse([r for r in self.client.get('/api/crm/home').json()['attention'] if r['record_id'] == rid and r['rule'] == 'overdue_follow_up'])
        record = self.crm('stage.change', rid, {'stage': 'won', 'reason': 'Synthetic acceptance'}).json()['record']
        response = self.crm('handoff.prepare', rid, {'confirmed_scope': 'Synthetic scope review only', 'next_delivery_action': 'Casey reviews the verified website evidence'}, source_refs=[job_id], expected_revision=record['revision'])
        self.assertEqual(response.status_code, 200, response.text)
        record = response.json()['record']
        self.assertEqual(record['handoff']['sources'], [job_id])
        self.assertEqual([r['stage'] for r in record['history']], ['discovery', 'qualified', 'proposal', 'won'])
        self.assertEqual(self.client.get(f'/api/crm/records/{rid}').json()['handoff'], record['handoff'])

    def test_artifact_failure_reaches_shared_listing_and_preview_fails_closed(self):
        order = self.prepare()
        job_id = self.queued(order)
        job = self.fixture.execute(job_id)
        web.preview_file(self.fixture.root, job, 'index.html').write_text('synthetic altered artifact')
        shown = self.client.get('/api/website/projects').json()['jobs'][0]
        self.assertEqual(shown['status'], 'done')  # execution history is preserved
        self.assertEqual(shown['readiness'], 'blocked')  # current evidence is refused
        self.assertTrue(shown['artifact_problems'])
        self.assertNotEqual(self.client.get(shown['preview']['url']).status_code, 200)
        self.assertEqual(self.client.get(f'/api/website/preparations/{order["id"]}').json()['state'], 'blocked')

    def test_failed_job_scope_missing_source_and_external_actions(self):
        for overrides in ({'client_scope': 'client-a'}, {'source_references': ['site/missing.md']}, {'allowed_actions': ['deploy']}):
            order = self.prepare(**overrides)
            self.assertEqual(order['state'], 'blocked')
            self.assertEqual(self.client.post(f'/api/website/preparations/{order["id"]}/queue').status_code, 422)
        self.assertEqual(aos_jobs.list_jobs(self.fixture.root), [])
        rid = 'opp:negative-integration'
        self.assertEqual(self.crm('opportunity.create', rid, {'name': 'Synthetic negative case', 'contact': CONTACT, 'workflow': 'sales'}, client_scope='client-a').status_code, 422)
        record = self.crm('opportunity.create', rid, {'name': 'Synthetic negative case', 'contact': CONTACT, 'workflow': 'sales'}).json()['record']
        old = record['revision']
        self.crm('opportunity.update', rid, {'name': 'Synthetic updated case'})
        self.assertEqual(self.crm('next_action.set', rid, {'text': 'Obsolete', 'due': '2026-11-17'}, expected_revision=old).status_code, 409)
        self.assertEqual(self.crm('email.send', rid).status_code, 422)
        order = self.prepare(acceptance_checks=[{'path': 'site/index.html', 'contains': 'Text deliberately absent from fixture output'}])
        job_id = self.queued(order)
        self.assertEqual(self.fixture.execute(job_id)['status'], 'failed')
        self.assertEqual(self.client.get('/api/website/projects').json()['jobs'][0]['readiness'], 'blocked')
        self.assertEqual(self.client.get('/api/david/jobs').json()['jobs'][0]['status'], 'failed')
        self.crm('link.add', rid, source_refs=[job_id])
        self.assertIn('linked_job_needs_attention', [r['rule'] for r in self.client.get('/api/crm/home').json()['attention'] if r['record_id'] == rid])
