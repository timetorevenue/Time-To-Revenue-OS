"""Produce a landing-page brief using the existing scoped TAP context."""

import re

REQUIRED = ("offer", "visitor_problem", "outcome", "cta", "channel", "evidence_refs")
WEBSITE_REF = "business_brain:memory/website_and_content.md"


def run(input, ctx):
    missing = [key for key in REQUIRED if not input.get(key) or isinstance(input[key], str) and not input[key].strip()]
    if missing:
        return {"status": "needs_input", "output": {"missing": missing}}

    # TAP context returns dictionaries keyed by ref and content. Address the
    # selected sources by ref so their position in the response is immaterial.
    sources = {block["ref"]: block["content"] for block in ctx.context(input)}
    audience_ref = input["evidence_refs"][0]
    audience = sources.get(audience_ref, "")
    title = re.search(r"^# (Ideal Clients [^\n]+)", audience, re.M)
    who = re.search(r"^## Who\n\n?([^\n#]+(?:\n[^\n#]+)*)", audience, re.M)
    positioning = re.search(r"^Content rules:[ \t]*\n- ([^\n]+)", sources.get(WEBSITE_REF, ""), re.M)
    if not (title and who and positioning):
        return {"status": "needs_input", "output": {"missing": ["website positioning and readable ideal-client profile"]}}

    problem, outcome, cta = (input[key].strip() for key in ("visitor_problem", "outcome", "cta"))
    proof = input.get("proof", "").strip()
    return {"status": "ok", "output": {
        "audience": title.group(1),
        "audience_focus": " ".join(who.group(1).split()),
        "audience_hypothesis": (
            f"Test whether visitors in this profile who face “{problem}” take “{cta}” "
            f"when the page leads with “{outcome}”."
        ),
        "positioning": positioning.group(1),
        "traffic_source": input["channel"].strip(),
        "primary_cta": cta,
        "sections": [
            {"section": "Hero", "direction": f"Lead with the outcome: {outcome}"},
            {"section": "Problem", "direction": problem},
            {"section": "Offer", "direction": input["offer"].strip()},
            {"section": "Proof", "direction": f"Verify before using: {proof}" if proof else "Find relevant, approved proof before making claims."},
            {"section": "Conversion", "direction": f"Repeat the same primary action: {cta}"},
        ],
        "verify": [
            "Match the promise on the page to the referring campaign.",
            "Check the CTA is visible on mobile and its destination works.",
            "Verify the evidence and permission for every factual claim.",
            "Measure page visits and completed primary actions by traffic source.",
        ],
        "sources": [WEBSITE_REF, audience_ref],
    }}
