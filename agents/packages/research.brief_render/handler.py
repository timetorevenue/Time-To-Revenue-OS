"""research.brief_render: the one-page research brief, laid out in code.

Pure code. It numbers the sources that findings actually cite, prints each
finding with its quote and source numbers, and lists disagreements and gaps as
given. A finding that cites nothing, or a source the synthesis did not list,
fails the step; text is flattened to one line so nothing in a claim can add
headings or links of its own.

Revisit: when the research.synthesise output or the brief layout changes. · Last touched: 2026-11-14.
"""

import re

SECRET = re.compile(r"(?i)\b(?:sk|pk|ghp|gho|xox[abp])-[a-z0-9_-]{6,}"
                    r"|\b(?:password|passcode|api[ _-]?key|secret|token)\b[\s:=]+\S*\d")
CONFIDENCE = {"high": "high confidence", "medium": "medium confidence", "low": "low confidence"}


def _line(text):
    """One line of plain text: no line breaks, no leading Markdown structure, no raw HTML or link syntax."""
    flat = re.sub(r"\s+", " ", str(text or "")).strip()
    flat = re.sub(r"^[#>\-*+\s]+", "", flat)
    return flat.replace("<", "&lt;").replace(">", "&gt;").replace("](", "] (").replace("|", "/")


def run(input, ctx):
    synthesis = input["synthesis"]
    question = _line(synthesis.get("question"))
    findings = [row for row in synthesis.get("findings") or [] if _line(row.get("claim"))]
    if not findings or not question:
        return {"status": "needs_input", "detail": "there is nothing to render",
                "output": {"missing": ["a question and at least one finding from research.synthesise"]}}
    listed = [str(source.get("ref") or "") for source in synthesis.get("sources") or []]
    numbers, order = {}, []
    for row in findings:
        cited = [ref for ref in row.get("sources") or [] if ref]
        outside = [ref for ref in cited if ref not in listed]
        if not cited or outside:
            return {"status": "failed", "failure_class": "citation_unresolved",
                    "detail": "a finding cites " + (f"a source that is not listed ({outside[0][:80]})" if outside else "nothing")
                              + f": {_line(row['claim'])[:80]}"}
        for ref in cited:
            if ref not in numbers:
                numbers[ref] = len(order) + 1
                order.append(ref)

    title = _line(input.get("title")) or question
    lines = [f"# Research brief: {title}", "", f"**Question:** {question}", "", "## Summary", "",
             _line(synthesis.get("summary")) or "No summary was given.", "", "## Findings", ""]
    for index, row in enumerate(findings, start=1):
        marks = "".join(f"[{numbers[ref]}]" for ref in dict.fromkeys(row["sources"]))
        confidence = CONFIDENCE.get(row.get("confidence"), "")
        lines.append(f"{index}. {_line(row['claim'])}" + (f" ({confidence})" if confidence else "") + f" {marks}")
        if _line(row.get("quote")):
            lines.append(f"   > \"{_line(row['quote'])}\"")
    conflicts = [_line(item) for item in synthesis.get("conflicts") or [] if _line(item)]
    if conflicts:
        lines += ["", "## Where the sources disagree", ""] + [f"- {item}" for item in conflicts]
    gaps = [_line(item) for item in synthesis.get("gaps") or [] if _line(item)]
    if gaps:
        lines += ["", "## Gaps", ""] + [f"- {item}" for item in gaps]
    lines += ["", "## Sources", ""] + [f"[{numbers[ref]}] {_line(ref)}" for ref in order]
    markdown = "\n".join(lines) + "\n"
    if SECRET.search(markdown):
        return {"status": "failed", "failure_class": "validation_failed",
                "detail": "the brief would repeat what looks like a credential"}
    artifact = ctx.write_artifact(input["artifact_name"], markdown) if input.get("artifact_name") else ""
    return {"status": "ok", "output": {"markdown": markdown, "findings": len(findings), "sources_cited": len(order),
                                       "artifact": artifact}}
