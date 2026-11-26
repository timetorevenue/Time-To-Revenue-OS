#!/usr/bin/env python3
"""Tier 0: bounded, hash-verified passages of a preserved original source.

The exact bytes of every captured source already live in its Business Brain record
(``sources/intake/records/<sha256>.md``, TTROS:SOURCE-BYTES block). Every read re-hashes those preserved bytes
against the record's declared SHA-256 first. There is no second database or search index: windows are computed
on demand from the record, and ``tools/aos_indexer`` stays the only search system.

Two reference forms resolve here:

``source:<sha256>#C<start>-<end>`` (offsets; revision-3 interpretations)
    ``start`` and ``end`` are code-point offsets into ``raw.decode("utf-8")`` -- the exact decoding of the
    verified preserved bytes with NOTHING removed or normalised: a leading BOM (U+FEFF) is offset 0, every CR of a
    CRLF pair keeps its place, and every other character is counted once. The range is half-open, like a Python
    slice, so ``text[start:end].encode("utf-8")`` is exactly the corresponding original bytes. ``read_passage``
    returns that exact slice (``exact``) bounded by MAX_PASSAGE_CHARS and MAX_PASSAGE_LINES measured from
    ``start``; any BOM/CR cleaning for display or for a model is applied to the returned ``text`` only, never to
    the offsets.
``source:<sha256>#L<a>-L<b>`` (lines; older notes and search rows)
    1-based lines in the ``tools/v3_grounding_check.py::split_lines`` convention, resolved exactly as before.

``segments`` cuts the same decoded text into sentence-bounded pieces of roughly SEGMENT_MIN-SEGMENT_MAX
characters that never cross a physical line break, never include a line's CR or the BOM, and never split a code
point; a short line is one segment. Segment numbers are only a model-facing handle (``S<n>``): what is stored is
the offset range, so the segmenter can change later without breaking any reference.

Revisit: when the record byte format, the line convention or the offset convention changes. · Last touched: 2026-11-12.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.source_intake import RECORDS_RELATIVE, SourceIntakeError, extract_exact_bytes  # noqa: E402
from tools.v3_grounding_check import split_lines  # noqa: E402

REF_RE = re.compile(r"source:([0-9a-f]{64})#L(\d+)(?:-L?(\d+))?\Z")
OFFSET_REF_RE = re.compile(r"source:([0-9a-f]{64})#C(\d+)-(\d+)\Z")
MAX_PASSAGE_LINES = 80
MAX_PASSAGE_CHARS = 6000
_WORD = re.compile(r"[a-z0-9][a-z0-9'-]{2,}")
_STOP = frozenset("the and for that this with from are was were have has had you your not but they them their what "
                  "when where which who how why does did can could should would about into than then also".split())


class PassageError(SourceIntakeError):
    pass


def passage_ref(sha256: str, start: int, end: int | None = None) -> str:
    return f"source:{sha256}#L{start}" + (f"-L{end}" if end and end != start else "")


def parse_ref(ref: str) -> tuple[str, int, int]:
    match = REF_RE.fullmatch(ref.strip())
    if not match:
        raise PassageError(f"not a source passage reference: {ref!r}")
    start = int(match.group(2))
    end = int(match.group(3) or start)
    return match.group(1), start, end


def offset_ref(sha256: str, start: int, end: int) -> str:
    """``source:<sha256>#C<start>-<end>``: a half-open code-point range of the exact decoded original."""
    return f"source:{sha256}#C{start}-{end}"


def parse_offset_ref(ref: str) -> tuple[str, int, int]:
    match = OFFSET_REF_RE.fullmatch(ref.strip())
    if not match:
        raise PassageError(f"not a source offset reference: {ref!r}")
    return match.group(1), int(match.group(2)), int(match.group(3))


