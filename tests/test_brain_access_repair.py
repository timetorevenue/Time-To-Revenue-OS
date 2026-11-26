"""November read/write repair. Revisit: when scope ownership changes."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tools import brain_memory as bm
from tools.business_brain_scope import load_registry, ClientScopeError

class AccessRepairTests(unittest.TestCase):
    def test_future_nested_records_share_read_search_graph_ownership(self):
        r=load_registry()
        for relative in ('sources/historical_calls/future-meeting/topic.md','sources/intake/records/future.md','people/future.md','entities/future.md'):
            p='business_brain:'+relative
            self.assertEqual(p,r.validate_brain_pointer('global',p))
            self.assertEqual('global',r.scope_for_search_identity('business_brain',p))
            self.assertEqual(p,r.validate_graph_target('global','ttros-business-brain',p))
            self.assertEqual(p,r.lexical_graph_gate('global','ttros-business-brain')(p))
    def test_boundaries(self):
        r=load_registry()
        for p in ('clients/other/private.md','people/example_dealership/private.md','people/credentials.md','people/../../.env'):
            with self.assertRaises(ClientScopeError): r.validate_brain_pointer('global','business_brain:'+p)
        with self.assertRaises(ClientScopeError):r.validate_brain_pointer('client:block-3-fixture-a','business_brain:people/a.md')
    def test_atomic_ordinary_creation_update_and_index(self):
        # Local disposable vault; no production records, commits or remote operations.
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            subprocess.run(['git','init','-q',directory],check=True)
            subprocess.run(['git','-C',directory,'config','user.name','Fixture'],check=True)
            subprocess.run(['git','-C',directory,'config','user.email','fixture@local'],check=True)
            with patch.object(bm,'VAULT_ROOT',root), patch('tools.brain_git_closure.push_commit',return_value='local_only'), patch('tools.brain_git_closure.record'), patch('tools.aos_indexer.index_one',return_value={'status':'success'}), patch('tools.aos_entity_index.ensure_current'), patch('tools.aos_embeddings.safe_refresh'), patch('tools.brain_graph_links.after_brain_change'):
                p=bm.resolve_ordinary_knowledge_pointer('people/repair-fixture.md')
                for text in ('Cara discussed contract pricing and a follow-up to send.','Updated attributed observation.'):
                    result=bm.update_note_section(p,section_id='observation',title='Interpretation',content=text,source='fixture meeting',session_id='local-repair-fixture')
                    self.assertTrue(result.commit)
                    self.assertIn(text,bm.read_vault_note(p)[1])
                    self.assertEqual('refreshed',result.index_status)
                self.assertNotIn('verified_fact',bm.read_vault_note(p)[1])
                (root/'people/alias.md').symlink_to(root/p)
                with self.assertRaises(bm.BrainMemoryError): bm.read_vault_note('people/alias.md')

class DavidWriteRepairTests(unittest.TestCase):
    def test_david_write_is_immediately_searchable_with_provenance(self):
        try:
            from tools import brain_memory_mcp as m
        except ModuleNotFoundError:
            self.skipTest('David MCP requires backend runtime Python')
        from tools import aos_indexer
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); db=root/'search.db'
            subprocess.run(['git','init','-q',directory],check=True)
            subprocess.run(['git','-C',directory,'config','user.name','Fixture'],check=True)
            subprocess.run(['git','-C',directory,'config','user.email','fixture@local'],check=True)
            index_one=aos_indexer.index_one
            with patch.object(bm,'VAULT_ROOT',root), patch.object(aos_indexer,'BUSINESS_BRAIN_ROOT',root), patch('tools.brain_git_closure.push_commit',return_value='local_only'), patch('tools.brain_git_closure.record'), patch('tools.aos_indexer.index_one',side_effect=lambda p:index_one(p,db_path=db)), patch('tools.aos_entity_index.ensure_current'), patch('tools.aos_embeddings.safe_refresh'), patch('tools.brain_graph_links.after_brain_change'):
                result=m.remember_brain_knowledge('people/repair-fixture.md','meeting','Fixture discussed pricing and a contract to send.','interpretation','fixture meeting lines 1-2','local-fixture')
                self.assertTrue(result['success'],result)
                self.assertFalse(result['review_required'])
                self.assertEqual('refreshed',result['index_status'])
                hits=aos_indexer.search('Fixture',source='business_brain',client_scope='global',db_path=db)
                self.assertIn('repair-fixture',str(hits))
                self.assertIn('fixture meeting lines 1-2',m.open_note('people/repair-fixture.md')['content'])
                denied=m.remember_brain_knowledge('people/repair-fixture.md','fact','Unverified assertion','verified_fact','historical statement','local-fixture')
                self.assertFalse(denied['success'])
                for pointer in ('people/../../escape.md','memory/example_dealership_sales_coach.md','clients/other/note.md'):
                    self.assertFalse(m.remember_brain_knowledge(pointer,'bad','unsafe','interpretation','fixture','local')['success'])

class CodexClosureBoundaryTests(unittest.TestCase):
    def test_codex_cannot_trigger_the_writer_autopush(self):
        from tools import brain_git_closure
        with patch.dict('os.environ',{'CODEX_THREAD_ID':'fixture'}), patch.object(brain_git_closure,'git') as git:
            self.assertEqual('pending',brain_git_closure.push_commit(Path('/unused'),'fixture'))
            git.assert_not_called()
