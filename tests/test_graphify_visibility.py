"""Dashboard Graphify visibility: scope, freshness, missing artifacts, and provenance.

Revisit: when the visibility contract changes. Last touched: 2026-11-09.
"""
import hashlib
import importlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class GraphifyVisibilityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = importlib.import_module('dashboard.backend.main')
        cls.feature = cls.main.graphify_visibility

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.vault = self.root / 'vault'
        self.vault.mkdir()
        self.db = self.root / 'index.db'
        self.graph_root = self.root / 'graphs'
        self.graph_path = self.graph_root / 'document_graphs/ttros-business-brain/published/graph.json'
        self.graph_path.parent.mkdir(parents=True)
        self.patch = mock.patch.object(self.main.business_brain, 'BUSINESS_BRAIN_ROOT', self.vault)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.registry = self.main.business_brain_scope.load_registry()
        self.a = self.file('memory/company.md', '# Company')
        self.b = self.file('memory/offers.md', '# Offers')
        self.c = self.file('people/new-note.md', '# Not yet indexed')
        self.file('memory/example_dealership_sales_coach.md', 'PROTECTED')
        self.file('sessions/private.md', 'PRIVATE')
        self.file('memory/.hidden.md', 'HIDDEN')
        self.file('people/credentials.txt', 'SECRET')
        outside = self.root / 'outside.md'
        outside.write_text('OUTSIDE')
        (self.vault / 'people/escape.md').symlink_to(outside)
        (self.vault / 'people/linked-folder').symlink_to(self.root, target_is_directory=True)
        with sqlite3.connect(self.db) as conn:
            conn.executescript('''
                CREATE TABLE documents(path TEXT, source TEXT, client_scope TEXT, title TEXT, mtime REAL, size_bytes INTEGER, indexed_at TEXT);
                CREATE TABLE brain_documents(path TEXT);
                CREATE TABLE entity_index_meta(key TEXT, value TEXT);
                CREATE TABLE graph_suggested_links(source_path TEXT, target_path TEXT, client_scope TEXT, relation TEXT, status TEXT, reasons TEXT, source_sha256 TEXT, target_sha256 TEXT);
            ''')
            self.index(conn, self.a, 'global')
            self.index(conn, self.b, 'global', stale=True)
            self.index(conn, self.file('memory/example_dealership_sales_coach.md', 'PROTECTED'), 'global')
            self.index(conn, self.file('memory/private-client.md', 'CLIENT'), 'client:other')
            # A Search-owned file that is not in the global Brain reader remains invisible.
            conn.execute("INSERT INTO brain_documents VALUES (?)", (self.a,))
            conn.execute("INSERT INTO entity_index_meta VALUES ('built_at', '2026-11-09T00:00:00Z')")
            conn.execute('INSERT INTO graph_suggested_links VALUES (?,?,?,?,?,?,?,?)',
                         (self.a, self.b, 'global', 'related_to', 'suggested', '["shared entity"]', self.sha(self.a), self.sha(self.b)))
            conn.execute('INSERT INTO graph_suggested_links VALUES (?,?,?,?,?,?,?,?)',
                         (self.a, 'business_brain:memory/example_dealership_sales_coach.md', 'global', 'related_to', 'suggested', '[]', '', ''))
        self.publish()

    def file(self, rel, text):
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return 'business_brain:' + rel

    def sha(self, pointer):
        return hashlib.sha256((self.vault / pointer.split(':', 1)[1]).read_bytes()).hexdigest()

    def index(self, conn, pointer, scope, stale=False):
        stat = (self.vault / pointer.split(':', 1)[1]).stat()
        conn.execute('INSERT INTO documents VALUES (?,?,?,?,?,?,?)',
                     (pointer, 'business_brain', scope, pointer.rsplit('/', 1)[-1], 0 if stale else stat.st_mtime, stat.st_size, '2026-11-09T00:00:00Z'))

    def publish(self):
        self.graph_path.write_text(json.dumps({'nodes': [
            {'id': 'a', 'kind': 'note', 'source_path': self.a, 'content_sha256': self.sha(self.a)},
            {'id': 'b', 'kind': 'note', 'source_path': self.b, 'content_sha256': 'old'},
            {'id': 'private', 'kind': 'note', 'source_path': 'business_brain:memory/example_dealership_sales_coach.md'},
            {'id': 'heading', 'kind': 'heading', 'source_path': self.a},
        ], 'edges': [
            {'source': 'a', 'target': 'b', 'relation': 'wiki_link', 'edge_kind': 'explicit', 'extractor': 'ttros.wiki', 'confidence': 'EXTRACTED'},
            {'source': 'a', 'target': 'private', 'relation': 'wiki_link'},
            {'source': 'a', 'target': 'heading', 'relation': 'contains'},
        ]}))

    def snapshot(self):
        return self.feature.snapshot(vault=self.vault, registry=self.registry, graph_root=self.graph_root,
                                     db_path=self.db, repositories=[{'id': 'owner-gh-account/TTROS', 'commit_hash': 'abc', 'timestamp': '2026-11-09'}])

    def test_inventory_scoped_search_graph_and_unindexed_documents(self):
        result = self.snapshot()
        docs = {d['path']: d for d in result['business_brain']['documents']}
        self.assertIn(self.c, docs)
        for forbidden in ('example_dealership', 'escape', 'linked-folder', 'sessions/', 'credentials', '.hidden'):
            self.assertNotIn(forbidden, json.dumps(result))
        self.assertEqual(docs[self.a]['search_status'], 'current')
        self.assertEqual(docs[self.b]['search_status'], 'stale')
        self.assertEqual(docs[self.c]['search_status'], 'unindexed')
        self.assertEqual(docs[self.a]['graph_status'], 'current')
        self.assertEqual(docs[self.b]['graph_status'], 'stale')
        self.assertEqual(docs[self.c]['graph_status'], 'unindexed')
        self.assertTrue(docs[self.a]['entity_indexed'])
        self.assertEqual(result['business_brain']['document_edges'], 1)
        self.assertEqual(result['business_brain']['suggested_edges'], 1)
        link = docs[self.a]['connections'][0]
        self.assertEqual((link['path'], link['direction'], link['extractor']), (self.b, 'outgoing', 'ttros.wiki'))
        self.assertFalse(docs[self.a]['suggestions'][0]['stale'])
        self.assertEqual(result['repository_coverage'][0]['code_graph'], 'published snapshot')
        self.assertEqual(result['repository_coverage'][1]['code_graph'], 'not indexed')

    def test_readonly_no_process_no_new_database_or_content_writes(self):
        before = self.db.read_bytes()
        graph = self.graph_path.read_bytes()
        with mock.patch('subprocess.run', side_effect=AssertionError('no process')), mock.patch('subprocess.Popen', side_effect=AssertionError('no process')):
            self.snapshot()
        self.assertEqual(before, self.db.read_bytes())
        self.assertEqual(graph, self.graph_path.read_bytes())

    def test_missing_and_corrupt_graph_are_unknown_not_empty_success(self):
        for content in (None, '{invalid', '[]', '{"nodes": [], "edges": {}}'):
            if content is None:
                self.graph_path.unlink()
            else:
                self.graph_path.write_text(content)
            result = self.snapshot()
            self.assertIsNone(result['business_brain']['graph_documents'])
            self.assertIsNone(result['business_brain']['unlinked'])
            self.assertTrue(result['warnings'])
            self.assertEqual(result['business_brain']['documents'][0]['graph_status'], 'unknown')

    def test_missing_index_is_unknown_and_does_not_create_database(self):
        self.db.unlink()
        result = self.snapshot()
        self.assertFalse(self.db.exists())
        self.assertIsNone(result['business_brain']['search_indexed'])
        self.assertTrue(all(d['search_status'] == 'unknown' for d in result['business_brain']['documents']))

    def test_suggestion_staleness_does_not_generate_new_links(self):
        self.file('memory/company.md', '# Changed')
        docs = {d['path']: d for d in self.snapshot()['business_brain']['documents']}
        self.assertTrue(docs[self.a]['suggestions'][0]['stale'])
        self.assertEqual(len(docs[self.a]['suggestions']), 1)

    def test_existing_activity_connections_keep_their_provenance(self):
        graph = json.loads(self.graph_path.read_text())
        graph['nodes'].append({'id': 'activity:AOS-2026-0001', 'kind': 'activity', 'title': 'AOS-2026-0001'})
        graph['edges'].append({'source': 'activity:AOS-2026-0001', 'target': 'a', 'relation': 'touched',
                               'edge_kind': 'derived', 'confidence': 'DERIVED', 'extractor': 'ttros.frontmatter.queue_ids'})
        self.graph_path.write_text(json.dumps(graph))
        docs = {d['path']: d for d in self.snapshot()['business_brain']['documents']}
        activity = next(link for link in docs[self.a]['connections'] if link['relation'] == 'touched')
        self.assertEqual(activity['title'], 'AOS-2026-0001')
        self.assertEqual(activity['activity_id'], 'activity:AOS-2026-0001')
        self.assertEqual(activity['extractor'], 'ttros.frontmatter.queue_ids')
        self.assertNotIn('path', activity)

    def test_allowed_subtrees_match_reader_gate_and_no_root_crawl(self):
        real_scandir = self.feature.os.scandir
        visited = []
        def scoped_scandir(path):
            visited.append(Path(path))
            self.assertNotEqual(Path(path), self.vault)
            return real_scandir(path)
        with mock.patch.object(self.feature.os, 'scandir', scoped_scandir):
            files = self.feature.authorised_files(self.vault, self.registry)
        for pointer in files:
            self.assertEqual(self.registry.validate_brain_pointer('global', pointer), pointer)
        self.assertTrue(visited)

    def test_endpoint_preserves_repo_contract_and_adds_visibility(self):
        marker = {'business_brain': {'total': 3}}
        with mock.patch.object(self.feature, 'snapshot', return_value=marker), mock.patch.object(self.main.GRAPHIFY_SERVICE, 'list_repositories', return_value=[{'id': 'repo'}]), mock.patch.object(self.main.GRAPHIFY_SERVICE, '_graphify_version', return_value='test'):
            result = self.main.dashboard_graphify()
        self.assertEqual(result['repos'], [{'id': 'repo'}])
        self.assertEqual(result['visibility'], marker)
        self.assertFalse(result['model_invoked'])
