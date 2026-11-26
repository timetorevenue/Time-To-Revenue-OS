"""Bounded query excerpts. Revisit: when evidence budgets change. · Last touched: 2026-11-12."""
import re
PASSAGE_MAX_CHARS = 12_000
PASSAGE_CONTEXT_LINES = 2
_QUERY_TERM_RE = re.compile(r"[a-z0-9']+")

def _bounded_passage(text: str, query: str, *, max_bytes: int | None = None, keep_headings: bool = False) -> str | None:
    """Return a small excerpt of `text` built from the lines that best match
    `query`'s terms (plus a couple of lines of surrounding context), or None
    if no line matches any term. Bounded by PASSAGE_MAX_CHARS regardless of
    the source note's size -- the point is on-demand reading of the actual
    words, not a full-document dump.

    ``max_bytes`` sizes the selection to a caller's allowance instead (UTF-8 bytes of the returned text, separators
    included), so nothing selected is cut afterwards: matching lines are taken by score first, then their headings,
    then context lines while they fit; a single matching line longer than what is left (a long paragraph) is reduced
    to its best-matching sentences, keeping a trailing citation. ``keep_headings`` also keeps the first heading (the
    title) and the heading above each selected line."""
    terms = list(dict.fromkeys(t for t in _QUERY_TERM_RE.findall(query.lower()) if len(t) > 2))
    if not terms:
        return None
    lines = text.splitlines()
    lower_lines = [line.lower() for line in lines]
    # Down-weight terms by how common they are in THIS note (a plain
    # inverse-document-frequency-style weight, computed fresh per call) --
    # a query word repeated in a speaker label or filler phrase throughout
    # the transcript (e.g. a participant's own name) must not drown out a
    # rare word that actually pinpoints the answer. A term absent from the
    # note contributes nothing (and never raises).
    term_totals = {term: sum(line.count(term) for line in lower_lines) for term in terms}
    weights = {term: (1.0 / count) if count else 0.0 for term, count in term_totals.items()}
    scored = [
        (sum(line.count(term) * weights[term] for term in terms), idx)
        for idx, line in enumerate(lower_lines)
    ]
    scored = [(score, idx) for score, idx in scored if score > 0]
    if not scored:
        return None
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    if max_bytes is not None:
        return _fitted(lines, lower_lines, scored, terms, weights, max_bytes, keep_headings)

    included: set[int] = set()
    total = 0
    for _score, idx in scored:
        lo = max(0, idx - PASSAGE_CONTEXT_LINES)
        hi = min(len(lines), idx + PASSAGE_CONTEXT_LINES + 1)
        block_new = [j for j in range(lo, hi) if j not in included]
        if not block_new:
            continue
        block_chars = sum(len(lines[j]) + 1 for j in block_new)
        if included and total + block_chars > PASSAGE_MAX_CHARS:
            continue
        included.update(block_new)
        total += block_chars
        if total >= PASSAGE_MAX_CHARS:
            break

    ordered = sorted(included)
    parts: list[str] = []
    run: list[int] = []
    for j in ordered:
        if run and j != run[-1] + 1:
            parts.append("\n".join(lines[k] for k in run))
            run = []
        run.append(j)
    if run:
        parts.append("\n".join(lines[k] for k in run))
    return "\n\n[...]\n\n".join(parts)



_HEADING_RE = re.compile(r"\s{0,3}#{1,6}\s+\S")
_TABLE_RULE_RE = re.compile(r"\s*\|[\s:|-]*-[\s:|-]*\|?\s*\Z")   # a Markdown table's |---| rule carries no content
_SEPARATOR = "\n\n[...]\n\n"
_CITATION_TAIL_RE = re.compile(r"\s*\([^()]*\)\s*\Z")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
MIN_CLIPPED_BYTES = 160


def _render(lines: list[str], chosen: dict[int, str]) -> str:
    parts, run, last = [], [], None
    for j in sorted(chosen):
        if run and j != last + 1:
            parts.append("\n".join(run))
            run = []
        run.append(chosen[j])
        last = j
    if run:
        parts.append("\n".join(run))
    return _SEPARATOR.join(parts)


def _clip_line(line: str, lower: str, terms: list[str], weights: dict[str, float], room: int) -> str | None:
    """The best-matching sentences of one long line, in their order, within ``room`` bytes (citation kept)."""
    tail_match = _CITATION_TAIL_RE.search(line)
    tail = tail_match.group(0).strip() if tail_match else ""
    core = line[:tail_match.start()] if tail_match else line
    sentences = [x for x in _SENTENCE_RE.split(core) if x.strip()]
    ranked = sorted(range(len(sentences)), key=lambda i: (-sum(sentences[i].lower().count(t) * weights[t] for t in terms), i))
    keep: list[int] = []
    for i in ranked:
        trial = " … ".join(sentences[k] for k in sorted(keep + [i])) + (" " + tail if tail else "")
        if len(trial.encode("utf-8")) <= room:
            keep.append(i)
    if not keep:
        return None
    return " … ".join(sentences[k] for k in sorted(keep)) + (" " + tail if tail else "")


def _fitted(lines: list[str], lower_lines: list[str], scored: list[tuple[float, int]], terms: list[str],
            weights: dict[str, float], max_bytes: int, keep_headings: bool) -> str | None:
    chosen: dict[int, str] = {}

    def fits(extra: dict[int, str]) -> bool:
        return len(_render(lines, {**chosen, **extra}).encode("utf-8")) <= max_bytes

    headings = [j for j, line in enumerate(lines) if _HEADING_RE.match(line)]

    def heading_above(j: int) -> int | None:
        return next((h for h in reversed(headings) if h < j), None)

    if keep_headings and headings and fits({headings[0]: lines[headings[0]]}):
        chosen[headings[0]] = lines[headings[0]]
    hits = [idx for _score, idx in scored]
    for idx in hits:
        if idx in chosen:
            continue
        heading = heading_above(idx) if keep_headings else None
        extra = {idx: lines[idx], **({heading: lines[heading]} if heading is not None and heading not in chosen else {})}
        if fits(extra):
            chosen.update(extra)
        elif fits({idx: lines[idx]}):
            chosen[idx] = lines[idx]
        else:
            room = max_bytes - len(_render(lines, {**chosen, idx: ""}).encode("utf-8"))
            clipped = _clip_line(lines[idx], lower_lines[idx], terms, weights, room) if room >= MIN_CLIPPED_BYTES else None
            if clipped and fits({idx: clipped}):
                chosen[idx] = clipped
                if heading is not None and heading not in chosen and fits({heading: lines[heading]}):
                    chosen[heading] = lines[heading]
    if not any(idx in chosen for idx in hits):
        return None
    for distance in range(1, PASSAGE_CONTEXT_LINES + 1):
        for idx in hits:
            if idx not in chosen:
                continue
            for j in (idx - distance, idx + distance):
                if 0 <= j < len(lines) and j not in chosen and not _TABLE_RULE_RE.match(lines[j]) and fits({j: lines[j]}):
                    chosen[j] = lines[j]
    return _render(lines, chosen)
