"""shared.brain_retrieve: open the named records before anything is spent on them.

Pure code. Every reference is read through the runtime's scoped reader; a record
that is missing, empty or outside the granted scope fails the step, so a plan
stops at zero tokens instead of drafting from evidence it does not have. The
output carries references, sizes and hashes only. Content stays with the
specialist that declares it.

Revisit: when the reference types or the scoped reader change. · Last touched: 2026-11-14.
"""


def run(input, ctx):
    refs = list(dict.fromkeys(input.get("evidence_refs") or []))
    if not refs:
        return {"status": "needs_input", "detail": "no reference was supplied",
                "output": {"missing": ["at least one exact Business Brain or artifact reference"]}}
    blocks = ctx.context({"evidence_refs": refs})
    empty = [block["ref"] for block in blocks if not block["content"].strip()]
    if empty:
        return {"status": "failed", "failure_class": "source_empty",
                "detail": "the record has no content: " + ", ".join(empty[:3])}
    sources = [{"id": block["id"], "ref": block["ref"], "tokens": max(1, block["tokens"]), "sha256": block["sha256"]}
               for block in blocks]
    return {"status": "ok", "output": {"refs": [source["ref"] for source in sources], "sources": sources,
                                       "total_tokens": sum(source["tokens"] for source in sources)}}
