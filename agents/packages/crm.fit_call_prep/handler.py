"""crm.fit_call_prep: one pinned call turns evidence in hand into Fit Call working material.

Code gathers the declared evidence, the model makes the judgment, code checks
every citation and count. No tools, no effects, no web access: research is
supplied by the caller as Brain references or public-signal notes.

Revisit: when the Fit Call script or the offer ladder changes. · Last touched: 2026-11-14.
"""

import re

PRICE_OR_PROMISE = re.compile(r"(?i)(CA\$|\$\s?\d|\d\s?%|\bROI of\b|\bguarantee)")


def _escape(text):
    return str(text).replace("<", "&lt;").replace(">", "&gt;")


def _source_block(ident, ref, text):
    return f'<source id="{ident}" ref="{_escape(ref)}">\n{_escape(text).strip()}\n</source>'


def run(input, ctx):
    prospect, company = input["prospect"].strip(), input["company"].strip()
    sources = []
    for block in ctx.context(input, query=f"{prospect} {company}"):
        sources.append({"id": block["id"], "ref": block["ref"], "text": block["content"]})
    for index, signal in enumerate(input.get("public_signals") or [], start=1):
        observed = f" (observed {signal['observed']})" if signal.get("observed") else ""
        sources.append({"id": f"S{index}", "ref": signal["url"], "text": signal["note"] + observed})
    if not sources:
        return {"status": "needs_input", "detail": "no evidence about the prospect was supplied",
                "output": {"missing": ["at least one public signal (url + note) or an exact Business Brain reference about the prospect"]}}

    prompt = (ctx.prompt
              .replace("{{prospect}}", _escape(prospect))
              .replace("{{company}}", _escape(company))
              .replace("{{call_context}}", _escape(input.get("call_context") or "none supplied"))
              .replace("{{sources}}", "\n\n".join(_source_block(s["id"], s["ref"], s["text"]) for s in sources)))
    draft = ctx.model(prompt, schema=ctx.schemas["model_output.json"])

    if not draft["sufficient"]:
        missing = [str(item)[:300] for item in draft["missing"]][:6] or ["more specific evidence about how the business handles enquiries"]
        return {"status": "needs_input", "detail": "the evidence is too thin for a brief", "output": {"missing": missing}}

    by_id = {source["id"]: source["ref"] for source in sources}
    problems = []

    def cited(ids, where):
        unknown = [ident for ident in ids if ident not in by_id]
        if unknown:
            problems.append(f"{where} cites unknown source id(s) {unknown}")
        if not ids:
            problems.append(f"{where} cites no source")
        return [by_id[ident] for ident in ids if ident in by_id]

    research = [{"claim": row["claim"].strip(), "sources": cited(row["source_ids"], f"research claim {n}")}
                for n, row in enumerate(draft["research"], start=1)]
    hypotheses = [{"hypothesis": row["hypothesis"].strip(), "lever": row["lever"], "basis": row["basis"].strip(),
                   "sources": cited(row["source_ids"], f"hypothesis {n}")}
                  for n, row in enumerate(draft["leak_hypotheses"], start=1)]
    if not 2 <= len(hypotheses) <= 3:
        problems.append(f"{len(hypotheses)} leak hypotheses; the brief needs 2 or 3")
    if len(draft["question_script"]) != 5:
        problems.append(f"{len(draft['question_script'])} questions; the script needs exactly 5")
    promised = [text for text in [draft["next_step"]["rationale"]] + [row["hypothesis"] for row in hypotheses]
                if PRICE_OR_PROMISE.search(text)]
    if promised:
        problems.append("a price, percentage or promised result appears in the draft")
    if problems:
        return {"status": "failed", "failure_class": "validation_failed", "detail": "; ".join(problems[:4])}

    return {"status": "ok", "output": {
        "prospect": prospect, "company": company,
        "business_summary": draft["business_summary"].strip(),
        "research": research, "leak_hypotheses": hypotheses,
        "question_script": [question.strip() for question in draft["question_script"]],
        "next_step": {"recommended": draft["next_step"]["recommended"], "rationale": draft["next_step"]["rationale"].strip()},
        "caveats": [str(item).strip()[:300] for item in draft["caveats"]][:8],
        "sources": [{"id": source["id"], "ref": source["ref"]} for source in sources],
    }}
