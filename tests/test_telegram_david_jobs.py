"""Actual bridge -> Ask David contract, polling, recovery and idempotent delivery."""
import concurrent.futures
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from tests.test_telegram_bridge_formatting import load_bridge

JOB = 'DJ-20261114T093513Z-13e77a33'
ACK = {'kind': 'david_job', 'job_id': JOB, 'message': 'David is still working.'}
DONE = {'kind': 'david_reply', 'success': True, 'response': 'Completed answer'}

class DavidJobDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.bridge = load_bridge()
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.bridge.UPDATE_STATE_FILE = Path(self.tmp.name)/'updates.json'
        self.bridge.DAVID_JOB_POLL_SECONDS = .001
        self.bridge.DAVID_JOB_WAIT_SECONDS = .1
        p=patch.object(self.bridge,'log');p.start();self.addCleanup(p.stop)

    def urlopen(self, value):
        from unittest.mock import MagicMock
        response=MagicMock(); response.__enter__.return_value.read.return_value=json.dumps(value).encode()
        return response

    def test_actual_route_stable_id_and_poll_to_same_message_once(self):
        b=self.bridge
        with patch.object(b,'post_backend_json',return_value=ACK) as post, \
             patch.object(b.urllib.request,'urlopen',return_value=self.urlopen({'status':'done','result':DONE})) as get, \
             patch.object(b,'send',return_value={'message_sent':True,'message_id':7}) as send, \
             patch.object(b,'_edit_david_reply',return_value=True) as edit:
            b._run_david_request(123,'hello','update-1')
            b._run_david_request(123,'hello','update-1')
        post.assert_called_once_with('/api/dashboard/ask-david',{'text':'hello','conversation_id':'telegram-123','delivery_id':'update-1'},timeout=180)
        self.assertTrue(get.call_args.args[0].full_url.endswith('/jobs/'+JOB))
        send.assert_called_once();edit.assert_called_once_with(123,7,'[David] Completed answer')
        self.assertEqual(b._david_delivery(123,'update-1')['status'],'sent')
        self.assertTrue(b.claim_update(1))
        self.assertEqual(b._david_delivery(123,'update-1')['status'],'sent')

    def test_restart_resumes_pending_without_post_or_ack(self):
        b=self.bridge;b._david_delivery(123,'update-1',status='pending',job_id=JOB,message_id=7)
        # Reload the module to prove only durable state supplies recovery.
        b=load_bridge();b.UPDATE_STATE_FILE=self.bridge.UPDATE_STATE_FILE
        with patch.object(b,'post_backend_json') as post,patch.object(b,'send') as send, \
             patch.object(b.urllib.request,'urlopen',return_value=self.urlopen({'status':'done','result':DONE})), \
             patch.object(b,'_edit_david_reply',return_value=True) as edit,patch.object(b,'log'):
            b._run_david_request(123,'','update-1')
        post.assert_not_called();send.assert_not_called();edit.assert_called_once()

    def test_failed_and_interrupted_jobs_delivered_without_model_replay(self):
        for status in ('failed','interrupted'):
            b=self.bridge;b._david_delivery(123,status,status='pending',job_id=JOB,message_id=7)
            with patch.object(b,'post_backend_json') as post,patch.object(b,'_edit_david_reply',return_value=True) as edit, \
                 patch.object(b.urllib.request,'urlopen',return_value=self.urlopen({'status':status,'error':'fixture'})):
                b._run_david_request(123,'',status)
            self.assertIn(status,edit.call_args.args[2]);post.assert_not_called()

    def test_uncertain_send_never_duplicates_and_edit_retries_same_id(self):
        b=self.bridge
        with patch.object(b,'post_backend_json',return_value=DONE) as post, \
             patch.object(b,'send',return_value={'message_sent':False}) as send:
            b._run_david_request(123,'hi','uncertain');b._run_david_request(123,'hi','uncertain')
        post.assert_called_once();send.assert_called_once()
        self.assertEqual(b._david_delivery(123,'uncertain')['status'],'uncertain')
        b._david_delivery(123,'edit',status='pending',job_id=JOB,message_id=8)
        with patch.object(b.urllib.request,'urlopen',return_value=self.urlopen({'status':'done','result':DONE})), \
             patch.object(b,'_edit_david_reply',side_effect=[OSError(),True]) as edit:
            b._run_david_request(123,'','edit')
        self.assertEqual(edit.call_count,2)
        self.assertEqual([c.args[1] for c in edit.call_args_list],[8,8])

    def test_two_chats_isolate_identity_and_pending_job_does_not_block_other_chat(self):
        b=self.bridge;gate=threading.Event();entered=threading.Event();posts=[]
        def post(route,payload,timeout):
            posts.append(payload)
            return ACK if payload['text']=='slow' else DONE
        def get(*a,**k):
            entered.set();gate.wait(2)
            return self.urlopen({'status':'done','result':DONE})
        with patch.object(b,'post_backend_json',side_effect=post),patch.object(b.urllib.request,'urlopen',side_effect=get), \
             patch.object(b,'send',return_value={'message_sent':True,'message_id':7}),patch.object(b,'_edit_david_reply',return_value=True), \
             concurrent.futures.ThreadPoolExecutor(2) as pool:
            slow=pool.submit(b._run_david_request,123,'slow','slow');self.assertTrue(entered.wait(1))
            pool.submit(b._run_david_request,456,'hi','fast').result(timeout=1)
            self.assertFalse(slow.done());gate.set();slow.result(timeout=2)
        self.assertEqual({p['conversation_id'] for p in posts},{'telegram-123','telegram-456'})

    def test_uncertain_submission_recovers_read_only_without_reposting(self):
        b=self.bridge
        b._david_delivery(123,'posted',status='submitting')
        with patch.object(b,'post_backend_json') as post, \
             patch.object(b.urllib.request,'urlopen',side_effect=[self.urlopen({'job_id':JOB}),self.urlopen({'status':'done','result':DONE})]), \
             patch.object(b,'send',return_value={'message_sent':True,'message_id':7}):
            b._run_david_request(123,'','posted')
        post.assert_not_called()
        self.assertEqual(b._david_delivery(123,'posted')['status'],'sent')

    def test_unknown_submission_is_not_reposted_and_old_send_tombstone_survives(self):
        import urllib.error
        b=self.bridge;b._david_delivery(123,'lost',status='submitting')
        with patch.object(b,'post_backend_json') as post,patch.object(b.urllib.request,'urlopen',side_effect=urllib.error.HTTPError('fixture',404,'missing',{},None)):
            b._run_david_request(123,'','lost')
        post.assert_not_called();self.assertEqual(b._david_delivery(123,'lost')['status'],'uncertain')
        b.MAX_RECORDED_UPDATE_IDS=1
        b._david_delivery(123,'old',status='sent',result=DONE,payload={'text':'hello'})
        b._david_delivery(123,'new',status='sent',result=DONE)
        old=b._david_delivery(123,'old')
        self.assertEqual(old['status'],'sent');self.assertNotIn('result',old)
        with patch.object(b,'post_backend_json') as post,patch.object(b,'send') as send:
            b._run_david_request(123,'hello','old')
        post.assert_not_called();send.assert_not_called()

    def test_corrupt_state_refuses_model_replay(self):
        b=self.bridge;b.UPDATE_STATE_FILE.write_text('corrupt')
        with patch.object(b,'post_backend_json') as post:
            b._run_david_request(123,'hi','corrupt')
        post.assert_not_called()

