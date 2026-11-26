"""Three-tier ingestion: passages, interpretation validation, one-call persistence, v3 package from the same output.

Temp-dir fixtures and a fake model only: no network, no Luna call, no live Brain. Rendering and selection tests here
run on saved revision-2 interpretations (they must keep rendering exactly as before); the call flow runs on revision-3
(segment-cited) mock output. Revision-3 behaviour is tested in tests/test_source_interpret_rev3.py.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path, PurePosixPath

from tools import memory_exchange_import as mei
from tools import source_interpret as si
from tools import source_intake
from tools import source_passages as sp
from tools import v3_grounding_check as grounding

SOURCE = "\n".join([
    "# Lucian Assistant Brief",            # L1
    "",                                   # L2
    "Prepared by Casey Linden for review, June 2026.",  # L3
    "Lucian Ashdown is a baker in Lisbon.",   # L4
    "",                                   # L5
    "## Hosting",                         # L6
    "We will host on ExampleHost Region-A as a fallback.",  # L7
    "Monthly cost must stay under 20 dollars.",             # L8
    "",                                   # L9
    "## Security",                        # L10
    "Identity documents stay out of the model context.",    # L11
    "OAuth tokens live in a secrets file only.",            # L12
    "",
])


def good_response() -> dict:
    return {
        "description": "Architecture brief for a realtor assistant.",
        "source_class": "reference", "document_kind": "architecture brief", "participants": ["Casey Linden"],
        "entities": [
            {"name": "Lucian Ashdown", "kind": "person", "lines": "L4", "quote": "Lucian Ashdown is a baker"},
            {"name": "Invented Corp", "kind": "organization", "lines": "L4", "quote": "Invented Corp builds it"},
        ],
        "topics": [
            {"title": "Hosting choice", "slug": "hosting-choice", "knowledge_type": "architecture", "importance": 3,
             "lines": ["L6-L8"], "one_line": "Host on ExampleHost Region-A.", "summary": "Fallback hosting and a cost cap.",
             "questions": ["Where is the assistant hosted?"],
             "points": [{"speaker": "Author", "text": "Fallback host is ExampleHost Region-A.", "lines": "L7"}],
             "quotes": [{"text": "Monthly cost must stay under 20 dollars.", "speaker": "Author", "lines": "L8"},
                        {"text": "This sentence is not in the source.", "speaker": "Author", "lines": "L8"}],
             "open_loops": []},
            {"title": "Security boundaries", "slug": "security-boundaries", "knowledge_type": "risk", "importance": 3,
             "lines": ["L10-L12"], "one_line": "Keep identity documents and tokens out of context.",
             "summary": "Identity documents and OAuth tokens are isolated.",
             "questions": ["What stays out of the model context?"],
             "points": [{"speaker": "Author", "text": "OAuth tokens live in a secrets file.", "lines": "L12"}],
             "quotes": [], "open_loops": []},
            {"title": "Out of bounds", "slug": "out-of-bounds", "lines": ["L400-L410"], "importance": 1},
            {"title": "Background", "slug": "background", "knowledge_type": "reference", "importance": 1,
             "lines": ["L3-L4"], "one_line": "Who prepared it.", "summary": "Prepared by Casey.", "questions": [],
             "points": [], "quotes": [], "open_loops": []},
        ],
        "uncertainty": ["Date is only given as a month."],
    }


# The test SOURCE's non-blank lines are short, so each is one segment: L1->S1, L3->S2, L4->S3, L6->S4, L7->S5, L8->S6,
# L10->S7, L11->S8, L12->S9. seg() re-cites a line-cited mock output as the revision-3 model would (S<n>).
_LINE_TO_SEGMENT = {n: i for i, n in enumerate((1, 3, 4, 6, 7, 8, 10, 11, 12), 1)}


def _to_segments(value):
    def one(text: str) -> str:
        out = []
        for a, b in grounding.parse_ranges(text):
            seg = [_LINE_TO_SEGMENT[n] for n in range(a, b + 1) if n in _LINE_TO_SEGMENT]
            out.append(f"S{seg[0]}" + (f"-S{seg[-1]}" if seg[-1] != seg[0] else "") if seg else "S999")
        return ", ".join(out)
    return one(", ".join(value) if isinstance(value, list) else str(value or ""))


def seg(response: dict) -> dict:
    """A revision-3 (segment-cited) copy of a line-cited mock output; nothing else changes."""
    import copy

    def walk(node):
        if isinstance(node, dict):
            out = {k: walk(v) for k, v in node.items() if k != "lines"}
            if "lines" in node:
                out["cite"] = _to_segments(node["lines"])
            return out
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node
    return walk(copy.deepcopy(response))


class Vault:
    def __init__(self, test: unittest.TestCase):
        self.tmp = tempfile.TemporaryDirectory()
        test.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.brain, self.repo = self.root / "brain", self.root / "repo"
        (self.brain / RECORDS).mkdir(parents=True)
        (self.repo).mkdir()
        raw = SOURCE.encode()
        self.sha = hashlib.sha256(raw).hexdigest()
        item = source_intake.SourceItem(PurePosixPath("brief.md"), raw, self.sha, "text/markdown", "Lucian Assistant Brief", None)
        (self.brain / RECORDS / f"{self.sha}.md").write_text(source_intake.render_record(item, "2026-11-12T00:00:00Z"), encoding="utf-8")


RECORDS = source_intake.RECORDS_RELATIVE


class PassageTests(unittest.TestCase):
    def test_passage_is_bounded_hash_verified_and_addressed_by_stable_reference(self):
        v = Vault(self)
        passage = sp.read_passage(v.brain, f"source:{v.sha}#L7-L8")
        self.assertTrue(passage["verified"])
        self.assertEqual(f"source:{v.sha}#L7-L8", passage["ref"])
        self.assertIn("ExampleHost", passage["text"])
        self.assertEqual(12, passage["total_lines"])
        with self.assertRaises(sp.PassageError):
            sp.read_passage(v.brain, f"source:{v.sha}#L1-L99")
        self.assertTrue(sp.read_passage(v.brain, v.sha, 1, 12, max_lines=5)["truncated"])

    def test_corrupt_preserved_bytes_are_refused(self):
        v = Vault(self)
        path = v.brain / RECORDS / f"{v.sha}.md"
        # corrupt the exact-byte payload itself
        text = path.read_text(encoding="utf-8")
        start = text.index(source_intake.BYTES_BEGIN) + len(source_intake.BYTES_BEGIN)
        path.write_text(text[:start] + "\nAAAA" + text[start:].lstrip("\n"), encoding="utf-8")
        with self.assertRaises(sp.SourceIntakeError):
            sp.load_source(v.brain, v.sha)

    def test_question_finds_the_window_containing_the_evidence(self):
        v = Vault(self)
        hit = sp.find_passages(v.brain, v.sha, "where is the fallback hosting ExampleHost", limit=1, window=4)[0]
        self.assertIn("ExampleHost", hit["text"])


class ValidationTests(unittest.TestCase):
    def test_unsupported_items_are_dropped_and_supported_items_kept(self):
        clean, dropped = si.validate_interpretation(good_response(), SOURCE.split("\n"))
        slugs = [t["slug"] for t in clean["topics"]]
        self.assertEqual(["hosting-choice", "security-boundaries", "background"], slugs)
        self.assertEqual(1, len(clean["topics"][0]["quotes"]))
        self.assertEqual(["Lucian Ashdown"], [e["name"] for e in clean["entities"]])
        kinds = sorted(d["kind"] for d in dropped)
        self.assertEqual(["entity", "quote", "topic"], kinds)
        self.assertEqual(["person:lucian-ashdown"], si.person_keys(clean))

    def test_interpretation_without_a_supported_topic_is_rejected(self):
        with self.assertRaises(si.InterpretationError):
            si.validate_interpretation({"description": "x", "topics": [{"title": "t", "slug": "t", "lines": ["L99"]}]}, ["a"])

    def test_reference_ties_break_in_document_order_not_string_order(self):
        topics = [{"slug": f"t{i}", "importance": 2, "lines": f"L{a}-L{a + 5}"} for i, a in enumerate((1196, 333, 40, 900, 15, 700, 2000, 60, 5000))]
        chosen = si.select_topics({"source_class": "reference", "topics": topics})
        self.assertEqual(si.MAX_SELECTIVE_NOTES, len(chosen))
        self.assertNotIn("t8", [t["slug"] for t in chosen])      # L5000 is the one cut, not L333

    def test_selection_is_full_for_meetings_and_selective_for_reference(self):
        clean, _ = si.validate_interpretation(good_response(), SOURCE.split("\n"))
        self.assertEqual(["hosting-choice", "security-boundaries"], [t["slug"] for t in si.select_topics(clean)])
        clean["source_class"] = "internal_meeting"
        self.assertEqual(3, len(si.select_topics(clean)))


class OneCallTests(unittest.TestCase):
    def fake(self, calls: list):
        def llm(prompt, *, root, sha):
            calls.append(sha)
            return {"text": json.dumps(seg(good_response())), "usage": {"available": True, "input_tokens": 100, "output_tokens": 20,
                    "cached_input_tokens": 0, "reasoning_output_tokens": 0}, "session_id": "s1", "model": "gpt-6-luna",
                    "reasoning_effort": "low", "scope": None}
        return llm

    def test_one_paid_call_is_saved_and_never_repeated(self):
        v, calls = Vault(self), []
        first = si.interpret(v.sha, brain_root=v.brain, root=v.repo, llm=self.fake(calls), allow_model_call=True)
        self.assertFalse(first["reused"])
        self.assertTrue(si.interpretation_path(v.repo, v.sha).is_file())
        again = si.interpret(v.sha, brain_root=v.brain, root=v.repo, llm=self.fake(calls), allow_model_call=True)
        self.assertTrue(again["reused"])
        self.assertEqual(1, len(calls))

    def test_no_saved_interpretation_and_no_permission_means_no_call(self):
        v, calls = Vault(self), []
        with self.assertRaises(si.InterpretationError):
            si.interpret(v.sha, brain_root=v.brain, root=v.repo, llm=self.fake(calls))
        self.assertEqual([], calls)

    def test_unusable_output_counts_an_attempt_and_attempts_are_bounded(self):
        v, calls = Vault(self), []

        def bad(prompt, *, root, sha):
            calls.append(1)
            return {"text": "not json", "usage": {"available": False, "reason": "none"}, "session_id": "s", "model": "gpt-6-luna", "scope": None}
        for _ in range(si.MAX_PAID_ATTEMPTS):
            with self.assertRaises(si.InterpretationError):
                si.interpret(v.sha, brain_root=v.brain, root=v.repo, llm=bad, allow_model_call=True)
        with self.assertRaises(si.InterpretationError):
            si.interpret(v.sha, brain_root=v.brain, root=v.repo, llm=bad, allow_model_call=True)
        self.assertEqual(si.MAX_PAID_ATTEMPTS, len(calls))

    def test_rate_limit_sets_a_cooldown_without_another_call(self):
        v, calls = Vault(self), []

        def limited(prompt, *, root, sha):
            calls.append(1)
            raise si.RateLimited("429")
        with self.assertRaises(si.RateLimited):
            si.interpret(v.sha, brain_root=v.brain, root=v.repo, llm=limited, allow_model_call=True)
        with self.assertRaises(si.RateLimited):
            si.interpret(v.sha, brain_root=v.brain, root=v.repo, llm=limited, allow_model_call=True)
        self.assertEqual(1, len(calls))


class PackageTests(unittest.TestCase):
    def test_package_from_the_saved_interpretation_passes_v3_validation_and_grounding(self):
        v = Vault(self)
        interp = saved_revision2(v, good_response())
        _full, lines = sp.load_source(v.brain, v.sha)
        built = si.build_v3_package(interp, sha=v.sha, raw=SOURCE.encode(), original_filename="brief.md", lines=lines,
                                    package_dir=v.repo / "pkg")
        plan = mei.validate_v3(built["package"], v.brain)
        self.assertEqual(3, len(plan["outputs"]))               # index + two selected notes
        self.assertEqual(["background"], built["topics_not_filed"])
        report = grounding.build_report([grounding.load_package(built["package"])])
        failing = [c for p in report["packages"] for c in p["checks"] if c["status"] == "fail"]
        self.assertEqual([], failing)

    def test_rebuilding_the_package_from_the_same_interpretation_gives_the_same_fingerprint(self):
        v = Vault(self)
        interp = saved_revision2(v, good_response())
        _full, lines = sp.load_source(v.brain, v.sha)
        prints = []
        for _ in range(2):
            built = si.build_v3_package(interp, sha=v.sha, raw=SOURCE.encode(), original_filename="brief.md", lines=lines,
                                        package_dir=v.repo / "pkg")
            prints.append(mei.validate_v3(built["package"], v.brain)["fingerprint"])
        self.assertEqual(prints[0], prints[1])

    def test_slug_words_the_indexer_excludes_are_dropped_from_the_path_not_the_title(self):
        v = Vault(self)
        response = good_response()
        response["topics"][1]["slug"] = "token-efficiency"
        interp = saved_revision2(v, response)
        _full, lines = sp.load_source(v.brain, v.sha)
        built = si.build_v3_package(interp, sha=v.sha, raw=SOURCE.encode(), original_filename="brief.md", lines=lines,
                                    package_dir=v.repo / "pkg")
        self.assertIn("efficiency", built["topics_filed"])
        self.assertEqual(3, len(mei.validate_v3(built["package"], v.brain)["outputs"]))
        self.assertEqual("token-efficiency", interp["topics"][1]["slug"])    # saved interpretation untouched

    def test_topic_map_card_cites_stable_passage_references(self):
        from tools import source_intake_semantic as sis
        v = Vault(self)
        interp = saved_revision2(v, good_response())
        card = sis.render_interpretation_card(source_id=v.sha, source_path=f"{RECORDS}/{v.sha}.md", source_sha256=v.sha,
                                              ingested_at="2026-11-12T00:00:00Z", interpretation=si.topic_map_card_fields(interp),
                                              receipt_relative="queue/x")
        self.assertIn(f"source:{v.sha}#L6-L8", card)
        self.assertIn("entity_key: [person:lucian-ashdown]", card)
        self.assertNotIn("Invented Corp", card)


def rich_response() -> dict:
    r = good_response()
    r.update(document_title="Realtor Assistant Hosting and Security Brief",
             authors=[{"name": "Casey Linden", "role": "author", "lines": "L3", "quote": "Prepared by Casey Linden for review"}],
             source_date={"text": "June 2026", "iso": "2026-06-01", "lines": "L3", "quote": "for review, June 2026."},
             relationships=[{"subject": "Lucian Ashdown", "predicate": "works_with", "object": "Casey Linden", "lines": "L3-L4",
                             "quote": "Lucian Ashdown is a baker"},
                            {"subject": "Lucian Ashdown", "predicate": "uses", "object": "ExampleHost", "lines": "L7",
                             "quote": "host on Linode"}])
    r["entities"].append({"name": "ExampleHost", "kind": "organization", "lines": "L7", "quote": "We will host on ExampleHost Region-A"})
    r["participants"] = ["Casey Linden"]
    hosting, security = r["topics"][0], r["topics"][1]
    hosting.update(entities=["ExampleHost", "Nobody"], steps=[{"text": "Host on ExampleHost Region-A first.", "lines": "L7"},
                                                               {"text": "Keep monthly cost under 20 dollars.", "lines": "L8"},
                                                               {"text": "No citation here.", "lines": "L900"}],
                   decisions=[{"text": "Use ExampleHost Region-A as the fallback.", "who": "Casey Linden", "lines": "L7"}],
                   outcomes=[{"text": "Cost is capped.", "lines": "L8"}], uncertainty=["Primary host is not named."])
    security.update(entities=[], principles=[{"text": "Identity documents never enter model context.", "lines": "L11"}],
                    claims=[{"speaker": "Casey Linden", "text": "Tokens live only in a secrets file.", "lines": "L12"}])
    return r


def saved_revision2(v: Vault, response: dict) -> dict:
    """A saved revision-2 interpretation (line citations), as interpret() wrote them before revision 3; reused, no call."""
    clean, dropped = si.validate_interpretation(response, SOURCE.split("\n"))
    si._write_json(si.interpretation_path(v.repo, v.sha), {
        "source_sha256": v.sha, "contract": si.CONTRACT, "generated_at": "2026-11-12T00:00:00.000000Z", "model": "gpt-6-luna",
        "reasoning_effort": "medium", "session_id": "s", "usage": {}, "total_lines": 12, "dropped": dropped,
        "canonical_truth": False, **clean})
    interp = si.interpret(v.sha, brain_root=v.brain, root=v.repo)
    assert interp["reused"] and interp["extraction_schema"] == 2
    return interp


def saved(test: unittest.TestCase, response: dict):
    v = Vault(test)
    return v, saved_revision2(v, response)


def link_targets(text: str) -> set[str]:
    return {mei.canonical_wiki_target(raw) for raw in mei.WIKI_LINK_RE.findall(text)}


class RouteAndContractTests(unittest.TestCase):
    def test_interpreter_uses_luna_at_medium_without_touching_the_shared_tier_table(self):
        from unittest import mock
        from tools import aos_codex_policy as policy
        tiers = {"tiers": {"simple": {"model": "gpt-6-luna", "reasoning_effort": "low"}}, "supported_models": ["gpt-6-luna", "gpt-6-sol"]}
        with mock.patch.object(policy, "load_codex_tiers", return_value=tiers), \
                mock.patch.object(policy, "readiness", return_value={"available": True, "authenticated": True}):
            route = si.luna_route(Path("."))
        self.assertEqual(("gpt-6-luna", "medium"), (route["model"], route["reasoning_effort"]))
        self.assertEqual("low", tiers["tiers"]["simple"]["reasoning_effort"])
        with mock.patch.object(policy, "load_codex_tiers", return_value={**tiers, "supported_models": ["gpt-6-sol"]}), \
                self.assertRaises(si.ModelUnavailable):
            si.luna_route(Path("."))

    def test_contract_key_is_unchanged_and_prompt_has_no_artificial_topic_count(self):
        self.assertEqual("source-interpretation-v1", si.CONTRACT)
        self.assertNotIn("4 to 10", si.PROMPT)
        self.assertIn("Do NOT pad", si.PROMPT)

    def test_saved_revision_one_interpretation_is_reused_with_zero_calls(self):
        v = Vault(self)
        old = {"source_sha256": v.sha, "contract": si.CONTRACT, "model": "gpt-6-luna", "reasoning_effort": "low", "usage": {},
               "total_lines": 12, "dropped": [], **si.validate_interpretation(good_response(), SOURCE.split("\n"))[0]}
        for k in ("extraction_schema", "document_title", "authors", "source_date", "relationships"):
            old.pop(k)
        si._write_json(si.interpretation_path(v.repo, v.sha), old)
        calls = []
        again = si.interpret(v.sha, brain_root=v.brain, root=v.repo, allow_model_call=True,
                             llm=lambda *a, **k: calls.append(1))
        self.assertTrue(again["reused"])
        self.assertEqual([], calls)
        self.assertFalse(si._attempts_path(v.repo, v.sha).exists())


class ExtractionValidationTests(unittest.TestCase):
    def test_new_structures_are_kept_only_when_cited_and_supported(self):
        clean, dropped = si.validate_interpretation(rich_response(), SOURCE.split("\n"))
        hosting = clean["topics"][0]
        self.assertEqual(["Host on ExampleHost Region-A first.", "Keep monthly cost under 20 dollars."], [x["text"] for x in hosting["steps"]])
        self.assertEqual("Casey Linden", hosting["decisions"][0]["who"])
        self.assertEqual(["ExampleHost"], hosting["entity_names"])           # unknown names are not kept
        self.assertEqual("Casey Linden", clean["authors"][0]["name"])
        self.assertEqual("2026-06-01", clean["source_date"]["iso"])
        self.assertEqual(1, len(clean["relationships"]))   # "uses" quote is not in the cited line
        self.assertEqual("works_with", clean["relationships"][0]["predicate"])
        self.assertIn("step", {d["kind"] for d in dropped})
        self.assertIn("relationship", {d["kind"] for d in dropped})

    def test_unsupported_author_and_date_are_dropped(self):
        r = rich_response()
        r["authors"] = [{"name": "Someone Else", "role": "author", "lines": "L3", "quote": "Prepared by Casey Linden for review"}]
        r["source_date"] = {"text": "June 2026", "iso": "2026-13-45", "lines": "L3", "quote": "for review, June 2026."}
        clean, dropped = si.validate_interpretation(r, SOURCE.split("\n"))
        self.assertEqual([], clean["authors"])
        self.assertIsNone(clean["source_date"])
        self.assertTrue({"author", "source_date"} <= {d["kind"] for d in dropped})

    def test_a_single_topic_is_accepted(self):
        r = {"description": "Short note.", "topics": [good_response()["topics"][0]]}
        clean, _ = si.validate_interpretation(r, SOURCE.split("\n"))
        self.assertEqual(1, len(clean["topics"]))


class RenderingTests(unittest.TestCase):
    NAME = "Tomas Verhoeven - Hosting Brief - Transcript.txt"

    def build(self, response, name="brief.md", with_drive=False, resolver=None):
        v, interp = saved(self, response)
        if with_drive:
            drive = v.brain / "sources/intake/drive"
            drive.mkdir(parents=True)
            (drive / "DRV123.md").write_text("# Drive source provenance\n\n```json\n" + json.dumps({
                "drive_id": "DRV123", "url": "https://drive.google.com/file/d/DRV123/view",
                "source_record": f"business_brain:{RECORDS}/{v.sha}.md"}) + "\n```\n", encoding="utf-8")
        _full, lines = sp.load_source(v.brain, v.sha)
        built = si.build_v3_package(interp, sha=v.sha, raw=SOURCE.encode(), original_filename=name, lines=lines,
                                    package_dir=v.repo / "pkg", brain_root=v.brain, resolve_key=resolver)
        return v, interp, built

    def note(self, built, slug):
        return (built["package"] / "notes" / f"{slug}.md").read_text(encoding="utf-8")

    def test_filename_metadata_is_separated_from_text_metadata(self):
        meta = si.filename_metadata("Tomas Verhoeven - Drafting the First Page - Transcript.txt")
        self.assertEqual(("Drafting the First Page", "Tomas Verhoeven"), (meta["title"], meta["author"]))
        self.assertEqual("2026-03-04", si.filename_metadata("2026-03-04 Weekly Call.txt")["date"])
        v, _interp, built = self.build(good_response(), name=self.NAME)          # revision-1 style: author only in filename
        text = self.note(built, "hosting-choice")
        self.assertIn("Author: Tomas Verhoeven — from the filename only; not stated in the text", text)
        self.assertIn("source_authors: \"Tomas Verhoeven (filename)\"", text)
        self.assertIn("title: \"Hosting Brief: Hosting choice\"", text)
        self.assertIn("source_date_text: \"unavailable\"", text)
        _v, _i, rich = self.build(rich_response(), name=self.NAME)
        self.assertIn("Author: Casey Linden — stated in the text (L3)", self.note(rich, "hosting-choice"))
        self.assertIn("source_date_text: \"2026-06-01\"", self.note(rich, "hosting-choice"))
        self.assertTrue(rich["folder"].startswith("sources/historical_calls/2026-06-01-realtor-assistant-hosting-and-security-"))

    def test_every_note_carries_identity_drive_url_hash_verified_passages_and_resolving_links(self):
        v, _interp, built = self.build(rich_response(), name=self.NAME, with_drive=True)
        outputs = {o["pointer"].split(":", 1)[1] for o in mei.validate_v3(built["package"], v.brain)["outputs"]}
        for slug in ["INDEX", *built["topics_filed"]]:
            text = self.note(built, slug)
            for needle in (f"original_filename: \"{self.NAME}\"", "source_drive_url: \"https://drive.google.com/file/d/DRV123/view\"",
                           f"source_record: business_brain:{RECORDS}/{v.sha}.md", f"source_sha256: {v.sha}"):
                self.assertIn(needle, text)
            self.assertIn("Google Drive: https://drive.google.com/file/d/DRV123/view", text)
            for target in link_targets(text):
                self.assertTrue((v.brain / target).is_file() or target in outputs, f"{slug}: unresolved link {target}")
        hosting = self.note(built, "hosting-choice")
        self.assertIn(f"source_passages: \"source:{v.sha}#L6-L8\"", hosting)
        self.assertEqual(f"source:{v.sha}#L6-L8", sp.read_passage(v.brain, f"source:{v.sha}#L6-L8")["ref"])
        self.assertIn("[[" + built["folder"] + "INDEX|Source index]]", hosting)
        self.assertIn("security-boundaries", hosting)                             # sibling link

    def test_a_drive_url_is_omitted_when_none_is_recorded(self):
        _v, _i, built = self.build(rich_response())
        self.assertNotIn("source_drive_url", self.note(built, "hosting-choice"))

    def test_structured_sections_render_with_citations_and_order(self):
        _v, _i, built = self.build(rich_response())
        text = self.note(built, "hosting-choice")
        self.assertIn("1. Host on ExampleHost Region-A first. (L7)\n2. Keep monthly cost under 20 dollars. (L8)", text)
        self.assertIn("## Decisions\n- **Casey Linden:** Use ExampleHost Region-A as the fallback. (L7)", text)
        self.assertIn("## Uncertainty\n- Primary host is not named.", text)
        security = self.note(built, "security-boundaries")
        self.assertIn("## Claims (attributed, not verified)\n- **Casey Linden:** Tokens live only in a secrets file. (L12)", security)
        self.assertIn("## Principles", security)

    def test_entity_metadata_is_topic_specific_and_keys_reuse_established_ids(self):
        resolver = lambda name: "prospect:2026-07-lucian" if name == "Lucian Ashdown" else None
        _v, _i, built = self.build(rich_response(), resolver=resolver)
        hosting, security = self.note(built, "hosting-choice"), self.note(built, "security-boundaries")
        self.assertIn('entities: "ExampleHost"', hosting)       # Lucian's evidence (L4) is outside this topic's L6-L8
        self.assertNotIn("Lucian Ashdown", security.split("---")[1])
        index = self.note(built, "INDEX")
        self.assertIn("entity_key: [prospect:2026-07-lucian]", index)

    def test_revision_one_interpretations_still_render_without_new_fields(self):
        v = Vault(self)
        old = {"source_sha256": v.sha, "contract": si.CONTRACT, "model": "gpt-6-luna", "reasoning_effort": "low", "usage": {},
               "total_lines": 12, "dropped": [], **si.validate_interpretation(good_response(), SOURCE.split("\n"))[0]}
        for k in ("extraction_schema", "document_title", "authors", "source_date", "relationships"):
            old.pop(k)
        for t in old["topics"]:
            for k in ("principles", "steps", "decisions", "actions", "outcomes", "claims", "entity_names", "uncertainty"):
                t.pop(k)
        si._write_json(si.interpretation_path(v.repo, v.sha), old)
        interp = si.interpret(v.sha, brain_root=v.brain, root=v.repo)
        self.assertTrue(interp["reused"])
        _full, lines = sp.load_source(v.brain, v.sha)
        built = si.build_v3_package(interp, sha=v.sha, raw=SOURCE.encode(), original_filename="brief.md", lines=lines, package_dir=v.repo / "pkg")
        self.assertTrue(built["folder"].startswith("sources/historical_calls/undated-architecture-brief-for-a-realtor-assistant-"))
        self.assertEqual(3, len(mei.validate_v3(built["package"], v.brain)["outputs"]))

    def test_suggested_links_stay_suggested_and_topic_entities_narrow_them(self):
        from tools import brain_graph_links as links
        ent = {"hosting": {"e1": {"name": "ExampleHost", "type": "topic", "basis": "card entities metadata"}},
               "other": {"e1": {"name": "ExampleHost", "type": "topic", "basis": "card entities metadata"}}}
        link = links.classify_pair("hosting", "other", similarity=0.9, entities=ent, icm={})
        self.assertEqual(("related_to", "suggested", "low"), (link["relation"], link["status"], link["confidence"]))
        self.assertIsNone(links.classify_pair("hosting", "other", similarity=0.99, entities={"hosting": {}, "other": {}}, icm={}))


if __name__ == "__main__":
    unittest.main()
