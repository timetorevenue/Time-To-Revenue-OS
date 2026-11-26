"""marketing.post_check: the publishing rules for a LinkedIn draft, checked in code.

Pure code. A post that breaks a rule fails the step and the reasons are listed;
softer points come back as warnings and do not block. A passing post is filed as
a draft artifact when the caller names one. Nothing is published: the workflow
rule is drafts only, and Casey posts by hand.

Revisit: when the rules in workflows/linkedin_content change. · Last touched: 2026-11-14.
"""

import re
import unicodedata

DEFAULT_MAX_CHARS = 1300
MAX_HASHTAGS = 3
HOOK_MAX_CHARS = 140
INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)
HASHTAG = re.compile(r"(?<![\w&])#([A-Za-z][A-Za-z0-9]*)")
BAD_HASHTAG = re.compile(r"(?<![\w&])#(?![A-Za-z])\S+")
PLACEHOLDER = re.compile(r"\[[^\]\n]{0,40}\]|\{\{.*?\}\}|<[^>\n]{1,40}>|\bTODO\b|\bTBD\b|lorem ipsum|(?-i:\bXX+\b)", re.I)
SECRET = re.compile(r"(?i)\b(?:sk|pk|ghp|gho|xox[abp])-[a-z0-9_-]{6,}"
                    r"|\b(?:password|passcode|api[ _-]?key|secret|token)\b[\s:=]+\S*\d")
GUARANTEE = re.compile(r"(?i)\bguarantee[ds]?\b|\brisk[- ]free\b|\b100\s?%|\bnever fails?\b|\bbest in the (?:world|industry|business)\b|#1\b")
HYPE = re.compile(r"(?i)\b(?:revolutionary|game[- ]chang\w+|supercharge\w*|unlock\w*|synerg\w+|cutting[- ]edge|world[- ]class|skyrocket\w*|10x)\b")
LINK = re.compile(r"https?://\S+")


def _plain(text):
    return unicodedata.normalize("NFKC", str(text).translate(INVISIBLE))


def _squash(text):
    return re.sub(r"[^a-z0-9]+", "", _plain(text).casefold())


def _names(text, term):
    """True when the term appears, ignoring case, invisible characters and spacing inside the name."""
    wanted = _squash(term)
    if len(wanted) >= 5:
        return wanted in _squash(text)
    return bool(wanted) and re.search(rf"(?<![a-z0-9]){re.escape(_plain(term).casefold().strip())}(?![a-z0-9])", _plain(text).casefold()) is not None


def run(input, ctx):
    post = str(input.get("post") or "").strip()
    if not post:
        return {"status": "needs_input", "detail": "there is no post to check",
                "output": {"missing": ["the post text"]}}
    limit = int(input.get("max_chars") or DEFAULT_MAX_CHARS)
    plain = _plain(post)
    tags = list(dict.fromkeys("#" + tag for tag in HASHTAG.findall(plain)))
    for raw in input.get("hashtags") or []:
        tag = "#" + str(raw).strip().lstrip("#")
        if tag not in tags:
            tags.append(tag)

    broken = []
    if len(post) > limit:
        broken.append(f"the post is {len(post)} characters; the limit is {limit}")
    named = [term for term in input.get("confidential_terms") or [] if _names(post, term) or any(_names(tag, term) for tag in tags)]
    if named:
        broken.append("it names what must stay confidential: " + ", ".join(named[:3]))
    found = PLACEHOLDER.search(plain)
    if found:
        broken.append(f"it still has placeholder text: {found.group(0)[:40]!r}")
    if SECRET.search(plain):
        broken.append("it contains what looks like a credential")
    found = GUARANTEE.search(plain)
    if found:
        broken.append(f"it makes a claim EXCO does not make: {found.group(0)!r}")
    if len(tags) > MAX_HASHTAGS:
        broken.append(f"it has {len(tags)} hashtags; at most {MAX_HASHTAGS}")
    malformed = [tag for tag in tags if not re.fullmatch(r"#[A-Za-z][A-Za-z0-9]*", tag)] + BAD_HASHTAG.findall(plain)
    if malformed:
        broken.append(f"a hashtag is malformed: {str(malformed[0])[:30]!r}")
    if broken:
        return {"status": "failed", "failure_class": "post_check_failed", "detail": "; ".join(broken[:5])}

    warnings = []
    lines = [line for line in post.splitlines() if line.strip()]
    if len(lines[0]) > HOOK_MAX_CHARS:
        warnings.append(f"the first line is {len(lines[0])} characters; it is cut off after about {HOOK_MAX_CHARS}")
    if len(lines) < 3 and len(post) > 400:
        warnings.append("it reads as one block; add line breaks")
    hype = sorted({word.lower() for word in HYPE.findall(plain)})
    if hype:
        warnings.append("hype wording: " + ", ".join(hype[:4]))
    if len(LINK.findall(plain)) > 1:
        warnings.append("more than one link")
    if plain.count("!") > 2:
        warnings.append("more than two exclamation marks")
    if sum(1 for char in plain if unicodedata.category(char) == "So") > 3:
        warnings.append("more than three emoji")
    if len([word for word in re.findall(r"\b[A-Z]{4,}\b", plain)]) > 2:
        warnings.append("several words in capitals")
    if not tags:
        warnings.append("no hashtag")

    artifact = ""
    if input.get("artifact_name"):
        extra = [tag for tag in tags if tag not in plain]
        text = post + ("\n\n" + " ".join(extra) if extra else "") + "\n"
        artifact = ctx.write_artifact(input["artifact_name"], "<!-- Draft for Casey's approval. Not published. -->\n\n" + text)
    return {"status": "ok", "output": {"pass": True, "status": "draft_for_approval", "char_count": len(post),
                                       "word_count": len(post.split()), "hashtags": tags, "warnings": warnings,
                                       "artifact": artifact}}
