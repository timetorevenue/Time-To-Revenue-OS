#!/usr/bin/env python3
"""Retrieval evaluation for David's search path: correctness, source grounding, latency, returned tokens.

Runs tests/fixtures/retrieval_eval.json through ``brain_memory_mcp.search_history`` (the tool David calls) and scores,
per question: whether a verified source passage overlapping the gold lines is returned (and at which rank), whether
any result is tied to the gold source, whether the gold topic note is returned, whether the answer term appears in
the top-3 excerpts, the estimated tokens of the returned result (characters / 4) and wall-clock latency.
``--legacy`` scores the pre-2026-11-12 shape (eight rows, 600-character snippets, no passages) for comparison.
Local only: no model other than the search's own embedding model is used. Revisit: when the fixture or result shape changes.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "tools")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

FIXTURE = ROOT / "tests" / "fixtures" / "retrieval_eval.json"


def _tokens(value) -> int:
    return (len(json.dumps(value, ensure_ascii=False)) + 3) // 4


def _legacy(query: str) -> dict:
    import aos_indexer
    result = aos_indexer.search(query, source="business_brain", limit=8, client_scope="global", hybrid=True)
    rows = [row for group in result["groups"].values() for row in group]
    return {"success": True, "matches": [{"pointer": r["path"], "title": r.get("title"), "modified": r.get("modified"),
                                          "snippet": r.get("snippet")} for r in rows]}


def _item_sha(item: dict) -> str:
    ref = str(item.get("ref") or item.get("source_ref") or "")
    if ref.startswith("source:"):
        return ref[7:71]
    pointer = str(item.get("pointer") or "")
    return pointer.rsplit("/", 1)[-1][:64] if "/records/" in pointer else ""


def score(question: dict, result: dict, ms: float) -> dict:
    gold, (low, high) = question["gold"]["sha"], question["gold"]["lines"]
    matches = result.get("matches") or []
    passage_rank = source_rank = note_rank = None
    for rank, item in enumerate(matches, 1):
        ref = str(item.get("ref") or "")
        if passage_rank is None and ref.startswith(f"source:{gold}#L"):
            a, _, b = ref.rsplit("#L", 1)[1].partition("-L")
            if int(a) <= high and low <= int(b or a):
                passage_rank = rank
        pointer = str(item.get("pointer") or "")
        if source_rank is None and (_item_sha(item) == gold or gold[:8] in pointer):
            source_rank = rank
        if note_rank is None and question.get("gold_note") and question["gold_note"] in pointer and gold[:8] in pointer:
            note_rank = rank
    top3 = " ".join(str(i.get("excerpt") or i.get("snippet") or "") for i in matches[:3]).lower()
    return {"id": question["id"], "group": question["group"], "passage_rank": passage_rank, "source_rank": source_rank,
            "note_rank": note_rank, "answer_in_top3": question["answer_term"].lower() in top3,
            "tokens": _tokens(result), "latency_ms": round(ms, 1), "results": len(matches)}


def run(legacy: bool = False, repeats: int = 3, fixture: Path = FIXTURE) -> dict:
    import brain_memory_mcp
    questions = json.loads(Path(fixture).read_text(encoding="utf-8"))
    call = _legacy if legacy else brain_memory_mcp.search_history
    rows = []
    for question in questions:
        times = []
        for _ in range(repeats):
            start = time.perf_counter()
            result = call(question["question"])
            times.append((time.perf_counter() - start) * 1000)
        rows.append(score(question, result, statistics.median(times)))
    def rate(key, group=None, k=None):
        pool = [r for r in rows if group in (None, r["group"])]
        if not pool:
            return None
        if key == "answer_in_top3":
            return sum(r[key] for r in pool) / len(pool)
        return sum(1 for r in pool if r[key] is not None and (k is None or r[key] <= k)) / len(pool)
    summary = {"questions": len(rows),
               "passage_hit@1": rate("passage_rank", k=1), "passage_hit@3": rate("passage_rank", k=3), "passage_hit@5": rate("passage_rank", k=5),
               "source_hit@3": rate("source_rank", k=3), "source_hit@5": rate("source_rank", k=5),
               "note_hit@5(lucian)": rate("note_rank", "lucian", 5), "answer_in_top3": rate("answer_in_top3"),
               "lucian_passage_hit@3": rate("passage_rank", "lucian", 3), "heldout_passage_hit@3": rate("passage_rank", "heldout", 3),
               "tokens_median": statistics.median(r["tokens"] for r in rows), "tokens_max": max(r["tokens"] for r in rows),
               "latency_ms_median": statistics.median(r["latency_ms"] for r in rows),
               "latency_ms_max": max(r["latency_ms"] for r in rows)}
    return {"mode": "legacy" if legacy else "current", "summary": summary, "rows": rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--legacy", action="store_true")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--rows", action="store_true")
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    args = parser.parse_args(argv)
    report = run(args.legacy, args.repeats, args.fixture)
    print(json.dumps(report if args.rows else report["summary"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
