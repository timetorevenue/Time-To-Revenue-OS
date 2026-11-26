#!/usr/bin/env python3
"""Three-tier automatic memory ingestion for one preserved source.

Tier 0  tools/source_intake.py + tools/source_passages.py: exact bytes already captured; passages are bounded,
        hash-verified and addressed ``source:<sha256>#L<a>-L<b>``.
Tier 1  ONE Luna (gpt-6-luna, medium) interpretation per source. Revision 3: the model reads deterministic
        sentence-bounded segments (``S<n>|``) and cites them; the validator converts every citation to code-point
        offsets of the exact decoded original (``C<start>-<end>``, tools/source_passages.py) and gives every item
        and explanation paragraph one lexical evidence status (exact / supported / cited / failed;
        tools/v3_grounding_check.evidence_status). Failed items are kept only in ``dropped``. Topics are distinct
        subjects (non-adjacent segments allowed) with an attributed explanation and typed items; there are no
        topic, note, item or length quotas, only malformed-output guards. It is PERSISTED, keyed by source SHA and
        extraction contract, before anything downstream is written. A topic-map card is then written through
        the existing semantic-intake transaction (tools/source_intake.py, mode="semantic").
Tier 2  The same saved interpretation becomes a Memory Ingest v3 package imported by the existing
        ``memory_exchange_import.process_v3``. Revision 3 files every validated topic (knowledge first, Source block
        last, a TTROS:MACHINE source-graph block of entities and typed relationships); saved revision 1-2 files
        render exactly as before (full notes for internal meetings, selective for reference).

Retry rule: a saved interpretation is never regenerated. Git, import, index or Graphify failures downstream
re-run from the saved file with zero model calls. Paid attempts per source and contract are bounded
(``MAX_PAID_ATTEMPTS``), a rejected call sets a cooldown, and the call itself runs under the existing Step 6
cost controls. The model is Luna only; nothing else is substituted. No timer or watcher calls this module.

Revisit: when the interpretation contract, the v3 note format, the Luna route or the Codex account contract changes.
· Last touched: 2026-11-16.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import source_passages as sp  # noqa: E402
from tools.source_intake import BUSINESS_BRAIN_ROOT, RECORDS_RELATIVE, SourceIntakeError  # noqa: E402
from tools import v3_grounding_check as grounding  # noqa: E402
from tools.v3_grounding_check import merge_offsets, parse_offsets, parse_ranges  # noqa: E402

CONTRACT = "source-interpretation-v1"       # state key; unchanged so every saved interpretation stays valid (no new paid calls)
EXTRACTION_REVISION = 3                      # segment citations stored as offsets, explanations, evidence status; 1-2 render as before
OFFSET_CONVENTION = "utf8-codepoint-halfopen-v1"   # tools/source_passages.py: C<start>-<end> of raw.decode("utf-8"), nothing removed
REQUIRED_MODEL = "gpt-6-luna"
REQUIRED_EFFORT = "medium"                   # this interpreter only; the global Codex tiers are not touched
STATE_RELATIVE = Path("queue") / "semantic_state"
PACKAGE_RELATIVE = STATE_RELATIVE / "v3_packages"
MAX_PAID_ATTEMPTS = 2
COOLDOWN_SECONDS = 3600
CALL_TIMEOUT_SECONDS = 420
CALL_TOKEN_FUSE = 120_000          # exact-usage fuse for this one call; the Step 6 session fuse still applies
MAX_TOPICS = 12                    # revision 1-2 only (saved files); revision 3 files every validated topic
MAX_SELECTIVE_NOTES = 8            # revision 1-2 only
# Revision 3 has no semantic quotas. These are malformed-output guards, generous by design: a value beyond one is
# dropped with a reason (never silently clipped), and the output is otherwise kept.
FIELD_GUARD_CHARS = 20_000         # one text field
LIST_GUARD = 1_000                 # entries in one list field
TOPIC_GUARD = 400                  # topics in one output
TITLE_CHARS = 80                   # topic titles become note titles and link labels; slugs stay at 48 for paths
SLUG_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,58}[a-z0-9])?\Z")
# framework/method/comparison (revision 3) are reference knowledge. Every consumer of knowledge_type was checked: the
# Entity Index maps unknown values under sources/ to historical evidence, filing shows the slug as a label, and suggested
# links only test for explicit_decision/historical_evidence.
KNOWLEDGE_TYPES = ("relationship_context", "decision_record", "open_loop", "opportunity", "product_feedback",
                   "attributed_claim", "commitment", "reference", "architecture", "requirement", "risk",
                   "framework", "method", "comparison")
SOURCE_CLASSES = ("internal_meeting", "reference", "other")
RELATION_PREDICATES = ("uses", "built_on", "integrates_with", "part_of", "created_by", "depends_on", "compares_to",
                       "criticises", "recommends", "replaces", "works_with", "related_to")


class InterpretationError(SourceIntakeError):
    pass


class ModelUnavailable(InterpretationError):
    """The Luna route is not ready or usage cannot be reported; nothing was spent."""


class RateLimited(InterpretationError):
    pass


# ----------------------------------------------------------------------------- paths and bounded retries
def _state_file(root: Path, sha: str, kind: str) -> Path:
    """State is keyed by source SHA AND extraction contract: <sha>.<contract>.<kind>.json."""
    return root / STATE_RELATIVE / f"{sha}.{CONTRACT}.{kind}.json"


def interpretation_path(root: Path, sha: str) -> Path:
    return _state_file(root, sha, "interpretation")


def _attempts_path(root: Path, sha: str) -> Path:
    return _state_file(root, sha, "attempts")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def load_interpretation(root: Path, sha: str) -> dict | None:
    """The saved, validated interpretation for this source and contract, or None."""
    saved = _read_json(interpretation_path(root, sha))
    if saved and saved.get("source_sha256") == sha and saved.get("contract") == CONTRACT and saved.get("topics"):
        return saved
    return None


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _check_attempt_allowed(root: Path, sha: str) -> dict:
    state = _read_json(_attempts_path(root, sha)) or {"paid_attempts": 0, "history": []}
    if state["paid_attempts"] >= MAX_PAID_ATTEMPTS:
        raise InterpretationError(
            f"paid attempts for {sha[:8]} are exhausted ({state['paid_attempts']} of {MAX_PAID_ATTEMPTS}); "
            "no further model call is made automatically")
    until = state.get("cooldown_until")
    if until and until > _now():
        raise RateLimited(f"model call for {sha[:8]} is cooling down until {until}")
    return state


# ----------------------------------------------------------------------------- prompt
PROMPT = """You are indexing ONE source document for a business memory system. Read it once and return ONE JSON object and nothing else (no prose, no code fence).

The source is cut into numbered segments, one per line, written `S<number>| text`. A segment never spans a line break of the original. Cite segments only as `S12`, `S12-S15`, or a list such as `S3, S7-S9`, and cite exactly the segments that contain what you state.

Use only what the source says. Never add anything from your own knowledge; never invent names, dates, numbers, steps or facts; never present a claim as established fact: attribute every assertion to whoever makes it (the author or a named speaker). Copy figures, prices, percentages, dates and names exactly as the source writes them. Put anything unclear, ambiguous or unsupported in "uncertainty". Leave a field empty ("" or []) when the source has nothing for it.

Write for density, not length: capture what the source actually contains, in proportion to how much it contains, and nothing more. No generic statements, no filler, and no repeating the same point in several topics.

JSON shape:
{
 "document_title": "a specific title for THIS document (its subject), not the document type",
 "description": "what this document is and what it is for",
 "source_class": "internal_meeting" | "reference" | "other",
 "document_kind": "short label such as meeting transcript, talk, architecture brief, memo",
 "participants": ["people who speak or are the document's author/recipient, as written"],
 "authors": [{"name": "author or presenter named IN THE TEXT", "role": "author|presenter|speaker", "cite": "S1", "quote": "copied exactly, showing the name"}],
 "source_date": {"text": "date as written", "iso": "YYYY-MM-DD", "cite": "S1", "quote": "copied exactly, containing the date text"},
 "entities": [{"name": "", "kind": "person|organization|product|topic", "cite": "S12", "quote": "copied exactly from the cited segment, containing the name"}],
 "relationships": [{"subject": "entity name", "predicate": "uses|built_on|integrates_with|part_of|created_by|depends_on|compares_to|criticises|recommends|replaces|works_with|related_to", "object": "entity name", "cite": "S12", "quote": "copied exactly, showing the relationship"}],
 "topics": [{
   "title": "specific, at most 80 characters",
   "slug": "lowercase-hyphenated, at most 48 characters",
   "knowledge_type": "one of: relationship_context, decision_record, open_loop, opportunity, product_feedback, attributed_claim, commitment, reference, architecture, requirement, risk, framework, method, comparison",
   "importance": 1 | 2 | 3,
   "cite": "every segment this topic draws on, e.g. S4-S9, S31",
   "one_line": "one sentence: the topic's core point, attributed",
   "questions": ["concrete questions this topic answers"],
   "entities": ["names from the top-level entities list that this topic is actually about"],
   "explanation": [{"text": "one paragraph", "cite": "S4-S6"}],
   "principles": [{"text": "a reusable principle or rule the source states", "cite": "S5"}],
   "steps": [{"text": "one step of a method or process, in the source's order", "cite": "S6"}],
   "decisions": [{"text": "", "who": "who decided, if stated", "cite": "S7"}],
   "actions": [{"text": "an action or commitment", "owner": "who, if stated", "cite": "S8"}],
   "outcomes": [{"text": "a stated result", "cite": "S9"}],
   "claims": [{"speaker": "who asserts it", "text": "an assertion the source does not itself prove", "cite": "S10"}],
   "quotes": [{"text": "copied EXACTLY, same characters, from the cited segments", "speaker": "", "cite": "S11"}],
   "open_loops": [{"text": "an unresolved question or action the source states", "cite": "S12"}],
   "uncertainty": ["what this topic leaves unclear"]
 }],
 "uncertainty": ["things the source leaves ambiguous or unsupported"]
}

Topics: a topic is one distinct subject wherever it appears in the source, not a run of consecutive segments; it may cite segments that are far apart. Make one topic per distinct subject with real content: a short source may have one or two, a long one many. Do NOT pad, split one subject into several, or invent a topic. importance 3 = central to the document's purpose, 1 = background; importance only ranks topics.

Explanation: source-grounded paragraphs that teach the topic: what it is, why it matters, how it works, when it applies, its limits and trade-offs, and the concrete examples and figures the source gives. Cover only the aspects the source actually addresses. Every paragraph cites its segments and names who holds each view.

Typed items: use principles, steps, decisions, actions, outcomes, claims, quotes and open_loops only where the source actually contains them; steps keep the source's order. Entities, authors, dates and relationships must each be supported by an exact quote. Relationship subjects and objects must be names from your entities or participants lists. A filename or title shown outside the numbered segments is not text evidence.

