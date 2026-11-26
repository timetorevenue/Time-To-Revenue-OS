"""Invented local lifecycle fixtures, no provider/model calls.
Revisit: when the newsletter, delivery or suppression contract changes.
"""
import concurrent.futures
import datetime as dt
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from tests.command_centre_fixture import crm_environment
import aos_crm
import aos_jobs
import aos_lead_magnet
import aos_subscribers as sub
import main as backend

NOW = dt.datetime(2026, 11, 18, 12, tzinfo=dt.timezone.utc)


class SubscriberTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        context = crm_environment(self.root)
        self.paths = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.ledger = (self.root / 'queue/prospects.jsonl').read_bytes()

    def request(self, **kw):
        return sub.request_subscription(self.root, **{'name':'Fixture Owner', 'email':'owner@example.invalid',
            'consent':True, 'notice_version':sub.NOTICE, 'request_id':'fixture-000001', 'now':NOW, **kw})

    def detail(self, request):
        return aos_crm.record_detail(self.root, request['contact_id'])

    def confirm(self, request, **kw):
        return sub.confirm(self.root, token=request['transport']['verification_token'], now=NOW, **kw)

    def receipt(self, request, **kw):
        return sub.delivery(self.root, contact_id=request['contact_id'], request_id='fixture-000001',
                            status='accepted', message_id='<fixture@mail.invalid>', receipt_id='fixture-receipt', **kw)

    def provider(self, event='hard_bounce', **kw):
        return sub.provider_event(self.root, **{'event':event, 'email':'owner@example.invalid',
            'message_id':'<fixture@mail.invalid>', 'event_id':'fixture-event', **kw})

    def test_explicit_consent_and_notice_required_without_any_writes(self):
        for consent in (False, None, 1, 'true'):
            with self.assertRaises(aos_crm.CrmError):
                sub.request_subscription(self.root, name='Fixture', email='x@example.invalid', consent=consent,
                                         notice_version=sub.NOTICE, request_id='fixture-request')
        with self.assertRaises(aos_crm.CrmError):
            sub.request_subscription(self.root, name='Fixture', email='x@example.invalid', consent=True,
                                     notice_version='guide-opt-in-2026-11-17', request_id='fixture-request')
        self.assertFalse((self.root / aos_crm.EVENTS).exists())

    def test_pending_confirmed_replay_audit_and_no_opportunity(self):
        request = self.request()
        c = self.detail(request)
        self.assertFalse(sub.eligible(c))
        self.assertEqual(c['subscription']['status'], 'pending')
        text = (self.root / aos_crm.EVENTS).read_text()
        for token in ('verification_token', 'unsubscribe_token'):
            self.assertNotIn(request['transport'][token], text)
        self.assertIsNone(self.request()['transport'])
        self.confirm(request)
        self.assertTrue(self.confirm(request)['replayed'])
        self.assertTrue(sub.eligible(self.detail(request)))
        state = aos_crm.project(self.root, now=NOW)
        self.assertEqual(state['counts']['subscribers'], 1)
        self.assertEqual(state['counts']['opportunities'], 3)  # original fixture prospects only
        self.assertEqual(aos_jobs.list_jobs(self.root), [])
        self.assertEqual((self.root / 'queue/prospects.jsonl').read_bytes(), self.ledger)
        events = self.detail(request)['events']
        self.assertEqual(len(events), 3)
        for e in events:
            self.assertTrue(all(k in e for k in ('actor', 'at', 'source_refs', 'request_sha256', 'client_scope')))
        self.assertEqual(events[1]['payload']['notice_text'], sub.NOTICE_TEXT)

    def test_token_expiry_rotation_scope_and_malformed(self):
        r = self.request()
        with self.assertRaises(aos_crm.CrmError):
            sub.confirm(self.root, token=r['transport']['verification_token'], now=NOW, scope='client-a')
        with self.assertRaises(aos_crm.CrmError):
            sub.confirm(self.root, token='bad', now=NOW)
        self.assertEqual(aos_crm.project(self.root, now=NOW+dt.timedelta(minutes=16))['subscribers'][r['contact_id']]['status'], 'expired')
        with self.assertRaises(aos_crm.CrmError):
            sub.confirm(self.root, token=r['transport']['verification_token'], now=NOW+dt.timedelta(minutes=16))
        self.assertEqual(self.detail(r)['subscription']['status'], 'expired')
        rotated = sub.request_subscription(self.root, name='Fixture Owner', email='owner@example.invalid', consent=True,
            notice_version=sub.NOTICE, request_id='fixture-000002', now=NOW+dt.timedelta(minutes=17))
        with self.assertRaises(aos_crm.CrmError):
            sub.confirm(self.root, token=r['transport']['verification_token'], now=NOW)
        # Old unsubscribe remains valid after rotation, including before confirmation.
        sub.unsubscribe(self.root, token=r['transport']['unsubscribe_token'])
        with self.assertRaises(aos_crm.CrmError):
            sub.confirm(self.root, token=rotated['transport']['verification_token'], now=NOW+dt.timedelta(minutes=18))

    def test_concurrent_confirmation_consumes_one_event(self):
        r = self.request()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.confirm(r), range(12)))
        self.assertEqual(sum(not x['replayed'] for x in results), 1)
        self.assertEqual(len([e for e in self.detail(r)['events'] if e['payload'].get('action') == 'verified']), 1)

    def test_unsubscribe_sticky_replay_prevents_guide_draft(self):
        guide = aos_lead_magnet.capture(self.root, name='Owner', email='owner@example.invalid',
                                        guide_consent=True, follow_up_consent=True)
        r = self.request()
        self.assertEqual(guide['contact_id'], r['contact_id'])
        self.confirm(r)
        token = r['transport']['unsubscribe_token']
        sub.unsubscribe(self.root, token=token)
        before = len(aos_crm.read_events(self.root, 'global'))
        sub.unsubscribe(self.root, token=token)
        self.assertEqual(len(aos_crm.read_events(self.root, 'global')), before)
        self.assertFalse(sub.eligible(self.detail(r)))
        with self.assertRaises(aos_crm.CrmError):
            sub.request_subscription(self.root, name='Owner', email='owner@example.invalid', consent=True,
                notice_version=sub.NOTICE, request_id='fixture-reoptin')
        # Already queued draft rechecks suppression at execution time.
        result = aos_lead_magnet.run_draft_job(self.root, aos_jobs.load(self.root, guide['draft_job_id']), object())
        self.assertEqual(result['status'], 'refused')

    def test_all_suppression_events_are_correlated_and_never_cleared(self):
        r = self.request()
        self.confirm(r)
        self.receipt(r)
        for event in ('hard_bounce', 'soft_bounce', 'spam', 'unsubscribed', 'blocked', 'invalid'):
            with self.subTest(event=event):
                sub.provider_event(self.root, event=event, email='owner@example.invalid',
                    message_id='<fixture@mail.invalid>', event_id='event-'+event)
                self.assertFalse(sub.eligible(self.detail(r)))
                self.provider('delivered', event_id='delivery-'+event)
                self.assertFalse(sub.eligible(self.detail(r)))
        self.assertEqual(self.detail(r)['subscription']['suppression_reasons'],
                         ['blocked', 'complaint', 'hard_bounce', 'soft_bounce', 'unsubscribe'])

    def test_unknown_mismatched_and_duplicate_provider_events(self):
        r = self.request()
        self.assertEqual(self.provider()["status"], "held_for_delivery_review")
        self.assertFalse(sub.eligible(self.detail(r)))
        self.receipt(r)
        with self.assertRaises(aos_crm.CrmError):
            self.provider(email='other@example.invalid')
        self.assertEqual(self.provider(message_id='<wrong@example.invalid>', event_id="fixture-unmatched")["status"], "held_for_delivery_review")
        self.provider()
        before = len(aos_crm.read_events(self.root, 'global'))
        self.provider()
        self.assertEqual(len(aos_crm.read_events(self.root, 'global')), before)

    def test_delivery_failures_unknown_and_retries_never_resend(self):
        r = self.request()
        for state in ('failed', 'unknown'):
            sub.delivery(self.root, contact_id=r['contact_id'], request_id='fixture-000001',
                status=state, message_id='', receipt_id='receipt-'+state)
            self.assertFalse(sub.eligible(self.detail(r)))
            self.assertIsNone(self.request()['transport'])
        self.assertEqual(aos_jobs.list_jobs(self.root), [])
        payload = sub.brevo_verification_payload(r['transport'], sender='sender@example.invalid')
        self.assertEqual(payload['to'], [{'email': 'owner@example.invalid'}])
        self.assertIn(sub.NOTICE_TEXT, payload['textContent'])
        self.assertIn('#newsletter=', payload['textContent'])
        self.assertIn('unsubscribe#', payload['textContent'])

    def test_legacy_guide_opportunity_history_is_retained_but_excluded_from_sales(self):
        guide = aos_lead_magnet.capture(self.root, name='Owner', email='owner@example.invalid',
                                      guide_consent=True, follow_up_consent=False)
        aos_crm.record_event(self.root, client_scope='global', kind='opportunity.create', record_id='opp:guide-fixture',
            payload={'name': 'Guide interest', 'contact': guide['contact_id'], 'workflow': 'outreach', 'stage': 'identified', 'offer': aos_lead_magnet.OFFER},
            request_key='legacy-fixture')
        state = aos_crm.project(self.root)
        self.assertEqual(state['counts']['opportunities'], 3)
        self.assertEqual(state['counts']['legacy_guide_interests'], 1)
        self.assertEqual(aos_crm.record_detail(self.root, 'opp:guide-fixture')['classification'], 'guide_interest')

    def test_transport_positive_path_is_not_consent_until_confirmation(self):
        r = self.request()
        self.receipt(r)
        self.provider('delivered', event_id='fixture-delivered')
        self.assertEqual(self.detail(r)['subscription']['delivery_status'], 'delivered')
        self.assertFalse(sub.eligible(self.detail(r)))
        self.confirm(r)
        self.assertTrue(sub.eligible(self.detail(r)))

    def test_corrupted_audit_fails_closed_instead_of_losing_suppression(self):
        r = self.request()
        self.confirm(r)
        with (self.root / aos_crm.EVENTS).open('a') as handle:
            handle.write('{truncated suppression')
        with self.assertRaises(aos_crm.CrmError):
            aos_crm.project(self.root)
        with self.assertRaises(aos_crm.CrmError):
            self.confirm(r)

    def test_reordered_delivery_is_audited_without_overwriting_current_request(self):
        r = self.request()
        self.receipt(r)
        rotated = self.request(request_id='fixture-000002')
        self.assertEqual(self.provider('delivered')['status'], 'ignored_stale_delivery')
        self.assertEqual(self.detail(rotated)['subscription']['delivery_status'], 'not_sent')
        self.assertTrue(any(e['payload'].get('action') == 'stale_delivery' for e in self.detail(r)['events']))

    def test_partial_and_uncertain_request_commit_retries_do_not_duplicate_or_resend(self):
        original = aos_crm.record_event
        failed = False
        def interrupt(*args, **kw):
            nonlocal failed
            if kw.get('kind') == 'contact.subscription' and not failed:
                failed = True
                raise OSError('fixture interrupted before consent commit')
            return original(*args, **kw)
        with patch.object(aos_crm, 'record_event', side_effect=interrupt):
            with self.assertRaises(OSError):
                self.request()
        r = self.request()
        self.assertIsNotNone(r['transport'])
        self.assertEqual(len(self.detail(r)['events']), 2)
        self.assertIsNone(self.request()['transport'])
        # Simulate durable consent commit followed by a lost HTTP response.
        def uncertain(*args, **kw):
            result = original(*args, **kw)
            if kw.get('kind') == 'contact.subscription':
                raise OSError('fixture response lost after durable write')
            return result
        with patch.object(aos_crm, 'record_event', side_effect=uncertain):
            with self.assertRaises(OSError):
                self.request(request_id='fixture-000002')
        self.assertIsNone(self.request(request_id='fixture-000002')['transport'])
        self.assertEqual(len(self.detail(r)['events']), 3)

    def test_future_draft_links_are_hashed_and_preserve_older_unsubscribe(self):
        r = self.request()
        with self.assertRaises(aos_crm.CrmError):
            sub.issue_unsubscribe(self.root, contact_id=r['contact_id'], request_id='fixture-mail-01')
        self.confirm(r)
        result = sub.issue_unsubscribe(self.root, contact_id=r['contact_id'], request_id='fixture-mail-01')
        token = result['unsubscribe_url'].split('#')[1]
        self.assertNotIn(token, (self.root / aos_crm.EVENTS).read_text())
        self.assertIsNone(sub.issue_unsubscribe(self.root, contact_id=r['contact_id'], request_id='fixture-mail-01')['unsubscribe_url'])
        sub.unsubscribe(self.root, token=r['transport']['unsubscribe_token'])
        sub.unsubscribe(self.root, token=token)
        self.assertFalse(sub.eligible(self.detail(r)))
        with self.assertRaises(aos_crm.CrmError):
            sub.issue_unsubscribe(self.root, contact_id=r['contact_id'], request_id='fixture-mail-02')

    def test_http_auth_disabled_strict_consent_scope_native_brevo(self):
        env = {'TTROS_SUBSCRIBERS_ENABLED': 'true', 'TTROS_LEAD_MAGNET_CAPTURE_TOKEN': 'fixture-capture',
               'TTROS_BREVO_WEBHOOK_TOKEN': 'fixture-provider'}
        with patch.object(backend, 'BASE_DIR', self.root), patch.object(backend, 'RUNNER', None), patch.dict(os.environ, env):
            client = TestClient(backend.app)
            headers = {'x-ttros-capture-token': 'fixture-capture'}
            body = {'name': 'Owner', 'email': 'owner@example.invalid', 'consent': True,
                    'notice_version': sub.NOTICE, 'request_id': 'fixture-000001'}
            self.assertEqual(client.post('/api/subscribers/request', json=body).status_code, 403)
            for change in ({'consent': 'true'}, {'client_scope': 'client-a'}):
                self.assertEqual(client.post('/api/subscribers/request', json={**body, **change}, headers=headers).status_code, 422)
            response = client.post('/api/subscribers/request', json=body, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers['cache-control'], 'no-store')
            r = response.json()
            self.receipt(r)
            hook = {'event': 'hard_bounce', 'message-id': '<fixture@mail.invalid>', 'email': 'owner@example.invalid', 'id': 123, 'ts_event': 1795000000}
            self.assertEqual(client.post('/api/subscribers/brevo-events', json=hook, headers=headers).status_code, 403)
            good = {'x-ttros-brevo-token': 'fixture-provider'}
            self.assertEqual(client.post('/api/subscribers/brevo-events', json=hook, headers=good).status_code, 200)
            self.assertEqual(client.post('/api/subscribers/brevo-events', json=hook, headers=good).status_code, 200)
            self.assertFalse(sub.eligible(self.detail(r)))
            for action in ('confirm', 'unsubscribe'):
                self.assertEqual(client.get('/api/subscribers/'+action).status_code, 405)
                self.assertEqual(client.post('/api/subscribers/'+action, json={'token': 'bad'}).status_code, 403)
            forged = {'client_scope': 'global', 'kind': 'contact.subscription', 'record_id': r['contact_id'],
                      'payload': {'action': 'verified'}, 'request_key': 'forged-test'}
            self.assertEqual(client.post('/api/crm/events', json=forged).status_code, 422)
            with patch.dict(os.environ, {'TTROS_SUBSCRIBERS_ENABLED': 'false'}):
                self.assertEqual(client.post('/api/subscribers/request', json=body, headers=headers).status_code, 503)