def verified_raw(brain_root: Path, sha: str, cache: dict | None = None) -> tuple[str, bytes]:
    """(full sha256, exact preserved bytes); fails unless they hash to the record's declared SHA-256."""
    key = ("raw", sha)
    if cache is not None and key in cache:
        return cache[key]
    records = Path(brain_root) / RECORDS_RELATIVE
    matches = [records / f"{sha}.md"] if re.fullmatch(r"[0-9a-f]{64}", sha) else sorted(records.glob(f"{sha}*.md"))
    matches = [path for path in matches if path.is_file()]
    if len(matches) != 1 or len(sha) < 8:
        raise PassageError(f"source record not found or ambiguous for {sha!r}")
    path = matches[0]
    full = path.stem
    text = path.read_text(encoding="utf-8")
    declared = re.search(r"(?m)^source_sha256:\s*\"?([0-9a-f]{64})", text)
    raw = extract_exact_bytes(text)
    actual = hashlib.sha256(raw).hexdigest()
    if actual != full or (declared and declared.group(1) != actual):
        raise PassageError(f"preserved bytes do not match the declared SHA-256 for {full}")
    if cache is not None:
        cache[key] = (full, raw)
    return full, raw


def load_source(brain_root: Path, sha: str, cache: dict | None = None) -> tuple[str, list[str]]:
    """(full sha256, lines) of a preserved original; fails unless the preserved bytes hash to the record's SHA.

    ``cache`` (optional, owned by the caller for one lookup) holds verified sources so several passages of one
    source are hashed once."""
    if cache is not None and sha in cache:
        return cache[sha]
    full, raw = verified_raw(brain_root, sha, cache)
    result = (full, split_lines(raw))
    if cache is not None:
        cache[sha] = result
    return result


def load_text(brain_root: Path, sha: str, cache: dict | None = None) -> tuple[str, str]:
    """(full sha256, exact decoded text): ``raw.decode("utf-8")`` with nothing removed (the offset basis)."""
    full, raw = verified_raw(brain_root, sha, cache)
    try:
        return full, raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PassageError(f"preserved bytes of {full} are not UTF-8: {exc}") from exc


def display_text(text: str) -> str:
    """Display/model cleaning of a returned slice only: drop a BOM and the CR of each CRLF pair."""
    return text.replace("\ufeff", "").replace("\r\n", "\n")


def read_passage(brain_root: Path, ref_or_sha: str, start: int | None = None, end: int | None = None,
                 *, max_lines: int = MAX_PASSAGE_LINES, cache: dict | None = None) -> dict:
    """One bounded passage with its stable reference and verified hash (``#L`` lines or ``#C`` offsets)."""
    if start is None and OFFSET_REF_RE.fullmatch(str(ref_or_sha).strip()):
        return read_offsets(brain_root, *parse_offset_ref(ref_or_sha), max_lines=max_lines, cache=cache)
    if start is None:
        sha, start, end = parse_ref(ref_or_sha)
    else:
        sha, end = ref_or_sha, end or start
    full, lines = load_source(brain_root, sha, cache)
    if not 1 <= start <= end <= len(lines):
        raise PassageError(f"lines L{start}-L{end} are outside 1..{len(lines)}")
    stop = min(end, start + max_lines - 1)
    text = "\n".join(lines[start - 1:stop])
    truncated = stop < end or len(text) > MAX_PASSAGE_CHARS
    return {"ref": passage_ref(full, start, stop), "sha256": full, "verified": True, "start": start, "end": stop,
            "total_lines": len(lines), "truncated": truncated, "text": text[:MAX_PASSAGE_CHARS],
            "record": f"business_brain:{RECORDS_RELATIVE}/{full}.md"}


def read_offsets(brain_root: Path, sha: str, start: int, end: int, *, max_lines: int = MAX_PASSAGE_LINES,
                 cache: dict | None = None) -> dict:
    """The exact span [start, end) of the verified decoded original, bounded from ``start``.

    ``exact`` is the untouched slice (its UTF-8 encoding equals original bytes ``byte_start:byte_end``); ``text`` is
    the same slice with display cleaning only. The returned ``ref`` names the span actually returned."""
    full, text = load_text(brain_root, sha, cache)
    if not 0 <= start < end <= len(text):
        raise PassageError(f"offsets C{start}-{end} are outside 0..{len(text)}")
    stop = min(end, start + MAX_PASSAGE_CHARS)
    newlines = [i for i in range(start, stop) if text[i] == "\n"]
    if len(newlines) >= max_lines:
        stop = newlines[max_lines - 1]
    exact = text[start:stop]
    byte_start = len(text[:start].encode("utf-8"))
    return {"ref": offset_ref(full, start, stop), "sha256": full, "verified": True, "unit": "codepoint",
            "start": start, "end": stop, "total_chars": len(text), "truncated": stop < end,
            "byte_start": byte_start, "byte_end": byte_start + len(exact.encode("utf-8")),
            "start_line": text.count("\n", 0, start) + 1, "end_line": text.count("\n", 0, max(start, stop - 1)) + 1,
            "total_lines": len(split_lines(text.encode("utf-8"))),
            "exact": exact, "text": display_text(exact), "record": f"business_brain:{RECORDS_RELATIVE}/{full}.md"}


