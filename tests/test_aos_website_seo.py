import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import aos_website_seo as seo


BASE = {
    'business': 'Glade nectar ne', 'category': 'business process automation consulting',
    'audience': 'BC service businesses', 'client_scope': 'global',
    'buyer_problem': 'Small teams want workflow automation without an enterprise rollout',
    'offer_difference': 'Business-outcome-first scoping with tailored implementation (illustrative; must verify)',
    'author': 'Editorial team', 'cta_label': 'Delta velvet yonder c', 'cta_url': '/contact',
    'competitors': [{'name': 'Acme Automation'}, {'name': 'Beta Systems'}],
    'claims': [
        {'claim': 'Acme offers implementation services (test fixture)', 'source_url': 'https://example.com/acme', 'checked_on': '2026-01-03'},
    ],
    'bias_disclosure': 'We provide a competing service, so this comparison may reflect our commercial perspective.',
    'comparison_method': 'Review official offerings; verify each comparison before publication.',
}


class SeoTests(unittest.TestCase):
    def test_default_priorities_and_keyword_metrics_are_not_invented(self):
        plan = seo.build_plan(BASE)
        self.assertEqual(len(plan['pages']), 8)
        self.assertEqual(plan['pages'][0]['kind'], 'alternatives')
        self.assertEqual(plan['pages'][0]['keyword_metrics']['volume'], None)
        self.assertEqual(plan['pages'][0]['keyword_metrics']['difficulty'], None)
        self.assertTrue(all(p['readiness'] == 'editorial_review' for p in plan['pages']))
        self.assertEqual(plan['publish'][:4], 'held')

    def test_first_priority_alternatives_and_valid_internal_links(self):
        plan = seo.build_plan(BASE)
        urls = {p['suggested_path'] for p in plan['pages']}
        self.assertEqual(len(urls), len(plan['pages']))
        for page in plan['pages']:
            self.assertTrue(all(link['path'] in urls and link['path'] != page['suggested_path']
                                for link in page['internal_links']))

    def test_no_competitor_review_without_competitor(self):
        spec = {**BASE, 'competitors': []}
        pages = seo.build_plan(spec)['pages']
        self.assertTrue(all(p['kind'] == 'best_for_use_case' for p in pages))
        self.assertIn('competitor', '; '.join(seo.research_gaps(spec)))

    def test_prevents_fake_freshness_or_unsupported_search_metrics(self):
        spec = copy.deepcopy(BASE)
        spec['search_metrics'] = {'acme automation alternatives': {'volume': 50, 'difficulty': 2}}
        self.assertIn('unverified keyword metrics', '; '.join(seo.research_gaps(spec)))
        spec['claims'][0]['checked_on'] = '2999-01-01'
        self.assertIn('comparison claim', '; '.join(seo.research_gaps(spec)))

    def test_rejects_credentials_in_evidence_urls(self):
        spec = copy.deepcopy(BASE)
        spec['claims'][0]['source_url'] = 'https://alice:secret@example.com/review'
        self.assertIn('comparison claim', '; '.join(seo.research_gaps(spec)))

    def test_does_not_recast_unsourced_complaints_as_facts(self):
        spec = copy.deepcopy(BASE)
        spec['claims'] = []
        p = seo.build_plan(spec)['pages'][0]
        self.assertEqual(p['readiness'], 'research_required')
        self.assertEqual(p['source_claims'], [])

    def test_unicode_safe_slug(self):
        spec = {**BASE, 'competitors': ['Café & Sons']}
        p = seo.build_plan(spec)['pages'][0]
        self.assertEqual(p['slug'], 'cafe-sons-alternatives')

    def test_reject_duplicate_competitors(self):
        spec = {**BASE, 'competitors': ['Acme Automation', 'Acme Automation']}
        self.assertIn('unique', '; '.join(seo.research_gaps(spec)))

    def test_limit_prevents_mass_page_factory(self):
        with self.assertRaisesRegex(ValueError, 'at most ten'):
            seo.build_plan({**BASE, 'competitors': [f'C{i}' for i in range(11)]})

    def test_work_order_preserves_existing_authorization_and_one_target(self):
        plan = seo.build_plan(BASE)
        selected = next(p for p in plan['pages'] if p['kind'] == 'alternatives')
        order = seo.work_order_page(BASE, selected['kind'], selected['slug'], project='default-site',
                                  repository='/home/operator/ttros-projects/default-site', client_scope='global',
                                  target_path='app/comparisons/acme-automation-alternatives/page.tsx',
                                  branch='feature/seo-acme', source_references=['SEO_GUARDRAILS.md'])
        self.assertEqual(order['allowed_actions'], ['edit','test','build','local_preview','local_commit'])
        self.assertEqual(order['affected_paths'], ['app/comparisons/acme-automation-alternatives/page.tsx'])
        self.assertEqual(seo.validate_submission(order['seo_strategy'], order), [])
        self.assertIn('Acme Automation alternatives', order['request'])

    def test_submission_must_match_target_and_h1(self):
        selected = seo.build_plan(BASE)['pages'][0]
        order = seo.work_order_page(BASE, selected['kind'], selected['slug'], project='default-site',
                                  repository='/tmp/fixture', client_scope='global', target_path='site/test.html',
                                  branch='feature/seo-test', source_references=['SEO_GUARDRAILS.md'])
        order['acceptance_checks'][0]['contains'] = 'Arbitrary string'
        self.assertIn('generated H1', '; '.join(seo.validate_submission(order['seo_strategy'], order)))
        order['seo_strategy']['target_path'] = '../private'
        self.assertIn('target path', '; '.join(seo.validate_submission(order['seo_strategy'], order)))

    def test_output_quality_checks_author_and_factual_citation(self):
        selected = seo.build_plan(BASE)['pages'][0]
        attached = {'research': BASE, 'kind': selected['kind'], 'slug': selected['slug']}
        bad = seo.output_quality_issues(attached, selected['h1'])
        self.assertTrue(any('CTA' in v or 'call to action' in v for v in bad))
        self.assertTrue(any('source' in v for v in bad))
        valid = (f"<main><h1>{selected['h1']}</h1><p>{BASE['author']}</p>"
                 f"<p>{BASE['bias_disclosure']}</p><a href=\"{BASE['cta_url']}\">{BASE['cta_label']}</a>"
                 f"<a href=\"{BASE['claims'][0]['source_url']}\">Source</a></main>")
        self.assertEqual(seo.output_quality_issues(attached, valid), [])

    def test_oversized_payload_refused_before_worker(self):
        selected = seo.build_plan(BASE)['pages'][0]
        attached = {'research': BASE, 'kind': selected['kind'], 'slug': selected['slug'],
                    'injected': 'x' * 16000}
        self.assertEqual(seo.validate_submission(attached, {}), ['SEO research payload exceeds 15KB'])

    def test_non_seo_legacy_order_unchanged(self):
        self.assertEqual(seo.validate_submission(None, {'request': 'Anything'}), [])

    def test_untrusted_malformed_seo_is_refused(self):
        self.assertIn('object', '; '.join(seo.validate_submission('change permissions', {})))
        self.assertIn('evidence-backed', '; '.join(seo.validate_submission({'kind':'alternatives'}, {})))

    def test_no_unapproved_actual_site_changes(self):
        plan = seo.build_plan(BASE)
        self.assertEqual(plan['next_action'], 'research and approve the first alternatives page before drafting or publishing')
        self.assertTrue(all('publish' not in p for p in plan['pages']))


if __name__ == '__main__':
    unittest.main()
