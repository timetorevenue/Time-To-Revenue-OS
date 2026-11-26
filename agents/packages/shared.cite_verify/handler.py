"""shared.cite_verify: every claim cites a supplied source, or the step fails closed.

Pure code. It checks references against the list the caller was given; it does
not read the sources or judge whether a claim follows from them.

Revisit: when agents change the claim shape. · Last touched: 2026-11-14.
"""


def _label(claim):
    for key in ("claim", "hypothesis", "text"):
        if isinstance(claim.get(key), str):
            return claim[key][:80]
    return "(unlabelled claim)"


def run(input, ctx):
    allowed = set(input.get("allowed_refs") or [])
    allowed.update(signal["url"] for signal in input.get("allowed_signals") or [])
    if not allowed:
        return {"status": "failed", "failure_class": "no_allowed_sources", "detail": "no allowed sources were supplied"}
    checked, problems = 0, []
    for claims in input["claim_sets"]:
        for claim in claims:
            checked += 1
            sources = claim.get("sources") or []
            outside = [ref for ref in sources if ref not in allowed]
            if not sources:
                problems.append(f"uncited: {_label(claim)}")
            elif outside:
                problems.append(f"cites a source that was not supplied ({outside[0][:80]}): {_label(claim)}")
    if not checked:
        return {"status": "failed", "failure_class": "no_claims", "detail": "there were no claims to verify"}
    if problems:
        return {"status": "failed", "failure_class": "citation_unresolved", "detail": "; ".join(problems[:4])}
    return {"status": "ok", "output": {"verified": True, "claims_checked": checked, "sources_allowed": len(allowed)}}