if __name__=='__main__':unittest.main()

class UnifiedOperatorRouteTests(unittest.TestCase):
    def setUp(self):
        self.b=load_bridge()

    def test_plain_work_and_chain_share_david_and_keep_delivery_and_chat(self):
        b=self.b
        for text in ('hello', 'David, what happened?', '/work codex inspect local source',
                     '/work claude inspect local source', '/chain prepare a local checklist'):
            with self.subTest(text=text), patch.object(b,'load_allowed',return_value={'operator_chat_ids':[123],'pilots':{}}), \
                 patch.object(b,'dispatch_david_request') as dispatch,patch.object(b,'send'):
                b.handle_message({'chat':{'id':123},'text':text},delivery_id='telegram-update-42')
                dispatch.assert_called_once()
                self.assertEqual(dispatch.call_args.args[0],123)
                self.assertEqual(dispatch.call_args.kwargs['delivery_id'],'telegram-update-42')
                if text.startswith('/work'):
                    self.assertEqual(dispatch.call_args.args[1],text)
        for name in ('dispatch_agent_request','post_agent','get_chat_mode','set_chat_mode'):
            self.assertFalse(hasattr(b,name))

    def test_unregistered_or_pilot_cannot_route_operator_work(self):
        b=self.b
        with patch.object(b,'load_allowed',return_value={'operator_chat_ids':[123],'pilots':{'456':'other-pilot'}}), \
             patch.object(b,'dispatch_david_request') as dispatch,patch.object(b,'post_backend_json') as post, \
             patch.object(b,'send'):
            for chat in (456,789):
                b.handle_message({'chat':{'id':chat},'text':'/work codex read private files'},delivery_id='untrusted')
                b.handle_message({'chat':{'id':chat},'text':'Did my task finish?'},delivery_id='untrusted-2')
            dispatch.assert_not_called();post.assert_not_called()

    def test_hermes_work_and_orchestrator_lane_are_rejected_locally(self):
        b=self.b
        with patch.object(b,'dispatch_david_request') as dispatch,patch.object(b,'post_backend_json') as post, \
             patch.object(b,'send'),patch.object(b,'log'):
            b.handle_operator(123,'/work hermes do something')
            b.handle_operator(123,'/delegate orchestrator do something')
        dispatch.assert_not_called();post.assert_not_called()

    def test_dispatch_duplicate_is_single_flight(self):
        b=self.b;entered=threading.Event();release=threading.Event()
        def run(*args):
            entered.set();release.wait(2)
            with b._AGENT_REQUEST_LOCK:b._ACTIVE_AGENT_REQUESTS.discard(args[2])
        with patch.object(b,'_run_david_request',side_effect=run) as worker,patch.object(b,'log'):
            self.assertTrue(b.dispatch_david_request(123,'hello',delivery_id='same'))
            self.assertTrue(entered.wait(1))
            self.assertFalse(b.dispatch_david_request(123,'hello',delivery_id='same'))
            release.set()
        self.assertEqual(worker.call_count,1)
