"""marketing.linkedin_post: one LinkedIn draft from supplied notes, every factual point quoted.

Code gathers the declared notes, the model writes the post, and code checks it:
each claim's quote must appear word for word in the source it cites, no figure
may appear that is not in the notes, no confidential name, guarantee wording,
placeholder or credential may appear, and the post must fit the length. The
result is a draft for Casey's approval; nothing is published and no effect is
proposed.

Revisit: when the content workflow rules or the post shape change. · Last touched: 2026-11-15.
"""

import re
import unicodedata

MIN_QUOTE_WORDS = 3
MAX_POST_CHARS = 1300
EDGE_PUNCTUATION = " .,;:!?\"'()[]-"
TYPOGRAPHY = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "–": "-", "—": "-", " ": " "})
INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)
SECRET = re.compile(r"(?i)\b(?:sk|pk|ghp|gho|xox[abp])-[a-z0-9_-]{6,}"
                    r"|\b(?:password|passcode|api[ _-]?key|secret|token)\b[\s:=]+\S*\d")
PLACEHOLDER = re.compile(r"\[[^\]\n]{0,40}\]|\{\{.*?\}\}|\bTODO\b|\bTBD\b")
GUARANTEE = re.compile(r"(?i)\bguarantee[ds]?\b|\brisk[- ]free\b|\b100\s?%|\bnever fails?\b")
NUMBER = re.compile(r"\d+(?:[.,:/]\d+)*")
HASHTAG = re.compile(r"^#[A-Za-z][A-Za-z0-9]*$")


def _escape(text):
    return str(text).replace("<", "&lt;").replace(">", "&gt;")


def _source_block(ident, ref, text):
    return f'<source id="{ident}" ref="{_escape(ref)}">\n{_escape(text).strip()}\n</source>'


MARKDOWN_EMPHASIS = re.compile(r"\*+|`+|(?<![A-Za-z0-9])_+|_+(?![A-Za-z0-9])")


def _norm(text):
    # Brain notes are markdown: a quote copied word for word drops **bold**, _italic_ and `code` markers.
    return re.sub(r"\s+", " ", MARKDOWN_EMPHASIS.sub("", str(text).translate(TYPOGRAPHY))).strip().casefold()


def _squash(text):
    return re.sub(r"[^a-z0-9]+", "", unicodedata.normalize("NFKC", str(text).translate(INVISIBLE)).casefold())


def _quoted_in(quote, text):
    """True when the quote, or each part of it around an ellipsis, appears word for word in the text."""
    haystacks = (_norm(text), _norm(_escape(text)))
    parts = [part.strip(EDGE_PUNCTUATION) for part in re.split(r"\.\.\.|…", _norm(quote))]
    parts = [part for part in parts if part]
    if not parts or max(len(part.split()) for part in parts) < MIN_QUOTE_WORDS:
        return False
    return any(all(part in haystack for part in parts) for haystack in haystacks)


def run(input, ctx):
    topic = input["topic"].strip()
    sources = []
    for block in ctx.context(input, query=topic):
        sources.append({"id": block["id"], "ref": block["ref"], "text": block["content"]})
    for index, note in enumerate(input.get("notes") or [], start=1):
        sources.append({"id": f"N{index}", "ref": note["ref"], "text": note["text"]})
    if not sources:
        return {"status": "needs_input", "detail": "no notes were supplied",
                "output": {"missing": ["the notes, idea or lesson to write from, inline (ref + text) or as an exact Business Brain reference"]}}
    confidential = [str(term).strip() for term in input.get("confidential_terms") or [] if str(term).strip()]

    prompt = (ctx.prompt
              .replace("{{topic}}", _escape(topic))
              .replace("{{angle}}", _escape(input.get("angle") or "not given; lead with the clearest lesson in the sources"))
              .replace("{{audience}}", _escape(input.get("audience") or "owners and managers of small and medium service businesses"))
              .replace("{{cta}}", _escape(input.get("cta") or "not given; end on a question"))
              .replace("{{confidential}}", _escape("; ".join(confidential) or "any client, person or company in the sources"))
              .replace("{{sources}}", "\n\n".join(_source_block(s["id"], s["ref"], s["text"]) for s in sources)))
    draft = ctx.model(prompt, schema=ctx.schemas["model_output.json"])

    if not draft["sufficient"]:
        missing = [str(item)[:300] for item in draft["missing"]][:6] or ["an observation, lesson or result to build the post on"]
        return {"status": "needs_input", "detail": "the notes give the post nothing to stand on", "output": {"missing": missing}}

    def stop(reason):
        return {"status": "failed", "failure_class": "validation_failed", "detail": reason}

    hook, body, cta = draft["hook"].strip(), draft["body"].strip(), draft["cta"].strip()
    hashtags = []
    for raw in draft["hashtags"]:
        tag = "#" + re.sub(r"[^A-Za-z0-9]", "", str(raw))
        if HASHTAG.match(tag) and tag not in hashtags:
            hashtags.append(tag)
    hashtags = hashtags[:3]
    if not cta and not str(input.get("cta") or "").strip() and body:
        # No call to action was given, so the prompt asks the post to end on a question; the model
        # often ends the body with it and leaves cta empty. Use that closing question as the cta.
        lines = body.splitlines()
        if lines and lines[-1].strip().endswith("?"):
            cta, body = lines[-1].strip(), "\n".join(lines[:-1]).strip()
    if not hook or not body or not cta or not hashtags:
        return stop("the draft lacks a hook, a body, a call to action or a usable hashtag")
    post = f"{hook}\n\n{body}\n\n{cta}\n\n{' '.join(hashtags)}"
    if len(post) > MAX_POST_CHARS:
        return stop(f"the post is {len(post)} characters; the limit is {MAX_POST_CHARS}")

    by_id = {source["id"]: source for source in sources}
    claims = []
    for row in draft["claims"]:
        source = by_id.get(row["source_id"])
        if source is None or not _quoted_in(row["quote"], source["text"]):
            return stop(f"a claim is not backed by a quote found in the notes: {row['text'][:80]}")
        if SECRET.search(row["quote"]) or SECRET.search(row["text"]):
            return stop("a claim repeats what looks like a credential")
        if row["text"].strip():
            claims.append({"text": row["text"].strip()[:300], "quote": row["quote"].strip(), "sources": [source["ref"]]})
    if not claims:
        return stop("the post rests on no sourced point")
    squashed = _squash(post)
    named = [term for term in confidential if len(_squash(term)) >= 3 and _squash(term) in squashed]
    if named:
        return stop(f"the post names what must stay confidential: {named[0][:40]}")
    for pattern, reason in ((SECRET, "repeats what looks like a credential"), (PLACEHOLDER, "contains a placeholder"),
                            (GUARANTEE, "makes a guarantee EXCO does not make")):
        if pattern.search(post):
            return stop(f"the post {reason}")
    known = " ".join([source["text"] for source in sources] + [topic, str(input.get("angle") or ""), str(input.get("cta") or "")])
    allowed = set(NUMBER.findall(known))
    invented = [number for number in NUMBER.findall(f"{hook}\n{body}\n{cta}") if number not in allowed]
    if invented:
        return stop(f"the post contains a figure that is not in the notes: {invented[0]}")

    return {"status": "ok", "output": {
        "post": post, "hook": hook, "body": body, "cta": cta, "hashtags": hashtags, "claims": claims[:6],
        "char_count": len(post), "status": "draft_for_approval",
        "sources": [{"id": source["id"], "ref": source["ref"]} for source in sources]}}
