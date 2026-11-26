"""Step journal for one queue job: ``queue/threads/<item_id>.jsonl``.

Each completed plan step is appended once, with the hash of its resolved input
and its full result. When the runner re-executes a recovered item, the plan
runner replays this file and skips every step already recorded with the same
input, agent and package content. A kill between a model call and its journal
line costs one repeated call and nothing else. This is a journal for one job,
not a queue: aos_jobs owns claims, recovery, receipts and notifications.

Revisit: when recovery semantics or the plan step shape change. · Last touched: 2026-11-14.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from aos_job_ids import storage_key

THREADS_DIR = Path("queue") / "threads"
_LOCK = threading.Lock()


class StepJournal:
    def __init__(self, root: Path, job: str):
        safe = re.sub(r"[^A-Za-z0-9_-]+", "-", str(job)).strip("-")
        if not safe:
            raise ValueError("a journal needs a job id")
        self.path = Path(root) / THREADS_DIR / f"{storage_key(safe)}.jsonl"

    def records(self) -> list[dict]:
        if not self.path.is_file():
            return []
        rows = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue  # a torn final line from a kill mid-append is not a completed step
            if isinstance(row, dict) and row.get("step"):
                rows.append(row)
        return rows

    def completed(self, plan_ref: str) -> dict[str, dict]:
        """Latest ok record per step for this plan version."""
        done: dict[str, dict] = {}
        for row in self.records():
            if row.get("plan") == plan_ref and (row.get("result") or {}).get("status") == "ok":
                done[str(row["step"])] = row
        return done

    def append(self, *, plan_ref: str, step: str, agent: str, package_sha256: str, input_sha256: str, result: dict) -> dict:
        record = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "plan": plan_ref, "step": step,
                  "agent": agent, "package_sha256": package_sha256, "input_sha256": input_sha256, "result": result}
        line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        with _LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Start on a fresh line if a previous append was torn by a kill.
            torn = self.path.is_file() and self.path.stat().st_size > 0 and not self.path.read_bytes().endswith(b"\n")
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(("\n" if torn else "") + line)
                handle.flush()
                os.fsync(handle.fileno())
        return record