SOURCE (title: {title}):
{segmented}
"""


def numbered_text(lines: list[str]) -> str:
    """Revision 1-2 model view (kept for reference; no new call uses it)."""
    return "\n".join(f"L{i}| {line}" for i, line in enumerate(lines, 1))


class SegmentedSource:
    """The exact decoded original and its deterministic segments: the model cites ``S<n>``, files store offsets."""

    def __init__(self, text: str):
        self.text = text
        self.segments = sp.segments(text)
        self.length = len(text)

    def model_view(self) -> str:
        return "\n".join(f"S{i}| {sp.display_text(self.text[a:b])}" for i, (a, b) in enumerate(self.segments, 1))

    def resolve(self, value: Any) -> list[tuple[int, int]] | None:
        """Offset ranges for ``S12`` / ``S12-S15`` / lists; None when any part does not resolve to real segments."""
        raw = ", ".join(str(v) for v in value) if isinstance(value, list) else str(value or "")
        found = list(_SEG_CITE_RE.finditer(raw))
        if not found or re.sub(r"[\s,;&]+|\band\b", "", _SEG_CITE_RE.sub("", raw)):
            return None
        n, out = len(self.segments), []
        for m in found:
            i, j = int(m.group(1)), int(m.group(2) or m.group(1))
            if not 1 <= i <= j <= n:
                return None
            out.append((self.segments[i - 1][0], self.segments[j - 1][1]))
        return merge_offsets(out)

    def span_text(self, spans: list[tuple[int, int]]) -> str:
        return "\n".join(self.text[a:b] for a, b in spans)

    def is_whole(self, span: tuple[int, int]) -> bool:
        return bool(self.segments) and span[0] <= self.segments[0][0] and span[1] >= self.segments[-1][1]


_SEG_CITE_RE = re.compile(r"\bS(\d+)(?:\s*[-–]\s*S?(\d+))?\b")


def offsets_cite(spans: list[tuple[int, int]]) -> str:
    """Compact citation stored and rendered for offsets: ``C8080-8420, C9000-9100``."""
    return ", ".join(f"C{a}-{b}" for a, b in merge_offsets(spans))


# ----------------------------------------------------------------------------- the one paid call
def _agent_text(stdout: str) -> str:
    text = ""
    for raw in stdout.splitlines():
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        item = event.get("item") if isinstance(event, dict) else None
        if isinstance(item, dict) and event.get("type") == "item.completed" and item.get("type") == "agent_message":
            text = str(item.get("text") or "")
    return text


def select_luna_account() -> Any:
    """The Codex account (slot A/B) for one new interpretation session. Nothing is spent here.

    No eligible account raises RateLimited (quota) or ModelUnavailable (sign-in) before any
    paid attempt is counted."""
    from tools import aos_codex_policy as policy
    try:
        return policy.select_codex_account()
    except policy.AccountUnavailable as exc:
        if exc.failure_class == policy.QUOTA_UNAVAILABLE:
            raise RateLimited(f"no Codex account has capacity: {exc.detail}") from exc
        raise ModelUnavailable(f"no Codex account is signed in and eligible: {exc.detail}") from exc


def luna_route(root: Path, target: Any = None) -> dict:
    """The live Luna route at medium effort: catalog entry and authentication. Nothing is spent here.

    The model and effort are this interpreter's own contract; the shared Codex tier table is only consulted to confirm the
    catalog lists the model, and it is never edited."""
    from tools import aos_codex_policy as policy
    supported = policy.load_codex_tiers(root)["supported_models"]
    if REQUIRED_MODEL not in supported:
        raise ModelUnavailable(f"{REQUIRED_MODEL} is not in the configured Codex model catalog {supported}; "
                               "no other model is substituted")
    state = policy.readiness(target) if target is not None else policy.readiness()
    if not (state.get("available") and state.get("authenticated")):
        raise ModelUnavailable(f"Codex route is not ready: {state.get('state')}")
    return {"model": REQUIRED_MODEL, "reasoning_effort": REQUIRED_EFFORT, "readiness": state}


def call_luna(prompt: str, *, root: Path, sha: str, target: Any = None) -> dict:
    """One bounded read-only Luna turn. Returns text plus exact usage; raises without substituting a model.

    Runs on ``target`` (the selected Codex account) or a fresh selection; subscription route only."""
    from tools import aos_codex_policy as policy
    from tools import step6_cost_control as cost
    target = target if target is not None else select_luna_account()
    env = policy.build_environment(target)
    try:
        policy.require_subscription_environment(env)
    except policy.CodexPolicyError as exc:
        raise ModelUnavailable(str(exc)) from exc
    route = luna_route(root, target)
    scope = cost.derive_scope(session_id=f"source-interpret-{sha}")
    status = cost.preflight(scope, root=root)
    if status.get("paused"):
        raise InterpretationError(f"cost-control fuse is paused for this scope: {status.get('pause_reason')}")
    command = policy.build_exec_command(target, selection={"model": route["model"],
                                                           "reasoning_effort": route["reasoning_effort"]})
    scratch = Path(tempfile.mkdtemp(prefix="source-interpret-"))
    try:
        command[command.index("--sandbox") + 1] = "read-only"      # stricter than the workbench default
        command[command.index("-C") + 1] = str(scratch)             # no repository access for an extraction turn
        full_prompt = policy.prepare_fresh_prompt(prompt)
        fuse = policy.StreamingSessionFuse(full_prompt, limit=CALL_TOKEN_FUSE)
        result = policy.run_streaming(command, cwd=scratch, env=env, prompt=full_prompt,
                                      timeout_seconds=CALL_TIMEOUT_SECONDS, fuse=fuse)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    combined = (result["stdout"] or "") + (result["stderr"] or "")
    if re.search(r"\b429\b|rate.?limit|quota", combined, re.IGNORECASE) and not _agent_text(result["stdout"]):
        raise RateLimited(f"provider rejected the call: {combined[-300:]!r}")
    if result["timed_out"] or result["returncode"] not in (0, None) and not _agent_text(result["stdout"]):
        raise InterpretationError(f"Luna call failed (rc={result['returncode']}, timed_out={result['timed_out']}): "
                                  f"{(result['stderr'] or '')[-300:]!r}")
    session = policy.require_clean_session_id(result["stdout"])
    usage = policy.cumulative_usage_snapshot(result["stdout"])
    return {"text": _agent_text(result["stdout"]), "usage": usage, "session_id": session, "model": route["model"],
            "reasoning_effort": route["reasoning_effort"], "fuse": fuse.summary(), "scope": scope,
            "codex_account": target.account_record()}


def record_usage(call: dict, *, root: Path, sha: str) -> dict:
    from tools import step6_cost_control as cost
    usage, scope = call["usage"], call["scope"]
    account = {"model_selection": {"codex_account": call["codex_account"]}} if call.get("codex_account") else {}
    if not usage.get("available"):
        return cost.record_unavailable_invocation(
            scope, invocation_id=call["session_id"], provider="openai-codex", model=call["model"],
            reason=str(usage.get("reason") or "usage unavailable"), root=root, surface="memory-intake:interpretation",
            **account)
    return cost.record_invocation(
        scope, invocation_id=call["session_id"], provider="openai-codex", model=call["model"],
        usage={"input_tokens": usage["input_tokens"], "cached_input_tokens": usage.get("cached_input_tokens"),
               "output_tokens": usage["output_tokens"], "reasoning_output_tokens": usage.get("reasoning_output_tokens"),
               "provider": "openai-codex", "model": call["model"], "cache_semantics": "included_in_provider_input"},
        root=root, surface="memory-intake:interpretation", **account)


# ----------------------------------------------------------------------------- validation against exact bytes
def _norm(text: str) -> str:
    return " ".join(str(text or "").split())


def _spans(value: Any, n: int) -> list[tuple[int, int]]:
    text = ", ".join(value) if isinstance(value, list) else str(value or "")
    spans = parse_ranges(text)
    return [s for s in spans if 1 <= s[0] <= s[1] <= n]


def _cite(spans: list[tuple[int, int]]) -> str:
    return ", ".join(f"L{a}" if a == b else f"L{a}-L{b}" for a, b in sorted(set(spans)))


def _supported(quote: str, spans: list[tuple[int, int]], lines: list[str]) -> bool:
    q = _norm(quote)
    if not q or not spans:
        return False
    hay = _norm("\n".join(lines[i - 1] for a, b in spans for i in range(a, b + 1)))
    return q in hay


def _clip(text: Any, limit: int) -> str:
    return _norm(text)[:limit]


def _safe_label(text: Any, fallback: str) -> str:
    cleaned = re.sub(r"[()*\[\]|:]", " ", str(text or ""))
    return _norm(cleaned)[:60] or fallback


def _slug(text: Any) -> str:
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", str(text or "").lower())).strip("-")[:48].strip("-")


def _cited(raw: Any, n: int, limit: int, extra: tuple[str, ...], drop: Callable[..., None], kind: str) -> list[dict]:
    """Items with text and an in-bounds line citation; anything else is dropped with a reason."""
    out = []
    for x in (raw if isinstance(raw, list) else [])[:limit]:
        spans = _spans(x.get("lines"), n) if isinstance(x, dict) else []
        text = _clip(x.get("text"), 240) if isinstance(x, dict) else ""
        if spans and text:
            out.append({"text": text, **{k: _safe_label(x.get(k), "") for k in extra}, "lines": _cite(spans)})
        else:
            drop(kind, x, "no in-bounds line citation or empty text")
    return out


def parse_model_json(text: str) -> dict:
    body = text.strip()
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        raise InterpretationError("model output has no JSON object")
    try:
        value = json.loads(body[start:end + 1])
    except json.JSONDecodeError as exc:
        raise InterpretationError(f"model output is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise InterpretationError("model output is not a JSON object")
    return value


def validate_interpretation(raw: dict, source: "SegmentedSource | list[str]") -> tuple[dict, list[dict]]:
    """Keep only what the exact source supports. Returns (clean interpretation, dropped items with reasons).

    A ``SegmentedSource`` validates a revision-3 output (segment citations, stored as offsets, with an evidence status
    per item). A list of lines is the revision-2 validator, kept so saved revision 1-2 files keep their meaning."""
    if isinstance(source, SegmentedSource):
        return _validate_segments(raw, source)
    return _validate_lines(raw, source)


def _validate_lines(raw: dict, lines: list[str]) -> tuple[dict, list[dict]]:
    """Revision 2 (line citations). No new interpretation is validated here."""
    n, dropped = len(lines), []

    def drop(kind: str, item: Any, reason: str) -> None:
        dropped.append({"kind": kind, "reason": reason, "item": item if isinstance(item, (dict, str)) else str(item)})

    topics, seen = [], set()
    for item in raw.get("topics") if isinstance(raw.get("topics"), list) else []:
        if not isinstance(item, dict):
            drop("topic", item, "not an object")
            continue
        spans = _spans(item.get("lines"), n)
        title, slug = _clip(item.get("title"), 80), _slug(item.get("slug") or item.get("title"))
        if not title or not spans or not SLUG_RE.fullmatch(slug) or slug in seen or slug == "index":
            drop("topic", {"title": title, "slug": slug}, "missing title/in-bounds lines, bad or duplicate slug")
            continue
        if len(topics) >= MAX_TOPICS:
            drop("topic", {"title": title}, f"more than {MAX_TOPICS} topics")
            continue
        seen.add(slug)
        ktype = str(item.get("knowledge_type") or "reference")
        points, quotes = [], []
        for p in (item.get("points") if isinstance(item.get("points"), list) else [])[:5]:
            ps = _spans(p.get("lines"), n) if isinstance(p, dict) else []
            if ps and _clip(p.get("text"), 240):
                points.append({"speaker": _safe_label(p.get("speaker"), "Author"), "text": _clip(p["text"], 240),
                               "lines": _cite(ps)})
            else:
                drop("point", p, "no in-bounds line citation or empty text")
        for q in (item.get("quotes") if isinstance(item.get("quotes"), list) else [])[:2]:
            qs = _spans(q.get("lines"), n) if isinstance(q, dict) else []
            text = _clip(q.get("text"), 200) if isinstance(q, dict) else ""
            if '"' not in text and "”" not in text and "“" not in text and _supported(text, qs, lines):
                quotes.append({"text": text, "speaker": _safe_label(q.get("speaker"), "Author"), "lines": _cite(qs)})
            else:
                drop("quote", q, "not an exact substring of the cited lines (or contains a double quote)")
        importance = item.get("importance") if item.get("importance") in (1, 2, 3) else 2
        extras = {
            "principles": _cited(item.get("principles"), n, 5, (), drop, "principle"),
            "steps": _cited(item.get("steps"), n, 12, (), drop, "step"),
            "decisions": _cited(item.get("decisions"), n, 5, ("who",), drop, "decision"),
            "actions": _cited(item.get("actions"), n, 5, ("owner",), drop, "action"),
            "outcomes": _cited(item.get("outcomes"), n, 5, (), drop, "outcome"),
            "claims": _cited(item.get("claims"), n, 5, ("speaker",), drop, "claim"),
            "entity_names": [_clip(x, 60) for x in (item.get("entities") if isinstance(item.get("entities"), list) else [])[:12] if _clip(x, 60)],
            "uncertainty": [_clip(x, 200) for x in (item.get("uncertainty") if isinstance(item.get("uncertainty"), list) else [])[:3] if _clip(x, 200)],
        }
        topics.append({**extras,
            "title": title, "slug": slug, "knowledge_type": ktype if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", ktype) else "reference",
            "importance": importance, "lines": _cite(spans), "one_line": _clip(item.get("one_line"), 160),
            "summary": _clip(item.get("summary"), 500),
            "questions": [_clip(x, 160) for x in (item.get("questions") if isinstance(item.get("questions"), list) else [])[:5] if _clip(x, 160)],
            "points": points, "quotes": quotes,
            "open_loops": [_clip(x, 200) for x in (item.get("open_loops") if isinstance(item.get("open_loops"), list) else [])[:4] if _clip(x, 200)],
        })
    source_text = _norm("\n".join(lines)).casefold()
    entities, names = [], set()
    for e in raw.get("entities") if isinstance(raw.get("entities"), list) else []:
        if not isinstance(e, dict):
            drop("entity", e, "not an object")
            continue
        name, kind = _clip(e.get("name"), 60), str(e.get("kind") or "topic")
        es = _spans(e.get("lines"), n)
        tokens = [t for t in re.split(r"\W+", name.casefold()) if len(t) >= 3]
        quote = _clip(e.get("quote"), 160)
        if (name and es and tokens and _supported(quote, es, lines) and any(t in quote.casefold() for t in tokens)
                and name.casefold() not in names):
            names.add(name.casefold())
            entities.append({"name": name, "kind": kind if kind in {"person", "organization", "product", "topic"} else "topic",
                             "lines": _cite(es), "quote": quote,
                             "full_name_in_source": name.casefold() in source_text})
        else:
            drop("entity", e, "name not supported by an exact quote in the cited lines")
    known = {e["name"].casefold() for e in entities} | {_safe_label(x, "").casefold() for x in
                                                       (raw.get("participants") if isinstance(raw.get("participants"), list) else [])}
    for topic in topics:
        topic["entity_names"] = [x for x in topic.pop("entity_names") if x.casefold() in {e["name"].casefold() for e in entities}]
    relationships = []
    for r in (raw.get("relationships") if isinstance(raw.get("relationships"), list) else [])[:20]:
        rs = _spans(r.get("lines"), n) if isinstance(r, dict) else []
        subject, obj, pred = (_clip(r.get(k), 60) for k in ("subject", "object", "predicate")) if isinstance(r, dict) else ("", "", "")
        if (rs and pred in RELATION_PREDICATES and subject.casefold() in known and obj.casefold() in known
                and subject.casefold() != obj.casefold() and _supported(_clip(r.get("quote"), 160), rs, lines)):
            relationships.append({"subject": subject, "predicate": pred, "object": obj, "lines": _cite(rs),
                                  "quote": _clip(r.get("quote"), 160)})
        else:
            drop("relationship", r, "endpoints, predicate or quote not supported by the source")
    authors = []
    for a in (raw.get("authors") if isinstance(raw.get("authors"), list) else [])[:4]:
        aspans = _spans(a.get("lines"), n) if isinstance(a, dict) else []
        name = _safe_label(a.get("name"), "") if isinstance(a, dict) else ""
        tokens = [t for t in re.split(r"\W+", name.casefold()) if len(t) >= 3]
        quote = _clip(a.get("quote"), 160) if isinstance(a, dict) else ""
        if name and tokens and _supported(quote, aspans, lines) and all(t in quote.casefold() for t in tokens):
            role = a.get("role") if a.get("role") in ("author", "presenter", "speaker") else "author"
            authors.append({"name": name, "role": role, "lines": _cite(aspans), "quote": quote})
        else:
            drop("author", a, "name not supported by an exact quote in the text")
    source_date = None
    d = raw.get("source_date")
    if isinstance(d, dict):
        dspans, dtext, iso = _spans(d.get("lines"), n), _clip(d.get("text"), 60), _clip(d.get("iso"), 10)
        try:
            dt.date.fromisoformat(iso)
            valid = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso))
        except ValueError:
            valid = False
        if valid and dtext and _supported(_clip(d.get("quote"), 160), dspans, lines) and dtext.casefold() in _clip(d.get("quote"), 160).casefold():
            source_date = {"text": dtext, "iso": iso, "lines": _cite(dspans), "quote": _clip(d.get("quote"), 160)}
        else:
            drop("source_date", d, "date text not supported by an exact quote")
    cls = raw.get("source_class")
    clean = {
        "extraction_schema": 2, "document_title": _clip(raw.get("document_title"), 90),
        "authors": authors, "source_date": source_date, "relationships": relationships,
        "description": _clip(raw.get("description"), 300),
        "source_class": cls if cls in SOURCE_CLASSES else "other",
        "document_kind": _clip(raw.get("document_kind"), 60),
        "participants": [_safe_label(x, "") for x in (raw.get("participants") if isinstance(raw.get("participants"), list) else [])[:8] if _safe_label(x, "")],
        "entities": entities, "topics": topics,
        "uncertainty": [_clip(x, 240) for x in (raw.get("uncertainty") if isinstance(raw.get("uncertainty"), list) else [])[:8] if _clip(x, 240)],
    }
    if not clean["description"] or not topics:
        raise InterpretationError("interpretation has no description or no source-supported topic")
    return clean, dropped


# ----------------------------------------------------------------------------- revision 3: offsets and evidence status
_STATUS_ORDER = grounding.EVIDENCE_STATUSES
_ITEM_FIELDS = (("principles", "principle", ()), ("steps", "step", ()), ("decisions", "decision", ("who",)),
                ("actions", "action", ("owner",)), ("outcomes", "outcome", ()), ("claims", "claim", ("speaker",)),
                ("open_loops", "open_loop", ()))


def _guarded_list(value: Any, kind: str, drop: Callable[..., None]) -> list:
    items = value if isinstance(value, list) else []
    for extra in items[LIST_GUARD:]:
        drop(kind, extra, f"malformed-output guard: more than {LIST_GUARD} entries")
    return items[:LIST_GUARD]


def _guarded_text(value: Any) -> str | None:
    """Whitespace-normalised text, or None when it exceeds the malformed-output guard (never clipped)."""
    text = _norm(value)
    return None if len(text) > FIELD_GUARD_CHARS else text


def _validate_segments(raw: dict, src: SegmentedSource) -> tuple[dict, list[dict]]:
    """Revision 3: every citation is converted to offsets; every item gets one lexical evidence status."""
    dropped: list[dict] = []
    counts = {status: 0 for status in _STATUS_ORDER}

    def drop(kind: str, item: Any, reason: str, status: str | None = None) -> None:
        if status:
            counts[status] += 1
        dropped.append({"kind": kind, "reason": reason, **({"status": status} if status else {}),
                        "item": item if isinstance(item, (dict, str)) else str(item)})

    source_folded = src.text.casefold()
    participants = [x for x in (_safe_label(v, "") for v in _guarded_list(raw.get("participants"), "participant", drop)) if x]
    authors = []
    for a in _guarded_list(raw.get("authors"), "author", drop):
        spans = src.resolve(a.get("cite")) if isinstance(a, dict) else None
        name = _safe_label(a.get("name"), "") if isinstance(a, dict) else ""
        tokens = [t for t in re.split(r"\W+", name.casefold()) if len(t) >= 3]
        quote = _norm(a.get("quote")) if isinstance(a, dict) else ""
        if name and tokens and spans and grounding.evidence_status(quote, src.span_text(spans), quote=True)[0] == grounding.EXACT \
                and all(t in quote.casefold() for t in tokens):
            role = a.get("role") if a.get("role") in ("author", "presenter", "speaker") else "author"
            authors.append({"name": name, "role": role, "spans": offsets_cite(spans), "quote": quote})
        else:
            drop("author", a, "name not supported by an exact quote in the cited segments")
    attributed = frozenset(t for name in [*participants, *(a["name"] for a in authors)]
                           if name.casefold() in source_folded for t in grounding._word_set(name))

    def judge(kind: str, x: Any, text: str, spans: list[tuple[int, int]] | None, *, quote: bool = False) -> str | None:
        status, reason = grounding.evidence_status(text, src.span_text(spans) if spans else None, quote=quote,
                                                   attributed=attributed)
        if status == grounding.FAILED:
            drop(kind, x, reason, grounding.FAILED)
            return None
        counts[status] += 1
        return status

    def items(value: Any, kind: str, extra: tuple[str, ...]) -> list[dict]:
        out = []
        for x in _guarded_list(value, kind, drop):
            text = _guarded_text(x.get("text")) if isinstance(x, dict) else None
            if not text:
                drop(kind, x, "empty text, not an object, or over the malformed-output guard")
                continue
            spans = src.resolve(x.get("cite"))
            status = judge(kind, x, text, spans)
            if status:
                out.append({"text": text, **{k: _safe_label(x.get(k), "") for k in extra}, "spans": offsets_cite(spans),
                            "status": status})
        return out

    entities, names = [], set()
    for e in _guarded_list(raw.get("entities"), "entity", drop):
        if not isinstance(e, dict):
            drop("entity", e, "not an object")
            continue
        name, kind = _clip(e.get("name"), 60), str(e.get("kind") or "topic")
        spans = src.resolve(e.get("cite"))
        tokens = [t for t in re.split(r"\W+", name.casefold()) if len(t) >= 3]
        quote = _norm(e.get("quote"))
        exact = bool(spans) and grounding.evidence_status(quote, src.span_text(spans) if spans else None, quote=True)[0] == grounding.EXACT
        if name and exact and tokens and any(t in quote.casefold() for t in tokens) and name.casefold() not in names:
            names.add(name.casefold())
            entities.append({"name": name, "kind": kind if kind in {"person", "organization", "product", "topic"} else "topic",
                             "spans": offsets_cite(spans), "quote": quote, "full_name_in_source": name.casefold() in source_folded})
        else:
            drop("entity", e, "name not supported by an exact quote in the cited segments")
    entity_names = {e["name"].casefold() for e in entities}

    topics, seen = [], set()
    raw_topics = raw.get("topics") if isinstance(raw.get("topics"), list) else []
    for extra in raw_topics[TOPIC_GUARD:]:
        drop("topic", extra if isinstance(extra, dict) else str(extra), f"malformed-output guard: more than {TOPIC_GUARD} topics")
    for item in raw_topics[:TOPIC_GUARD]:
        if not isinstance(item, dict):
            drop("topic", item, "not an object")
            continue
        title = _clip(re.sub(r"[\[\]|]", " ", str(item.get("title") or "")), TITLE_CHARS)   # a note title and link label
        slug = _slug(item.get("slug") or item.get("title"))
        if not title or not SLUG_RE.fullmatch(slug) or slug in seen or slug == "index":
            drop("topic", {"title": title, "slug": slug}, "missing title, bad or duplicate slug")
            continue
        topic_spans = src.resolve(item.get("cite"))
        fields = {field: items(item.get(field), kind, extra) for field, kind, extra in _ITEM_FIELDS}
        explanation = items(item.get("explanation"), "explanation", ())
        quotes = []
        for q in _guarded_list(item.get("quotes"), "quote", drop):
            text = _guarded_text(q.get("text")) if isinstance(q, dict) else None
            if not text or '"' in text:
                drop("quote", q, "empty, over the guard, or contains a straight double quote", grounding.FAILED)
                continue
            spans = src.resolve(q.get("cite"))
            status = judge("quote", q, text, spans, quote=True)
            if status:
                quotes.append({"text": text, "speaker": _safe_label(q.get("speaker"), "Author"), "spans": offsets_cite(spans),
                               "status": status})
        evidence_spans = [s for group in [*fields.values(), explanation, quotes] for x in group for s in parse_offsets(x["spans"])]
        if not topic_spans and not evidence_spans:
            drop("topic", {"title": title, "slug": slug}, "no citation resolves to the source")
            continue
        if not topic_spans:
            drop("topic_citation", {"title": title, "cite": item.get("cite")}, "topic citation does not resolve; spans taken from its items")
        all_spans = merge_offsets([*(topic_spans or []), *evidence_spans])
        covered = merge_offsets([s for s in [*(topic_spans or []), *evidence_spans] if not src.is_whole(s)])
        one_line = _guarded_text(item.get("one_line")) or ""
        one_line_status = None
        if one_line:
            one_line_status = judge("one_line", {"title": title, "text": one_line}, one_line, all_spans)
            one_line = one_line if one_line_status else ""
        seen.add(slug)
        ktype = str(item.get("knowledge_type") or "reference")
        importance = item.get("importance") if item.get("importance") in (1, 2, 3) else 2
        topics.append({
            "title": title, "slug": slug, "knowledge_type": ktype if ktype in KNOWLEDGE_TYPES else "reference",
            "importance": importance, "spans": offsets_cite(all_spans), "covered": offsets_cite(covered) if covered else "",
            "one_line": one_line, "one_line_status": one_line_status,
            "questions": [x for x in (_guarded_text(v) for v in _guarded_list(item.get("questions"), "question", drop)) if x],
            "entity_names": [x for x in (_clip(v, 60) for v in _guarded_list(item.get("entities"), "topic_entity", drop))
                             if x and x.casefold() in entity_names],
            "explanation": explanation, "quotes": quotes, **fields,
            "uncertainty": [x for x in (_guarded_text(v) for v in _guarded_list(item.get("uncertainty"), "uncertainty", drop)) if x],
        })

    known = entity_names | {p.casefold() for p in participants}
    relationships = []
    for r in _guarded_list(raw.get("relationships"), "relationship", drop):
        subject, obj, pred = (_clip(r.get(k), 60) for k in ("subject", "object", "predicate")) if isinstance(r, dict) else ("", "", "")
        spans = src.resolve(r.get("cite")) if isinstance(r, dict) else None
        quote = _norm(r.get("quote")) if isinstance(r, dict) else ""
        if not (pred in RELATION_PREDICATES and subject.casefold() in known and obj.casefold() in known
                and subject.casefold() != obj.casefold()):
            drop("relationship", r, "endpoints or predicate not supported by the source")
            continue
        status, reason = grounding.evidence_status(quote, src.span_text(spans) if spans else None, quote=True)
        if status != grounding.EXACT:
            drop("relationship", r, reason, grounding.FAILED)
            continue
        words = grounding._word_set(src.span_text(spans))
        named = all(grounding._word_set(x) <= words for x in (subject, obj))
        status = grounding.SUPPORTED if named else grounding.CITED
        counts[status] += 1
        relationships.append({"subject": subject, "predicate": pred, "object": obj, "spans": offsets_cite(spans),
                              "quote": quote, "status": status})

    source_date = None
    d = raw.get("source_date")
    if isinstance(d, dict):
        spans, dtext, iso, quote = src.resolve(d.get("cite")), _clip(d.get("text"), 60), _clip(d.get("iso"), 10), _norm(d.get("quote"))
        try:
            dt.date.fromisoformat(iso)
            valid = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso))
        except ValueError:
            valid = False
        if (valid and dtext and spans and dtext.casefold() in quote.casefold()
                and grounding.evidence_status(quote, src.span_text(spans), quote=True)[0] == grounding.EXACT):
            source_date = {"text": dtext, "iso": iso, "spans": offsets_cite(spans), "quote": quote}
        else:
            drop("source_date", d, "date text not supported by an exact quote")
    cls = raw.get("source_class")
    clean = {
        "extraction_schema": EXTRACTION_REVISION, "offset_convention": OFFSET_CONVENTION,
        "document_title": _clip(raw.get("document_title"), 90), "authors": authors, "source_date": source_date,
        "relationships": relationships, "description": _guarded_text(raw.get("description")) or "",
        "source_class": cls if cls in SOURCE_CLASSES else "other", "document_kind": _clip(raw.get("document_kind"), 60),
        "participants": participants, "entities": entities, "topics": topics,
        "uncertainty": [x for x in (_guarded_text(v) for v in _guarded_list(raw.get("uncertainty"), "uncertainty", drop)) if x],
        "evidence": counts, "coverage": coverage_report(topics, src),
    }
    if not clean["description"] or not topics:
        raise InterpretationError("interpretation has no description or no source-supported topic")
    return clean, dropped


def _nonblank(ch: str) -> bool:
    return not ch.isspace() and ch != "﻿"


def coverage_report(topics: list[dict], src: SegmentedSource) -> dict:
    """Share of non-blank source characters inside spans cited by filed topics (whole-source references excluded)."""
    mask = bytearray(src.length)
    for t in topics:
        for a, b in parse_offsets(t.get("covered") or ""):
            mask[a:b] = b"\x01" * (b - a)
    total = covered = 0
    gaps: list[list[int]] = []
    for i, ch in enumerate(src.text):
        if not _nonblank(ch):
            continue
        total += 1
        if mask[i]:
            covered += 1
            continue
        if gaps and all(not _nonblank(c) for c in src.text[gaps[-1][1]:i]):
            gaps[-1][1] = i + 1
        else:
            gaps.append([i, i + 1])
    return {"nonblank_chars": total, "covered_chars": covered, "share": round(covered / total, 4) if total else 0.0,
            "uncovered": offsets_cite([(a, b) for a, b in gaps]) if gaps else "",
            "basis": "non-blank code points inside spans cited by filed topics; whole-source references excluded"}


# ----------------------------------------------------------------------------- Tier 1: one interpretation, persisted
def interpret(sha: str, *, brain_root: Path, root: Path = ROOT,
              llm: Callable[..., dict] | None = None, allow_model_call: bool = False) -> dict:
    """The saved interpretation for ``sha``; otherwise exactly one bounded Luna call, validated and saved first."""
    full, lines = sp.load_source(brain_root, sha)            # Tier 0: bytes re-hashed against the record
    saved = load_interpretation(root, full)
    if saved is not None:
        return {**saved, "reused": True}
    if not allow_model_call:
        raise InterpretationError("no saved interpretation and model calls are not allowed in this run")
    state = _check_attempt_allowed(root, full)
    # The account is chosen before the attempt is counted: no eligible account spends no attempt.
    account = select_luna_account() if llm is None else None
    header = (brain_root / RECORDS_RELATIVE / f"{full}.md").read_text(encoding="utf-8")
    title = (re.search(r'(?m)^title:\s*"?(.*?)"?\s*$', header) or [None, full])[1]
    _full, text = sp.load_text(brain_root, full)               # the offset basis: exact decoding, nothing removed
    src = SegmentedSource(text)
    prompt = PROMPT.replace("{title}", title).replace("{segmented}", src.model_view())
    state["paid_attempts"] += 1
    state["history"].append({"started": _now(), "outcome": "started"})
    _write_json(_attempts_path(root, full), state)           # counted before the call: a crash still counts
    try:
        call = llm(prompt, root=root, sha=full) if llm is not None else call_luna(prompt, root=root, sha=full, target=account)
    except RateLimited as exc:
        state["cooldown_until"] = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=COOLDOWN_SECONDS)
                                   ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        state["history"][-1].update(outcome="rate_limited", detail=str(exc)[:300])
        _write_json(_attempts_path(root, full), state)
        raise
    except Exception as exc:
        state["history"][-1].update(outcome="failed", detail=str(exc)[:300])
        _write_json(_attempts_path(root, full), state)
        raise
    try:
        usage_row = record_usage(call, root=root, sha=full) if call.get("scope") else {"recorded": False}
    except Exception as exc:                                  # accounting trouble never discards a paid response
        usage_row = {"recorded": False, "error": str(exc)[:300]}
    try:
        clean, dropped = validate_interpretation(parse_model_json(call["text"]), src)
    except InterpretationError as exc:
        _write_json(_state_file(root, full, f"rejected{state['paid_attempts']}"),
                    {"reason": str(exc), "raw_text": call["text"], "usage": call["usage"]})
        state["history"][-1].update(outcome="unusable", detail=str(exc)[:300], usage=call["usage"])
        _write_json(_attempts_path(root, full), state)
        raise
    saved = {"source_sha256": full, "contract": CONTRACT, "generated_at": _now(), "model": call["model"],
             "reasoning_effort": call.get("reasoning_effort"), "session_id": call["session_id"],
             "usage": call["usage"], "usage_ledger": {k: usage_row.get(k) for k in ("recorded", "invocation_id") if k in usage_row},
             "total_lines": len(lines), "total_chars": src.length, "total_segments": len(src.segments),
             "dropped": dropped, "canonical_truth": False, **clean}
    _write_json(interpretation_path(root, full), saved)       # persisted BEFORE any downstream write
    state["history"][-1].update(outcome="saved", usage=call["usage"])
    _write_json(_attempts_path(root, full), state)
    return {**saved, "reused": False}


def uncovered_ranges(topics: list[dict], lines: list[str]) -> list[tuple[int, int]]:
    covered = {i for t in topics for a, b in parse_ranges(t["lines"]) for i in range(a, b + 1)}
    out, start = [], None
    for i in range(1, len(lines) + 1):
        gap = i not in covered
        if gap and start is None:
            start = i
        if not gap and start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(lines)))
    return out


def revision(interp: dict) -> int:
    return int(interp.get("extraction_schema") or 1)


def select_topics(interp: dict) -> list[dict]:
    """Revision 3: every validated topic (importance only ranks). Revision 1-2 files keep their original selection:
    full notes for an internal meeting, the most important topics only for reference material."""
    topics = interp["topics"]
    if revision(interp) >= 3 or interp["source_class"] == "internal_meeting":
        return list(topics)
    ranked = sorted(topics, key=lambda t: (-t["importance"], parse_ranges(t["lines"])[0][0]))   # document order within a tier
    chosen = [t for t in ranked if t["importance"] >= 2][:MAX_SELECTIVE_NOTES] or ranked[:1]
    return [t for t in topics if t in chosen]


def person_keys(interp: dict) -> list[str]:
    keys = []
    for e in interp["entities"]:
        parts = [p for p in re.split(r"\s+", e["name"]) if p]
        if e["kind"] == "person" and len(parts) >= 2 and e["full_name_in_source"]:
            keys.append("person:" + _slug(e["name"]))
    return sorted(set(keys))


CARD_TOPIC_BUDGET = 4600        # bytes on the bounded Tier 1 card (INTERPRETATION_CARD_BYTE_CAP 9000); the source INDEX
CARD_ENTITY_BUDGET = 1300       # and the notes list everything, so these bound a navigation card, not what is filed
CARD_UNCERTAINTY_BUDGET = 900
CARD_KEY_BUDGET = 900


def topic_map_card_fields(interp: dict) -> dict:
    if revision(interp) >= 3:
        return _card_fields_rev3(interp)
    return {"description": interp["description"], "source_class": interp["source_class"],
            "document_kind": interp["document_kind"], "participants": interp["participants"],
            "entities": [e["name"] for e in interp["entities"]], "entity_keys": person_keys(interp),
            "topics": [{k: t[k] for k in ("title", "slug", "lines", "one_line", "knowledge_type", "importance")}
                       for t in interp["topics"]],
            "entity_evidence": [{"name": e["name"], "kind": e["kind"], "lines": e["lines"]} for e in interp["entities"]],
            "uncertainty": interp["uncertainty"], "model": interp["model"], "contract": interp["contract"],
            "total_lines": interp["total_lines"]}


# ----------------------------------------------------------------------------- Tier 2: v3 package from the same interpretation
def _indexable_slug(slug: str, prefix: str, fallback: str) -> str:
    """Drop words the search indexer excludes from a path (e.g. a slug word naming credentials); the title keeps them."""
    from tools import aos_indexer
    if not aos_indexer.is_excluded(f"{prefix}{slug}.md"):
        return slug
    kept = [w for w in slug.split("-") if w and not aos_indexer.is_excluded(f"{prefix}{w}.md")]
    safe = "-".join(kept)[:48].strip("-")
    return safe if safe and not aos_indexer.is_excluded(f"{prefix}{safe}.md") else fallback


def package_created_at(root: Path, sha: str, interp: dict) -> str:
    """The manifest timestamp is fixed on first build and reused, so a rebuilt package keeps its fingerprint."""
    path = _state_file(root, sha, "package")
    saved = _read_json(path)
    if saved and saved.get("created_at"):
        return str(saved["created_at"])
    value = str(interp.get("generated_at") or _now())[:19] + "Z"
    _write_json(path, {"created_at": value})
    return value


# ----------------------------------------------------------------------------- source identity (deterministic)
_NOISE_TAIL = {"transcript", "transcription", "notes", "summary", "recording", "captions", "subtitles"}
_NAME_RE = re.compile(r"[A-Z][\w'’.-]+(?:\s+[A-Z][\w'’.-]+){1,3}\Z")
_ISO_IN_NAME = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")


def filename_metadata(original_filename: str) -> dict:
    """What the FILENAME alone suggests (title, author candidate, date). It is never text evidence."""
    stem = re.sub(r"\.[A-Za-z0-9]{1,5}\Z", "", Path(str(original_filename)).name)
    parts = [p.strip() for p in re.split(r"\s+[-–—|]\s+", stem) if p.strip()]
    while len(parts) > 1 and parts[-1].casefold() in _NOISE_TAIL:
        parts.pop()
    author = ""
    if len(parts) >= 2 and _NAME_RE.fullmatch(parts[0]):
        author, parts = parts[0], parts[1:]
    date = ""
    match = _ISO_IN_NAME.search(stem)
    if match:
        try:
            date = dt.date(int(match[1]), int(match[2]), int(match[3])).isoformat()
        except ValueError:
            date = ""
    return {"title": " - ".join(parts) or stem, "author": author, "date": date}


def drive_provenance(brain_root: Path | None, sha: str) -> dict:
    """The Drive id/URL the watcher recorded for this source (sources/intake/drive/<id>.md), or {}."""
    folder = Path(brain_root) / "sources" / "intake" / "drive" if brain_root else None
    if not folder or not folder.is_dir():
        return {}
    pointer = f"{RECORDS_RELATIVE}/{sha}.md"
    for path in sorted(folder.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if pointer not in text:
            continue
        match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
        try:
            data = json.loads(match.group(1)) if match else {}
        except json.JSONDecodeError:
            continue
        if data.get("source_record", "").endswith(pointer) and str(data.get("url", "")).startswith("https://drive.google.com/"):
            return {"drive_id": data.get("drive_id", path.stem), "url": data["url"], "original_path": data.get("original_path", ""),
                    "provenance": f"sources/intake/drive/{path.stem}",
                    "revision": data.get("revision"), "drive_version": data.get("drive_version"),
                    "original_name": data.get("original_name"), "original_type": data.get("original_type"),
                    "original_sha256": data.get("original_sha256"), "original_size_bytes": data.get("original_size_bytes"),
                    "derivative_sha256": data.get("derivative_sha256"),
                    "derivative_size_bytes": data.get("derivative_size_bytes"), "conversion": data.get("conversion")}
    return {}


def source_identity(interp: dict, original_filename: str, lines: list[str], sha: str) -> dict:
    """Title, authors and date with their basis: ``text`` (quoted from the source) or ``filename`` (not verified in the text)."""
    fname = filename_metadata(original_filename)
    haystack = _norm("\n".join(lines)).casefold()
    title = interp.get("document_title") or ""
    if not title or title.casefold().startswith(("video transcript", "transcript")):
        title = fname["title"] if fname["title"] and fname["title"].casefold() not in _NOISE_TAIL else ""
    title = title or interp["description"].split(".")[0][:90].strip() or "Source"
    authors = [{"name": a["name"], "role": a["role"], "basis": "text", "lines": a.get("lines") or a.get("spans", "")}
               for a in interp.get("authors") or []]
    if not authors and fname["author"]:
        in_text = fname["author"].casefold() in haystack
        authors = [{"name": fname["author"], "role": "author", "basis": "text" if in_text else "filename", "lines": ""}]
    date = interp.get("source_date")
    if date:
        when = {"iso": date["iso"], "text": date["text"], "basis": "text", "lines": date.get("lines") or date.get("spans", "")}
    elif fname["date"]:
        when = {"iso": fname["date"], "text": fname["date"], "basis": "filename", "lines": ""}
    else:
        when = {"iso": "", "text": "unavailable", "basis": "none", "lines": ""}
    return {"title": title, "authors": authors, "date": when, "filename": original_filename}


def _folder(interp: dict, sha: str, identity: dict | None = None) -> str:
    """New interpretations name the folder by the document title (and a text-supported date); saved revision-1 ones keep
    their description-based folder so already-filed notes do not move."""
    if interp.get("document_title") and identity:
        base = _slug(identity["title"])[:44].strip("-") or "source"
        date = identity["date"]["iso"] if identity["date"]["basis"] == "text" else ""
        return f"sources/historical_calls/{date or 'undated'}-{base}-{sha[:8]}/"
    base = _slug(interp["description"])[:44].strip("-") or "source"
    return f"sources/historical_calls/undated-{base}-{sha[:8]}/"


def _frontmatter(fields: dict[str, str]) -> str:
    return "---\n" + "\n".join(f"{k}: {v}" for k, v in fields.items()) + "\n---\n"


def _q(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _spans_of(text: str) -> list[tuple[int, int]]:
    return parse_ranges(text) if text else []


def _overlaps(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> bool:
    return any(not (b1 < a2 or b2 < a1) for a1, b1 in a for a2, b2 in b)


def established_person_key(name: str) -> str | None:
    """An existing Entity Index id for this exact person name (single exact alias match), else None. Read-only."""
    try:
        from tools import aos_entity_index as index
        found = index.search_entities(name, limit=4)
    except Exception:                                   # no index yet: the derived key is used
        return None
    exact = [e for e in found.get("entities", []) if e.get("match") == "exact" and e.get("entity_type") in ("person", "prospect")]
    return exact[0]["entity_id"] if found.get("resolution") == "single" and len(exact) == 1 else None


def topic_entities(interp: dict, topic: dict, lines: list[str]) -> list[dict]:
    """Entities this topic is ABOUT. Revision 3: the model's own per-topic list only (validated against the supported
    entities), so on a one-line source an entity never attaches to every topic. Revision-2 files: the model's own per-topic
    list or evidence inside the topic's lines. Saved revision-1 files have no per-topic list, so: evidence inside the topic's
    lines, or named at least twice in its text (a single passing mention does not make a topic about an entity)."""
    if revision(interp) >= 3:
        named = {x.casefold() for x in topic.get("entity_names") or []}
        return [e for e in interp["entities"] if e["name"].casefold() in named]
    spans = _spans_of(topic["lines"])
    text = _norm("\n".join(lines[i - 1] for a, b in spans for i in range(a, b + 1))).casefold()
    named = {x.casefold() for x in topic.get("entity_names") or []}
    modern = (interp.get("extraction_schema") or 1) >= 2
    return [e for e in interp["entities"]
            if e["name"].casefold() in named or _overlaps(spans, _spans_of(e["lines"]))
            or (not modern and text.count(e["name"].casefold()) >= 2)]


def topic_keys(entities: list[dict], resolve: Callable[[str], str | None] | None) -> list[str]:
    keys = []
    for e in entities:
        if e["kind"] == "person" and len(e["name"].split()) >= 2 and e.get("full_name_in_source"):
            keys.append((resolve(e["name"]) if resolve else None) or "person:" + _slug(e["name"]))
    return sorted(set(keys))


def passage_refs(sha: str, topic: dict, lines: list[str], cache: dict, brain_root: Path | None) -> list[str]:
    """Stable ``source:<sha>#L..`` references for the topic's ranges, each re-verified against the preserved bytes."""
    refs = []
    for a, b in sorted(set(_spans_of(topic["lines"]))):
        if not 1 <= a <= b <= len(lines):
            raise InterpretationError(f"topic {topic['slug']} cites lines outside the source")
        ref = sp.passage_ref(sha, a, b)
        if brain_root is not None:
            checked = sp.read_passage(brain_root, ref, max_lines=1, cache=cache)
            if not checked["verified"] or checked["sha256"] != sha:
                raise InterpretationError(f"passage {ref} did not verify")
        refs.append(ref)
    return refs