# ----------------------------------------------------------------------------- deterministic segmenter
SEGMENT_MIN = 300
SEGMENT_MAX = 500
_SENTENCE_END = re.compile(r"[.!?…]+[\"'”’)\]]*(?=\s)")


def line_bounds(text: str) -> list[tuple[int, int, int]]:
    """(line number, content start, content end) per physical line, in offsets of ``text``.

    Content excludes the line break, the CR of a CRLF pair and (on line 1) a BOM; line numbers follow
    ``split_lines`` (a final empty element after a closing newline is not a line)."""
    out, pos, number = [], 0, 0
    for piece in text.split("\n"):
        number += 1
        start, end = pos, pos + len(piece)
        if number == 1 and piece.startswith("\ufeff"):
            start += 1
        if end > start and text[end - 1] == "\r":
            end -= 1
        out.append((number, start, end))
        pos += len(piece) + 1
    if out and out[-1][1] >= len(text) and text.endswith("\n"):
        out.pop()
    return out


def _trim(text: str, a: int, b: int) -> tuple[int, int]:
    while a < b and text[a].isspace():
        a += 1
    while b > a and text[b - 1].isspace():
        b -= 1
    return a, b


def _split_line(text: str, a: int, b: int) -> list[tuple[int, int]]:
    """Sentence-bounded pieces of one long line; a sentence longer than SEGMENT_MAX is cut at whitespace."""
    ends = [m.end() for m in _SENTENCE_END.finditer(text, a, b)] + [b]
    out, start, last = [], a, a
    for end in ends:
        if end <= start:
            continue
        if end - start > SEGMENT_MAX and last > start:   # close at the last sentence end that kept it in bounds
            out.append((start, last))
            start = last
        while end - start > SEGMENT_MAX:                  # one over-long sentence: cut at the last space before the cap
            space = text.rfind(" ", start + SEGMENT_MIN // 2, start + SEGMENT_MAX)
            cut = space if space > start else start + SEGMENT_MAX
            out.append((start, cut))
            start = cut
        last = end
        if end - start >= SEGMENT_MIN:
            out.append((start, end))
            start = end
    if start < b:
        out.append((start, b))
    trimmed = [_trim(text, x, y) for x, y in out]
    return [(x, y) for x, y in trimmed if y > x]


def segments(text: str) -> list[tuple[int, int]]:
    """Ordered, non-overlapping (start, end) offsets: one per short line, sentence-bounded pieces of a long one."""
    out: list[tuple[int, int]] = []
    for _number, a, b in line_bounds(text):
        a, b = _trim(text, a, b)
        if b <= a:
            continue
        out.extend([(a, b)] if b - a <= SEGMENT_MAX else _split_line(text, a, b))
    return out


def _terms(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP]


def find_passages(brain_root: Path, sha: str, question: str, *, limit: int = 3, window: int = 12) -> list[dict]:
    """Highest-scoring non-overlapping line windows of one source for a question (lexical, idf-weighted)."""
    full, lines = load_source(brain_root, sha)
    want = set(_terms(question))
    if not want:
        return []
    tokens = [set(_terms(line)) for line in lines]
    df = {w: sum(1 for t in tokens if w in t) for w in want}
    weight = {w: math.log(1 + len(lines) / (1 + df[w])) for w in want if df[w]}
    scored = []
    step = max(1, window // 2)
    for start in range(0, max(1, len(lines) - window + 1), step):
        seen: set[str] = set()
        for t in tokens[start:start + window]:
            seen |= t
        score = sum(weight.get(w, 0.0) for w in want & seen)
        if score:
            scored.append((score, start + 1, min(len(lines), start + window)))
    chosen: list[tuple[float, int, int]] = []
    for score, a, b in sorted(scored, reverse=True):
        if all(b < c or a > d for _s, c, d in chosen):
            chosen.append((score, a, b))
        if len(chosen) >= limit:
            break
    return [{**read_passage(brain_root, full, a, b), "score": round(score, 3)} for score, a, b in chosen]


INDEX_TARGET_CHARS = 1400   # a passage row is about one embedding chunk
INDEX_MAX_CHARS = 1800
INDEX_OVERLAP_LINES = 2
INDEX_MAX_LINES = 60
INDEX_MIN_SECTION_CHARS = 300
_HEADING = re.compile(r"\s{0,3}#{1,6}\s+\S")


def index_windows(lines: list[str]) -> list[tuple[int, int, str]]:
    """Contiguous line windows (start, end, text), 1-based inclusive, that cover every non-blank line.

    Greedy to about INDEX_TARGET_CHARS, never past INDEX_MAX_CHARS or INDEX_MAX_LINES (a single longer line is its
    own window), ending at a blank line when one is near, and closing before a Markdown heading once it holds INDEX_MIN_SECTION_CHARS (so a section opens its own window),
    and otherwise overlapping the next window by INDEX_OVERLAP_LINES lines so an answer straddling a boundary is whole in one of them. Used by the search indexer so that
    ``source:<sha>#L<a>-L<b>`` passages are ordinary hybrid-search rows; no second index exists.
    """
    windows: list[tuple[int, int, str]] = []
    count = len(lines)
    start = 0
    while start < count:
        end, size, aligned = start, 0, False
        while end < count and end - start < INDEX_MAX_LINES:
            size_next = size + len(lines[end]) + 1
            if end > start and size_next > INDEX_MAX_CHARS:
                break
            size = size_next
            end += 1
            if size >= INDEX_TARGET_CHARS and end < count and not lines[end - 1].strip():
                break
            if size >= INDEX_MIN_SECTION_CHARS and end < count and _HEADING.match(lines[end]):
                aligned = True  # a new section starts here: close this window before its heading
                break
        text = "\n".join(lines[start:end])
        if text.strip():
            windows.append((start + 1, end, text))
        if end >= count:
            break
        start = end if aligned else max(end - INDEX_OVERLAP_LINES, start + 1)
    return windows


def passage_windows(text: str) -> list[tuple[str, int, int, str]]:
    """Search-row passages (label, first line, last line, display text) of one exact decoded original.

    Built from segments: a short line is one segment, so windows of short lines are exactly ``index_windows`` and
    keep their ``L<a>-L<b>`` labels (existing rows and their embeddings do not change). A line longer than
    INDEX_MAX_CHARS -- which ``index_windows`` can only return whole -- becomes several rows instead, each a run of
    that line's consecutive segments up to about INDEX_TARGET_CHARS, overlapping the next by one segment, labelled
    by its offset range ``C<start>-<end>`` so text deep inside a long line is its own searchable row."""
    lines = split_lines(text.encode("utf-8"))
    bounds = {number: (a, b) for number, a, b in line_bounds(text)}
    segs = segments(text)
    out: list[tuple[str, int, int, str]] = []
    for start, end, body in index_windows(lines):
        if start != end or len(lines[start - 1]) <= INDEX_MAX_CHARS:
            out.append((f"L{start}-L{end}", start, end, body))
            continue
        a, b = bounds[start]
        mine = [seg for seg in segs if a <= seg[0] and seg[1] <= b]
        i = 0
        while i < len(mine):
            j = i
            while j + 1 < len(mine) and mine[j + 1][1] - mine[i][0] <= INDEX_TARGET_CHARS:
                j += 1
            first, last = mine[i][0], mine[j][1]
            out.append((f"C{first}-{last}", start, start, display_text(text[first:last])))
            if j + 1 >= len(mine):
                break
            i = j if j > i else j + 1             # overlap the next row by one segment
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read bounded, hash-verified passages of a preserved source")
    parser.add_argument("brain_root", type=Path)
    parser.add_argument("target", help="source:<sha256>#C<start>-<end> or #L<a>-L<b>, or a SHA prefix with --question")
    parser.add_argument("--question")
    args = parser.parse_args(argv)
    try:
        result = (find_passages(args.brain_root, args.target, args.question) if args.question
                  else read_passage(args.brain_root, args.target))
    except SourceIntakeError as exc:
        print(f"passage lookup failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0



SOURCE_GRAPH_SCHEMA = "ttros-source-graph-v1"
SOURCE_GRAPH_RE = re.compile(
    r"<!-- TTROS:MACHINE:source-graph:BEGIN -->\r?\n(.*?)\r?\n<!-- TTROS:MACHINE:source-graph:END -->", re.S
)
SOURCE_GRAPH_KEY = re.compile(r"[a-z][a-z0-9_-]*:[a-z0-9]+(?:-[a-z0-9]+)*")
SOURCE_GRAPH_STATUSES = {"exact", "supported"}


def source_graph_block(body: str, sha: str) -> dict:
    """Decode the optional machine block shared by prepared ICM and Luna."""
    matches = SOURCE_GRAPH_RE.findall(body)
    if not matches and "<!-- TTROS:MACHINE:source-graph:BEGIN -->" not in body:
        return {}
    if len(matches) != 1:
        raise PassageError("expected one complete source-graph block")
    block = json.loads(matches[0])
    if (not isinstance(block, dict) or block.get("schema") != SOURCE_GRAPH_SCHEMA
            or block.get("source_sha256") != sha or block.get("canonical_truth") is not False
            or block.get("stated_by") != "source" or block.get("authority") != "historical-evidence"):
        raise PassageError("invalid source-graph schema, source identity or authority")
    for name in ("about", "relationships"):
        if not isinstance(block.get(name, []), list):
            raise PassageError(f"source-graph {name} must be a list")
    return block


def source_relationship_evidence(item: dict, sha: str, text: str, *, lines: list[str]) -> dict | None:
    """Validate a typed assertion against preserved source text, irrespective of preparer."""
    if not isinstance(item, dict):
        raise PassageError("invalid source-graph relationship")
    predicate = str(item.get("predicate") or "")
    if item.get("status") not in SOURCE_GRAPH_STATUSES or predicate in {"related_to", "associated_with", "same_as"}:
        return None
    if not re.fullmatch(r"[a-z][a-z_]{1,40}", predicate):
        raise PassageError("source-graph relationship requires a typed predicate")
    keys = [str(item.get(f"{side}_key") or "") for side in ("subject", "object")]
    if keys[0] == keys[1] or any(not SOURCE_GRAPH_KEY.fullmatch(key) for key in keys):
        raise PassageError("source-graph relationship requires distinct entity keys")
    if any(not isinstance(item.get(side), str) or not item[side].strip() for side in ("subject", "object")):
        raise PassageError("source-graph relationship requires both entity names")
    refs = item.get("refs", [item.get("ref")])
    if not isinstance(refs, list) or not 1 <= len(refs) <= 12 or any(not isinstance(ref, str) for ref in refs):
        raise PassageError("source-graph relationship requires evidence references")
    spans = []
    for ref in refs:
        if OFFSET_REF_RE.fullmatch(ref):
            found, start, end = parse_offset_ref(ref)
            span = text[start:end] if 0 <= start < end <= len(text) else ""
        else:
            found, start, end = parse_ref(ref)
            span = "\n".join(lines[start - 1:end]) if 1 <= start <= end <= len(lines) else ""
        if found != sha or not span.strip():
            raise PassageError("source-graph reference does not resolve to the declared source")
        spans.append(span)
    quote = item.get("quote")
    if quote is None and len(spans) == 1:
        # Older Luna blocks attach the verified range itself as evidence.
        quote = spans[0]
    if not isinstance(quote, str) or not quote.strip() or len(quote) > MAX_PASSAGE_CHARS:
        raise PassageError("source-graph relationship requires a bounded exact quote")
    if not any(quote in span for span in spans) and quote not in "\n".join(spans):
        raise PassageError("source-graph quote does not match the cited original")
    return {"ref": refs[0], "evidence_refs": refs, "evidence_quote": quote,
            "evidence_status": item["status"], "source_sha256": sha, "source_authority": "historical-evidence"}

if __name__ == "__main__":
    raise SystemExit(main())
