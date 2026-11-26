"""Codex commit provenance blocks later service pushes without blocking local writes.
Revisit: on Brain synchronization changes. Last touched: 2026-11-10.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tools import brain_git_closure as closure, brain_memory


class ManualClosureTests(unittest.TestCase):
    def test_writer_marks_commit_and_separate_sweep_cannot_push_it(self):
        # No push command or service invocation, even against a local test remote.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(['git', 'init', '-q', '-b', 'main', str(root)], check=True)
            def git(*args):
                return subprocess.run(['git', '-C', str(root), *args], check=True,
                                      capture_output=True, text=True).stdout.strip()
            (root / 'baseline.md').write_text('baseline\n')
            git('add', 'baseline.md')
            git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@local', 'commit', '-qm', 'baseline')
            git('update-ref', 'refs/remotes/origin/main', git('rev-parse', 'HEAD'))
            with patch.object(brain_memory, 'VAULT_ROOT', root), \
                 patch.dict(os.environ, {'TTROS_CODEX_NO_PUSH': '1', 'TTROS_BRAIN_CLOSURE_STATE': str(root/'closure.json')}):
                result = brain_memory._write_transaction_inner(
                    {'index/WORK.md': '---\nid: fixture-work\ntype: index\n---\n# WORK\nSource: fixture\n'},
                    source='isolated validation fixture', session_id='codex-fixture')
            self.assertIsNotNone(result.commit)
            self.assertIn(closure.MANUAL_CLOSURE_TRAILER, git('log', '-1', '--format=%B'))
            real_git = closure.git
            calls = []
            def safe_git(path, *args):
                calls.append(args)
                if args[0] == 'push':
                    self.fail('A service attempted to push a Codex-origin commit')
                if args[0] == 'fetch':
                    return subprocess.CompletedProcess(args, 0, '', '')
                return real_git(path, *args)
            with patch.object(closure, 'codex_context', return_value=False), \
                 patch.object(closure, 'enabled', side_effect=AssertionError('must stop before config access')), \
                 patch.object(closure, 'git', side_effect=safe_git), \
                 patch.dict(os.environ, {'TTROS_BRAIN_CLOSURE_STATE': str(root/'closure.json')}):
                outcome = closure.sweep(root, alert=lambda _: None)
            self.assertEqual(outcome['result'], 'pending_non_codex_closure')
            self.assertEqual(outcome['pending_count'], 1)
            self.assertFalse(any(call[0] == 'push' for call in calls))

    def test_non_codex_unmarked_commit_reaches_existing_sync_boundary(self):
        # Assert normal synchronization stays available; never execute a push.
        with patch.object(closure, 'codex_context', return_value=False), \
             patch.object(closure, 'git', return_value=subprocess.CompletedProcess([], 0, '', '')), \
             patch.object(closure, 'manual_closure_pending', return_value=False), \
             patch.object(closure, 'enabled', return_value=False):
            self.assertEqual(closure.push_commit(Path('/fixture'), 'sha'), 'local_only')

    def test_writer_has_no_network_boundary_even_with_sweep_enabled(self):
        calls = []
        def fake_git(path, *args):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, '', '')
        with patch.dict('os.environ', {'TTROS_BRAIN_AUTOPUSH': '1'}), \
             patch.object(closure, 'codex_context', return_value=False), \
             patch.object(closure, 'manual_closure_pending', return_value=False), \
             patch.object(closure, 'enabled', return_value=True), \
             patch.object(closure, 'git', side_effect=fake_git):
            self.assertEqual(closure.push_commit(Path('/fixture'), 'sha'), 'local_only')
        self.assertNotIn('push', [call[0] for call in calls])
        with patch.dict('os.environ', {'TTROS_BRAIN_AUTOPUSH': '1', 'TTROS_CODEX_NO_PUSH': '1'}):
            self.assertFalse(closure.enabled())

    def test_remote_divergence_stops_before_sync_and_reports_immediately(self):
        def fake_git(path, *args):
            output = '1' if args[0] == 'rev-list' else ''
            return subprocess.CompletedProcess(args, 0, output, '')
        alerts = []
        from contextlib import nullcontext
        with patch.object(closure, 'brain_lock', return_value=nullcontext()), \
             patch.object(closure, 'git', side_effect=fake_git), \
             patch.object(closure, 'state', return_value={}), \
             patch.object(closure, 'record'), \
             patch.object(closure, 'push_commit', side_effect=AssertionError('sync on divergence')):
            self.assertEqual(closure.sweep(Path('/fixture'), alert=alerts.append)['result'],
                             'stopped_remote_diverged')
        self.assertEqual(len(alerts), 1)

    def test_three_pending_commits_are_visible_warning(self):
        with patch.object(closure, 'pending_count', return_value=3), \
             patch.object(closure, 'manual_closure_pending', return_value=True), \
             patch.object(closure, 'state', return_value={}):
            self.assertEqual(closure.status(Path('/fixture'))['warning'], 'three_or_more_unpushed_commits')
