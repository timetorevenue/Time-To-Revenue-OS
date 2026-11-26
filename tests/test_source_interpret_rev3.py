"""Revision-3 single-pass ingestion: lossless offsets, segment citations, evidence status, typed edges, retrieval.

The nine checks of the 2026-11-12 brief, on synthetic fixtures (tests/source_rev3_fixtures.py) with the Luna output
mocked through the existing ``llm=`` parameter: zero model calls, no network, temp Brain (git), temp search DB, temp
Graphify root. Golden files in tests/fixtures/source_interpret_golden/ were produced by the unmodified e7f3cc9 code.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "tools")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import aos_embeddings  # noqa: E402
import aos_indexer  # noqa: E402
from tests import source_rev3_fixtures as F  # noqa: E402
from tests.business_brain_test_support import make_registry, registry_data  # noqa: E402
from tests.test_memory_filing import vault  # noqa: E402
from tools import brain_memory, source_intake  # noqa: E402
from tools import memory_exchange_import as mei  # noqa: E402
from tools import source_interpret as si  # noqa: E402
from tools import source_passages as sp  # noqa: E402
from tools import v3_grounding_check as grounding  # noqa: E402

RECORDS = source_intake.RECORDS_RELATIVE
GOLDEN = ROOT / "tests" / "fixtures" / "source_interpret_golden"
USAGE = {"available": True, "input_tokens": 1, "output_tokens": 1, "cached_input_tokens": 0, "reasoning_output_tokens": 0}


def write_record(brain: Path, raw: bytes, name: str) -> str:
    sha = hashlib.sha256(raw).hexdigest()
    item = source_intake.SourceItem(PurePosixPath(name), raw, sha, "text/plain", name, None)
    path = brain / RECORDS / f"{sha}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source_intake.render_record(item, "2026-11-12T00:00:00Z"), encoding="utf-8")
    return sha


def fake_llm(response: dict, calls: list):
    def llm(prompt, *, root, sha):
        calls.append(prompt)
        return {"text": json.dumps(response, ensure_ascii=False), "usage": USAGE, "session_id": "mock", "model": "gpt-6-luna",
                "reasoning_effort": "medium", "scope": None}
    return llm


def link_targets(text: str) -> set[str]:
    return {mei.canonical_wiki_target(raw) for raw in mei.WIKI_LINK_RE.findall(text)}


class Workspace:
    """A temp repo root and a temp Brain holding both fixture originals."""

    def __init__(self, test: unittest.TestCase):
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.brain = self.root / "TTROS Business Brain"
        self.one_text = F.one_line_text()
        self.one_raw = self.one_text.encode("utf-8")
        self.multi_raw = F.multiline_bytes()
        self.multi_text = self.multi_raw.decode("utf-8")
        self.brain.mkdir()
        self.one_sha = write_record(self.brain, self.one_raw, "Marisol Ortega - Northwind Field Service Briefing - Transcript.txt")
        self.multi_sha = write_record(self.brain, self.multi_raw, "harbour-pricing-playbook.md")
        self.calls: list = []

    def interpret(self, which: str = "one") -> dict:
        sha, text = (self.one_sha, self.one_text) if which == "one" else (self.multi_sha, self.multi_text)
        response = F.one_line_response(text) if which == "one" else F.multiline_response(text)
        return si.interpret(sha, brain_root=self.brain, root=self.repo, llm=fake_llm(response, self.calls), allow_model_call=True)

    def build(self, which: str = "one", **kwargs) -> dict:
        interp = self.interpret(which)
        sha, raw = (self.one_sha, self.one_raw) if which == "one" else (self.multi_sha, self.multi_raw)
        name = ("Marisol Ortega - Northwind Field Service Briefing - Transcript.txt" if which == "one"
                else "harbour-pricing-playbook.md")
        return si.build_v3_package(interp, sha=sha, raw=raw, original_filename=name, lines=sp.split_lines(raw),
                                   package_dir=self.repo / f"pkg-{which}", created_at="2026-11-12T00:00:00Z",
                                   brain_root=self.brain, **kwargs)


class PreparedICMBypassTests(unittest.TestCase):
    def test_exact_drive_identity_and_full_sha_bypass_interpretation_and_replay_deterministically(self):
        w = Workspace(self)
        pkg = w.repo / si.PACKAGE_RELATIVE / "prepared"
        pkg.mkdir(parents=True)
        (pkg / "INGEST_MANIFEST.json").write_text(json.dumps({
            "prepared_package_folder_id": si.CHATGPT_PREPARED_ICM_FOLDER_ID,
            "source": {"sha256": w.one_sha, "original_drive_id": "drive-source-1"}}), encoding="utf-8")
        receipt = {"outcome": "already_imported", "search_refresh": {"status": "success"},
                   "graphify_refresh": {"status": "success"}}
        with mock.patch.object(si, "drive_provenance", return_value={"drive_id": "drive-source-1"}), \
                mock.patch.object(mei, "validate_v3") as validate, \
                mock.patch.object(mei, "process_v3", return_value=receipt) as process, \
                mock.patch.object(si, "interpret", side_effect=AssertionError("semantic interpretation called")):
            first = si.run(w.one_sha, brain_root=w.brain, root=w.repo, allow_model_call=True)
            second = si.run(w.one_sha, brain_root=w.brain, root=w.repo, allow_model_call=True)
        self.assertEqual("reused", first["prepared_icm"]["status"])
        self.assertEqual("already_imported", second["prepared_icm"]["outcome"])
        self.assertEqual(2, validate.call_count)
        self.assertEqual(2, process.call_count)

    def test_hash_only_or_wrong_identity_candidate_is_held_before_interpretation(self):
        for identity in (None, "another-drive-source"):
            with self.subTest(identity=identity):
                w = Workspace(self)
                pkg = w.repo / si.PACKAGE_RELATIVE / "prepared"
                pkg.mkdir(parents=True)
                source = {"sha256": w.one_sha}
                if identity:
                    source["original_drive_id"] = identity
                (pkg / "INGEST_MANIFEST.json").write_text(json.dumps({
                    "prepared_package_folder_id": si.CHATGPT_PREPARED_ICM_FOLDER_ID,
                    "source": source}), encoding="utf-8")
                with mock.patch.object(si, "drive_provenance", return_value={"drive_id": "drive-source-1"}), \
                        mock.patch.object(si, "interpret", side_effect=AssertionError("semantic interpretation called")):
                    out = si.run(w.one_sha, brain_root=w.brain, root=w.repo, allow_model_call=True)
                self.assertEqual("held", out["prepared_icm"]["status"])


def note(built: dict, slug: str) -> str:
    return (built["package"] / "notes" / f"{slug}.md").read_text(encoding="utf-8")


# ----------------------------------------------------------------------------- 1. lossless offset addressing
class OffsetAddressingTests(unittest.TestCase):
    def assert_exact(self, brain: Path, sha: str, raw: bytes, text: str, a: int, b: int) -> dict:
        got = sp.read_passage(brain, sp.offset_ref(sha, a, b))
        self.assertTrue(got["verified"])
        self.assertEqual(text[a:b], got["exact"])
        self.assertEqual(len(text[:a].encode("utf-8")), got["byte_start"])
        self.assertEqual(raw[got["byte_start"]:got["byte_end"]], got["exact"].encode("utf-8"))
        self.assertEqual(sp.offset_ref(sha, a, b), got["ref"])
        self.assertNotIn("﻿", got["text"])
        self.assertNotIn("\r", got["text"])
        return got

    def test_offset_refs_return_exact_text_and_bytes_at_start_middle_and_end_of_both_fixtures(self):
        w = Workspace(self)
        t = w.one_text
        self.assertEqual(0, t.count("\n"))
        self.assertGreater(len(t), 25_000)
        middle = t.index("missed appointments fell from 18% to 7%")
        late = t.index("Halifax depot will trial assigned vans")
        for a, b in ((0, 120), (middle, middle + 160), (late - 40, len(t))):
            self.assert_exact(w.brain, w.one_sha, w.one_raw, t, a, b)
        self.assertIn("18% to 7%", sp.read_passage(w.brain, sp.offset_ref(w.one_sha, middle, middle + 160))["text"])
        self.assertGreater(late, sp.MAX_PASSAGE_CHARS * 3)            # far past what a line reference could reach
        # A span across a multi-byte run (curly quotes, emoji, non-Latin) re-encodes byte-exactly.
        emoji = t.index("🚚")
        self.assertGreater(len(t[emoji - 30:emoji + 30].encode()), 60)
        self.assert_exact(w.brain, w.one_sha, w.one_raw, t, emoji - 30, emoji + 30)
        m = w.multi_text
        self.assertTrue(m.startswith("﻿") and "\r\n" in m)
        quebec = m.index("## Retainer pricing")
        for a, b in ((0, 40), (quebec, m.index("is the rule.") + len("is the rule.\r\n")), (len(m) - 60, len(m))):
            got = self.assert_exact(w.brain, w.multi_sha, w.multi_raw, m, a, b)
        start = sp.read_passage(w.brain, sp.offset_ref(w.multi_sha, 0, 40))
        self.assertTrue(start["exact"].startswith("﻿") and start["exact"].encode().startswith(b"\xef\xbb\xbf"))
        crossing = sp.read_passage(w.brain, sp.offset_ref(w.multi_sha, quebec, m.index("is the rule.") + len("is the rule.\r\n")))
        self.assertIn("\r\n", crossing["exact"])
        self.assertIn("Québec", crossing["text"])
        self.assertIn("“no surcharge”", crossing["text"])
        self.assertTrue(got["exact"].endswith("\r\n"))

    def test_long_spans_are_bounded_from_their_start_and_bad_offsets_are_refused(self):
        w = Workspace(self)
        a = 10_000
        self.assertGreater(len(w.one_text) - a, sp.MAX_PASSAGE_CHARS)
        got = sp.read_passage(w.brain, sp.offset_ref(w.one_sha, a, len(w.one_text)))
        self.assertTrue(got["truncated"])
        self.assertEqual(w.one_text[a:a + sp.MAX_PASSAGE_CHARS], got["exact"])
        self.assertEqual(sp.offset_ref(w.one_sha, a, a + sp.MAX_PASSAGE_CHARS), got["ref"])
        for bad in ((0, len(w.one_text) + 1), (50, 50), (60, 10)):
            with self.assertRaises(sp.PassageError):
                sp.read_passage(w.brain, sp.offset_ref(w.one_sha, *bad))

    def test_legacy_line_refs_return_exactly_what_they_returned_before(self):
        w = Workspace(self)
        golden = json.loads((GOLDEN / "legacy_line_refs.json").read_text(encoding="utf-8"))
        for name, sha in (("one_line", w.one_sha), ("multiline", w.multi_sha)):
            self.assertEqual(golden[name]["sha256"], sha)
            for ref, expected in golden[name]["reads"].items():
                got = sp.read_passage(w.brain, f"source:{sha}#{ref}")
                got.pop("record")
                self.assertEqual(expected, got, ref)

    def test_segments_are_deterministic_sentence_bounded_and_never_cross_a_line(self):
        w = Workspace(self)
        for text in (w.one_text, w.multi_text):
            segs = sp.segments(text)
            self.assertEqual(segs, sp.segments(text))
            self.assertEqual(sorted(segs), segs)
            for (a, b), (c, _d) in zip(segs, segs[1:]):
                self.assertLessEqual(b, c)
            for a, b in segs:
                piece = text[a:b]
                self.assertTrue(piece and len(piece) <= sp.SEGMENT_MAX)
                self.assertFalse(set(piece) & {"\n", "\r", "﻿"})
        lines = [line for line in sp.split_lines(w.multi_raw) if line.strip()]
        self.assertEqual(lines, [sp.display_text(w.multi_text[a:b]) for a, b in sp.segments(w.multi_text)])   # a short line is one segment
        long_segments = sp.segments(w.one_text)
        self.assertGreater(len(long_segments), 40)
        self.assertGreater(sum(1 for a, b in long_segments if b - a >= sp.SEGMENT_MIN), len(long_segments) * 0.8)

    def test_passage_rows_split_a_long_line_and_leave_short_line_windows_unchanged(self):
        w = Workspace(self)
        rows = sp.passage_windows(w.one_text)
        self.assertGreater(len(rows), 10)
        self.assertTrue(all(label.startswith("C") for label, *_ in rows))
        ends = [int(label[1:].split("-")[1]) for label, *_ in rows]
        self.assertEqual(max(ends), len(w.one_text.rstrip()))
        self.assertTrue(all(len(body) <= sp.INDEX_MAX_CHARS for *_x, body in rows))
        multi = sp.passage_windows(w.multi_text)
        self.assertEqual([(f"L{a}-L{b}", a, b, t) for a, b, t in sp.index_windows(sp.split_lines(w.multi_raw))], multi)
        pointer = f"business_brain:{RECORDS}/{w.one_sha}.md#{rows[-1][0]}"
        self.assertTrue(aos_indexer.is_passage_pointer(pointer))
        self.assertEqual(f"source:{w.one_sha}#{rows[-1][0]}", aos_indexer.passage_source_ref(pointer))
        self.assertTrue(aos_embeddings.is_embeddable(pointer))


# ----------------------------------------------------------------------------- 2. evidence status and coverage
class EvidenceStatusTests(unittest.TestCase):
    def test_note_evidence_status_counts_include_the_rendered_one_line_summary(self):
        w = Workspace(self)
        interp = w.interpret()
        topic = next(t for t in interp["topics"] if t.get("one_line"))
        # Exercise the valid cited-only state deterministically; the response and
        # all source bytes are local fixtures and the model entrypoint is mocked.
        previous = topic.get("one_line_status")
        if previous:
            interp["evidence"][previous] -= 1
        topic["one_line_status"] = "cited"
        interp["evidence"]["cited"] += 1
        built = si.build_v3_package(
            interp, sha=w.one_sha, raw=w.one_raw,
            original_filename="Marisol Ortega - Northwind Field Service Briefing - Transcript.txt",
            lines=sp.split_lines(w.one_raw), package_dir=w.repo / "cited-summary-package",
            created_at="2026-11-12T00:00:00Z", brain_root=w.brain,
        )
        filed = note(built, topic["slug"])
        expected_counts = {status: 0 for status in grounding.EVIDENCE_STATUSES[:3]}
        for field in ("explanation", "quotes", "principles", "steps", "decisions", "actions", "outcomes", "claims", "open_loops"):
            for item in topic.get(field) or []:
                expected_counts[item["status"]] += 1
        expected_counts["cited"] += 1  # one_line_status, rendered in the Summary section
        expected = "; ".join(f"{status}={expected_counts[status]}" for status in grounding.EVIDENCE_STATUSES[:3])
        self.assertIn(f'evidence_status: "{expected}"', filed)
        self.assertRegex(filed, rf"{re.escape(grounding.UNVERIFIED_MARK)}\)")

    def test_revision3_package_carries_available_drive_identity_and_original_title(self):
        w = Workspace(self)
        drive_id = "1aeoynpndC3HaCttuoW98GvumdvvQ0fr4"
        drive_title = "Planning Software Is Planning Choices. Here's How I Sketch It With AI-Tobias Hartley.txt"
        record = w.brain / RECORDS / f"{w.one_sha}.md"
        item = source_intake.SourceItem(PurePosixPath(drive_title), w.one_raw, w.one_sha,
                                        "text/plain", drive_title, None)
        record.write_text(source_intake.render_record(item, "2026-11-12T00:00:00Z",
                                                      source_path=f"drive:eligible/{drive_title}"), encoding="utf-8")
        provenance = w.brain / "sources/intake/drive" / f"{drive_id}.md"
        provenance.parent.mkdir(parents=True)
        provenance.write_text(
            "# Drive source provenance\n\n```json\n" + json.dumps({
                "drive_id": drive_id,
                "original_path": f"eligible/{drive_title}",
                "url": f"https://drive.google.com/file/d/{drive_id}/view",
                "source_record": f"business_brain:{RECORDS}/{w.one_sha}.md",
            }, ensure_ascii=False) + "\n```\n",
            encoding="utf-8",
        )

        interp = w.interpret()
        built = si.build_v3_package(
            interp, sha=w.one_sha, raw=w.one_raw, original_filename=drive_title,
            lines=sp.split_lines(w.one_raw), package_dir=w.repo / "drive-provenance-package",
            created_at="2026-11-12T00:00:00Z", brain_root=w.brain,
        )
        notes = [path.read_text(encoding="utf-8") for path in (built["package"] / "notes").glob("*.md")]
        self.assertTrue(notes)
        for filed in notes:
            self.assertIn(f'original_filename: "{drive_title}"', filed)
            self.assertIn(f'source_sha256: {w.one_sha}', filed)
            self.assertIn(f'source_record: business_brain:{RECORDS}/{w.one_sha}.md', filed)
            self.assertIn(f'source_drive_url: "https://drive.google.com/file/d/{drive_id}/view"', filed)
            self.assertIn(f"[[sources/intake/drive/{drive_id}|Drive provenance]]", filed)

    def test_all_four_statuses_occur_and_are_counted_and_the_wrong_figure_fails(self):
        w = Workspace(self)
        interp = w.interpret()
        self.assertEqual(3, interp["extraction_schema"])
        tally = {s: 0 for s in grounding.EVIDENCE_STATUSES}
        for t in interp["topics"]:
            for field in ("explanation", "quotes", "principles", "steps", "decisions", "actions", "outcomes", "claims", "open_loops"):
                for x in t[field]:
                    tally[x["status"]] += 1
            if t["one_line_status"]:
                tally[t["one_line_status"]] += 1
        for r in interp["relationships"]:
            tally[r["status"]] += 1
        tally["failed"] = sum(1 for d in interp["dropped"] if d.get("status") == "failed")
        self.assertEqual(tally, interp["evidence"])
        self.assertTrue(all(interp["evidence"][s] >= 1 for s in grounding.EVIDENCE_STATUSES), interp["evidence"])
        wrong = next(d for d in interp["dropped"] if d["kind"] == "claim")
        self.assertEqual("failed", wrong["status"])
        self.assertIn("'4'", wrong["reason"])
        self.assertIn("does not resolve", next(d for d in interp["dropped"] if d["kind"] == "action")["reason"])
        principle = next(t for t in interp["topics"] if t["slug"] == "why-missed-appointments-fell")["principles"][0]
        self.assertEqual("cited", principle["status"])
        self.assertEqual("exact", next(t for t in interp["topics"] if t["slug"] == "service-call-tiers")["quotes"][0]["status"])
        statuses = {(r["predicate"], r["status"]) for r in interp["relationships"]}
        self.assertEqual({("integrates_with", "supported"), ("recommends", "cited"), ("related_to", "supported")}, statuses)
        self.assertIn("relationship", {d["kind"] for d in interp["dropped"] if d.get("status") == "failed"})

    def test_coverage_is_measured_by_character_and_a_whole_source_reference_never_counts(self):
        w = Workspace(self)
        interp = w.interpret()
        cov = interp["coverage"]
        self.assertLess(cov["share"], 1.0)
        self.assertGreater(cov["share"], 0.0)
        self.assertTrue(cov["uncovered"])
        overview = next(t for t in interp["topics"] if t["slug"] == "briefing-overview")
        segs = sp.segments(w.one_text)
        self.assertEqual(f"C{segs[0][0]}-{segs[-1][1]}", overview["spans"])       # it cites the whole source
        self.assertEqual("", overview["covered"])                                  # ... which covers nothing
        src = si.SegmentedSource(w.one_text)
        without = si.coverage_report([t for t in interp["topics"] if t["slug"] != "briefing-overview"], src)
        self.assertEqual(without["covered_chars"], cov["covered_chars"])
        only_whole = si.coverage_report([overview], src)
        self.assertEqual(0, only_whole["covered_chars"])

    def test_evidence_status_rules(self):
        span = "Since adopting Northwind Dispatch, missed appointments fell from 18% to 7% in six weeks."
        es = grounding.evidence_status
        self.assertEqual("exact", es("missed appointments fell from 18%", span, quote=True)[0])
        self.assertEqual("failed", es("missed appointments fell from 19%", span, quote=True)[0])
        self.assertEqual("supported", es("Missed appointments fell from 18% to 7% after adopting Northwind Dispatch.", span)[0])
        self.assertEqual("failed", es("Missed appointments fell to 4%.", span)[0])
        self.assertEqual("failed", es("Missed appointments fell after adopting Vantage CRM.", span)[0])
        self.assertEqual("cited", es("Reliability builds lasting customer loyalty.", span)[0])
        self.assertEqual("failed", es("Anything at all.", None)[0])
        self.assertEqual("supported", es("Missed appointments fell, according to Marisol Ortega.", span,
                                         attributed=frozenset({"marisol", "ortega"}))[0])


# ----------------------------------------------------------------------------- 3-4. rendering, depth, identities
class RenderingTests(unittest.TestCase):
    def test_more_than_twelve_topics_and_long_explanations_survive_to_rendered_notes(self):
        w = Workspace(self)
        built = w.build()
        interp = w.interpret()
        self.assertGreater(len(interp["topics"]), si.MAX_TOPICS)
        self.assertEqual([t["slug"] for t in interp["topics"]], built["topics_filed"])     # every topic, any importance
        self.assertEqual([], built["topics_not_filed"])
        playbook = note(built, "field-service-playbook")
        self.assertGreater(len(playbook.encode()), 4096)
        for para in next(t for t in interp["topics"] if t["slug"] == "field-service-playbook")["explanation"]:
            self.assertIn(para["text"], playbook)
        long = max((p["text"] for t in interp["topics"] for p in t["explanation"]), key=len)
        self.assertGreater(len(long), 240)
        body = playbook.split("\n---\n", 1)[1]
        headings = re.findall(r"(?m)^## (.+)$", body)
        self.assertEqual("Source", headings[-1])                                      # knowledge first, Source block last
        self.assertLess(headings.index("Explanation"), headings.index("Source"))
        self.assertRegex(body, r"\(C\d+-\d+(, C\d+-\d+)*\)")
        why = note(built, "why-missed-appointments-fell")
        self.assertIn(f"{grounding.UNVERIFIED_MARK})", why)                           # cited, rendered with its marker
        results = note(built, "missed-appointment-results")
        self.assertNotIn("4%", results)                                              # failed: not rendered as knowledge
        self.assertNotIn("never used text confirmations", note(built, "halifax-counterexample"))
        self.assertIn("extraction_schema: 3", results)

    def test_saved_revision_one_and_two_interpretations_render_byte_identically(self):
        from tests import test_source_interpret as T
        raw = T.SOURCE.encode()
        sha = hashlib.sha256(raw).hexdigest()
        for rev in ("rev1", "rev2"):
            interp = json.loads((GOLDEN / rev / "interpretation.json").read_text(encoding="utf-8"))
            meta = json.loads((GOLDEN / rev / "meta.json").read_text(encoding="utf-8"))
            with tempfile.TemporaryDirectory() as tmp:
                built = si.build_v3_package(interp, sha=sha, raw=raw, original_filename=meta["original_filename"],
                                            lines=T.SOURCE.split("\n")[:-1], package_dir=Path(tmp) / "pkg",
                                            created_at=meta["created_at"], resolve_key=meta["resolver"].get)
                got = {p.relative_to(built["package"]).as_posix(): p.read_bytes() for p in built["package"].rglob("*") if p.is_file()}
            want = {p.relative_to(GOLDEN / rev / "package").as_posix(): p.read_bytes()
                    for p in (GOLDEN / rev / "package").rglob("*") if p.is_file()}
            self.assertEqual(want, got, rev)

    def test_entities_stay_with_their_own_topics_on_a_one_line_source(self):
        w = Workspace(self)
        built = w.build(resolve_key=lambda name: None, resolve_other=lambda name: "prospect:2026-09-vantage" if name == "Vantage CRM" else None)

        def entities(slug: str) -> str:
            return re.search(r'(?m)^entities: "(.*)"$', note(built, slug)).group(1)
        self.assertEqual("Halifax depot", entities("halifax-counterexample"))
        self.assertEqual("Northwind Dispatch", entities("missed-appointment-results"))
        self.assertEqual("", entities("dispatch-method"))
        self.assertNotIn("Halifax", entities("crm-integration"))
        self.assertNotIn("Northwind Dispatch", entities("halifax-counterexample"))
        crm = note(built, "crm-integration")
        self.assertIn("entity_key: [prospect:2026-09-vantage]", crm)                  # exact established id reused
        self.assertNotIn("topic:northwind-dispatch", crm.split("\n---\n", 1)[0])       # no second identity declared
        self.assertIn("entity_key: [person:marisol-ortega]", note(built, "briefing-intro"))
        self.assertNotIn("entity_key", note(built, "halifax-counterexample").split("\n---\n", 1)[0])

    def test_grounding_check_passes_offset_notes_and_keeps_unverifiable_separate(self):
        w = Workspace(self)
        for which in ("one", "multi"):
            built = w.build(which)
            report = grounding.build_report([grounding.load_package(built["package"])])
            failing = [c for p in report["packages"] for c in p["checks"] if c["status"] == "fail"]
            self.assertEqual([], failing)
            kinds = report["packages"][0]["by_kind"]
            self.assertGreater(kinds["evidence"]["pass"], 0)
            self.assertGreater(kinds["evidence"]["unverifiable"], 0)                   # cited items stay unverifiable
            self.assertEqual({"pass": 1, "fail": 0, "unverifiable": 0},
                             {k: sum(1 for c in report["packages"][0]["checks"] if c["ref"] == "INDEX:coverage:complete"
                                     and c["status"] == k) for k in ("pass", "fail", "unverifiable")})

    def test_the_topic_map_card_stays_within_its_cap(self):
        from tools import source_intake_semantic as sis
        w = Workspace(self)
        interp = w.interpret()
        card = sis.render_interpretation_card(source_id=w.one_sha, source_path=f"{RECORDS}/{w.one_sha}.md", source_sha256=w.one_sha,
                                              ingested_at="2026-11-12T00:00:00Z", interpretation=si.topic_map_card_fields(interp),
                                              receipt_relative="queue/x")
        self.assertLessEqual(len(card.encode()), sis.INTERPRETATION_CARD_BYTE_CAP)
        self.assertRegex(card, rf"source:{w.one_sha}#C\d+-\d+")
        # No quotas on what is filed means the card must bound itself: a very rich interpretation still fits.
        rich = {**interp, "topics": interp["topics"] * 12, "uncertainty": ["x" * 200] * 60,
                "entities": [{**interp["entities"][0], "name": f"Entity number {i} with a long name"} for i in range(300)]}
        card = sis.render_interpretation_card(source_id=w.one_sha, source_path=f"{RECORDS}/{w.one_sha}.md", source_sha256=w.one_sha,
                                              ingested_at="2026-11-12T00:00:00Z", interpretation=si.topic_map_card_fields(rich),
                                              receipt_relative="queue/x")
        self.assertLessEqual(len(card.encode()), sis.INTERPRETATION_CARD_BYTE_CAP)


# ----------------------------------------------------------------------------- 5-9. import, graph, search, assembler, rerun
SYNONYMS = {"shows": "missed", "noshows": "missed", "scheduling": "dispatch", "tool": "dispatch", "change": "fell",
            "drop": "fell", "absences": "missed"}


class FakeEmbedder:
    def __init__(self):
        self.load_ms = 0.0
        self.texts_embedded = 0

    def embed(self, texts):
        out = []
        for text in texts:
            vector = np.zeros(128, dtype=np.float32)
            for word in re.findall(r"[a-z]+", text.lower()):
                word = SYNONYMS.get(word, word)
                vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % 128] += 1.0
            out.append(vector / (np.linalg.norm(vector) or 1.0))
            self.texts_embedded += 1
        return np.vstack(out)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None


def covered(pointers):
    return {"status": "success"}, {"status": "success", "coverage": [{"pointer": p} for p in pointers]}


class ImportedBrainTests(unittest.TestCase):
    """One fixture package imported through process_v3 into a temp git Brain; graph, search and assembler on top."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name).resolve()
        cls.root, cls.repo, cls.brain = root, root / "repo", root / "TTROS Business Brain"
        cls.repo.mkdir()
        vault(cls.brain, notes={})
        cls.text = F.one_line_text()
        cls.raw = cls.text.encode()
        cls.sha = write_record(cls.brain, cls.raw, "Marisol Ortega - Northwind Field Service Briefing - Transcript.txt")
        subprocess.run(["git", "init", "-q", str(cls.brain)], check=True)
        subprocess.run(["git", "-C", str(cls.brain), "add", "."], check=True)
        subprocess.run(["git", "-C", str(cls.brain), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "commit", "-qm", "fixture"], check=True)
        cls.patchers = [mock.patch.object(brain_memory, "VAULT_ROOT", cls.brain),
                        mock.patch("tools.brain_git_closure.push_commit", return_value="local_only"),
                        mock.patch("tools.brain_git_closure.record"),
                        mock.patch("tools.aos_indexer.index_one", return_value={"status": "success"}),
                        mock.patch("tools.aos_entity_index.ensure_current"),
                        mock.patch.dict(os.environ, {"TTROS_MEMORY_FILING": "0"})]
        for patcher in cls.patchers:
            patcher.start()
        cls.calls = []
        cls.interp = si.interpret(cls.sha, brain_root=cls.brain, root=cls.repo, allow_model_call=True,
                                  llm=fake_llm(F.one_line_response(cls.text), cls.calls))
        cls.built = si.build_v3_package(cls.interp, sha=cls.sha, raw=cls.raw,
                                        original_filename="Marisol Ortega - Northwind Field Service Briefing - Transcript.txt",
                                        lines=sp.split_lines(cls.raw), package_dir=cls.repo / "pkg", created_at="2026-11-12T00:00:00Z",
                                        brain_root=cls.brain)
        cls.receipt = mei.process_v3(cls.built["package"], brain=cls.brain, root=cls.repo, refresh=covered)
        cls.folder = cls.built["folder"]
        cls.pointers = {slug: f"business_brain:{cls.folder}{slug}.md" for slug in cls.built["topics_filed"]}

    @classmethod
    def tearDownClass(cls):
        for patcher in reversed(cls.patchers):
            patcher.stop()
        cls.tmp.cleanup()

    # 5 ----------------------------------------------------------------------
    def test_package_imports_through_process_v3_with_every_link_resolving(self):
        self.assertEqual("imported", self.receipt["outcome"], self.receipt.get("reason"))
        self.assertEqual(len(self.built["topics_filed"]) + 1, len(self.receipt["outputs"]))
        from tools.validate_business_brain import analyze_vault
        result = analyze_vault(self.brain)
        written = {o["pointer"].split(":", 1)[1] for o in self.receipt["outputs"]}
        self.assertEqual([], [row for row in result["broken_links"] if row["source"] in written])
        self.assertEqual(set(), written & set(result["unreachable"]))
        for relative in written:
            text = (self.brain / relative).read_text(encoding="utf-8")
            for target in link_targets(text):
                self.assertTrue((self.brain / f"{target}.md").is_file() or (self.brain / target).is_file(), f"{relative} -> {target}")
            self.assertIn("canonical_truth: false", text)
            self.assertIn("status: historical-evidence", text)

    # 6 ----------------------------------------------------------------------
    def graph_service(self, graphify: Path, state_db: Path | None = None):
        from dashboard.backend.business_brain_graph import BusinessBrainGraphService
        data = registry_data()
        notes = sorted(self.pointers.values()) + [f"business_brain:{self.folder}INDEX.md"]
        original = f"business_brain:{RECORDS}/{self.sha}.md"
        data["scopes"]["global"]["brain_pointers"] += [*notes, original]
        data["scopes"]["global"]["search_source_identities"].append(
            {"source": "business_brain", "paths": [*notes, original]}
        )
        data["scopes"]["global"]["graphify_targets"][0]["paths"] += notes
        return BusinessBrainGraphService(graphify_root=graphify, vault_root=self.brain, registry=make_registry(data),
                                         state_db=state_db)

    def test_graph_projects_entities_and_only_the_evidenced_typed_edge_and_one_hop_returns_the_stating_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self.graph_service(Path(tmp) / "graphify")
            self.assertEqual("success", service.build()["status"])
            graph = json.loads((service.published / "graph.json").read_text(encoding="utf-8"))
            entities = {n["id"]: n for n in graph["nodes"] if n.get("kind") == "entity"}
            self.assertTrue({"entity:topic:northwind-dispatch", "entity:topic:vantage-crm", "entity:topic:halifax-depot",
                             "entity:person:marisol-ortega"} <= set(entities))
            self.assertTrue(all(n["authoritative"] is False for n in entities.values()))
            typed = [e for e in graph["edges"] if e.get("extractor") == "ttros.source-graph" and e["relation"] != "about"]
            self.assertEqual([("entity:topic:northwind-dispatch", "entity:topic:vantage-crm", "integrates_with")],
                             [(e["source"], e["target"], e["relation"]) for e in typed])          # cited + related_to omitted
            edge = typed[0]
            crm_id = next(n["id"] for n in graph["nodes"] if n.get("source_path") == self.pointers["crm-integration"])
            self.assertEqual((crm_id, "supported", False, "derived"),
                             (edge["stated_in"], edge["evidence_status"], edge["authoritative"], edge["edge_kind"]))
            self.assertTrue(edge["ref"].startswith(f"source:{self.sha}#C"))
            self.assertTrue(edge["evidence_quote"])
            self.assertEqual(edge["source_authority"], "historical-evidence")
            self.assertEqual(edge["source_sha256"], self.sha)
            about = {(e["source"], e["stated_in_path"]) for e in graph["edges"] if e["relation"] == "about"}
            self.assertIn(("entity:topic:halifax-depot", self.pointers["halifax-counterexample"]), about)
            self.assertNotIn(("entity:topic:halifax-depot", self.pointers["dispatch-method"]), about)
            projection = json.loads((service.published / "projection_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(1, projection["source_relationship_edge_count"])
            result = service.query_targets("Which system does Vantage CRM work alongside?", client_scope="global")
            hit = next(row for row in result["targets"] if row["path"] == self.pointers["crm-integration"])
            self.assertTrue(any("integrates_with" in reason for reason in hit["relationship_reasons"]))
            none = service.query_targets("recommends vantage crm marisol ortega", client_scope="global")
            self.assertFalse(any("recommends" in r for row in none["targets"] for r in row["relationship_reasons"]))

    def test_incremental_update_keeps_derived_entities_equal_to_a_full_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self.graph_service(Path(tmp) / "graphify", Path(tmp) / "state.db")
            service.build()
            target = self.brain / self.folder / "halifax-counterexample.md"
            original = target.read_bytes()
            try:
                target.write_bytes(original.replace(b"## Related notes", b"Edited for the incremental check.\n\n## Related notes", 1))
                self.assertEqual("success", service.apply_changes([str(target)])["status"])
                incremental = json.loads((service.published / "graph.json").read_text(encoding="utf-8"))
                full = self.graph_service(Path(tmp) / "graphify-full")
                full.build()
                rebuilt = json.loads((full.published / "graph.json").read_text(encoding="utf-8"))
                pick = lambda g: ([n for n in g["nodes"] if n.get("kind") == "entity"],
                                  [e for e in g["edges"] if e.get("extractor") == "ttros.source-graph"])
                self.assertEqual(pick(rebuilt), pick(incremental))
            finally:
                target.write_bytes(original)

    # 7 ----------------------------------------------------------------------
    def search_db(self, tmp: Path) -> Path:
        db = tmp / "search" / "os_index.db"
        with mock.patch.object(aos_indexer, "BUSINESS_BRAIN_ROOT", self.brain):
            conn = aos_indexer.connect(db)
            for pointer in [*self.pointers.values(), f"business_brain:{self.folder}INDEX.md", f"business_brain:{RECORDS}/{self.sha}.md"]:
                doc = aos_indexer.document_from_path(self.brain / pointer.split(":", 1)[1])
                self.assertIsNotNone(doc, pointer)
                aos_indexer.upsert_with_passages(conn, doc)
            conn.commit()
            conn.close()
        with mock.patch.object(aos_embeddings, "Embedder", FakeEmbedder):
            aos_embeddings.refresh(db, create=True)
        return db

    def search(self, db: Path, query: str) -> list[dict]:
        with mock.patch.object(aos_embeddings, "Embedder", FakeEmbedder):
            result = aos_indexer.search(query, source="business_brain", client_scope="global", db_path=db, hybrid=True,
                                        limit=10, passages=True, rerank=True)
        self.assertEqual("hybrid", result["search_mode"])
        return [row for group in result["groups"].values() for row in group]

    def test_hybrid_search_finds_the_note_for_keyword_and_paraphrase_ahead_of_the_record_and_late_passages(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self.search_db(Path(tmp))
            record = f"business_brain:{RECORDS}/{self.sha}.md"
            expected = self.pointers["missed-appointment-results"]
            # keyword: top 3; paraphrase (no shared keyword with the answer): within David's default 5 results
            for query, depth in (("Northwind Dispatch missed appointments 18% 7%", 3),
                                 ("how did no-shows change after the new scheduling tool", 5)):
                paths = [r["path"] for r in self.search(db, query)]
                notes = [p for p in paths if not aos_indexer.is_passage_pointer(p)]
                self.assertIn(expected, notes[:depth], (query, paths[:6]))
                self.assertLess(notes.index(expected), notes.index(record) if record in notes else len(notes), query)
            rows = self.search(db, "Halifax depot trial assigned vans in May")
            late = [r for r in rows if aos_indexer.is_passage_pointer(r["path"]) and "#C" in r["path"]]
            self.assertTrue(late, [r["path"] for r in rows])
            start = int(late[0]["source_ref"].split("#C")[1].split("-")[0])
            self.assertGreater(start, 20_000)
            self.assertIn("assigned vans", sp.read_passage(self.brain, late[0]["source_ref"])["text"])

    # 8 ----------------------------------------------------------------------

    # 9 ----------------------------------------------------------------------
    def test_a_downstream_failure_reruns_from_the_saved_interpretation_with_zero_model_calls(self):
        calls = []
        before = len(self.calls)
        with mock.patch.object(source_intake, "run_intake", side_effect=RuntimeError("simulated Brain/Git failure")):
            with self.assertRaises(RuntimeError):
                si.run(self.sha, brain_root=self.brain, root=self.repo, allow_model_call=True,
                       llm=fake_llm(F.one_line_response(self.text), calls))
        ok = mock.Mock(imported_pointers=[], duplicate_pointers=[], search_status="success", graphify_status="success", brain_commit="x")
        with mock.patch.object(source_intake, "run_intake", return_value=ok), \
                mock.patch.object(mei, "process_v3", return_value={"outcome": "already_imported"}) as imported:
            out = si.run(self.sha, brain_root=self.brain, root=self.repo, allow_model_call=True,
                         llm=fake_llm(F.one_line_response(self.text), calls))
        self.assertEqual([], calls)
        self.assertEqual(before, len(self.calls))
        self.assertEqual(1, len(self.calls))                      # the one mocked call made in setUpClass
        self.assertTrue(out["interpretation"]["reused"])
        self.assertEqual("already_imported", out["tier2"]["outcome"])
        self.assertEqual(1, imported.call_count)


if __name__ == "__main__":
    unittest.main()
