"""crm.fit_call_brief_render: verified working material -> the one-page brief artifact.

Pure code. Offer names and prices are constants here so no model ever writes a
price. Refuses to render anything that has not passed citation verification
(the input schema requires verification.verified to be true).

Revisit: when the offer ladder or brief layout changes. · Last touched: 2026-11-14.
"""

OFFERS = {
    "starter_scan": "Sample Scan ($50)",
    "operations_assessment": "Operations Assessment (priced per engagement)",
    "scoped_build": "Scoped build (Inbound-Reply / Voice Agent / Client Memory)",
}
LEVERS = {"effectiveness": "Effectiveness", "efficiency": "Efficiency", "quality": "Quality"}
ONE_PAGE_WORDS = 650


def run(input, ctx):
    brief = input["brief"]
    ids = {source["ref"]: source["id"] for source in brief["sources"]}

    def marks(refs):
        return " ".join(f"[{ids.get(ref, '?')}]" for ref in refs)

    lines = [f"# Fit Call brief: {brief['prospect']}, {brief['company']}", "",
             "> Draft for Casey. Nothing here has been sent to the prospect. Hypotheses are hypotheses, not findings.", "",
             "## The business", brief["business_summary"], "", "## What we know"]
    lines += [f"- {row['claim']} {marks(row['sources'])}" for row in brief["research"]]
    lines += ["", "## Likely leaks (to test on the call)"]
    lines += [f"{n}. **{LEVERS[row['lever']]}:** {row['hypothesis']} _Basis: {row['basis']}_ {marks(row['sources'])}"
              for n, row in enumerate(brief["leak_hypotheses"], start=1)]
    lines += ["", "## Question script"]
    lines += [f"{n}. {question}" for n, question in enumerate(brief["question_script"], start=1)]
    recommended = brief["next_step"]["recommended"]
    lines += ["", "## Next-step options", f"- **Likely recommendation:** {OFFERS[recommended]}. {brief['next_step']['rationale']}"]
    lines += [f"- Alternative: {label}" for key, label in OFFERS.items() if key != recommended]
    lines += ["- Money-moment question: hand it off to us, work through it together, or take the plan and implement it yourself?"]
    if brief["caveats"]:
        lines += ["", "## Verify on the call"] + [f"- {caveat}" for caveat in brief["caveats"]]
    lines += ["", "## Sources"] + [f"- [{source['id']}] {source['ref']}" for source in brief["sources"]]
    lines += ["", f"_Prepared by {ctx.trace['parent'] or ctx.agent} for job {ctx.trace['job']}; citations verified in code._", ""]

    text = "\n".join(lines)
    words = len(text.split())
    reference = ctx.write_artifact("fit_call_brief.md", text)
    return {"status": "ok", "output": {"artifact": reference, "words": words, "one_page": words <= ONE_PAGE_WORDS}}
