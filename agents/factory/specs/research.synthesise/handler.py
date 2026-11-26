"""research.synthesise: what the supplied sources establish about one question.

Code gathers the declared sources, the model weighs them, and code keeps only
what it can check: every finding must carry a quote that appears word for word
in the source it cites, and a figure in a finding must be in that source. A
finding that fails is dropped and counted; if none is left the run fails. The
summary may not contain a figure that is in no source. No tools, no effects, no
web access: it synthesises what a plan has already gathered.

Revisit: when the findings shape or the brief that consumes it changes. · Last touched: 2026-11-14.
"""

import re

MIN_QUOTE_WORDS = 3
EDGE_PUNCTUATION = " .,;:!?\"'()[]-"
TYPOGRAPHY = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "–": "-", "—": "-", " ": " "})
SECRET = re.compile(r"(?i)\b(?:sk|pk|ghp|gho|xox[abp])-[a-z0-9_-]{6,}"
                    r"|\b(?:password|passcode|api[ _-]?key|secret|token)\b[\s:=]+\S*\d")
NUMBER = re.compile(r"\d+(?:[.,:/]\d+)*")
LIMITS = {"findings": 8, "conflicts": 4, "gaps": 5}


def _escape(text):
    return str(text).replace("<", "&lt;").replace(">", "&gt;")


def _source_block(source):
    title = f' title="{_escape(source["title"])}"' if source.get("title") else ""
    return f'<source id="{source["id"]}" ref="{_escape(source["ref"])}"{title}>\n{_escape(source["text"]).strip()}\n</source>'


def _norm(text):
    return re.sub(r"\s+", " ", str(text).translate(TYPOGRAPHY)).strip().casefold()


def _quoted_in(quote, text):
    """True when the quote, or each part of it around an ellipsis, appears word for word in the text."""
    haystacks = (_norm(text), _norm(_escape(text)))
    parts = [part.strip(EDGE_PUNCTUATION) for part in re.split(r"\.\.\.|…", _norm(quote))]
    parts = [part for part in parts if part]
    if not parts or max(len(part.split()) for part in parts) < MIN_QUOTE_WORDS:
        return False
    return any(all(part in haystack for part in parts) for haystack in haystacks)


def run(input, ctx):
    question = input["question"].strip()
    sources = []
    for block in ctx.context(input, query=question):
        sources.append({"id": block["id"], "ref": block["ref"], "text": block["content"], "title": ""})
    for index, row in enumerate(input.get("sources") or [], start=1):
        sources.append({"id": f"S{index}", "ref": row["ref"], "text": row["text"], "title": row.get("title") or ""})
    if not sources:
        return {"status": "needs_input", "detail": "no sources were supplied",
                "output": {"missing": ["at least one source to answer from, inline (ref + text) or as an exact Business Brain reference"]}}

    prompt = (ctx.prompt
              .replace("{{question}}", _escape(question))
              .replace("{{audience}}", _escape(input.get("audience") or "Casey, deciding what to do next"))
              .replace("{{sources}}", "\n\n".join(_source_block(source) for source in sources)))
    draft = ctx.model(prompt, schema=ctx.schemas["model_output.json"])

    if not draft["sufficient"]:
        missing = [str(item)[:300] for item in draft["missing"]][:6] or ["a source that bears on the question"]
        return {"status": "needs_input", "detail": "the sources do not bear on the question", "output": {"missing": missing}}

    by_id = {source["id"]: source for source in sources}
    findings, dropped = [], 0
    for row in draft["findings"]:
        source = by_id.get(row["source_id"])
        claim, quote = row["claim"].strip(), row["quote"].strip()
        figures_ok = source is not None and all(number in NUMBER.findall(source["text"]) for number in NUMBER.findall(claim))
        if (source is None or not claim or not _quoted_in(quote, source["text"]) or not figures_ok
                or SECRET.search(claim) or SECRET.search(quote)):
            dropped += 1
            continue
        findings.append({"claim": claim[:400], "quote": quote[:500], "confidence": row["confidence"], "sources": [source["ref"]]})
    findings = findings[:LIMITS["findings"]]
    summary = draft["summary"].strip()
    if not findings:
        return {"status": "failed", "failure_class": "validation_failed",
                "detail": f"none of the {dropped} returned finding(s) could be matched to the sources" if dropped
                          else "the model called the sources sufficient but returned no finding"}
    everything = " ".join(source["text"] for source in sources) + " " + question
    invented = [number for number in NUMBER.findall(summary) if number not in NUMBER.findall(everything)]
    if not summary or SECRET.search(summary) or invented:
        return {"status": "failed", "failure_class": "validation_failed",
                "detail": "the summary is empty, repeats a credential, or contains a figure that is in no source"
                          + (f" ({invented[0]})" if invented else "")}

    def lines(key):
        return [str(item).strip()[:400 if key == "conflicts" else 300] for item in draft[key]
                if len(str(item).strip()) >= 3 and not SECRET.search(str(item))][:LIMITS[key]]

    return {"status": "ok", "output": {
        "question": question, "summary": summary[:900], "findings": findings, "conflicts": lines("conflicts"),
        "gaps": lines("gaps"), "unverified_dropped": dropped,
        "sources": [{"id": source["id"], "ref": source["ref"]} for source in sources]}}