def _related(topic: dict, selected: list[dict], ents: dict[str, list[str]], interp: dict, folder: str,
             cite: str = "lines") -> list[str]:
    """Relevant sibling notes: shared entities, a stated relationship between their entities, or document neighbours."""
    me = {x.casefold() for x in ents[topic["slug"]]}
    order = [t["slug"] for t in selected]
    pos = order.index(topic["slug"])
    scored = []
    for other in selected:
        if other["slug"] == topic["slug"]:
            continue
        theirs = {x.casefold() for x in ents[other["slug"]]}
        shared = sorted(me & theirs)
        linked = any((r["subject"].casefold() in me and r["object"].casefold() in theirs)
                     or (r["object"].casefold() in me and r["subject"].casefold() in theirs) for r in interp.get("relationships") or [])
        adjacent = abs(order.index(other["slug"]) - pos) == 1
        score = len(shared) + (2 if linked else 0) + (1 if adjacent else 0)
        if score:
            why = ("shares " + ", ".join(sorted({e["name"] for e in interp["entities"] if e["name"].casefold() in set(shared)})[:3])) if shared \
                else "has a stated relationship with this topic" if linked else "adjacent in the source"
            scored.append((-score, order.index(other["slug"]), other, why))
    return [f"- [[{folder}{o['slug']}|{o['title']}]] ({why}; {o[cite]})" for _s, _p, o, why in sorted(scored, key=lambda x: x[:2])[:4]]


