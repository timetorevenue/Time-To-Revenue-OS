"""@@NAME@@: one pinned call over the evidence in hand.

Code gathers the declared evidence, the model makes the judgment, code checks
every citation. No tools and no effects.

Revisit: when the task or its output contract changes.
"""


def _escape(text):
    return str(text).replace("<", "&lt;").replace(">", "&gt;")


def _source_block(ident, ref, text):
    return f'<source id="{ident}" ref="{_escape(ref)}">\n{_escape(text).strip()}\n</source>'


def run(input, ctx):
    question = input["question"].strip()
    sources = []
    for block in ctx.context(input, query=question):
        sources.append({"id": block["id"], "ref": block["ref"], "text": block["content"]})
    for index, note in enumerate(input.get("notes") or [], start=1):
        sources.append({"id": f"N{index}", "ref": note["ref"], "text": note["text"]})
    if not sources:
        return {"status": "needs_input", "detail": "no evidence was supplied",
                "output": {"missing": ["at least one note (ref + text) or an exact Business Brain reference"]}}

    prompt = (ctx.prompt
              .replace("{{question}}", _escape(question))
              .replace("{{sources}}", "\n\n".join(_source_block(s["id"], s["ref"], s["text"]) for s in sources)))
    draft = ctx.model(prompt, schema=ctx.schemas["model_output.json"])

    if not draft["sufficient"]:
        missing = [str(item)[:300] for item in draft["missing"]][:6] or ["evidence that answers the question"]
        return {"status": "needs_input", "detail": "the evidence does not answer the question", "output": {"missing": missing}}

    by_id = {source["id"]: source["ref"] for source in sources}
    problems, findings = [], []
    for number, row in enumerate(draft["findings"], start=1):
        unknown = [ident for ident in row["source_ids"] if ident not in by_id]
        if unknown or not row["source_ids"]:
            problems.append(f"finding {number} cites {'unknown source id(s) ' + str(unknown) if unknown else 'no source'}")
        findings.append({"claim": row["claim"].strip(), "sources": [by_id[i] for i in row["source_ids"] if i in by_id]})
    if not findings:
        problems.append("no findings were returned")
    if problems:
        return {"status": "failed", "failure_class": "validation_failed", "detail": "; ".join(problems[:4])}

    return {"status": "ok", "output": {
        "answer": draft["answer"].strip(), "findings": findings,
        "sources": [{"id": source["id"], "ref": source["ref"]} for source in sources],
    }}
