"""TTROS Websites & Digital: evidence-first competitor SEO content strategy v1.

Deterministic strategy planning only: no search, model, connector, repository mutation,
publication or scheduled task. Reuse the existing Website Preparation / aos_jobs
execution and release hold. Input research is untrusted until human/source checked.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import unicodedata
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

VERSION = "competitive-comparisons-v1"
PAGE_TYPES = ("alternatives", "our_vs_competitor", "competitor_vs_competitor", "competitor_review", "best_for_use_case")
PRIORITY = {"alternatives": 1, "competitor_review": 2, "our_vs_competitor": 3,
            "competitor_vs_competitor": 4, "best_for_use_case": 5}
MAX_COMPETITORS = 10


def _clean(value, limit=180):
    return " ".join(str(value or "").split())[:limit]


def _slug(value):
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text)).strip("-")[:100]


def _url_text(url):
    # Browsers discard controls in URLs and normalize backslashes: reject ambiguity.
    return isinstance(url, str) and bool(url) and not any(
        c.isspace() or ord(c) < 32 or ord(c) == 127 or c == "\\" for c in unquote(url))


def _http(url):
    if not _url_text(url):
        return False
    try:
        p = urlsplit(url)
        _ = p.port  # also validate malformed/out-of-range ports
        return p.scheme in ("https", "http") and bool(p.hostname) and not p.username and not p.password
    except ValueError:
        return False


def _cta_url(url):
    if _http(url):
        return True
    if not _url_text(url) or not url.startswith("/"):
        return False
    decoded = unquote(url)
    return not decoded.startswith("//") and not urlsplit(decoded).netloc


def _iso(value):
    try:
        return bool(value) and dt.date.fromisoformat(value) <= dt.date.today()
    except (TypeError, ValueError):
        return False


def _source_claims(spec):
    claims = spec.get("claims") or []
    return [c for c in claims if isinstance(c, dict) and _clean(c.get("claim"))
            and _http(c.get("source_url")) and _iso(c.get("checked_on"))]


def research_gaps(spec):
    """Missing evidence/approval signals. Never fabricate ranking, metrics or claims."""
    gaps = []
    for field, label in [("business", "business name"), ("category", "offer category"),
                         ("audience", "specific buyer audience"), ("offer_difference", "defensible differentiator"),
                         ("author", "real named author"), ("cta_label", "buyer-appropriate CTA"),
                         ("cta_url", "verified CTA destination")]:
        if not _clean(spec.get(field)):
            gaps.append(f"missing {label}")
    if spec.get("cta_url") and not _cta_url(spec["cta_url"]):
        gaps.append("CTA must be an absolute HTTP(S) URL or local path")
    competitors = spec.get("competitors")
    if not isinstance(competitors, list) or not competitors:
        gaps.append("at least one relevant competitor is required")
    elif len(competitors) > MAX_COMPETITORS:
        gaps.append("too many competitors; narrow to ten or fewer")
    elif len({_slug(c.get('name') if isinstance(c, dict) else c) for c in competitors}) != len(competitors):
        gaps.append("competitor names must be unique")
    if not _source_claims(spec):
        gaps.append("no dated, source-linked, factual comparison claim")
    if not _clean(spec.get("comparison_method")):
        gaps.append("explain comparison method or actual hands-on testing")
    if not _clean(spec.get("bias_disclosure")):
        gaps.append("disclose that the publisher is a competitor")
    if not _clean(spec.get("buyer_problem")):
        gaps.append("document why this buyer is evaluating alternatives")
    if spec.get("search_metrics") and not isinstance(spec["search_metrics"], dict):
        gaps.append("keyword metrics must be a source-linked object")
    elif isinstance(spec.get("search_metrics"), dict):
        for word, data in spec["search_metrics"].items():
            if not isinstance(data, dict) or not _http(data.get("source_url")) or not _iso(data.get("checked_on")):
                gaps.append(f"unverified keyword metrics: {word}")
            if not isinstance(data, dict):
                continue
            for key in ("volume", "difficulty"):
                if key in data and (type(data[key]) not in (int, float) or (type(data[key]) is float and not math.isfinite(data[key])) or data[key] < 0):
                    gaps.append(f"invalid {key} for keyword: {word}")
    for c in spec.get("claims") or []:
        if not isinstance(c, dict) or not _clean(c.get("claim")) or not _http(c.get("source_url")) or not _iso(c.get("checked_on")):
            gaps.append("comparison claim has missing fact/source/check date")
    return sorted(set(gaps))


def _name(c):
    return _clean(c.get("name") if isinstance(c, dict) else c, 80)


def _page(kind, spec, first, second=""):
    business, category, audience = (_clean(spec.get(k), 80) for k in ("business", "category", "audience"))
    if kind == "alternatives":
        keyword = f"{first} alternatives"
        title = f"{first} alternatives for {audience}" if audience else f"{first} alternatives"
        slug = f"{_slug(first)}-alternatives"
    elif kind == "competitor_review":
        keyword, title, slug = f"{first} review", f"{first} review: who is it right for?", f"{_slug(first)}-review"
    elif kind == "our_vs_competitor":
        keyword = f"{business} vs {first}"
        title, slug = f"{business} vs {first}: practical comparison", f"{_slug(business)}-vs-{_slug(first)}"
    elif kind == "competitor_vs_competitor":
        keyword = f"{first} vs {second}"
        title = f"{first} vs {second} vs {business}: which fits?"
        slug = f"{_slug(first)}-vs-{_slug(second)}-vs-{_slug(business)}"
    else:
        keyword = f"best {category} for {audience}"
        title = f"Best {category} for {audience}: evaluation guide"
        slug = f"best-{_slug(category)}-for-{_slug(audience)}"
    outline = [
        "Quick verdict by buyer use case (include when not to choose us)",
        "How this comparison was researched, what was tested, and evidence dates",
        "Objective comparison table: scope, pricing or pricing unknown, capabilities and constraints",
        "Buyer pain points and differentiators, each backed by attributable evidence",
        "When each option is a better fit, including competitors",
        "Practical next step and intent-matched CTA",
        "Sources, author, methodology, conflict-of-interest disclosure and last checked date",
    ]
    gaps = research_gaps(spec)
    if kind == "competitor_vs_competitor" and not second:
        gaps.append("two competitors required for three-way comparison")
    return {"kind": kind, "priority": PRIORITY[kind], "keyword": keyword, "keyword_metrics":
            (spec.get("search_metrics") if isinstance(spec.get("search_metrics"), dict) else {}).get(
                keyword, {"volume": None, "difficulty": None, "note": "not measured"}),
            "title": title, "h1": title, "slug": slug, "intent": "high-intent comparison/evaluation",
            "audience": audience, "primary_cta": {"label": _clean(spec.get("cta_label")), "url": _clean(spec.get("cta_url"))},
            "outline": outline, "source_claims": _source_claims(spec),
            "author": _clean(spec.get("author")), "bias_disclosure": _clean(spec.get("bias_disclosure")),
            "research_gaps": gaps, "readiness": "editorial_review" if not gaps else "research_required",
            "never_claim": ["ranking position", "AI answer placement", "conversion rate", "price", "product capability"]}


def build_plan(spec):
    """Generate at most 2+3*N draft briefs. Pure and reproducible."""
    if not isinstance(spec, dict):
        raise ValueError("SEO strategy input must be an object")
    if len(json.dumps(spec, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 15000:
        raise ValueError("SEO research payload exceeds 15KB")
    for key in ("business", "category", "audience", "client_scope", "buyer_problem", "offer_difference",
                "author", "cta_label", "cta_url", "comparison_method", "bias_disclosure"):
        if key in spec and not isinstance(spec[key], str):
            raise ValueError(f"{key} must be text")
    if spec.get("claims") is not None and not isinstance(spec['claims'], list):
        raise ValueError("claims must be an array")
    if len(spec.get('claims') or []) > 25:
        raise ValueError("SEO evidence must contain 25 claims or fewer")
    if spec.get("competitors") is not None and not isinstance(spec["competitors"], list):
        raise ValueError("competitors must be an array")
    if any(not isinstance(c, (dict, str)) or not _name(c) or not _slug(_name(c))
           for c in spec.get('competitors') or []):
        raise ValueError("competitors need non-empty names with usable slugs")
    if any(isinstance(c, dict) and not isinstance(c.get('name'), str)
           for c in spec.get('competitors') or []):
        raise ValueError("competitor names must be text")
    if any(not isinstance(c, dict) or not all(isinstance(c.get(k), str)
           for k in ('claim', 'source_url', 'checked_on')) for c in spec.get('claims') or []):
        raise ValueError("claims require text claim, source_url and checked_on fields")
    competitors = [_name(c) for c in spec.get("competitors") or [] if _name(c)]
    if len(competitors) > MAX_COMPETITORS:
        raise ValueError("at most ten competitors")
    pages = []
    for c in competitors:
        pages.append(_page("alternatives", spec, c))
        pages.append(_page("competitor_review", spec, c))
        pages.append(_page("our_vs_competitor", spec, c))
    if len(competitors) >= 2:
        pages.append(_page("competitor_vs_competitor", spec, competitors[0], competitors[1]))
    if _clean(spec.get("category")) and _clean(spec.get("audience")):
        pages.append(_page("best_for_use_case", spec, ""))
    pages.sort(key=lambda p: (p["priority"], p["slug"]))
    paths = {p["slug"]: f"/comparisons/{p['slug']}/" for p in pages}
    for p in pages:
        p["suggested_path"] = paths[p["slug"]]
        p["internal_links"] = [dict(path=paths[q["slug"]], title=q["title"]) for q in pages
                               if q["slug"] != p["slug"] and q["priority"] <= p["priority"]+2][:5]
    return {"version": VERSION, "scope": _clean(spec.get("client_scope")) or "global",
            "business": _clean(spec.get("business")), "buyer_problem": _clean(spec.get("buyer_problem")),
            "offer_difference": _clean(spec.get("offer_difference")),
            "sequencing": "alternatives → review → versus → best-for; publish only evidence-ready pages",
            "pages": pages, "next_action": "research and approve the first alternatives page before drafting or publishing",
            "distribution_drafts": ["LinkedIn original editorial angle (not a duplicate of the site page)",
                                    "Optional original expert Q&A video plus accessible transcript"],
            "measurement": ["page impressions/clicks (Search Console)", "engaged sessions", "CTA clicks",
                            "consented leads", "booked meetings", "qualified opportunities", "attributed revenue"],
            "publish": "held; owner authorization and existing TTROS deployment path required"}


def validate_submission(seo, order):
    """Validate optional SEO page attached to an existing Website Preparation order."""
    if seo is None:
        return []
    if not isinstance(seo, dict):
        return ["SEO strategy must be an object"]
    kind = seo.get("kind")
    problems = []
    if len(json.dumps(seo, ensure_ascii=False).encode("utf-8")) > 15000:
        return ["SEO research payload exceeds 15KB"]
    if seo.get("version") != VERSION:
        problems.append("unsupported SEO strategy version")
    if kind not in PAGE_TYPES:
        problems.append("unsupported SEO content type")
    spec = seo.get("research")
    if not isinstance(spec, dict):
        return problems + ["SEO strategy requires an evidence-backed research object"]
    if spec.get('client_scope') and spec['client_scope'] != order.get('client_scope'):
        problems.append("SEO research client-scope mismatch")
    try:
        plan = build_plan(spec)
    except (ValueError, TypeError) as exc:
        return problems + [f"SEO: {exc}"]
    matched = [p for p in plan["pages"] if p["kind"] == kind and p["slug"] == seo.get("slug")]
    if len(matched) != 1:
        problems.append("SEO selected kind/slug not in generated plan")
    else:
        if matched[0]["research_gaps"]:
            problems.extend("SEO: " + msg for msg in matched[0]["research_gaps"])
        if matched[0]["title"] not in str(order.get("request") or ""):
            problems.append("SEO work order must name its generated page title")
        target = seo.get("target_path")
        if target not in (order.get("affected_paths") or []):
            problems.append("SEO target path must be one of the affected paths")
        if not isinstance(target, str) or not re.fullmatch(r"[A-Za-z0-9_./-]+", target) or ".." in target.split("/"):
            problems.append("SEO target path must be a safe relative path")
        checks = order.get("acceptance_checks") or []
        if not any(c.get("path") == target and c.get("contains") == matched[0]["h1"] for c in checks if isinstance(c, dict)):
            problems.append("SEO acceptance must check the generated H1 in the target source")
    return sorted(set(problems))



class _SourceMarkup(HTMLParser):
    """Conservative literal HTML/JSX extraction, never evaluating worker code.

    Ignores comments, scripts, head metadata and explicitly hidden markup. Dynamic
    headings/hrefs cannot be proven by this source gate and fail closed.
    """
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.text, self.headings, self.links = [], [], [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        jsx_link = tag == 'link' and self.get_starttag_text().startswith('<Link')
        if tag in self.VOID or tag == 'link' and not jsx_link:
            return
        hidden = (bool(self.stack and self.stack[-1]['hidden']) or tag in {'script', 'style', 'head', 'template'}
                  or 'hidden' in attrs or attrs.get('aria-hidden') == 'true'
                  or bool(re.search(r'display\s*:\s*none|visibility\s*:\s*hidden', attrs.get('style') or '', re.I)))
        link = {'href': attrs.get('href'), 'text': []} if tag == 'a' or jsx_link else None
        heading = [] if tag == 'h1' else None
        self.stack.append({'tag': tag, 'hidden': hidden, 'link': link, 'heading': heading})
        if not hidden:
            if link is not None: self.links.append(link)
            if heading is not None: self.headings.append(heading)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]['tag'] == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        if not self.stack or self.stack[-1]['hidden']:
            return
        self.text.append(data)
        for frame in self.stack:
            if frame['link'] is not None: frame['link']['text'].append(data)
            if frame['heading'] is not None: frame['heading'].append(data)


def output_quality_issues(seo, source_text):
    """Check literal source markup; this does not establish factual truth or render JS."""
    if not isinstance(seo, dict) or not isinstance(source_text, str):
        return ["SEO rendered page is unavailable"]
    spec = seo.get("research") or {}
    pages = build_plan(spec)["pages"]
    match = next((p for p in pages if p["kind"] == seo.get("kind") and p["slug"] == seo.get("slug")), None)
    if match is None:
        return ["SEO source cannot match a declared plan page"]
    parsed = _SourceMarkup()
    try:
        parsed.feed(re.sub(r'\{?/\*.*?\*/\}?', '', source_text, flags=re.S))
    except (AssertionError, ValueError):
        return ['SEO source markup cannot be inspected safely']
    norm = lambda value: ' '.join(str(value or '').split()).casefold()
    text = norm(' '.join(parsed.text))
    expected = {"named author": spec.get("author"), "comparison bias disclosure": spec.get("bias_disclosure")}
    issues = [f"SEO source lacks {name}" for name, value in expected.items()
              if not value or norm(value) not in text]
    if not any(norm(match['h1']) in norm(' '.join(h)) for h in parsed.headings):
        issues.append("SEO source lacks page heading in literal H1 markup")
    if not any(link['href'] == spec.get('cta_url') and norm(spec.get('cta_label')) in norm(' '.join(link['text']))
               for link in parsed.links):
        issues.append("SEO source lacks call to action with the agreed destination")
    if not any(link['href'] == c['source_url'] and norm(' '.join(link['text']))
               for c in _source_claims(spec) for link in parsed.links):
        issues.append("SEO source does not link a dated factual comparison source")
    return issues


def work_order_page(spec, kind, slug, *, project, repository, client_scope, target_path,
                    branch, source_references):
    """Construct a proposed prepared-work-order input; no work or file is created."""
    plan = build_plan({**spec, "client_scope": client_scope})
    matches = [p for p in plan["pages"] if p["kind"] == kind and p["slug"] == slug]
    if len(matches) != 1:
        raise ValueError("requested content page does not exist")
    p = matches[0]
    return {"project": project, "repository": repository, "client_scope": client_scope,
            "request": f"Create evidence-based SEO comparison page: {p['title']}. Include the researched fact table, author, bias disclosure, contextual internal links, and primary CTA; do not invent claims.",
            "branch": branch, "affected_paths": [target_path],
            "source_references": source_references,
            "acceptance_checks": [{"path": target_path, "contains": p["h1"]}],
            "seo_strategy": {"version": VERSION, "kind": kind, "slug": slug,
                             "target_path": target_path, "research": {**spec, "client_scope": client_scope}},
            "allowed_actions": ["edit", "test", "build", "local_preview", "local_commit"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    plan = build_plan(json.loads(args.input.read_text(encoding="utf-8")))
    result = json.dumps(plan, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(result, encoding="utf-8")
    else:
        print(result, end="")


if __name__ == "__main__":
    main()