def _cited_bullets(items: list[dict], label: str | None = None, keys: tuple[str, ...] = (), ordered: bool = False) -> list[str]:
    out = []
    for i, x in enumerate(items, 1):
        who = next((x[k] for k in keys if x.get(k)), "")
        prefix = f"{i}. " if ordered else "- "
        out.append(f"{prefix}{('**' + who + ':** ') if who else ''}{x['text']} ({x['lines']})")
    return out


def build_v3_package(interp: dict, *, sha: str, raw: bytes, original_filename: str, lines: list[str],
                     package_dir: Path, created_at: str = "", brain_root: Path | None = None,
                     resolve_key: Callable[[str], str | None] | None = None,
                     resolve_other: Callable[[str], str | None] | None = None) -> dict:
    """Write a v3 package for the selected topics into ``package_dir`` (not a READY directory)."""
    if revision(interp) >= 3:
        return _build_rev3(interp, sha=sha, raw=raw, original_filename=original_filename, lines=lines,
                           package_dir=package_dir, created_at=created_at, brain_root=brain_root,
                           resolve_key=resolve_key, resolve_other=resolve_other)
    interp = copy.deepcopy(interp)   # slugs may be adjusted for the indexer; the saved interpretation is not
    identity = source_identity(interp, original_filename, lines, sha)
    drive = drive_provenance(brain_root, sha)
    folder = _folder(interp, sha, identity)
    folder_slug = folder.rstrip("/").rsplit("/", 1)[1]
    selected, used = select_topics(interp), set()
    for number, topic in enumerate(interp["topics"], 1):      # on a copy: the saved interpretation is never edited
        safe = _indexable_slug(topic["slug"], folder, f"topic-{number}")
        while safe in used or safe == "index":
            safe = f"{safe}-{number}"
        used.add(safe)
        topic["slug"] = safe
    selected = [t for t in interp["topics"] if any(t["lines"] == s["lines"] and t["title"] == s["title"] for s in selected)]
    title_of = identity["title"]
    record_path = f"{RECORDS_RELATIVE}/{sha}"
    record = f"business_brain:{record_path}.md"
    cache: dict = {}
    keys = topic_keys(interp["entities"], resolve_key)
    entities = ", ".join(e["name"] for e in interp["entities"][:12])
    participants = "; ".join(interp["participants"]) if interp["source_class"] == "internal_meeting" else ""
    text_date = identity["date"]["iso"] if identity["date"]["basis"] == "text" else ""
    author_text = "; ".join(f"{a['name']} ({a['basis']})" for a in identity["authors"])
    identity_fm = {"source_title": _q(title_of), "original_filename": _q(original_filename),
                   **({"source_drive_url": _q(drive["url"])} if drive else {}),
                   **({"source_authors": _q(author_text)} if author_text else {}),
                   **({"source_date_filename": _q(identity["date"]["iso"])} if identity["date"]["basis"] == "filename" else {})}
    common = {"status": "historical-evidence", "participants_text": _q(participants), "source_date_text": _q(text_date or "unavailable")}
    ents = {t["slug"]: topic_entities(interp, t, lines) for t in interp["topics"]}
    names = {slug: [e["name"] for e in found] for slug, found in ents.items()}
    source_links = [f"[[{record_path}|Original source]]"] + ([f"[[{drive['provenance']}|Drive provenance]]"] if drive else [])

    def identity_block(refs: list[str], line_text: str) -> list[str]:
        rows = ["## Source", f"- Document: {title_of}", f"- Original file: `{original_filename}`"]
        if drive:
            rows.append(f"- Google Drive: {drive['url']}")
        rows.append("- Brain source: " + " · ".join(source_links) + f" (`{record}`)")
        for a in identity["authors"]:
            rows.append(f"- {a['role'].title()}: {a['name']} — " + ("stated in the text" + (f" ({a['lines']})" if a["lines"] else "")
                        if a["basis"] == "text" else "from the filename only; not stated in the text"))
        d = identity["date"]
        rows.append("- Date: " + ("not available" if d["basis"] == "none" else
                    f"{d['iso']} — " + (f"stated in the text ({d['lines']})" if d["basis"] == "text" else "from the filename only; not stated in the text")))
        rows.append(f"- Source SHA-256: `{sha}`")
        rows.append(f"- {line_text}: " + ", ".join(f"`{r}`" for r in refs))
        return rows + [""]

    notes: dict[str, str] = {}
    for t in selected:
        tents = ents[t["slug"]]
        tkeys = topic_keys(tents, resolve_key)
        refs = passage_refs(sha, t, lines, cache, brain_root)
        fm = _frontmatter({"id": f"ingest-{folder_slug}-{t['slug']}", "type": "historical_record",
                           "status": common["status"], "canonical_truth": "false",
                           "title": _q(f"{title_of}: {t['title']}"), "knowledge_type": t["knowledge_type"],
                           "participants_text": common["participants_text"],
                           **({"entity_key": "[" + ", ".join(tkeys) + "]"} if tkeys else {}),
                           "entities": _q(", ".join(e["name"] for e in tents)),
                           "source_sha256": sha, "source_record": record, "source_date_text": common["source_date_text"],
                           **identity_fm, "source_passages": _q("; ".join(refs))})
        body = [f"# {title_of}: {t['title']}", "",
                f"[[{folder}INDEX|Source index]] · " + " · ".join(source_links) + f" · Lines {t['lines']}",
                "Attributed historical evidence, not canonical TTROS truth.", "", *identity_block(refs, "Passages"),
                "## Summary", t["summary"] or t["one_line"], ""]
        if t["questions"]:
            body += ["## Questions this answers", *[f"- {x}" for x in t["questions"]], ""]
        for heading, field, kw in (("Principles", "principles", {}), ("Method (in the source's order)", "steps", {"ordered": True}),
                                   ("Decisions", "decisions", {"keys": ("who",)}), ("Actions and commitments", "actions", {"keys": ("owner",)}),
                                   ("Outcomes reported", "outcomes", {}), ("Claims (attributed, not verified)", "claims", {"keys": ("speaker",)})):
            if t.get(field):
                body += [f"## {heading}", *_cited_bullets(t[field], **kw), ""]
        if t["points"]:
            body += ["## Points", *[f"- **{p['speaker']}:** {p['text']} ({p['lines']})" for p in t["points"]], ""]
        if t["open_loops"]:
            body += ["## Commitments and open loops", *[f"- **Open:** {x}" for x in t["open_loops"]], ""]
        if t["quotes"]:
            body += ["## Key quotes", *[f"> \"{q['text']}\" ({q['speaker']}, {q['lines']})" for q in t["quotes"]], ""]
        mine = {n.casefold() for n in names[t["slug"]]}
        rels = [r for r in interp.get("relationships") or [] if r["subject"].casefold() in mine or r["object"].casefold() in mine]
        if rels:
            body += ["## Relationships stated in the source", *[f"- {r['subject']} — {r['predicate'].replace('_', ' ')} → {r['object']} ({r['lines']})" for r in rels],
                     "These are as stated by the source, not verified.", ""]
        if t.get("uncertainty"):
            body += ["## Uncertainty", *[f"- {x}" for x in t["uncertainty"]], ""]
        related = _related(t, selected, names, interp, folder)
        body += ["## Related notes in this source", *(related or ["- None beyond the [[" + folder + "INDEX|source index]]."]), ""]
        notes[t["slug"]] = fm + "\n".join(body).rstrip() + "\n"
    # source INDEX: identity, topic table, entities, relationships, coverage (every non-blank line declared), uncertainty
    rows = [f"| {i} | [[{folder}{t['slug']}|{t['title']}]] | {t['lines']} | {t['knowledge_type']} | {t['one_line']} |"
            for i, t in enumerate(selected, 1)]
    cover = [f"| {t['lines']} | topic {i} | {t['title']} |" for i, t in enumerate(selected, 1)]
    for a, b in uncovered_ranges(selected, lines):
        unfiled = [t["title"] for t in interp["topics"] if t not in selected and any(
            not (d < a or c > b) for c, d in parse_ranges(t["lines"]))]
        reason = ("Topic not filed as a note (lower importance): " + "; ".join(unfiled)) if unfiled else "No distinct topic recorded."
        cover.append(f"| {_cite([(a, b)])} | not filed | {reason[:200]} |")

    def where(e: dict) -> str:
        hits = [f"[[{folder}{t['slug']}|{t['title']}]]" for t in selected if e["name"] in names[t["slug"]]]
        return ", ".join(hits) or "not in a filed note"
    people = [f"| {e['name']} | {('person:' + _slug(e['name'])) if 'person:' + _slug(e['name']) in keys else ''} | {e['kind']} (see {e['lines']}) | {where(e)} |"
              for e in interp["entities"] if e["kind"] in {"person", "organization"}]
    others = [f"| {e['name']} | {e['kind']} (see {e['lines']}) | {where(e)} |" for e in interp["entities"] if e["kind"] not in {"person", "organization"}]
    index_fm = _frontmatter({"id": f"ingest-{folder_slug}-index", "type": "source_index", "status": common["status"],
                             "canonical_truth": "false", "title": _q(title_of), "knowledge_type": "source_index",
                             "participants_text": common["participants_text"],
                             **({"entity_key": "[" + ", ".join(keys) + "]"} if keys else {}), "entities": _q(entities),
                             "source_sha256": sha, "source_record": record, "source_date_text": common["source_date_text"],
                             **identity_fm})
    index_body = [f"# {title_of} (index)", "",
                  " · ".join(source_links) + f" · {original_filename} · {len(lines)} lines · " + (identity["date"]["iso"] or "unavailable"),
                  "", *identity_block([sp.passage_ref(sha, 1, len(lines))], "Whole source"),
                  "## Summary", interp["description"], "", "## Topics",
                  "| # | Topic | Lines | Type | One line |", "|---|---|---|---|---|", *rows, ""]
    if people:
        index_body += ["## People", "| Name | Entity key | Role as stated | Topics |", "|---|---|---|---|", *people, ""]
    if others:
        index_body += ["## Other entities", "| Name | Kind | Topics |", "|---|---|---|", *others, ""]
    if interp.get("relationships"):
        index_body += ["## Relationships stated in the source", *[
            f"- {r['subject']} — {r['predicate'].replace('_', ' ')} → {r['object']} ({r['lines']})" for r in interp["relationships"]],
            "Suggested cross-document links are derived separately and stay suggestions.", ""]
    index_body += ["## Coverage", "| Lines | Where | Note |", "|---|---|---|", *cover, ""]
    if interp["uncertainty"]:
        index_body += ["## To check against existing memory", *[f"- {x}" for x in interp["uncertainty"]], ""]
    built = _write_package(package_dir, sha=sha, raw=raw, original_filename=original_filename,
                           index_text=index_fm + "\n".join(index_body), notes=notes, folder=folder, folder_slug=folder_slug,
                           created_at=created_at or str(interp.get("generated_at") or _now())[:19] + "Z")
    return {**built, "topics_filed": [t["slug"] for t in selected],
            "topics_not_filed": [t["slug"] for t in interp["topics"] if t not in selected]}


