"""Website preparation acceptance: isolated repositories, zero models/network.
Revisit on website work-order contract changes. Last touched: 2026-11-16.
"""
import copy
import json
import os
import sys
from unittest.mock import patch

from tests.test_command_centre_website import WebsiteCase, HEADLINE
from tests.command_centre_fixture import git, make_site_repo, write_website_registry
import aos_jobs
import aos_website as web
import aos_website_prepare as prep


class Readiness(WebsiteCase):
    def setUp(self):
        super().setUp()
        self.inputs = {'project': 'fixture-site', 'repository': str(self.repo), 'client_scope': 'global',
                       'request': f"Change the homepage headline to '{HEADLINE}'", 'branch': 'feature/website-copy',
                       'affected_paths': ['site/index.html'], 'source_references': ['AGENTS.md', 'site/index.html'],
                       'acceptance_checks': [{'path': 'site/index.html', 'contains': HEADLINE}]}
        guard = patch.object(web, 'codex_worker', side_effect=AssertionError('model worker forbidden in offline tests'))
        guard.start()
        self.addCleanup(guard.stop)

    def prepared(self):
        order = prep.prepare(self.root, self.inputs)
        self.assertEqual(order['state'], 'prepared', order['validation'])
        return order

    def completed(self, worker=None):
        order = self.prepared()
        job = prep.queue(self.root, order['id'])
        if worker:
            with patch.object(web, 'fixture_worker', return_value=worker):
                job = aos_jobs.execute(self.root, job['id'], brain_read=self.brain_read)
        else:
            job = aos_jobs.execute(self.root, job['id'], brain_read=self.brain_read)
        return order, job

    def test_offline_dependency_copy_and_lock_protection(self):
        cache = self.repo.parent / 'cache' / 'node_modules'
        cache.mkdir(parents=True)
        (cache / 'example.js').write_text('offline dependency')
        (self.repo / 'package-lock.json').write_text('{"lockfileVersion": 3}')
        git(self.repo, 'add', 'package-lock.json')
        git(self.repo, 'commit', '-qm', 'Add dependency lock fixture')
        project = {'dependency_cache': {'path': str(cache),
                   'lock_sha256': web._sha((self.repo / 'package-lock.json').read_bytes())}}
        worktree = self.repo.parent / 'isolated-job'; worktree.mkdir()
        web.install_cached_dependencies(project, self.repo, worktree)
        copied = worktree / 'node_modules/example.js'
        self.assertEqual(copied.read_text(), 'offline dependency')
        copied.write_text('job edit')
        self.assertEqual((cache / 'example.js').read_text(), 'offline dependency')
        (self.repo / 'package-lock.json').write_text('changed')
        with self.assertRaisesRegex(web.WebsiteError, 'lockfile mismatch'):
            web.dependency_cache(project, self.repo)

    def test_offline_dependencies_reject_escaping_links(self):
        cache = self.repo.parent / 'cache' / 'node_modules'; cache.mkdir(parents=True)
        (self.repo / 'package-lock.json').write_text('{}')
        project = {'dependency_cache': {'path': str(cache),
                   'lock_sha256': web._sha(b'{}')}}
        (cache / 'escape').symlink_to(self.repo)
        with self.assertRaisesRegex(web.WebsiteError, 'escaping symlink'):
            web.dependency_cache(project, self.repo)
        redirected = self.repo.parent / 'redirected-job'; redirected.mkdir()
        (redirected / 'node_modules').symlink_to(cache, target_is_directory=True)
        (cache / 'escape').unlink()
        with self.assertRaisesRegex(web.WebsiteError, 'redirect'):
            web.install_cached_dependencies(project, self.repo, redirected)

    def test_complete_workflow_has_snapshot_spec_acceptance_and_verified_preview(self):
        order, job = self.completed()
        self.assertEqual(job['status'], 'done', job.get('detail'))
        shown = prep.view(self.root, prep.load(self.root, order['id']))
        self.assertEqual((shown['state'], shown['released'], shown['job']['publish']['status']), ('validated', False, 'held'))
        self.assertEqual(shown['job']['branch'], self.inputs['branch'])
        self.assertEqual(shown['job']['usage']['model_calls'], 0)
        self.assertTrue(shown['job']['acceptance']['passed'])
        self.assertEqual(shown['job']['artifact_problems'], [])
        self.assertIn(HEADLINE, web.preview_file(self.root, job, 'index.html').read_text())
        self.assertTrue(shown['job']['receipts'])
        self.assertTrue(shown['executor']['input']['preparation']['snapshot']['sources'])
        self.assertEqual(prep.queue(self.root, order['id'])['id'], job['id'])
        self.assertEqual(len(aos_jobs.list_jobs(self.root)), 1)
        self.assert_checkout_untouched()

    def test_draft_and_missing_requirements_never_queue(self):
        draft = prep.prepare(self.root, {'project': 'fixture-site'}, draft=True)
        self.assertEqual(draft['state'], 'draft')
        with self.assertRaises(web.WebsiteError):
            prep.queue(self.root, draft['id'])
        for field in ('repository', 'client_scope', 'request', 'branch', 'affected_paths', 'acceptance_checks', 'source_references'):
            with self.subTest(field=field):
                inputs = copy.deepcopy(self.inputs)
                inputs.pop(field)
                row = prep.prepare(self.root, inputs)
                self.assertEqual(row['state'], 'blocked')
                with self.assertRaises(web.WebsiteError):
                    prep.queue(self.root, row['id'])
        self.assertEqual(aos_jobs.list_jobs(self.root), [])

    def test_wrong_project_repository_scope_and_publish_attempt(self):
        for override, message in [({'project': 'unknown-site'}, 'no registered'),
                                  ({'repository': str(self.root)}, 'repository identity'),
                                  ({'client_scope': 'another-client'}, 'client-scope mismatch'),
                                  ({'allowed_actions': prep.ACTIONS + ['publish']}, 'publish/deploy')]:
            with self.subTest(override=override):
                row = prep.prepare(self.root, {**self.inputs, **override})
                self.assertEqual(row['state'], 'blocked')
                self.assertIn(message, '; '.join(row['validation']['problems']))
        self.assertEqual(aos_jobs.list_jobs(self.root), [])

    def test_stale_git_blocks_both_queue_and_execution(self):
        order = self.prepared()
        (self.repo / 'site/styles.css').write_text('body {color: navy;}')
        self.assertEqual(prep.view(self.root, prep.load(self.root, order['id']))['state'], 'blocked')
        with self.assertRaises(web.WebsiteError):
            prep.queue(self.root, order['id'])
        git(self.repo, 'add', '-A'); git(self.repo, 'commit', '-qm', 'advance baseline')
        self.assertIn('stale Git', '; '.join(prep.inspect(self.root, prep.load(self.root, order['id']))['problems']))
        job, _ = aos_jobs.create(self.root, conversation_id='fixture', topic='website', request=order['request'], executor=order['executor'])
        result = aos_jobs.execute(self.root, job['id'])
        self.assertEqual((result['status'], result['failure_class']), ('refused', 'website_preparation_blocked'))
        self.assertFalse((self.root / web.RESULTS / job['id'] / 'worktree').exists())

    def test_locally_known_behind_upstream_blocks(self):
        git(self.repo, 'branch', 'upstream')
        git(self.repo, 'checkout', '-q', 'upstream')
        (self.repo / 'site/styles.css').write_text('body {}')
        git(self.repo, 'add', '-A'); git(self.repo, 'commit', '-qm', 'upstream advance')
        git(self.repo, 'checkout', '-q', 'main')
        git(self.repo, 'branch', '--set-upstream-to=upstream', 'main')
        row = prep.prepare(self.root, self.inputs)
        self.assertIn('behind', '; '.join(row['validation']['problems']))

    def test_unsafe_paths_output_symlink_and_missing_asset(self):
        for value in ('../escape', '/tmp/escape', 'site/../../escape', 'site/.env', '.git/config'):
            with self.subTest(path=value):
                row = prep.prepare(self.root, {**self.inputs, 'affected_paths': [value]})
                self.assertEqual(row['state'], 'blocked')
        write_website_registry(self.root, self.repo, required_assets=['site/missing-logo.png'])
        row = prep.prepare(self.root, self.inputs)
        self.assertIn('required source/asset missing', '; '.join(row['validation']['problems']))
        isolated = self.root.parent / 'unsafe-runtime'; isolated.mkdir()
        (isolated / 'results').mkdir()
        external = self.root.parent / 'outside'; external.mkdir()
        (isolated / 'results/website').symlink_to(external, target_is_directory=True)
        with self.assertRaises(web.WebsiteError):
            prep.prepare(isolated, self.inputs)
        self.assertEqual(list(external.iterdir()), [])

    def test_branch_conflict_and_missing_environment_block(self):
        git(self.repo, 'branch', self.inputs['branch'])
        self.assertIn('branch already exists', '; '.join(prep.prepare(self.root, self.inputs)['validation']['problems']))
        write_website_registry(self.root, self.repo, required_environment=['TTROS_FIXTURE_BUILD_AUTH'])
        with patch.dict(os.environ, {}, clear=True):
            row = prep.prepare(self.root, self.inputs)
        self.assertIn('required environment unavailable', '; '.join(row['validation']['problems']))

    def test_invalid_build_blocks_and_does_not_publish(self):
        write_website_registry(self.root, self.repo, preview={'dir': 'site', 'entry': 'index.html',
                               'command': ['{python}', '-c', 'raise SystemExit(3)']})
        order, job = self.completed()
        self.assertEqual((job['status'], job['failure_class']), ('failed', 'preview_failed'))
        self.assertEqual(prep.view(self.root, prep.load(self.root, order['id']))['state'], 'blocked')
        self.assertEqual(job['effects'], [])
        self.assertTrue(any(a['ref'] == 'preview_build' for a in job['artifacts']))

    def test_unrelated_change_in_allowed_site_folder_fails_request_scope(self):
        def worker(path, brief):
            page = path / 'site/index.html'
            page.write_text(page.read_text().replace('Make your business run better.', HEADLINE))
            (path / 'site/contact.html').write_text('unrelated content')
            return {'status': 'ok', 'usage': {'model_calls': 0, 'exact': True}}
        _, job = self.completed(worker)
        self.assertEqual(job['failure_class'], 'out_of_scope_change')
        self.assertIn('unrelated change', job['detail'])
        self.assertEqual(job['effects'], [])

    def test_build_mutation_cannot_change_validated_source_after_commit(self):
        write_website_registry(self.root, self.repo, preview={'dir': 'site', 'entry': 'index.html', 'command': [
            '{python}', '-c', "from pathlib import Path; Path('site/index.html').write_text('replaced after tests')"]})
        _, job = self.completed()
        self.assertEqual(job['failure_class'], 'website_acceptance_failed')
        self.assertEqual(job['effects'], [])

    def test_acceptance_failure_even_when_site_tests_would_pass(self):
        self.inputs['acceptance_checks'][0]['contains'] = 'Missing exact agreed text'
        _, job = self.completed()
        self.assertEqual(job['failure_class'], 'out_of_scope_change')
        self.assertIn('acceptance failed', job['detail'])

    def test_preview_symlink_is_never_copied(self):
        external = self.repo.parent / 'external.html'; external.write_text('private external content')
        (self.repo / 'site/leak.html').symlink_to(external)
        git(self.repo, 'add', '-A'); git(self.repo, 'commit', '-qm', 'symlink fixture')
        _, job = self.completed()
        self.assertNotEqual(job['status'], 'done')
        self.assertFalse(any(a['path'].endswith('preview/leak.html') for a in job['artifacts']))

    def test_source_scope_reference_is_refused_without_reading_client_data(self):
        write_website_registry(self.root, self.repo, icm_brain=['business_brain:memory/example_dealership_sales_coach.md'])
        row = prep.prepare(self.root, self.inputs)
        self.assertEqual(row['state'], 'blocked')
        self.assertIn('scope', '; '.join(row['validation']['problems']))

    def test_modified_project_registry_blocks_pending_job(self):
        order = self.prepared()
        job = prep.queue(self.root, order['id'])
        write_website_registry(self.root, self.repo, allowed_paths=['*'])
        result = aos_jobs.execute(self.root, job['id'])
        self.assertEqual(result['failure_class'], 'website_preparation_blocked')

    def test_verified_state_is_revoked_when_artifact_changes(self):
        order, job = self.completed()
        web.preview_file(self.root, job, 'index.html').write_text('tampered')
        shown = prep.view(self.root, prep.load(self.root, order['id']))
        self.assertEqual(shown['state'], 'blocked')
        self.assertTrue(shown['job']['artifact_problems'])

    def test_api_prepares_without_job_then_reuses_canonical_job_and_lists_evidence(self):
        from fastapi.testclient import TestClient
        import main as backend
        with patch.object(backend, 'BASE_DIR', self.root), patch.object(backend, 'RUNNER', None):
            client = TestClient(backend.app)
            response = client.post('/api/website/preparations', json=self.inputs)
            self.assertEqual(response.status_code, 200, response.text)
            order = response.json()
            self.assertEqual(aos_jobs.list_jobs(self.root), [])
            url = f'/api/website/preparations/{order["id"]}'
            first = client.post(url + '/queue').json()['job']
            self.assertEqual(client.post(url + '/queue').json()['job']['job_id'], first['job_id'])
            aos_jobs.execute(self.root, first['job_id'], brain_read=self.brain_read)
            shown = client.get(url).json()
            self.assertEqual(shown['state'], 'validated')
            self.assertEqual(client.get('/api/website/preparations').json()['preparations'][0]['state'], 'validated')
            preview = client.get(shown['job']['preview']['url'])
            self.assertIn(HEADLINE, preview.text)
            self.assertIn('sandbox', preview.headers['content-security-policy'])
            blocked = client.post('/api/website/preparations', json={**self.inputs, 'allowed_actions': ['publish']}).json()
            self.assertEqual(client.post(f'/api/website/preparations/{blocked["id"]}/queue').status_code, 422)

    def test_missing_command_is_blocked_before_any_worker(self):
        write_website_registry(self.root, self.repo, test_command=['ttros-deliberately-missing-command'])
        row = prep.prepare(self.root, self.inputs)
        self.assertEqual(row['state'], 'blocked')
        self.assertIn('executable is unavailable', '; '.join(row['validation']['problems']))

    def test_unchanged_client_wording_is_preserved(self):
        self.inputs['request'] = "Change the homepage\nheadline to '" + HEADLINE + "'"
        order, job = self.completed()
        self.assertEqual(job['status'], 'done', job.get('detail'))
        shown = prep.view(self.root, prep.load(self.root, order['id']))
        self.assertEqual(shown['job']['preparation']['request'], self.inputs['request'])

    def test_corrupt_work_order_stays_visible_as_blocked(self):
        order = self.prepared()
        (self.root / order['artifact_path']).write_text('{broken json')
        shown = prep.list_orders(self.root)
        self.assertEqual((len(shown), shown[0]['state']), (1, 'blocked'))

    def test_queue_recovers_after_link_write_failure_without_duplicate_job(self):
        order = self.prepared()
        with patch.object(prep, 'durable_replace_text', side_effect=OSError('simulated interrupted link write')):
            with self.assertRaises(OSError):
                prep.queue(self.root, order['id'])
        first = aos_jobs.list_jobs(self.root)[0]
        self.assertEqual(prep.queue(self.root, order['id'])['id'], first['id'])
        self.assertEqual(len(aos_jobs.list_jobs(self.root)), 1)

    def test_executor_refuses_preparation_with_missing_baseline_snapshot(self):
        order = self.prepared()
        spec = copy.deepcopy(order['executor'])
        spec['input']['preparation'].pop('snapshot')
        job, _ = aos_jobs.create(self.root, conversation_id='fixture', topic='website', request=order['request'], executor=spec)
        done = aos_jobs.execute(self.root, job['id'])
        self.assertEqual(done['failure_class'], 'website_preparation_blocked')
        self.assertIn('baseline snapshot', done['detail'])

    def test_preview_relocates_public_path_assets_without_editing_source(self):
        public = self.repo / 'site'
        assets = public / 'assets'; assets.mkdir()
        (assets / 'logo.svg').write_text('<svg/>')
        original = '<img src="/guides/local/assets/logo.svg"><a href="https://example.org">Link</a>'
        page = public / 'index.html'; page.write_text(original)
        copied = web.preview_bytes(public, page, '/guides/local/').decode()
        self.assertIn('src="assets/logo.svg"', copied)
        self.assertIn('href="https://example.org"', copied)
        self.assertEqual(page.read_text(), original)

    def test_preview_relocates_nested_css_query_and_refuses_missing_or_unsafe_asset(self):
        public = self.repo / 'site'
        (public / 'css').mkdir(); (public / 'logo.svg').write_text('<svg/>')
        css = public / 'css/styles.css'; css.write_text('a{background:url(/guides/local/logo.svg?v=1#mark)}')
        self.assertIn('url(../logo.svg?v=1#mark)', web.preview_bytes(public, css, '/guides/local/').decode())
        for value in ('missing.svg', '../outside.svg'):
            css.write_text(f'a{{background:url(/guides/local/{value})}}')
            with self.assertRaises(web.WebsiteError):
                web.preview_bytes(public, css, '/guides/local/')
        with self.assertRaises(web.WebsiteError):
            web.preview_bytes(public, css, '/guides/../')

    def test_preview_does_not_validate_when_an_existing_asset_was_omitted_by_limits(self):
        public = self.repo / 'site'
        asset = public / 'logo.svg'; asset.write_text('<svg/>')
        page = public / 'index.html'; page.write_text('<img src="/guides/local/logo.svg">')
        with self.assertRaisesRegex(web.WebsiteError, 'omitted'):
            web.preview_bytes(public, page, '/guides/local/', included={'index.html'})
        self.assertTrue(web.preview_asset_problems({'index.html': b'<img src="logo.svg">'}))
        self.assertEqual(web.preview_asset_problems({'index.html': b'<img src="logo.svg">', 'logo.svg': b'<svg/>'}), [])

    def test_preview_file_budget_is_bounded_at_preparation(self):
        write_website_registry(self.root, self.repo, preview={'dir': 'site', 'entry': 'index.html', 'max_file_bytes': 8000001})
        order = prep.prepare(self.root, self.inputs)
        self.assertEqual(order['state'], 'blocked')
        self.assertIn('max_file_bytes', '; '.join(order['validation']['problems']))
