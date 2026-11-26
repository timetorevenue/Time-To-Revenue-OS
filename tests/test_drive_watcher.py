"""Drive Changes recovery, scope and bounded evidence-capture canary. No live auth."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from tools import drive_watcher as w


def file(ident, parent='root', name=None, mime='text/plain', version='1'):
    return dict(id=ident, name=name or ident + '.txt', parents=[parent], mimeType=mime,
                version=version, modifiedTime='2026-11-10T00:00:00Z')


class API:
    def __init__(self):
        self.nodes = {'root': file('root', parent='outside', name='root', mime=w.FOLDER)}
        self.pages, self.changes, self.downloads, self.calls = {}, [], [], []
        self.raw = b'Synthetic Drive evidence canary.\n'
        self.offline = False

    def metadata(self, ident):
        return self.nodes[ident].copy()

    def content(self, row):
        self.downloads.append(row['id'])
        return self.raw

    def get(self, endpoint, **kw):
        self.calls.append((endpoint, kw))
        if self.offline:
            raise OSError('offline')
        if endpoint == 'changes/startPageToken':
            return {'startPageToken': 'start'}
        if endpoint == 'files':
            return self.pages.get((kw['q'].split("'")[1], kw.get('pageToken')), {'files': []})
        if endpoint == 'changes':
            if self.changes:
                result = self.changes.pop(0)
                if isinstance(result, Exception):
                    raise result
                return result
            return {'changes': [], 'newStartPageToken': 'next'}
        raise AssertionError(endpoint)


class WatcherTests(unittest.TestCase):
    def setUp(self):
        # drive_watcher sets TTROS_BRAIN_AUTOPUSH=0 for its process; keep that out of later tests.
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make(self, temp, api=None, sink=None):
        api = api or API()
        sink = sink or (lambda rows: [(r['id'], 'imported', 'business_brain:sources/intake/records/x.md')
                                      for r, _, raw, _, _ in rows if raw is not None])
        return w.Watcher(Path(temp), api, sink, root_id='root'), api

    def test_resumable_inventory_and_page_cursor(self):
        with tempfile.TemporaryDirectory() as temp:
            watcher, api = self.make(temp)
            api.nodes['nested'] = file('nested', mime=w.FOLDER)
            api.nodes['doc'] = file('doc', 'nested', mime=w.DOC)
            api.pages[('root', None)] = {'files': [api.nodes['nested']], 'nextPageToken': 'two'}
            api.pages[('root', 'two')] = {'files': []}
            api.pages[('nested', None)] = {'files': [api.nodes['doc']]}
            watcher.run(pages=1, batch=1)
            self.assertEqual(watcher.value('cursor'), 'start')
            watcher.db.close()
            watcher, _ = self.make(temp, api)
            watcher.run(pages=2, batch=10)
            self.assertEqual(api.downloads, ['doc'])
            api.changes = [{'changes': [], 'nextPageToken': 'page2'}, {'changes': [], 'newStartPageToken': 'done'}]
            watcher.run(pages=2)
            self.assertEqual(watcher.value('cursor'), 'done')
            self.assertEqual(watcher.path(api.nodes['doc']), 'nested.txt/doc.txt')

    def test_offline_cursor_recovery_and_scope_before_content(self):
        with tempfile.TemporaryDirectory() as temp:
            watcher, api = self.make(temp)
            for ident, name in [('north', 'Example Dealership'), ('mirror', '04_IMPORTED'), ('clients', 'Clients')]:
                api.nodes[ident] = file(ident, name=name, mime=w.FOLDER)
            api.pages[('root', None)] = {'files': [api.nodes[i] for i in ('north', 'mirror', 'clients')]}
            watcher.run(pages=5, batch=10)
            self.assertFalse(api.downloads)
            self.assertFalse(any("'north'" in str(kw) for endpoint, kw in api.calls if endpoint == 'files'))
            previous = watcher.value('cursor')
            api.offline = True
            self.assertEqual(watcher.run()['failed'], 1)
            self.assertEqual(watcher.value('cursor'), previous)
            api.offline = False
            watcher.run()
            self.assertEqual(watcher.value('cursor'), 'next')

    def test_rename_move_delete_no_redownload_and_outside_move(self):
        with tempfile.TemporaryDirectory() as temp:
            watcher, api = self.make(temp)
            api.nodes['doc'] = file('doc')
            api.pages[('root', None)] = {'files': [api.nodes['doc']]}
            watcher.run(batch=10)
            renamed = {**api.nodes['doc'], 'name': 'renamed.txt'}
            api.changes = [{'changes': [{'fileId': 'doc', 'file': renamed}], 'newStartPageToken': 'renamed'}]
            self.assertEqual(watcher.run()['metadata_only'], 1)
            self.assertEqual(api.downloads, ['doc'])
            outside = {**renamed, 'parents': []}
            api.changes = [{'changes': [{'fileId': 'doc', 'file': outside}], 'newStartPageToken': 'outside'}]
            watcher.run()
            self.assertIsNone(watcher.path(watcher.node('doc')))
            api.changes = [{'changes': [{'fileId': 'doc', 'removed': True}], 'newStartPageToken': 'deleted'}]
            watcher.run()
            self.assertTrue(watcher.node('doc')['trashed'])
            self.assertEqual(api.downloads, ['doc'])

    def test_expired_cursor_reinventories_existing_folders(self):
        with tempfile.TemporaryDirectory() as temp:
            watcher, api = self.make(temp)
            api.nodes['folder'] = file('folder', mime=w.FOLDER)
            api.pages[('root', None)] = {'files': [api.nodes['folder']]}
            watcher.run(pages=5)
            api.changes = [w.CursorExpired()]
            watcher.run()
            self.assertIsNone(watcher.value('cursor'))
            watcher.run(pages=1)
            self.assertTrue(watcher.db.execute('SELECT 1 FROM inventory WHERE id="folder"').fetchone())

    def test_retry_fairness_and_changed_during_export(self):
        with tempfile.TemporaryDirectory() as temp:
            watcher, api = self.make(temp)
            api.nodes.update({i: file(i) for i in ('bad', 'good')})
            api.pages[('root', None)] = {'files': [api.nodes['bad'], api.nodes['good']]}
            original = api.content
            def content(row):
                if row['id'] == 'bad':
                    raise ValueError('unreadable')
                return original(row)
            api.content = content
            watcher.run(batch=1)
            self.assertEqual(watcher.run(batch=1)['failed'], 1)
            self.assertEqual(watcher.run(batch=1)['imported'], 1)
            api.nodes['good']['version'] = '2'
            watcher.remember(api.nodes['good'])
            api.metadata = lambda ident: {**api.nodes[ident], 'version': '3'}
            watcher.run(batch=10)
            self.assertEqual(watcher.node('good')['version'], '3')
            self.assertEqual(watcher.db.execute('SELECT dirty FROM nodes WHERE id="good"').fetchone()[0], 1)

    def test_sink_failure_replays_without_losing_pending_content(self):
        with tempfile.TemporaryDirectory() as temp:
            watcher, api = self.make(temp, sink=mock.Mock(side_effect=OSError('index offline')))
            api.nodes['doc'] = file('doc')
            api.pages[('root', None)] = {'files': [api.nodes['doc']]}
            self.assertEqual(watcher.run(batch=10)['failed'], 1)
            self.assertEqual(watcher.db.execute('SELECT dirty FROM nodes WHERE id="doc"').fetchone()[0], 1)
            self.assertEqual(watcher.value('cursor'), 'next')

    def test_local_canary_validated_writer_provenance_duplicate_no_push(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            brain, runtime = base / 'brain', base / 'runtime'
            brain.mkdir()
            (runtime / 'context').mkdir(parents=True)
            for name in ('client_scope_registry.json', 'client_scope_registry.schema.json'):
                shutil.copy(w.ROOT / 'context' / name, runtime / 'context' / name)
            subprocess.run(['git', 'init', '-q', str(brain)], check=True)
            row = file('canary', name='synthetic.txt')
            raw = b'Synthetic evidence; no customer data.\n'
            digest = hashlib.sha256(raw).hexdigest()
            inputs = [(row, 'synthetic.txt', raw, digest, None)]
            from tools import brain_git_closure
            # Real validated transaction, only automatic derived-refresh wrapper is isolated.
            def real_write(*args, refresh_indexes, **kwargs):
                self.assertFalse(refresh_indexes)
                return w.brain_memory._write_transaction_inner(*args, **kwargs)
            with mock.patch.object(w.brain_memory, 'write_transaction', side_effect=real_write), \
                 mock.patch.object(w.exchange, '_refresh', return_value=({'status': 'success'}, {'status': 'success'})), \
                 mock.patch.object(brain_git_closure, 'codex_context', return_value=True), \
                 mock.patch.object(brain_git_closure, 'git') as closure_git:
                result = w.capture_batch(inputs, brain=brain, root=runtime)
                self.assertEqual(result[0][1], 'imported')
                sha = subprocess.check_output(['git', '-C', str(brain), 'rev-parse', 'HEAD'], text=True).strip()
                pointer = result[0][2]
                self.assertEqual(w.source.extract_exact_bytes((brain / pointer.split(':', 1)[1]).read_text()), raw)
                self.assertEqual(w.capture_batch(inputs, brain=brain, root=runtime)[0][1], 'duplicate')
                self.assertEqual(subprocess.check_output(['git', '-C', str(brain), 'rev-parse', 'HEAD'], text=True).strip(), sha)
                closure_git.assert_not_called()
                provenance = (brain / 'sources/intake/drive/canary.md').read_text()
                self.assertIn('"category": "unknown"', provenance)
                self.assertIn(digest, provenance)
                row['name'] = 'renamed.txt'
                w.capture_batch([(row, 'renamed.txt', None, digest, pointer)], brain=brain, root=runtime)
                self.assertIn('renamed.txt', (brain / 'sources/intake/drive/canary.md').read_text())
                w.capture_batch([(row, None, None, digest, pointer)], brain=brain, root=runtime)
                self.assertIn('removed-or-out-of-scope', (brain / 'sources/intake/drive/canary.md').read_text())
                message = subprocess.check_output(['git', '-C', str(brain), 'log', '-1', '--format=%B'], text=True)
                self.assertIn(brain_git_closure.AUTOMATIC_CLOSURE_TRAILER, message)
            self.assertEqual(os.environ['TTROS_BRAIN_AUTOPUSH'], '0')

    def test_stale_ancestry_cannot_download_protected_content(self):
        with tempfile.TemporaryDirectory() as temp:
            watcher, api = self.make(temp)
            api.nodes['folder'] = file('folder', mime=w.FOLDER)
            api.nodes['doc'] = file('doc', parent='folder')
            watcher.remember(api.nodes['root'])
            watcher.remember(api.nodes['folder'])
            watcher.remember(api.nodes['doc'])
            watcher.put('cursor', 'snapshot')
            watcher.db.execute('DELETE FROM inventory')
            watcher.db.commit()
            api.nodes['folder']['name'] = 'Example Dealership'
            watcher.run(batch=10)
            self.assertFalse(api.downloads)
            self.assertIsNone(watcher.path(watcher.node('doc')))

    def test_no_daily_batch_or_byte_ceiling_and_usage_is_recorded(self):
        with tempfile.TemporaryDirectory() as temp:
            watcher, api = self.make(temp)
            self.assertFalse(hasattr(w, 'DAILY_BATCHES') or hasattr(w, 'DAILY_BYTES'))
            watcher.run()
            for n in range(30):  # far beyond the retired 24 batches/day
                name = 'f%d' % n
                api.nodes[name] = file(name)
                api.raw = b'distinct evidence %d\n' % n
                api.changes = [{'changes': [{'fileId': name, 'file': api.nodes[name]}], 'newStartPageToken': 't%d' % n}]
                receipt = watcher.run(batch=1)
                self.assertEqual(receipt['imported'], 1, n)
                self.assertNotIn('deferred', receipt)
            usage = receipt['usage_today']
            self.assertEqual(usage['captures'], 30)
            self.assertEqual(usage['failures'], 0)
            self.assertIn('not project-wide', usage['scope'])

    def test_protective_headroom_defers_without_acknowledging(self):
        with tempfile.TemporaryDirectory() as temp:
            watcher, api = self.make(temp)
            watcher.run()
            api.nodes['d'] = file('d')
            api.changes = [{'changes': [{'fileId': 'd', 'file': api.nodes['d']}], 'newStartPageToken': 'q'}]
            watcher.run(batch=0)
            watcher.metrics(est_quota_units=w.QUOTA_UNIT_HEADROOM)
            watcher.db.commit()
            receipt = watcher.run(batch=10)
            self.assertIn('headroom', receipt['deferred'])
            self.assertFalse(api.downloads)
            self.assertEqual(watcher.db.execute('SELECT dirty FROM nodes WHERE id="d"').fetchone()[0], 1)

    def test_throttling_backs_off_and_retains_pending(self):
        with tempfile.TemporaryDirectory() as temp:
            watcher, api = self.make(temp)
            watcher.run()
            api.nodes['t'] = file('t')
            api.changes = [w.Throttled('userRateLimitExceeded', 90)]
            receipt = watcher.run()
            self.assertEqual(receipt['throttled'], 1)
            self.assertIn('throttling', receipt['deferred'])
            self.assertGreaterEqual(float(watcher.value('backoff_until')) - __import__('time').time(), 80)
            calls = len(api.calls)
            self.assertIn('backoff', watcher.run()['deferred'])
            self.assertEqual(len(api.calls), calls)  # no API call during backoff
            watcher.put('backoff_until', '0')
            api.changes = [{'changes': [{'fileId': 't', 'file': api.nodes['t']}], 'newStartPageToken': 'ok'}]
            self.assertEqual(watcher.run()['imported'], 1)
            self.assertEqual(watcher.value('throttle_streak'), '0')

    def test_http_403_is_throttle_only_for_rate_reasons(self):
        class Resp:
            def __init__(self, code, reason, headers=None):
                self.status_code, self.headers = code, headers or {}
                self.body = json.dumps({'error': {'errors': [{'reason': reason}]}}).encode()
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def iter_content(self, n): yield self.body
        for code, reason, expect in ((429, '', w.Throttled), (403, 'userRateLimitExceeded', w.Throttled),
                                     (403, 'dailyLimitExceeded', w.Throttled), (403, 'forbidden', RuntimeError)):
            http = w.DriveHTTP.__new__(w.DriveHTTP)
            http.remaining, http.stats = 5, {'calls': 0, 'bytes': 0}
            http.session = mock.Mock(get=mock.Mock(return_value=Resp(code, reason, {'Retry-After': '7'})))
            with self.assertRaises(expect) as caught:
                http.get('files')
            if expect is w.Throttled:
                self.assertEqual(caught.exception.retry_after, 7)
            self.assertEqual(http.stats['calls'], 1)

    def test_unknown_ancestry_and_folder_move_into_scope(self):
        with tempfile.TemporaryDirectory() as temp:
            watcher, api = self.make(temp)
            watcher.run()
            api.nodes['newfolder'] = file('newfolder', mime=w.FOLDER)
            api.nodes['newdoc'] = file('newdoc', parent='newfolder')
            api.changes = [{'changes': [{'fileId': 'newdoc', 'file': api.nodes['newdoc']}],
                            'newStartPageToken': 'moved'}]
            receipt = watcher.run(pages=1, batch=10)
            self.assertEqual(receipt['imported'], 1)
            self.assertEqual(watcher.path(api.nodes['newdoc']), 'newfolder.txt/newdoc.txt')
            self.assertTrue(watcher.db.execute('SELECT 1 FROM inventory WHERE id="newfolder"').fetchone())

    def test_api_budget_resumes_inside_a_durable_change_page(self):
        with tempfile.TemporaryDirectory() as temp:
            watcher, api = self.make(temp)
            watcher.run()
            api.nodes['one'] = file('one')
            api.nodes['two'] = file('two', parent='newfolder')
            api.nodes['newfolder'] = file('newfolder', mime=w.FOLDER)
            api.changes = [{'changes': [{'fileId': ident, 'file': api.nodes[ident]} for ident in ('one', 'two')],
                            'newStartPageToken': 'finished'}]
            original = api.metadata
            def budget(ident):
                if ident == 'newfolder':
                    raise w.Budget()
                return original(ident)
            api.metadata = budget
            receipt = watcher.run(pages=1)
            self.assertIn('deferred', receipt)
            self.assertEqual(watcher.value('change_offset'), '1')
            self.assertIsNotNone(watcher.value('changes_page'))
            api.metadata = original
            watcher.run(pages=1, batch=10)
            self.assertEqual(watcher.value('cursor'), 'finished')
            self.assertIsNone(watcher.value('changes_page'))
            self.assertEqual(set(api.downloads), {'one', 'two'})

    def test_scope_and_size_negative_cases(self):
        for path in (['ExampleDealership'], ['example-dealership'], ['04_IMPORTED'], ['foo', '.env'], ['Clients'], ['a\nb']):
            self.assertFalse(w.safe_path(path), path)
        self.assertTrue(w.safe_path(['Research', 'notes.txt']))
        with tempfile.TemporaryDirectory() as temp:
            watcher, api = self.make(temp)
            api.nodes['big'] = {**file('big'), 'size': str(w.MAX_BYTES + 1)}
            api.pages[('root', None)] = {'files': [api.nodes['big']]}
            self.assertEqual(watcher.run(batch=10)['failed'], 1)
            self.assertFalse(api.downloads)


if __name__ == '__main__':
    unittest.main()
