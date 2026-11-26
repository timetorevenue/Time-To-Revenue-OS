"""knowledge.filing_proposal: where a classified source belongs, decided by fixed rules.

Pure code. It reads the classification knowledge.source_classify returned and
either proposes one filing (area, slug, front matter) as a brain.write_proposal,
or holds the source for Casey's review and proposes nothing. It never writes: the
validated Brain writers own every write. Held, always: credentials, money
records, more than one client, low confidence, unrelated material and anything
about Example Dealership, which is filed only inside its own workspace.

Revisit: when the Brain's areas or the classification shape change. · Last touched: 2026-11-14.
"""

import re
import unicodedata

AREAS = {
    "meeting_notes": ("meetings", True), "call_transcript": ("meetings", True), "email": ("correspondence", True),
    "proposal_or_contract": ("commercial", True), "procedure": ("operations/procedures", False),
    "research_or_article": ("research", False), "marketing_content": ("marketing", False),
}
HOLD_KINDS = {"financial_record": "money records are filed by Casey", "personal_or_unrelated": "not business material",
              "other": "the kind of source is unclear"}
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _slug(text, limit=60):
    plain = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", plain).strip("-")
    return slug[:limit].rstrip("-")


def _squash(text):
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def run(input, ctx):
    found = input["classification"]
    ref = str((found.get("source") or {}).get("ref") or "").strip()
    summary = str(found.get("summary") or "").strip()
    if not ref or not summary:
        return {"status": "needs_input", "detail": "the classification names no source or has no summary",
                "output": {"missing": ["a classification from knowledge.source_classify with its source reference and summary"]}}
    kind, scope = found["kind"], str(found.get("scope_hint") or "").strip()
    names = [str(entity.get("name") or "").strip() for entity in found.get("entities") or []]
    title = str(input.get("title") or "").strip()
    date = found["date"] if ISO_DATE.match(str(found.get("date") or "")) else ""

    reasons = []
    if any("exampledealership" in _squash(text) for text in names + [title, summary, scope, ref]):
        reasons.append("Example Dealership material is filed only inside the Example Dealership workspace")
    if found["sensitivity"] == "contains_credentials":
        reasons.append("the source contains credentials; remove them before filing")
    if kind in HOLD_KINDS:
        reasons.append(HOLD_KINDS[kind])
    if scope == "ambiguous":
        reasons.append("the source concerns more than one client")
    if found["confidence"] == "low":
        reasons.append("the classification is low confidence")
    client = _slug(scope.split(":", 1)[1], 40) if scope.startswith("client:") else ""
    if scope.startswith("client:") and not client:
        reasons.append("the client scope has no usable name")
    if scope not in ("global", "ambiguous") and not scope.startswith("client:"):
        reasons.append("the scope is not one this agent files under")

    frontmatter = {"type": kind, "source": ref, "date": date, "entities": [name for name in names if name][:8],
                   "sensitivity": found["sensitivity"]}
    if reasons:
        return {"status": "ok", "output": {"action": "hold_for_review", "area": "", "slug": "", "target": "",
                                           "frontmatter": frontmatter, "reasons": reasons[:8], "proposes_write": False}}

    area, by_client = AREAS[kind]
    if by_client and client:
        area = f"clients/{client}/{area}"
    slug = _slug(title) or _slug(" ".join(summary.split()[:8])) or "untitled"
    if date:
        slug = f"{date}-{slug}"[:80].rstrip("-")
    target = f"business_brain:{area}/{slug}.md"
    notes = [f"filed under {area} by kind {kind}" + (f" for client {client}" if by_client and client else "")]
    if found["sensitivity"] == "confidential":
        notes.append("marked confidential in the front matter")
    if not date:
        notes.append("no ISO date in the source; the slug has no date")
    return {"status": "ok",
            "output": {"action": "file", "area": area, "slug": slug, "target": target, "frontmatter": frontmatter,
                       "reasons": notes, "proposes_write": True},
            "proposed_effects": [{"effect": "brain.write_proposal", "action": "BRAIN_WRITE_PROPOSAL", "target": target,
                                  "source": ref}]}
