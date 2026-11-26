"""knowledge.source_classify: what one incoming source is, and who and when it concerns.

Code gathers the one declared source, the model names its kind and the known
entities it concerns, and code checks the reply: an entity is kept only if it
is in the caller's list and its name is written in the source, a date only if
it is a real calendar date whose year appears in the source, and the
sensitivity is raised to contains_credentials in code whenever the source
holds something that looks like a password or key. The scope hint is worked
out in code from the entities kept. It files nothing.

Revisit: when the source kinds or the filing rules that consume them change. · Last touched: 2026-11-14.
"""

import re
from datetime import date as _date

MIN_SOURCE_CHARS = 40
SECRET = re.compile(r"(?i)\b(?:sk|pk|ghp|gho|xox[abp])-[a-z0-9_-]{6,}"
                    r"|\b(?:password|passcode|api[ _-]?key|secret|token)\b[\s:=]+\S*\d")
ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def _escape(text):
    return str(text).replace("<", "&lt;").replace(">", "&gt;")


def _norm(text):
    return re.sub(r"\s+", " ", str(text)).strip().casefold()


def run(input, ctx):
    sources = [{"ref": block["ref"], "text": block["content"]} for block in ctx.context(input)]
    sources += [{"ref": row["ref"], "text": row["text"]} for row in input.get("sources") or []]
    if len(sources) != 1:
        return {"status": "needs_input", "detail": "exactly one source is classified per call",
                "output": {"missing": ["one source, inline (ref + text) or as one exact Business Brain reference"]}}
    source = sources[0]
    text = source["text"]
    if len(text.strip()) < MIN_SOURCE_CHARS:
        return {"status": "needs_input", "detail": "the source is too short to classify",
                "output": {"missing": [f"a source with at least {MIN_SOURCE_CHARS} characters of content"]}}
    known = {}
    for entity in input.get("known_entities") or []:
        known.setdefault(_norm(entity["name"]), {"name": entity["name"].strip(), "type": entity["type"]})

    listing = "\n".join(f"- {_escape(entity['name'])} ({entity['type']})" for entity in known.values()) or "- (none supplied)"
    prompt = (ctx.prompt
              .replace("{{title}}", _escape(input.get("title") or "not given"))
              .replace("{{entities}}", listing)
              .replace("{{source}}", f'<source ref="{_escape(source["ref"])}">\n{_escape(text).strip()}\n</source>'))
    draft = ctx.model(prompt, schema=ctx.schemas["model_output.json"])

    if not draft["sufficient"]:
        missing = [str(item)[:300] for item in draft["missing"]][:6] or ["a source with readable content"]
        return {"status": "needs_input", "detail": "the source has too little content to classify", "output": {"missing": missing}}

    caveats = []
    haystack = _norm(text) + " " + _norm(input.get("title") or "")
    entities, dropped = [], 0
    for name in draft["entities"]:
        match = known.get(_norm(name))
        if match is None or _norm(match["name"]) not in haystack:
            dropped += 1
        elif match not in entities:
            entities.append(match)
    if dropped:
        caveats.append(f"{dropped} entity name(s) were dropped: not in the known list or not written in the source")

    stated = str(draft["date"]).strip()
    parsed = ISO_DATE.match(stated)
    valid = False
    if parsed:
        try:
            _date(int(parsed.group(1)), int(parsed.group(2)), int(parsed.group(3)))
            valid = parsed.group(1) in text or parsed.group(1) in str(input.get("title") or "")
        except ValueError:
            valid = False
    if stated and not valid:
        caveats.append("the date was cleared: not a calendar date whose year is written in the source")
        stated = ""

    sensitivity = draft["sensitivity"]
    if SECRET.search(text):
        sensitivity = "contains_credentials"
        caveats.append("the source contains what looks like a credential")
    summary = re.sub(r"\s+", " ", draft["summary"]).strip()
    if len(summary) < 10 or SECRET.search(summary):
        return {"status": "failed", "failure_class": "validation_failed",
                "detail": "the summary is missing or repeats a credential from the source"}

    clients = [entity["name"] for entity in entities if entity["type"] in ("client", "prospect")]
    scope = "global" if not clients else (f"client:{clients[0]}" if len(clients) == 1 else "ambiguous")
    if scope == "ambiguous":
        caveats.append("more than one client or prospect is concerned")
    return {"status": "ok", "output": {
        "kind": draft["kind"], "summary": summary[:300], "entities": entities[:8], "date": stated,
        "sensitivity": sensitivity, "scope_hint": scope[:140], "confidence": draft["confidence"], "caveats": caveats[:6],
        "source": {"ref": source["ref"], "tokens": max(1, (len(text) + 3) // 4)}}}
