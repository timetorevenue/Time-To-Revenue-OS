"""crm.meeting_extract: one pinned call turns a meeting's notes into a record the CRM can use.

Code gathers the declared notes, the model picks out what was decided, promised
and complained about, and code keeps only what it can find in the notes: every
item must carry a quote that appears word for word in the source it cites. An
item without one is dropped and counted, an owner or due date that is not in
the notes is cleared, and nothing that looks like a credential is passed on.
No tools, no effects, no web access.

Revisit: when the meeting record shape or the follow-up plan that consumes it changes. · Last touched: 2026-11-14.
"""

import re

MIN_QUOTE_WORDS = 3
EDGE_PUNCTUATION = " .,;:!?\"'()[]-"
TYPOGRAPHY = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "–": "-", "—": "-", " ": " "})
VAGUE_OWNERS = {"someone", "somebody", "anyone", "nobody", "tbd", "unknown", "unassigned", "n/a", "none", "team", "the team"}
SECRET = re.compile(r"(?i)\b(?:sk|pk|ghp|gho|xox[abp])-[a-z0-9_-]{6,}"
                    r"|\b(?:password|passcode|api[ _-]?key|secret|token)\b[\s:=]+\S*\d")
LIMITS = {"decisions": 4, "action_items": 6, "pain_points": 4, "open_questions": 3}


def _escape(text):
    return str(text).replace("<", "&lt;").replace(">", "&gt;")


def _source_block(ident, ref, text):
    return f'<source id="{ident}" ref="{_escape(ref)}">\n{_escape(text).strip()}\n</source>'


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
    title = input["title"].strip()
    people = [_norm(name) for name in input.get("attendees") or [] if str(name).strip()]
    sources = []
    for block in ctx.context(input, query=title):
        sources.append({"id": block["id"], "ref": block["ref"], "text": block["content"]})
    for index, note in enumerate(input.get("notes") or [], start=1):
        sources.append({"id": f"N{index}", "ref": note["ref"], "text": note["text"]})
    if not sources:
        return {"status": "needs_input", "detail": "no meeting notes were supplied",
                "output": {"missing": ["the meeting notes or transcript, inline (ref + text) or as an exact Business Brain reference"]}}

    prompt = (ctx.prompt
              .replace("{{title}}", _escape(title))
              .replace("{{date}}", _escape(input.get("date") or "not given"))
              .replace("{{attendees}}", _escape(", ".join(input.get("attendees") or []) or "not given"))
              .replace("{{sources}}", "\n\n".join(_source_block(s["id"], s["ref"], s["text"]) for s in sources)))
    draft = ctx.model(prompt, schema=ctx.schemas["model_output.json"])

    if not draft["sufficient"]:
        missing = [str(item)[:300] for item in draft["missing"]][:6] or ["notes or a transcript of the meeting itself"]
        return {"status": "needs_input", "detail": "the sources are not a record of a meeting that took place",
                "output": {"missing": missing}}

    by_id = {source["id"]: source for source in sources}
    dropped = {"unquoted": 0, "credential": 0}
    cleared = {"owner": 0, "due": 0}

    def source_of(row, *texts):
        """The cited source when the quote is really in it and nothing repeats a credential, else None."""
        source = by_id.get(row["source_id"])
        if source is None or not _quoted_in(row["quote"], source["text"]):
            dropped["unquoted"] += 1
            return None
        if any(SECRET.search(str(text)) for text in texts + (row["quote"],)):
            dropped["credential"] += 1
            return None
        return source

    def backed(rows, limit):
        kept = []
        for row in rows:
            source = source_of(row, row["text"])
            if source is not None and row["text"].strip():
                kept.append({"text": row["text"].strip(), "quote": row["quote"].strip(), "sources": [source["ref"]]})
        return kept[:limit]

    decisions = backed(draft["decisions"], LIMITS["decisions"])
    pain_points = backed(draft["pain_points"], LIMITS["pain_points"])
    action_items = []
    for row in draft["action_items"]:
        source = source_of(row, row["task"], row["owner"], row["due"])
        if source is None or not row["task"].strip():
            continue
        text = _norm(source["text"])
        owner, due = row["owner"].strip(), row["due"].strip()
        named = _norm(owner)
        if named in VAGUE_OWNERS:
            owner = ""
        elif owner and named not in text and not any(named in person or person in named for person in people):
            owner, cleared["owner"] = "", cleared["owner"] + 1
        if due and _norm(due).strip(EDGE_PUNCTUATION) not in text:
            due, cleared["due"] = "", cleared["due"] + 1
        action_items.append({"task": row["task"].strip(), "owner": owner, "due": due,
                             "quote": row["quote"].strip(), "sources": [source["ref"]]})
    action_items = action_items[:LIMITS["action_items"]]

    next_step = None
    if draft["next_step"]["text"].strip():
        kept = backed([draft["next_step"]], 1)
        next_step = kept[0] if kept else None

    summary = draft["summary"].strip()
    questions = [str(item).strip() for item in draft["open_questions"] if str(item).strip() and not SECRET.search(str(item))]
    removed = sum(dropped.values())
    if not summary or SECRET.search(summary):
        return {"status": "failed", "failure_class": "validation_failed",
                "detail": "the summary is empty or repeats a credential from the notes"}
    if removed and not (decisions or action_items or pain_points or next_step):
        return {"status": "failed", "failure_class": "validation_failed",
                "detail": f"none of the {removed} returned item(s) could be matched to the notes"}

    caveats = []
    if dropped["unquoted"]:
        caveats.append(f"{dropped['unquoted']} item(s) were dropped because their quote is not in the notes")
    if dropped["credential"]:
        caveats.append(f"{dropped['credential']} item(s) were dropped because they repeated a credential")
    if cleared["owner"]:
        caveats.append(f"{cleared['owner']} owner(s) were cleared because the notes do not name them")
    if cleared["due"]:
        caveats.append(f"{cleared['due']} due date(s) were cleared because the notes do not state them")

    return {"status": "ok", "output": {
        "title": title, "date": input.get("date") or "", "summary": summary[:600],
        "decisions": decisions, "action_items": action_items, "pain_points": pain_points,
        "open_questions": [question[:300] for question in questions][:LIMITS["open_questions"]],
        "next_step": next_step, "unverified_dropped": removed, "caveats": caveats,
        "sources": [{"id": source["id"], "ref": source["ref"]} for source in sources],
    }}
