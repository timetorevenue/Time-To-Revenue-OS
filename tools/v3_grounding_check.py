#!/usr/bin/env python3
"""Read-only source-grounding validator for Memory Ingest v3 source folders.

It answers one narrow question: do the line citations, quotations, explicit
speaker labels and declared coverage in a v3 source folder agree with the exact
recorded source bytes?  It does NOT judge whether a summary, point or topic is a
*correct reading* of the source; a note can be perfectly grounded and still
misread what was meant.  Every report says so (``"semantic_correctness":
"not assessed"``).

Line convention (documented, 1-based): the exact source bytes are decoded as UTF-8
(a leading BOM is dropped), split on ``\\n`` only, one trailing ``\\r`` per line is
ignored, and one final empty element after a closing newline is not a line.  ``L1``
is the first line.  A citation ``La-Lb`` (or ``La``) is in bounds when
``1 <= a <= b <= line_count``.

Revision-3 notes cite ``C<start>-<end>``: half-open code-point offsets into the exact UTF-8
decoding of the preserved bytes, with nothing removed (a BOM and every CR are counted; see
tools/source_passages.py).  For those notes each evidence line also gets a lexical
``evidence`` check (``evidence_status``): exact quote or supported text is ``pass``, a
citation whose support cannot be confirmed is ``unverifiable``, and a missing figure or
name, a quote not in its span, or an unresolvable citation is ``fail``.  Coverage is then
checked per non-blank character instead of per line.  Line-cited notes are checked exactly
as before.

Each check returns ``pass``, ``fail`` or ``unverifiable``.  Speaker checks are only
decided when the cited line carries a reliable explicit label; Me/Them labels,
unlabelled continuation lines, and names that are not labels anywhere in the source
are ``unverifiable`` -- identity is never invented.  Nothing here writes, imports
the importer's writers, or calls a model.

Sources: a Business Brain (``--brain``, all or selected ``--folder`` entries; the
exact bytes come from the preserved original record and are hash-checked) or a
not-yet-imported v3 package directory (``--package``).

Usage:
    python3 tools/v3_grounding_check.py --brain <Brain> [--folder sources/historical_calls/<f>/ ...] [--json]
    python3 tools/v3_grounding_check.py --package <INGEST_...> [--json]
Exit status: 0 no failing check, 1 at least one failing check, 2 usage/read error.

Revisit: when the v3 note format (Key quotes, Points, Coverage, offset citations) changes. · Last touched: 2026-11-12.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.memory_exchange_import import V3_FOLDER_RE, V3_SCHEMA  # noqa: E402  (reuse the v3 folder contract)
from tools.source_intake import extract_exact_bytes  # noqa: E402
from tools.validate_business_brain import parse_frontmatter  # noqa: E402

PASS, FAIL, UNVERIFIABLE = "pass", "fail", "unverifiable"
HISTORICAL = "sources/historical_calls"
RANGE = r"L(\d+)(?:\s*[-–]\s*L?(\d+))?"
RANGE_RE = re.compile(r"\b" + RANGE + r"\b")
# Revision-3 notes cite code-point offset ranges of the exact decoded original: C<start>-<end>, half-open
# (tools/source_passages.py documents the convention). Line citations (L<a>-L<b>) stay valid in older notes.
OFFSET_RE = re.compile(r"\bC(\d+)-(\d+)\b")
_RANGE_NC = r"L\d+(?:\s*[-–]\s*L?\d+)?"
_OFFSET_NC = r"C\d+-\d+"
_CITE = r"(?:" + _RANGE_NC + r"|" + _OFFSET_NC + r")"
_CITES = _CITE + r"(?:\s*,\s*" + _CITE + r")*"
UNVERIFIED_MARK = "†"   # rendered after a citation whose support the lexical checks could not confirm
QUOTE_RE = re.compile(r"^>\s*[\"“](.+)[\"”]\s*\(([^()]+?),\s*(" + _CITES + r")(?:\s*" + UNVERIFIED_MARK + r")?\)\s*$")
POINT_RE = re.compile(r"^-\s+\*\*([^*]+?):\*\*.*\((" + _CITES + r")(?:\s*" + UNVERIFIED_MARK + r")?\)\.?\s*$")
# Any revision-3 evidence line: an optional bullet/number, an optional **Label:**, text, then (C..[, C..][ †]).
OFFSET_ITEM_RE = re.compile(r"^(?:-\s+|\d+\.\s+)?(?:\*\*([^*]+?):\*\*\s*)?(.+?)\s*\(((?:" + _OFFSET_NC + r")(?:\s*,\s*"
                            + _OFFSET_NC + r")*)(\s*" + UNVERIFIED_MARK + r")?\)\.?\s*$")
RELATION_LINE_RE = re.compile(r"^-\s+(.+?) — ([a-z ]+?) → (.+?) \(((?:" + _OFFSET_NC + r")(?:\s*,\s*" + _OFFSET_NC
                              + r")*)(?:\s*" + UNVERIFIED_MARK + r")?(?:;[^()]*)?\)\s*$")
# Explicit label shapes: "Name: text" (per-line) and "<ts> From Name to Everyone:" (block header).
LINE_LABEL_RE = re.compile(r"^(?:\[[^\]]{1,30}\]\s*|\d{1,2}:\d{2}(?::\d{2})?\s+)?([^\W\d_][^:\n]{0,40}?):\s+\S")
BLOCK_HEAD_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T][\d:]+\s+From\s+(.+?)\s+to\s+.+?:\s*$")
AMBIGUOUS_LABELS = {"me", "them", "you", "speaker", "unknown", "unknown speaker", "other", "caller", "participant"}
NOT_A_NAME = {"date", "attendees", "time", "title", "note", "notes", "subject", "location", "http", "https", "re", "fwd"}
MIN_LABEL_REPEATS = 3
SLUG_RE = re.compile(r"\[\[[^\]|]*?/([^/\]|]+)\|")


def split_lines(raw: bytes) -> list[str]:
    text = raw.decode("utf-8")
    if text.startswith("﻿"):
        text = text[1:]
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines]


def _norm(text: str) -> str:
    text = (text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
            .replace("–", "-").replace("—", "-"))
    return " ".join(text.split()).casefold()


def parse_ranges(text: str) -> list[tuple[int, int]]:
    out = []
    for m in RANGE_RE.finditer(text):
        a = int(m.group(1))
        out.append((a, int(m.group(2)) if m.group(2) else a))
    return out


def _in_bounds(span: tuple[int, int], n: int) -> bool:
    return 1 <= span[0] <= span[1] <= n


def parse_offsets(text: str) -> list[tuple[int, int]]:
    """``C<start>-<end>`` ranges (half-open code-point offsets) in citation text."""
    return [(int(m.group(1)), int(m.group(2))) for m in OFFSET_RE.finditer(text)]


def offsets_in_bounds(span: tuple[int, int], length: int) -> bool:
    return 0 <= span[0] < span[1] <= length


# ------------------------------------------------------------------ lexical evidence status (revision 3)
# One status per rendered item, decided by deterministic lexical checks only; a citation is never proof:
#   exact      a quote that is an exact (whitespace-normalised) substring of its cited span
#   supported  every figure and proper name in the text occurs in the span, and content-term overlap >= SUPPORT_OVERLAP
#   cited      the reference resolves but support cannot be confirmed lexically (counted as unverifiable)
#   failed     a figure or name is absent from the span, a quote is not in it, or the citation does not resolve
EXACT, SUPPORTED, CITED, FAILED = "exact", "supported", "cited", "failed"
EVIDENCE_STATUSES = (EXACT, SUPPORTED, CITED, FAILED)
SUPPORT_OVERLAP = 0.5
_FIGURE_RE = re.compile(r"(?<![\w.])[$€£¥]?\d[\d,]*(?:\.\d+)?%?")
_WORD_TOKEN_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")
_TERM_RE = re.compile(r"[^\W\d_]{4,}")
_SENTENCE_OPENERS = set(".!?:;\"“‘'(*—–-")
_CAPITAL_STOP = frozenset(
    "i i'm i've i'd i'll a an the this that these those there their they then thus it its it's he she we you our your "
    "his her if in on at by for from of to as and or but so yet not no yes when where while which who whom whose why "
    "how what after before because although though however also both each every all any some many most more less "
    "one two three first second third next finally author speaker source".split())
_CONTENT_STOP = frozenset(
    "that this with from have were they them their what when where which while would could should about into than "
    "then also there these those been being does said says just like more most much many some such very only over "
    "under each other will your yours ours because between through during before after again against".split())


def _clean_figure(token: str) -> str:
    return token.strip("$€£¥%").replace(",", "").rstrip(".")


def figures(text: str) -> set[str]:
    return {_clean_figure(m.group(0)) for m in _FIGURE_RE.finditer(text) if _clean_figure(m.group(0))}


def proper_names(text: str) -> set[str]:
    """Capitalised word tokens that do not open a sentence (names, places, products, acronyms), casefolded."""
    out = set()
    for m in _WORD_TOKEN_RE.finditer(text):
        word = m.group(0)
        if not word[0].isupper():
            continue
        before = text[:m.start()].rstrip()
        if not before or before[-1] in _SENTENCE_OPENERS:
            continue
        base = re.sub(r"['’]s\Z", "", word).casefold()
        if base not in _CAPITAL_STOP:
            out.add(base)
    return out


def _word_set(text: str) -> set[str]:
    return {re.sub(r"['’]s\Z", "", w).casefold() for w in _WORD_TOKEN_RE.findall(text)}


def _content_stems(text: str) -> set[str]:
    return {w.casefold()[:5] for w in _TERM_RE.findall(text) if w.casefold() not in _CONTENT_STOP}


def _ws(text: str) -> str:
    return " ".join(str(text or "").split())


def evidence_status(text: str, span_text: str | None, *, quote: bool = False,
                    attributed: frozenset[str] | set[str] = frozenset()) -> tuple[str, str]:
    """(status, reason) of one item against the exact text of its cited span (None: the citation did not resolve).

    ``attributed`` holds casefolded name tokens whose identity is established at document level (validated
    authors and participants named in the source); naming the speaker of an attributed statement is not
    itself a fact that must recur inside the cited span."""
    if span_text is None:
        return FAILED, "citation does not resolve to the source"
    if quote:
        return (EXACT, "") if _ws(text) and _ws(text) in _ws(span_text) else (FAILED, "quote is not an exact substring of the cited span")
    missing = sorted(figures(text) - figures(span_text))
    if missing:
        return FAILED, f"figure {missing[0]!r} is absent from the cited span"
    absent = sorted(proper_names(text) - _word_set(span_text) - set(attributed))
    if absent:
        return FAILED, f"name {absent[0]!r} is absent from the cited span"
    terms = _content_stems(text)
    if not terms:
        return (SUPPORTED, "") if (figures(text) or proper_names(text) - set(attributed)) else (CITED, "no content terms to check")
    share = len(terms & _content_stems(span_text)) / len(terms)
    if share >= SUPPORT_OVERLAP:
        return SUPPORTED, ""
    return CITED, f"content-term overlap {share:.2f} is below {SUPPORT_OVERLAP}"


class Source:
    """Per-line explicit speaker labels, derived only from unambiguous label shapes."""

    def __init__(self, raw: bytes):
        self.lines = split_lines(raw)
        self.n = len(self.lines)
        self.text = raw.decode("utf-8")            # exact decoding: offsets count a BOM and every CR
        self.length = len(self.text)
        self._line_starts = [0] + [m.end() for m in re.finditer("\n", self.text)]
        self.label: dict[int, str] = {}      # line -> label printed on that very line
        self.block: dict[int, str] = {}      # line -> label of the block header that opens its message
        current = None
        self._headers: set[int] = set()
        for i, line in enumerate(self.lines, 1):
            head = BLOCK_HEAD_RE.match(line)
            if head:
                current = head.group(1).strip()
                self.label[i] = current
                self._headers.add(i)
                continue
            if not line.strip():
                continue
            if current and (line.startswith("\t") or line.startswith("    ")):
                self.block[i] = current
                continue
            current = None
            m = LINE_LABEL_RE.match(line)
            if m and m.group(1).strip().casefold() not in NOT_A_NAME and not re.search(r"https?$", m.group(1)):
                self.label[i] = m.group(1).strip()
        # A per-line "Name:" is a speaker label only if it recurs; a one-off ("Product Catch Phrase:") is a field.
        tally: dict[str, int] = {}
        for i, v in self.label.items():
            if i not in self._headers:
                tally[_norm(v)] = tally.get(_norm(v), 0) + 1
        for i in [i for i, v in self.label.items() if i not in self._headers and tally[_norm(v)] < MIN_LABEL_REPEATS]:
            del self.label[i]
        self.known = {_norm(v) for v in list(self.label.values()) + list(self.block.values())}

    def speaker_of(self, i: int) -> tuple[str | None, str]:
        """(label, why-not).  A label is returned only when it is explicit for this line."""
        if i in self.label:
            return self.label[i], ""
        if i in self.block:
            return self.block[i], ""
        if not self.lines[i - 1].strip():
            return None, "blank line"
        return None, "no explicit speaker label on this line (continuation or unlabelled)"

    def lines_of(self, span: tuple[int, int]) -> list[int]:
        """1-based physical lines that an offset span [a, b) touches."""
        import bisect
        first = bisect.bisect_right(self._line_starts, span[0])
        last = bisect.bisect_right(self._line_starts, max(span[0], span[1] - 1))
        return [i for i in range(first, last + 1) if 1 <= i <= self.n]

    def span_text(self, spans: list[tuple[int, int]]) -> str | None:
        if not spans or not all(offsets_in_bounds(s, self.length) for s in spans):
            return None
        return "\n".join(self.text[a:b] for a, b in spans)

    def attributed(self, names: list[str]) -> frozenset[str]:
        """Casefolded tokens of document-level speaker/author names that the source itself contains."""
        hay = self.text.casefold()
        return frozenset(t for name in names if name.strip() and name.strip().casefold() in hay
                         for t in _word_set(name))


def _same_person(a: str, b: str) -> bool:
    ta, tb = set(_norm(a).split()), set(_norm(b).split())
    return bool(ta and tb) and (ta <= tb or tb <= ta)


def _check(kind: str, ref: str, status: str, detail: str = "") -> dict:
    return {"kind": kind, "ref": ref, "status": status, "detail": detail}


def _speaker_status(src: Source, speaker: str, span: tuple[int, int], lines: list[int]) -> tuple[str, str]:
    cited = _norm(speaker)
    if cited in AMBIGUOUS_LABELS:
        labels = {_norm(src.speaker_of(i)[0] or "") for i in lines}
        if cited in labels:
            return PASS, f"explicit label {speaker!r} present in range"
        return UNVERIFIABLE, f"{speaker!r} is an ambiguous role label, not an identity"
    if cited not in src.known and not any(_same_person(speaker, k) for k in src.known):
        return UNVERIFIABLE, f"{speaker!r} is not an explicit speaker label anywhere in the source"
    found, ambiguous = [], 0
    for i in lines:
        label, _why = src.speaker_of(i)
        if label is None:
            continue
        if _norm(label) in AMBIGUOUS_LABELS:
            ambiguous += 1
        else:
            found.append(label)
    if any(_same_person(speaker, label) for label in found):
        return PASS, "explicit label matches"
    if ambiguous:
        return UNVERIFIABLE, "cited lines are labelled with an ambiguous Me/Them-style role"
    if found:
        return FAIL, f"cited lines are explicitly labelled {sorted(set(found))!r}, not {speaker!r}"
    return UNVERIFIABLE, "no explicit speaker label on the cited lines"


def _quote_segments(quote: str) -> list[str]:
    return [seg for seg in re.split(r"\s*(?:…|\.\.\.)\s*", quote) if seg.strip()]


def _note_attribution(fields: dict, src: Source) -> frozenset[str]:
    names = [p for p in str(fields.get("participants_text") or "").split(";")]
    names += [re.sub(r"\s*\((?:text|filename)\)\s*\Z", "", a) for a in str(fields.get("source_authors") or "").split(";")]
    return src.attributed(names)


def _check_offset_note_line(name: str, line: str, src: Source, attributed: frozenset[str]) -> list[dict]:
    """Revision-3 evidence lines (C offsets): bounds, quote exactness, lexical support and explicit speaker labels."""
    quote = QUOTE_RE.match(line)
    if quote and parse_offsets(quote.group(3)) and not parse_ranges(quote.group(3)):
        qtext, speaker, spans = quote.group(1), quote.group(2).strip(), parse_offsets(quote.group(3))
        ref = f"{name}:quote:{_norm(qtext)[:40]}|{quote.group(3)}"
        span_text = src.span_text(spans)
        if span_text is None:
            return [_check("range", ref, FAIL, f"quotation cites an offset outside 0..{src.length}"),
                    _check("quote", ref, UNVERIFIABLE, "cited range invalid")]
        status, why = evidence_status(qtext, span_text, quote=True)
        lines = [i for s in spans for i in src.lines_of(s)]
        speaker_status, speaker_why = _speaker_status(src, speaker, (lines[0], lines[-1]), lines)
        return [_check("range", ref, PASS), _check("quote", ref, PASS if status == EXACT else FAIL, why),
                _check("speaker", ref, speaker_status, speaker_why)]
    relation = RELATION_LINE_RE.match(line)
    if relation:
        spans = parse_offsets(relation.group(4))
        ref = f"{name}:relationship:{_norm(relation.group(1))[:30]}>{_norm(relation.group(3))[:30]}|{relation.group(4)}"
        span_text = src.span_text(spans)
        if span_text is None:
            return [_check("range", ref, FAIL, f"relationship cites an offset outside 0..{src.length}")]
        words = _word_set(span_text)
        named = all(_word_set(relation.group(k)) <= words | attributed for k in (1, 3))
        return [_check("range", ref, PASS), _check("evidence", ref, PASS if named else UNVERIFIABLE,
                                                    "" if named else "an endpoint is not named in the cited span")]
    item = OFFSET_ITEM_RE.match(line)
    if not item or line.startswith(("#", "|", ">")) or "[[" in line:
        return []
    label, text, spans = (item.group(1) or "").strip(), item.group(2), parse_offsets(item.group(3))
    ref = f"{name}:item:{_norm(text)[:40]}|{item.group(3)}"
    span_text = src.span_text(spans)
    if span_text is None:
        return [_check("range", ref, FAIL, f"item cites an offset outside 0..{src.length}"),
                _check("evidence", ref, FAIL, "citation does not resolve")]
    status, why = evidence_status(text, span_text, attributed=attributed)
    checks = [_check("range", ref, PASS),
              _check("evidence", ref, PASS if status in (EXACT, SUPPORTED) else UNVERIFIABLE if status == CITED else FAIL,
                     f"{status}{': ' + why if why else ''}")]
    if label:
        lines = [i for s in spans for i in src.lines_of(s)]
        speaker_status, speaker_why = _speaker_status(src, label, (lines[0], lines[-1]), lines)
        checks.append(_check("speaker", ref, speaker_status, speaker_why))
    return checks


def check_note(name: str, text: str, src: Source) -> list[dict]:
    checks = []
    fields, body = parse_frontmatter(text)
    n = src.n
    attributed = _note_attribution(fields, src)
    section = ""
    for lineno, line in enumerate(body.splitlines(), 1):
        if line.startswith("## "):
            section = line[3:].strip()
        if section == "Source" and OFFSET_RE.search(line):   # identity metadata (author, date): bounds only
            for span in parse_offsets(line):
                ok = offsets_in_bounds(span, src.length)
                checks.append(_check("range", f"{name}:source:C{span[0]}-{span[1]}", PASS if ok else FAIL,
                                     "" if ok else f"outside 0..{src.length}"))
            continue
        if line.startswith("[[") and "Spans " in line:  # "Spans C120-480, ..." header of a revision-3 topic note
            for span in parse_offsets(line.split("Spans ", 1)[1]):
                ok = offsets_in_bounds(span, src.length)
                checks.append(_check("range", f"{name}:header:C{span[0]}-{span[1]}", PASS if ok else FAIL,
                                     "" if ok else f"outside 0..{src.length}"))
            continue
        if OFFSET_RE.search(line) and not RANGE_RE.search(line.split("(")[-1]):
            checks += _check_offset_note_line(name, line, src, attributed)
            continue
        if line.startswith("[[") and "Lines " in line:  # "Lines L7-L17, ..." header of a topic note
            for span in parse_ranges(line.split("Lines ", 1)[1]):
                ok = _in_bounds(span, n)
                checks.append(_check("range", f"{name}:header:L{span[0]}-L{span[1]}", PASS if ok else FAIL,
                                     "" if ok else f"outside 1..{n}"))
        quote = QUOTE_RE.match(line)
        if quote:
            qtext, speaker, citation = quote.group(1), quote.group(2).strip(), quote.group(3)
            spans = parse_ranges(citation)
            ref = f"{name}:quote:{_norm(qtext)[:40]}|{citation}"
            if not all(_in_bounds(s, n) for s in spans):
                checks.append(_check("range", ref, FAIL, f"quotation cites a range outside 1..{n}"))
                checks.append(_check("quote", ref, UNVERIFIABLE, "cited range invalid"))
                checks.append(_check("speaker", ref, UNVERIFIABLE, "cited range invalid"))
                continue
            checks.append(_check("range", ref, PASS))
            lines = [i for a, b in spans for i in range(a, b + 1)]
            haystack = _norm("\n".join(src.lines[i - 1] for i in lines))
            haystack_flat = " ".join(haystack.split())
            ok = all(_norm(seg) in haystack_flat for seg in _quote_segments(qtext))
            checks.append(_check("quote", ref, PASS if ok else FAIL, "" if ok else "quotation text not found in the cited lines"))
            hit = [i for i in lines if _norm(src.lines[i - 1]) and any(_norm(seg) in _norm(src.lines[i - 1])
                   for seg in _quote_segments(qtext))] or lines
            status, why = _speaker_status(src, speaker, spans[0], hit)
            checks.append(_check("speaker", ref, status, why))
            continue
        point = POINT_RE.match(line)
        if point:
            speaker, citation = point.group(1).strip(), point.group(2)
            spans = parse_ranges(citation)
            ref = f"{name}:point:{_norm(speaker)}|{citation}"
            if not all(_in_bounds(s, n) for s in spans):
                checks.append(_check("range", ref, FAIL, f"point cites a range outside 1..{n}"))
                checks.append(_check("speaker", ref, UNVERIFIABLE, "cited range invalid"))
                continue
            checks.append(_check("range", ref, PASS))
            lines = [i for a, b in spans for i in range(a, b + 1)]
            status, why = _speaker_status(src, speaker, spans[0], lines)
            checks.append(_check("speaker", ref, status, why))
    return checks


def _table_rows(body: str, heading: str) -> list[list[str]]:
    rows, inside = [], False
    for line in body.splitlines():
        if line.startswith("## "):
            inside = line.strip() == heading
            continue
        if inside and line.startswith("|"):
            protected = re.sub(r"\[\[[^\]]*\]\]", lambda m: m.group(0).replace("|", "\x00"), line.strip().strip("|"))
            cells = [c.strip().replace("\x00", "|") for c in protected.split("|")]
            if cells and not set("".join(cells)) <= set("-: "):
                rows.append(cells)
    return rows[1:] if rows else []  # drop the header row


def merge_offsets(spans) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _within(span: tuple[int, int], merged: list[tuple[int, int]]) -> bool:
    return any(a <= span[0] and span[1] <= b for a, b in merged)


def _check_offset_coverage(cov: list[list[str]], topics: dict[int, str], notes: dict[str, str], src: Source) -> list[dict]:
    """Revision-3 Coverage table: offset rows; every non-blank character must be declared in some row."""
    checks, mask = [], bytearray(src.length)
    for cells in cov:
        if cells and cells[0].strip().lower() in {"lines", "span", "spans"}:
            continue
        spans = parse_offsets(cells[0]) if cells else []
        where = cells[1] if len(cells) > 1 else ""
        ref = f"INDEX:coverage:{cells[0] if cells else '?'}|{where}"
        if not spans:
            checks.append(_check("coverage", ref, FAIL, "row declares no offset range"))
            continue
        if not all(offsets_in_bounds(s, src.length) for s in spans):
            checks.append(_check("coverage", ref, FAIL, f"range outside 0..{src.length}"))
            continue
        for a, b in spans:
            mask[a:b] = b"\x01" * (b - a)
        m = re.fullmatch(r"topic\s+(\d+)", where.strip(), flags=re.I)
        if m:
            slug = topics.get(int(m.group(1)))
            note = notes.get(f"{slug}.md") if slug else None
            if note is None:
                checks.append(_check("coverage", ref, FAIL, f"topic {m.group(1)} has no matching note in the Topics table / folder"))
                continue
            header = next((line for line in parse_frontmatter(note)[1].splitlines() if line.startswith("[[") and "Spans " in line), "")
            claimed = merge_offsets(parse_offsets(header.split("Spans ", 1)[1])) if header else []
            ok = all(_within(s, claimed) for s in spans)
            checks.append(_check("coverage", ref, PASS if ok else FAIL,
                                 "" if ok else f"{slug}.md does not list these offsets in its own 'Spans' header"))
        elif where.strip().lower() == "not filed":
            checks.append(_check("coverage", ref, PASS, "declared not filed"))
        else:
            checks.append(_check("coverage", ref, UNVERIFIABLE, f"unrecognised coverage target {where!r}"))
    gaps = [i for i, ch in enumerate(src.text) if not mask[i] and not ch.isspace() and ch != "﻿"]
    if gaps:
        checks.append(_check("coverage", "INDEX:coverage:complete", FAIL,
                             f"{len(gaps)} non-blank character(s) in no coverage row, first C{gaps[0]}"))
    else:
        checks.append(_check("coverage", "INDEX:coverage:complete", PASS,
                             f"every non-blank character of {src.length} is declared"))
    return checks


def check_coverage(index_text: str, notes: dict[str, str], src: Source) -> list[dict]:
    _fields, body = parse_frontmatter(index_text)
    n, checks = src.n, []
    cov = _table_rows(body, "## Coverage")
    if not cov:
        return [_check("coverage", "INDEX:coverage", UNVERIFIABLE, "no Coverage table declared")]
    topics = {}
    for cells in _table_rows(body, "## Topics"):
        slug = SLUG_RE.search(cells[1].replace("|", "|", 1)) if len(cells) > 1 else None
        if slug and cells[0].isdigit():
            topics[int(cells[0])] = slug.group(1)
    if any(parse_offsets(cells[0]) for cells in cov if cells):
        return _check_offset_coverage(cov, topics, notes, src)
    covered: set[int] = set()
    for cells in cov:
        if cells and cells[0].strip().lower() == "lines":  # a repeated header row, not a declaration
            continue
        spans = parse_ranges(cells[0]) if cells else []
        where = cells[1] if len(cells) > 1 else ""
        ref = f"INDEX:coverage:{cells[0] if cells else '?'}|{where}"
        if not spans:
            checks.append(_check("coverage", ref, FAIL, "row declares no line range"))
            continue
        if not all(_in_bounds(s, n) for s in spans):
            checks.append(_check("coverage", ref, FAIL, f"range outside 1..{n}"))
            continue
        wanted = {i for a, b in spans for i in range(a, b + 1)}
        covered |= wanted
        m = re.fullmatch(r"topic\s+(\d+)", where.strip(), flags=re.I)
        if m:
            slug = topics.get(int(m.group(1)))
            note = notes.get(f"{slug}.md") if slug else None
            if note is None:
                checks.append(_check("coverage", ref, FAIL, f"topic {m.group(1)} has no matching note in the Topics table / folder"))
                continue
            header = next((line for line in parse_frontmatter(note)[1].splitlines() if line.startswith("[[") and "Lines " in line), "")
            claimed = {i for a, b in parse_ranges(header.split("Lines ", 1)[1]) if 1 <= a <= b for i in range(a, b + 1)} if header else set()
            ok = wanted <= claimed
            checks.append(_check("coverage", ref, PASS if ok else FAIL,
                                 "" if ok else f"{slug}.md does not list these lines in its own 'Lines' header"))
        elif where.strip().lower() == "not filed":
            checks.append(_check("coverage", ref, PASS, "declared not filed"))
        else:
            checks.append(_check("coverage", ref, UNVERIFIABLE, f"unrecognised coverage target {where!r}"))
    gaps = [i for i in range(1, n + 1) if i not in covered and src.lines[i - 1].strip()]
    if gaps:
        checks.append(_check("coverage", "INDEX:coverage:complete", FAIL, f"{len(gaps)} non-blank line(s) in no coverage row, first L{gaps[0]}"))
    else:
        checks.append(_check("coverage", "INDEX:coverage:complete", PASS, f"every non-blank line of {n} is declared"))
    return checks


def check_package(label: str, source_raw: bytes, index_text: str, notes: dict[str, str], *, source_error: str = "") -> dict:
    checks = []
    if source_error:
        checks.append(_check("source", label, FAIL, source_error))
    else:
        try:
            src = Source(source_raw)
        except UnicodeDecodeError as exc:
            checks.append(_check("source", label, FAIL, f"source is not UTF-8: {exc}"))
        else:
            checks.append(_check("source", label, PASS, f"{src.n} lines (1-based, split on \\n)"))
            checks += check_note("INDEX.md", index_text, src)
            for name in sorted(notes):
                checks += check_note(name, notes[name], src)
            checks += check_coverage(index_text, notes, src)
    checks.sort(key=lambda c: (c["kind"], c["ref"], c["status"], c["detail"]))
    counts = {s: sum(1 for c in checks if c["status"] == s) for s in (PASS, FAIL, UNVERIFIABLE)}
    verdict = FAIL if counts[FAIL] else PASS if counts[PASS] else UNVERIFIABLE
    by_kind = {}
    for c in checks:
        by_kind.setdefault(c["kind"], {PASS: 0, FAIL: 0, UNVERIFIABLE: 0})[c["status"]] += 1
    return {"package": label, "verdict": verdict, "counts": counts, "by_kind": {k: by_kind[k] for k in sorted(by_kind)},
            "checks": checks}


# ------------------------------------------------------------------ loaders (read-only)

def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_brain_folder(brain: Path, folder: str) -> dict:
    folder = folder.rstrip("/") + "/"
    base = brain / folder
    notes = {p.name: _read(p) for p in sorted(base.glob("*.md")) if p.name != "INDEX.md"}
    index_text = _read(base / "INDEX.md")
    fields, _ = parse_frontmatter(index_text)
    sha = fields.get("source_sha256", "")
    pointer = fields.get("source_record", "").removeprefix("business_brain:")
    try:
        record = brain / pointer
        if not pointer.startswith("sources/intake/records/") or not record.is_file():
            raise ValueError(f"original record not found: {pointer or '(none declared)'}")
        raw = extract_exact_bytes(_read(record))
        if hashlib.sha256(raw).hexdigest() != sha:
            raise ValueError("exact bytes in the original record do not match source_sha256")
        err = ""
    except Exception as exc:
        raw, err = b"", f"{type(exc).__name__}: {exc}"
    return check_package(folder, raw, index_text, notes, source_error=err)


def load_package(package: Path) -> dict:
    manifest = json.loads(_read(package / "INGEST_MANIFEST.json"))
    if manifest.get("schema_version") != V3_SCHEMA:
        raise ValueError(f"not a v3 package: {package}")
    def declared(row: dict, key: str) -> bytes:
        raw = (package / row[key]).read_bytes()
        if row.get("sha256") != hashlib.sha256(raw).hexdigest():
            raise ValueError(f"declared sha256 does not match {row[key]}")
        return raw
    err = ""
    try:
        raw = declared(manifest["source"], "package_path")
    except Exception as exc:
        raw, err = b"", f"{type(exc).__name__}: {exc}"
    index_text, notes = "", {}
    for row in manifest["outputs"]:
        text = declared(row, "package_path").decode("utf-8")
        if row.get("role") == "source_index":
            index_text = text
        else:
            notes[Path(row["target_path"]).name] = text
    return check_package(package.name, raw, index_text, notes, source_error=err)


def v3_folders(brain: Path) -> list[str]:
    out = []
    for d in sorted((brain / HISTORICAL).iterdir()) if (brain / HISTORICAL).is_dir() else []:
        rel = f"{HISTORICAL}/{d.name}/"
        if d.is_dir() and V3_FOLDER_RE.fullmatch(rel) and (d / "INDEX.md").is_file():
            out.append(rel)
    return out


def build_report(packages: list[dict]) -> dict:
    packages = sorted(packages, key=lambda p: p["package"])
    totals = {s: sum(p["counts"][s] for p in packages) for s in (PASS, FAIL, UNVERIFIABLE)}
    return {"validator": "v3_grounding_check", "scope": "source grounding only",
            "semantic_correctness": "not assessed", "line_convention": "1-based, UTF-8, split on \\n",
            "offset_convention": "C<start>-<end>: half-open code-point offsets of the exact UTF-8 decoding (BOM and CR counted)",
            "model_calls": 0, "packages": packages, "totals": totals,
            "package_verdicts": {v: sum(1 for p in packages if p["verdict"] == v) for v in (PASS, FAIL, UNVERIFIABLE)}}


def summary_lines(report: dict) -> list[str]:
    lines = [f"v3 grounding check -- source grounding only; semantic correctness NOT assessed; model calls: 0"]
    for p in report["packages"]:
        c = p["counts"]
        lines.append(f"{p['verdict'].upper():<12} {p['package']}  pass={c[PASS]} fail={c[FAIL]} unverifiable={c[UNVERIFIABLE]}")
        for chk in p["checks"]:
            if chk["status"] == FAIL:
                lines.append(f"    FAIL {chk['kind']} {chk['ref']}: {chk['detail']}")
    t, v = report["totals"], report["package_verdicts"]
    lines.append(f"TOTAL {len(report['packages'])} package(s): pass={v[PASS]} fail={v[FAIL]} unverifiable={v[UNVERIFIABLE]}; "
                 f"checks pass={t[PASS]} fail={t[FAIL]} unverifiable={t[UNVERIFIABLE]}")
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--brain", type=Path, help="Business Brain root (read-only)")
    ap.add_argument("--folder", action="append", default=[], help="v3 source folder under the Brain (default: all)")
    ap.add_argument("--package", action="append", type=Path, default=[], help="v3 package directory")
    ap.add_argument("--json", action="store_true", help="print the machine-readable report")
    ap.add_argument("--report", type=Path, help="also write the JSON report to this path")
    args = ap.parse_args(argv)
    if not args.brain and not args.package:
        ap.error("give --brain and/or --package")
    packages = []
    try:
        if args.brain:
            for folder in args.folder or v3_folders(args.brain):
                packages.append(load_brain_folder(args.brain, folder))
        for package in args.package:
            packages.append(load_package(package))
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    report = build_report(packages)
    rendered = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False)
    if args.report:
        args.report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered if args.json else "\n".join(summary_lines(report)))
    return 1 if report["totals"][FAIL] else 0


if __name__ == "__main__":
    sys.exit(main())
