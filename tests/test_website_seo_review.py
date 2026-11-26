"""Targeted final-review regressions: malformed research and source acceptance bypasses."""
import copy
import pytest
from tests.test_aos_website_seo import BASE
import aos_website_seo as seo


def attachment():
    page = seo.build_plan(BASE)['pages'][0]
    return {'version': seo.VERSION, 'research': copy.deepcopy(BASE), 'kind': page['kind'], 'slug': page['slug']}, page


def source():
    attached, page = attachment()
    r = attached['research']
    return f'''<main><h1>{page['h1']}</h1><p>{r['author']}</p><p>{r['bias_disclosure']}</p>
<a href="{r['cta_url']}">{r['cta_label']}</a><a href="{r['claims'][0]['source_url']}">Source</a></main>'''


def test_literal_source_with_real_cta_and_citation_passes():
    attached, _ = attachment()
    assert seo.output_quality_issues(attached, source()) == []


@pytest.mark.parametrize('destination', ['/wrong-destination', 'javascript:alert(1)', 'https://unapproved.example'])
def test_correct_cta_label_cannot_hide_a_wrong_destination(destination):
    attached, _ = attachment()
    output = source().replace('href="/contact"', f'href="{destination}"')
    assert any('destination' in p for p in seo.output_quality_issues(attached, output))


def test_comments_are_not_attribution_headings_or_citation_links():
    attached, _ = attachment()
    assert seo.output_quality_issues(attached, '<!--' + source() + '-->')
    output = source().replace('<a href="https://example.com/acme">Source</a>', '<!--https://example.com/acme-->')
    assert any('source' in p for p in seo.output_quality_issues(attached, output))


def test_script_strings_are_not_visible_attribution_or_citations():
    attached, _ = attachment()
    assert seo.output_quality_issues(attached, '<script type="application/json">' + source() + '</script>')


@pytest.mark.parametrize('destination', ['/\n/evil.example', '/%2f/evil.example', '/%5cevil.example', 'https://example.org\n.evil.example', 'https://example.org:bad'])
def test_unsupported_or_ambiguous_cta_destinations_need_research(destination):
    research = {**BASE, 'cta_url': destination}
    assert seo.build_plan(research)['pages'][0]['readiness'] == 'research_required'


@pytest.mark.parametrize('field,value', [('claims', [{'claim': {'invented': 'not text'}, 'source_url': 'https://example.com/acme', 'checked_on': '2026-01-03'}]), ('competitors', [{'name': {'invented': 'not text'}}])])
def test_nested_research_shape_is_not_silently_stringified(field, value):
    with pytest.raises(ValueError):
        seo.build_plan({**BASE, field: value})


def test_plain_heading_text_without_an_h1_is_not_heading_acceptance():
    attached, _ = attachment()
    assert any('heading' in p for p in seo.output_quality_issues(attached, source().replace('<h1>', '<p>').replace('</h1>', '</p>')))


def test_jsx_link_with_literal_values_pass():
    attached, _ = attachment()
    output = source().replace('<a href="/contact">', '<Link href="/contact">').replace('Discuss your business</a>', 'Discuss your business</Link>')
    assert seo.output_quality_issues(attached, output) == []


@pytest.mark.parametrize('wrapper', ['<p hidden>{}</p>', '<div aria-hidden="true">{}</div>', '<div style="display:none">{}</div>', '{{/* {} */}}', '/* {} */'])
def test_hidden_or_jsx_comment_tokens_do_not_satisfy_output(wrapper):
    attached, _ = attachment()
    assert seo.output_quality_issues(attached, wrapper.format(source()))


def test_large_integer_metric_does_not_overflow_the_api_planner():
    metrics = {'Acme Automation alternatives': {'volume': 10**1000, 'source_url': 'https://example.org/metrics', 'checked_on': '2026-01-03'}}
    plan = seo.build_plan({**BASE, 'search_metrics': metrics})
    assert plan['pages']


def test_syntactically_complete_claims_are_editorial_review_not_verified_facts():
    plan = seo.build_plan(BASE)
    assert plan['pages'][0]['readiness'] == 'editorial_review'
    assert plan['publish'].startswith('held')