def _write_package(package_dir: Path, *, sha: str, raw: bytes, original_filename: str, index_text: str, notes: dict[str, str],
                   folder: str, folder_slug: str, created_at: str) -> dict:
    """The package files and frozen v3 manifest (the same bytes for every revision)."""
    notes_dir, source_dir = package_dir / "notes", package_dir / "source"
    shutil.rmtree(package_dir, ignore_errors=True)
    notes_dir.mkdir(parents=True)
    source_dir.mkdir(parents=True)
    suffix = Path(original_filename).suffix or ".txt"
    files = {f"source/original{suffix}": raw, "notes/INDEX.md": index_text.encode()}
    for slug, text in notes.items():
        files[f"notes/{slug}.md"] = text.encode()
    for name, data in files.items():
        (package_dir / name).write_bytes(data)
    digest = lambda b: hashlib.sha256(b).hexdigest()
    outputs = [{"role": "source_index" if n == "notes/INDEX.md" else "topic", "package_path": n,
                "target_path": folder + n.split("/", 1)[1], "sha256": digest(d), "size_bytes": len(d)}
               for n, d in files.items() if n.startswith("notes/")]
    manifest = {"schema_version": "3.0", "transport_mode": "directory-v1", "status": "ready_for_ttros",
                "package_id": f"MI-{folder_slug}"[:120], "created_at": created_at,   # stable: same output, same fingerprint
                "source": {"original_filename": original_filename, "package_path": f"source/original{suffix}",
                           "sha256": sha, "size_bytes": len(raw), "conversion": "none"},
                "source_folder": folder, "outputs": outputs,
                "package_files": [{"path": n, "sha256": digest(d), "size_bytes": len(d)} for n, d in files.items()]}
    (package_dir / "INGEST_MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"package": package_dir, "folder": folder}


# ----------------------------------------------------------------------------- revision 3 rendering
ORG_PRODUCT_KEY_TYPES = frozenset({"organization", "company", "client", "prospect", "product", "offer", "project"})
GRAPH_MARKER = "source-graph"          # TTROS:MACHINE block read by dashboard/backend/business_brain_graph.py
GRAPH_SCHEMA = sp.SOURCE_GRAPH_SCHEMA
_STATUS_MARK = {grounding.CITED: " " + grounding.UNVERIFIED_MARK}


def established_entity_key(name: str) -> str | None:
    """The existing Entity Index id of an organisation or product: exactly one exact alias match that is not a derived
    topic, of an organisation/product-like type. No partial-name merge; read-only; None when absent or ambiguous."""
    try:
        from tools import aos_entity_index as index
        found = index.search_entities(name, limit=8)
    except Exception:
        return None
    exact = [e for e in found.get("entities", []) if e.get("match") == "exact" and e.get("entity_type") != "topic"]
    return exact[0]["entity_id"] if len(exact) == 1 and exact[0].get("entity_type") in ORG_PRODUCT_KEY_TYPES else None


def entity_key_for(e: dict, resolve_person: Callable[[str], str | None] | None,
                   resolve_other: Callable[[str], str | None] | None) -> tuple[str, bool]:
    """(identity key, declare as entity_key). A person key needs a full name stated in the source (an exact established
    Entity Index id wins); an organisation/product key only an exact, unambiguous established id. Anything else keeps the
    ``topic:<slug>`` identity the Entity Index already derives from the note's ``entities`` metadata, so no second identity
    is ever created for the same entity."""
    if e["kind"] == "person" and len(e["name"].split()) >= 2 and e.get("full_name_in_source"):
        return (resolve_person(e["name"]) if resolve_person else None) or "person:" + _slug(e["name"]), True
    if e["kind"] in {"organization", "product"} and resolve_other:
        known = resolve_other(e["name"])
        if known:
            return known, True
    return "topic:" + _slug(e["name"]), False


def _fit(rows: list, cost: Callable[[Any], int], budget: int) -> list:
    out, used = [], 0
    for row in rows:
        if used + cost(row) > budget:
            break
        used += cost(row)
        out.append(row)
    return out


def _card_fields_rev3(interp: dict) -> dict:
    """The bounded Tier 1 card (a navigation summary under a hard byte cap): as many topics, entities and uncertainty
    lines as fit their budgets, in order. The source INDEX and notes carry everything; nothing here is a filing quota."""
    rows = [{"title": t["title"], "slug": t["slug"], "lines": ", ".join(t["spans"].split(", ")[:2]), "one_line": t["one_line"],
             "knowledge_type": t["knowledge_type"], "importance": t["importance"]} for t in interp["topics"]]
    # a row renders each span as source:<64-hex sha>#C.. (about 80 B), plus title, type and one line
    topics = _fit(rows, lambda r: (len(r["title"][:80].encode()) + len(r["one_line"][:140].encode()) + 50
                                   + sum(73 + len(part) for part in r["lines"].split(", "))), CARD_TOPIC_BUDGET)
    evidence = _fit([{"name": e["name"], "kind": e["kind"], "lines": e["spans"].split(", ")[0]} for e in interp["entities"]],
                    lambda e: len(e["name"][:60].encode()) + len(e["lines"][:40]) + 30, CARD_ENTITY_BUDGET)
    uncertainty = _fit(interp["uncertainty"], lambda u: len(u[:240].encode()) + 4, CARD_UNCERTAINTY_BUDGET)
    return {"description": interp["description"], "source_class": interp["source_class"],
            "document_kind": interp["document_kind"], "participants": interp["participants"][:8],
            "entities": [e["name"] for e in interp["entities"]],
            "entity_keys": _fit(person_keys(interp), lambda k: len(k) + 2, CARD_KEY_BUDGET), "topics": topics,
            "entity_evidence": evidence, "uncertainty": uncertainty, "model": interp["model"], "contract": interp["contract"],
            "total_lines": interp["total_lines"]}


def _cell(text: Any) -> str:
    return " ".join(str(text or "").replace("|", "/").split())


def _mark(item: dict) -> str:
    return f"({item['spans']}{_STATUS_MARK.get(item.get('status'), '')})"


def _graph_name(name: str) -> str:
    return " ".join(re.sub(r"[\[\]|]", " ", name).split())


def _build_rev3(interp: dict, *, sha: str, raw: bytes, original_filename: str, lines: list[str], package_dir: Path,
                created_at: str, brain_root: Path | None, resolve_key, resolve_other) -> dict:
    """Revision-3 notes: knowledge first, compact offset citations with an evidence marker, the Source block last."""
    interp = copy.deepcopy(interp)
    identity = source_identity(interp, original_filename, lines, sha)
    drive = drive_provenance(brain_root, sha)
    folder = _folder(interp, sha, identity)
    folder_slug = folder.rstrip("/").rsplit("/", 1)[1]
    used: set[str] = set()
    for number, topic in enumerate(interp["topics"], 1):
        safe = _indexable_slug(topic["slug"], folder, f"topic-{number}")
        while safe in used or safe == "index":
            safe = f"{safe}-{number}"
        used.add(safe)
        topic["slug"] = safe
    selected = select_topics(interp)
    for t in selected:                         # navigation lines name a topic's first span only
        first, *rest = t["spans"].split(", ")
        t["lead_span"] = first + (" …" if rest else "")
    title_of = identity["title"]
    record_path = f"{RECORDS_RELATIVE}/{sha}"
    record = f"business_brain:{record_path}.md"
    total_chars = int(interp.get("total_chars") or len(raw.decode("utf-8")))
    keyed = {e["name"].casefold(): entity_key_for(e, resolve_key, resolve_other) for e in interp["entities"]}
    keys = sorted({k for k, declare in keyed.values() if declare})
    participants = "; ".join(interp["participants"]) if interp["source_class"] == "internal_meeting" else ""
    text_date = identity["date"]["iso"] if identity["date"]["basis"] == "text" else ""
    author_text = "; ".join(f"{a['name']} ({a['basis']})" for a in identity["authors"])
    identity_fm = {"source_title": _q(title_of), "original_filename": _q(original_filename),
                   **({"source_drive_url": _q(drive["url"])} if drive else {}),
                   **({"source_authors": _q(author_text)} if author_text else {}),
                   **({"source_date_filename": _q(identity["date"]["iso"])} if identity["date"]["basis"] == "filename" else {})}
    common = {"status": "historical-evidence", "participants_text": _q(participants), "source_date_text": _q(text_date or "unavailable")}
    ents = {t["slug"]: topic_entities(interp, t, lines) for t in interp["topics"]}
    names = {slug: [e["name"] for e in found] for slug, found in ents.items()}
    source_links = [f"[[{record_path}|Original source]]"] + ([f"[[{drive['provenance']}|Drive provenance]]"] if drive else [])
    legend = (f"Attributed historical evidence, not canonical TTROS truth. `C<a>-<b>`: character offsets in the original; "
              f"{grounding.UNVERIFIED_MARK}: support not confirmed.")
    cache: dict = {}

    def refs_for(spans_text: str) -> list[str]:
        out = []
        for a, b in parse_offsets(spans_text):
            if not 0 <= a < b <= total_chars:
                raise InterpretationError(f"citation C{a}-{b} is outside the source")
            ref = sp.offset_ref(sha, a, b)
            if brain_root is not None:
                checked = sp.read_passage(brain_root, ref, cache=cache)
                if not checked["verified"] or checked["sha256"] != sha:
                    raise InterpretationError(f"passage {ref} did not verify")
            out.append(ref)
        return out

    def identity_block(refs: list[str], label: str) -> list[str]:
        rows = ["## Source", f"- Document: {title_of}", f"- Original file: `{original_filename}`"]
        if drive:
            rows.append(f"- Google Drive: {drive['url']}")
        rows.append("- Brain source: " + " · ".join(source_links) + f" (`{record}`)")
        for a in identity["authors"]:
            rows.append(f"- {a['role'].title()}: {a['name']} — " + ("stated in the text" + (f" ({a['lines']})" if a["lines"] else "")
                        if a["basis"] == "text" else "from the filename only; not stated in the text"))
        d = identity["date"]
        rows.append("- Date: " + ("not available" if d["basis"] == "none" else
                    f"{d['iso']} — " + (f"stated in the text ({d['lines']})" if d["basis"] == "text" else "from the filename only; not stated in the text")))
        rows.append(f"- Source SHA-256: `{sha}`")
        rows.append(f"- {label}: " + ", ".join(f"`{r}`" for r in refs))
        return rows + [""]

    def rel_line(r: dict) -> str:
        return f"- {r['subject']} — {r['predicate'].replace('_', ' ')} → {r['object']} {_mark(r)}"

    def stating_topic(r: dict) -> str | None:
        """The ONE note that states a relationship: the first filed topic whose spans contain its cited span and that
        names an endpoint, then any whose spans contain it, then the first naming both endpoints, then either."""
        rel_spans = parse_offsets(r["spans"])
        ends = {r["subject"].casefold(), r["object"].casefold()}

        def contains(t: dict) -> bool:
            mine = merge_offsets(parse_offsets(t["spans"]))
            return all(any(a <= x and y <= b for a, b in mine) for x, y in rel_spans)
        named = lambda t, k: len(ends & {n.casefold() for n in names[t["slug"]]}) >= k
        for rule in (lambda t: contains(t) and named(t, 1), contains, lambda t: named(t, 2), lambda t: named(t, 1)):
            hit = next((t["slug"] for t in selected if rule(t)), None)
            if hit:
                return hit
        return None
    stated_in = {id(r): stating_topic(r) for r in interp.get("relationships") or []}

    def graph_block(entities, relationships) -> list[str]:
        rows = []
        original = raw.decode("utf-8")
        for relation in relationships:
            spans = parse_offsets(relation["spans"])
            if not spans:
                continue
            refs = [sp.offset_ref(sha, start, end) for start, end in spans]
            quoted = r"\s+".join(re.escape(word) for word in relation["quote"].split())
            exact = re.search(quoted, "\n".join(original[start:end] for start, end in spans)) if quoted else None
            rows.append({
                "subject": _graph_name(relation["subject"]), "predicate": relation["predicate"],
                "object": _graph_name(relation["object"]),
                "subject_key": (keyed.get(relation["subject"].casefold()) or ("topic:" + _slug(relation["subject"]),))[0],
                "object_key": (keyed.get(relation["object"].casefold()) or ("topic:" + _slug(relation["object"]),))[0],
                "ref": refs[0], "refs": refs, "status": relation["status"] if exact else grounding.CITED,
                **({"quote": exact[0]} if exact else {}),
            })
        graph = {"schema": GRAPH_SCHEMA, "authority": "historical-evidence", "canonical_truth": False,
                 "stated_by": "source", "source_sha256": sha, "relationships": rows,
                 "about": [{"key": keyed[e["name"].casefold()][0], "name": _graph_name(e["name"]),
                            "kind": e["kind"]} for e in entities]}
        return [f"<!-- TTROS:MACHINE:{GRAPH_MARKER}:BEGIN -->", json.dumps(graph, ensure_ascii=False, sort_keys=True),
                f"<!-- TTROS:MACHINE:{GRAPH_MARKER}:END -->", ""]

    notes: dict[str, str] = {}
    for t in selected:
        tents = ents[t["slug"]]
        tkeys = sorted({keyed[e["name"].casefold()][0] for e in tents if keyed[e["name"].casefold()][1]})
        refs = refs_for(t["spans"])
        statuses = [x["status"] for f in ("explanation", "quotes", *(f for f, _k, _e in _ITEM_FIELDS)) for x in t.get(f) or []]
        if t.get("one_line_status"):
            statuses.append(t["one_line_status"])
        fm = _frontmatter({"id": f"ingest-{folder_slug}-{t['slug']}", "type": "historical_record",
                           "status": common["status"], "canonical_truth": "false",
                           "title": _q(f"{title_of}: {t['title']}"), "knowledge_type": t["knowledge_type"],
                           "participants_text": common["participants_text"],
                           **({"entity_key": "[" + ", ".join(tkeys) + "]"} if tkeys else {}),
                           "entities": _q(", ".join(e["name"] for e in tents)),
                           "source_sha256": sha, "source_record": record, "source_date_text": common["source_date_text"],
                           **identity_fm, "source_passages": _q("; ".join(refs)), "extraction_schema": "3",
                           "importance": str(t["importance"]),
                           "evidence_status": _q("; ".join(f"{k}={statuses.count(k)}" for k in _STATUS_ORDER[:3]))})
        body = [f"# {title_of}: {t['title']}", "",
                f"[[{folder}INDEX|Source index]] · " + " · ".join(source_links) + f" · Spans {t['spans']}", legend, ""]
        if t["one_line"]:
            body += ["## Summary", f"{t['one_line']} ({t['spans']}{_STATUS_MARK.get(t.get('one_line_status'), '')})", ""]
        if t["explanation"]:
            body += ["## Explanation"]
            for para in t["explanation"]:
                body += [f"{para['text']} {_mark(para)}", ""]
        for heading, field, kw in (("Principles", "principles", {}), ("Method (in the source's order)", "steps", {"ordered": True}),
                                   ("Decisions", "decisions", {"keys": ("who",)}), ("Actions and commitments", "actions", {"keys": ("owner",)}),
                                   ("Outcomes reported", "outcomes", {}), ("Claims (attributed, not verified)", "claims", {"keys": ("speaker",)}),
                                   ("Open loops", "open_loops", {})):
            if t.get(field):
                body += [f"## {heading}"]
                for i, x in enumerate(t[field], 1):
                    who = next((x[k] for k in kw.get("keys", ()) if x.get(k)), "")
                    prefix = f"{i}. " if kw.get("ordered") else "- "
                    body.append(f"{prefix}{('**' + who + ':** ') if who else ''}{x['text']} {_mark(x)}")
                body.append("")
        if t["quotes"]:
            body += ["## Key quotes", *[f"> \"{q['text']}\" ({q['speaker']}, {q['spans']})" for q in t["quotes"]], ""]
        mine = {n.casefold() for n in names[t["slug"]]}
        rels = [r for r in interp.get("relationships") or [] if r["subject"].casefold() in mine or r["object"].casefold() in mine]
        if rels:
            body += ["## Relationships stated in the source", *[rel_line(r) for r in rels],
                     "These are as stated by the source, not verified.", ""]
        if t.get("uncertainty"):
            body += ["## Uncertainty", *[f"- {x}" for x in t["uncertainty"]], ""]
        if t["questions"]:
            body += ["## Questions this answers", *[f"- {x}" for x in t["questions"]], ""]
        related = _related(t, selected, names, interp, folder, cite="lead_span")
        body += ["## Related notes in this source", *(related or ["- None beyond the [[" + folder + "INDEX|source index]]."]), ""]
        stated = [r for r in interp.get("relationships") or [] if stated_in[id(r)] == t["slug"]]
        if tents or stated:
            body += graph_block(tents, stated)
        body += identity_block(refs, "Passages")
        notes[t["slug"]] = fm + "\n".join(body).rstrip() + "\n"

    # source INDEX: knowledge first, then evidence and coverage, the Source block last
    rows = [f"| {i} | [[{folder}{t['slug']}|{t['title']}]] | {t['spans']} | {t['knowledge_type']} | {t['importance']} | {_cell(t['one_line'])} |"
            for i, t in enumerate(selected, 1)]

    def where(e: dict) -> str:
        hits = [f"[[{folder}{t['slug']}|{t['title']}]]" for t in selected if e["name"] in names[t["slug"]]]
        return ", ".join(hits) or "not in a filed note"
    people = [f"| {e['name']} | {keyed[e['name'].casefold()][0] if keyed[e['name'].casefold()][1] else ''} | {e['kind']} (see {e['spans']}) | {where(e)} |"
              for e in interp["entities"] if e["kind"] in {"person", "organization"}]
    others = [f"| {e['name']} | {e['kind']} (see {e['spans']}) | {where(e)} |" for e in interp["entities"] if e["kind"] not in {"person", "organization"}]
    counts = interp.get("evidence") or {}
    cov = interp.get("coverage") or coverage_report(selected, SegmentedSource(raw.decode("utf-8")))
    cover = [f"| {t['covered']} | topic {i} | {_cell(t['title'])} |" for i, t in enumerate(selected, 1) if t.get("covered")]
    cover += [f"| {span} | not filed | No filed topic cites this range. |" for span in cov["uncovered"].split(", ") if span]
    index_fm = _frontmatter({"id": f"ingest-{folder_slug}-index", "type": "source_index", "status": common["status"],
                             "canonical_truth": "false", "title": _q(title_of), "knowledge_type": "source_index",
                             "participants_text": common["participants_text"],
                             **({"entity_key": "[" + ", ".join(keys) + "]"} if keys else {}),
                             "entities": _q(", ".join(e["name"] for e in interp["entities"])),
                             "source_sha256": sha, "source_record": record, "source_date_text": common["source_date_text"],
                             **identity_fm, "extraction_schema": "3", "coverage_share": str(cov["share"])})
    index_body = [f"# {title_of} (index)", "",
                  " · ".join(source_links) + f" · {original_filename} · {len(lines)} lines · {total_chars} characters · "
                  + (identity["date"]["iso"] or "unavailable"), legend, "",
                  "## Summary", interp["description"], "", "## Topics",
                  "| # | Topic | Spans | Type | Importance | One line |", "|---|---|---|---|---|---|", *rows, ""]
    if people:
        index_body += ["## People", "| Name | Entity key | Role as stated | Topics |", "|---|---|---|---|", *people, ""]
    if others:
        index_body += ["## Other entities", "| Name | Kind | Topics |", "|---|---|---|", *others, ""]
    if interp.get("relationships"):
        index_body += ["## Relationships stated in the source", *[rel_line(r) for r in interp["relationships"]],
                       "Suggested cross-document links are derived separately and stay suggestions.", ""]
    unplaced = [r for r in interp.get("relationships") or [] if stated_in[id(r)] is None]
    if unplaced:
        index_body += graph_block([], unplaced)
    index_body += ["## Evidence",
                   f"- Items: exact quotes {counts.get('exact', 0)} · supported {counts.get('supported', 0)} · "
                   f"cited only, unverifiable {counts.get('cited', 0)} · failed and not filed {counts.get('failed', 0)}",
                   f"- Coverage: {cov['share'] * 100:.1f}% of {cov['nonblank_chars']} non-blank characters are cited by filed "
                   "topics; whole-source references do not count.", "",
                   "## Coverage", "| Span | Where | Note |", "|---|---|---|", *cover, ""]
    if interp["uncertainty"]:
        index_body += ["## To check against existing memory", *[f"- {x}" for x in interp["uncertainty"]], ""]
    index_body += identity_block([sp.offset_ref(sha, 0, total_chars)], "Whole source (not coverage)")
    built = _write_package(package_dir, sha=sha, raw=raw, original_filename=original_filename,
                           index_text=index_fm + "\n".join(index_body).rstrip() + "\n", notes=notes, folder=folder,
                           folder_slug=folder_slug, created_at=created_at or str(interp.get("generated_at") or _now())[:19] + "Z")
    return {**built, "topics_filed": [t["slug"] for t in selected], "topics_not_filed": [],
            "evidence": counts, "coverage": cov}


# ----------------------------------------------------------------------------- orchestration
_DRIVE_ID_KEYS = {"drive_id", "original_drive_id", "source_drive_id", "original_source_id", "provider_id"}
CHATGPT_PREPARED_ICM_FOLDER_ID = "18B65sUAbrxz0ou9Zz4MbXr39INwGNtSb"


def _prepared_drive_ids(value: Any) -> set[str]:
    """Collect explicit Drive identities from package metadata without inferring from titles."""
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in _DRIVE_ID_KEYS and isinstance(item, str) and item:
                found.add(item)
            else:
                found.update(_prepared_drive_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_prepared_drive_ids(item))
    return found


def _prepared_icm_candidates(root: Path, brain_root: Path, sha: str) -> tuple[list[Path], bool]:
    """Return exact identity+SHA packages and whether any SHA-only/uncertain candidate exists."""
    provenance = drive_provenance(brain_root, sha)
    drive_id = provenance.get("drive_id")
    candidates: list[Path] = []
    uncertain = False
    exchange_roots = [root / "queue/memory_exchange/03_READY_FOR_TTROS",
                      root / "queue/memory_exchange/04_IMPORTED"]
    for variable in ("TTROS_MEMORY_READY", "TTROS_MEMORY_IMPORTED"):
        configured = os.environ.get(variable)
        if configured:
            exchange_roots.append(Path(configured))
    for base in exchange_roots:
        if not base.is_dir():
            continue
        for manifest_path in base.rglob("INGEST_MANIFEST.json"):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                source = manifest.get("source") or {}
                if source.get("sha256") != sha:
                    continue
                ids = _prepared_drive_ids(manifest)
                if drive_id and ids == {str(drive_id)}:
                    candidates.append(manifest_path.parent)
                else:
                    uncertain = True
            except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
                continue
    # A package cache entry is eligible only when its manifest names the configured
    # ChatGPT Exchange folder; arbitrary semantic cache entries are not treated as prepared ICM.
    cache_root = root / PACKAGE_RELATIVE
    if cache_root.is_dir():
        for manifest_path in cache_root.rglob("INGEST_MANIFEST.json"):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                source = manifest.get("source") or {}
                if source.get("sha256") != sha:
                    continue
                if manifest.get("prepared_package_folder_id") != CHATGPT_PREPARED_ICM_FOLDER_ID:
                    continue
                ids = _prepared_drive_ids(manifest)
                if drive_id and ids == {str(drive_id)}:
                    candidates.append(manifest_path.parent)
                else:
                    uncertain = True
            except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
                continue
    return candidates, uncertain


def run(sha: str, *, brain_root: Path = BUSINESS_BRAIN_ROOT, root: Path = ROOT, tier2: bool = True,
        allow_model_call: bool = False, llm: Callable[..., dict] | None = None, commit: bool = True) -> dict:
    """Tier 0 verify -> Tier 1 (interpretation saved, then card) -> Tier 2 (v3 import). Safe to re-run."""
    from tools import memory_exchange_import as mei
    from tools import source_intake
    out: dict[str, Any] = {"source": sha}
    full, lines = sp.load_source(brain_root, sha)
    out["tier0"] = {"sha256": full, "verified": True, "lines": len(lines)}
    prepared, uncertain = _prepared_icm_candidates(Path(root), Path(brain_root), full)
    if len(prepared) > 1 or (not prepared and uncertain):
        out["prepared_icm"] = {"status": "held", "reason": "ambiguous source identity or package match"}
        return out
    if prepared:
        package = prepared[0]
        try:
            mei.validate_v3(package, Path(brain_root))
            receipt = mei.process_v3(package, brain=Path(brain_root), root=Path(root), dry_run=not commit,
                                     route="prepared_icm")
        except Exception as exc:
            out["prepared_icm"] = {"status": "held", "reason": f"package validation/import failed: {type(exc).__name__}"}
            return out
        out["prepared_icm"] = {"status": "reused", "package": package.name,
                               "outcome": receipt.get("outcome"), "reason": receipt.get("reason"),
                               "search": (receipt.get("search_refresh") or {}).get("status"),
                               "graphify": (receipt.get("graphify_refresh") or {}).get("status"),
                               "stages": {k: (v or {}).get("status") for k, v in (receipt.get("stages") or {}).items()}}
        return out
    # The importer ledger is the durable record of prepared ICM. A source it shows arriving as prepared ICM is
    # never reinterpreted, even when its package folder is no longer reachable. Rows predating route provenance
    # ("unrecorded") count as prepared unless TTROS holds its own saved Luna interpretation for this exact source.
    imported = mei.prepared_source_imports(Path(root), full)
    explicit = [row for row in imported if row["route"] == "prepared_icm"]
    if explicit or (imported and load_interpretation(Path(root), full) is None):
        out["prepared_icm"] = {"status": "held", "reason": "source was imported from prepared ICM; "
                               "Luna reinterpretation is refused", "packages": sorted({r["package_id"] for r in imported})}
        return out
    interp = interpret(full, brain_root=brain_root, root=root, llm=llm, allow_model_call=allow_model_call)
    out["interpretation"] = {"reused": interp["reused"], "topics": len(interp["topics"]),
                             "entities": len(interp["entities"]), "source_class": interp["source_class"],
                             "usage": interp["usage"], "model": interp["model"]}
    record = brain_root / RECORDS_RELATIVE / f"{full}.md"
    payload = {"claims": [], "card": {}, "model": interp["model"], "interpretation": topic_map_card_fields(interp)}
    result = source_intake.run_intake(record, brain_root=brain_root, repo_root=root, mode="semantic",
                                      semantic_payload=payload, commit=commit)
    out["tier1"] = {"written": list(result.imported_pointers), "already_present": list(result.duplicate_pointers),
                    "search": result.search_status, "graphify": result.graphify_status, "commit": result.brain_commit}
    if tier2:
        text = record.read_text(encoding="utf-8")
        original = (re.search(r'(?m)^original_filename:\s*"?(.*?)"?\s*$', text) or [None, "original.txt"])[1]
        raw = source_intake.extract_exact_bytes(text)
        built = build_v3_package(interp, sha=full, raw=raw, original_filename=original, lines=lines,
                                 package_dir=root / PACKAGE_RELATIVE / full[:16],
                                 created_at=package_created_at(root, full, interp), brain_root=brain_root,
                                 resolve_key=established_person_key, resolve_other=established_entity_key)
        drive = drive_provenance(brain_root, full)
        semantic = {"status": "success", "model": interp.get("model"), "reused": bool(interp.get("reused")),
                    "generated_at": interp.get("generated_at"), "usage": interp.get("usage"),
                    "state_ref": interpretation_path(Path("."), full).as_posix()}
        hint = {"external_id": drive.get("drive_id"), "original_location": drive.get("url"),
                "origin": "google_drive" if drive else None, "external_version": drive.get("revision"),
                "drive_version": drive.get("drive_version"), "original_type": drive.get("original_type"),
                "original_sha256": drive.get("original_sha256"), "original_size_bytes": drive.get("original_size_bytes"),
                "conversion": drive.get("conversion")}
        receipt = mei.process_v3(built["package"], brain=brain_root, root=root, route="luna_semantic",
                                 semantic=semantic, origin_hint=hint)
        out["tier2"] = {"outcome": receipt.get("outcome"), "reason": receipt.get("reason"), "folder": built["folder"],
                        "filed": built["topics_filed"], "not_filed": built["topics_not_filed"],
                        "search": (receipt.get("search_refresh") or {}).get("status"),
                        "graphify": (receipt.get("graphify_refresh") or {}).get("status"),
                        "commit": receipt.get("commit"),
                        **({"evidence": built["evidence"], "coverage": built["coverage"]["share"]} if "evidence" in built else {})}
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Three-tier ingestion of one preserved source (never run by a timer)")
    parser.add_argument("sha", help="source SHA-256 or unique prefix (at least 8 characters)")
    parser.add_argument("--brain-root", type=Path, default=BUSINESS_BRAIN_ROOT)
    parser.add_argument("--no-tier2", action="store_true")
    parser.add_argument("--allow-model-call", action="store_true",
                        help="permit ONE bounded Luna call when no saved interpretation exists")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(run(args.sha, brain_root=args.brain_root, tier2=not args.no_tier2,
                             allow_model_call=args.allow_model_call), indent=2, default=str))
    except SourceIntakeError as exc:
        print(f"source interpretation stopped: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
