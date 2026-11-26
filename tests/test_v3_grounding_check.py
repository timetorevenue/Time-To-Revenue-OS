"""tools/v3_grounding_check.py: read-only v3 source-grounding validator, on the fictional fixture only.

Revisit: when the v3 note format (Key quotes, Points, Coverage) changes. · Last touched: 2026-11-11.
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

from tools import v3_grounding_check as G

ROOT = Path(__file__).resolve().parents[1]
PKG = next((ROOT / "tests/fixtures/memory_ingest_v3").glob("INGEST_*"))
RAW = (PKG / "source/original.txt").read_bytes()
INDEX = (PKG / "notes/INDEX.md").read_text(encoding="utf-8")
NOTES = {p.name: p.read_text(encoding="utf-8") for p in sorted((PKG / "notes").glob("*.md")) if p.name != "INDEX.md"}


def run(raw=RAW, index=INDEX, label="fixture", **replace):
    notes = {k: v for k, v in NOTES.items()}
    for name, (old, new) in replace.items():
        name = name.replace("_", "-") + ".md"
        assert old in notes[name], (name, old)
        notes[name] = notes[name].replace(old, new)
    return G.check_package(label, raw, index, notes)


def failing(report, kind):
    return [c for c in report["checks"] if c["kind"] == kind and c["status"] == G.FAIL]


class GroundingTests(unittest.TestCase):
    def test_fixture_passes_with_no_failures_and_says_semantics_unassessed(self):
        report = run()
        self.assertEqual(G.PASS, report["verdict"], [c for c in report["checks"] if c["status"] != G.PASS])
        self.assertEqual(0, report["counts"][G.FAIL])
        full = G.build_report([report])
        self.assertEqual("not assessed", full["semantic_correctness"])
        self.assertEqual(0, full["model_calls"])

    def test_line_convention_is_one_based(self):
        lines = G.split_lines(RAW)
        self.assertEqual(35, len(lines))
        self.assertTrue(lines[14].endswith("it's just gone."))  # L15
        self.assertEqual(["a", "b"], G.split_lines(b"\xef\xbb\xbfa\r\nb\n"))

    def test_seeded_altered_quotation_fails(self):
        report = run(missed_calls_problem=("it's just gone.", "it is gone forever."))
        self.assertEqual(1, len(failing(report, "quote")))
        self.assertEqual(G.FAIL, report["verdict"])

    def test_seeded_invalid_range_fails_and_quote_is_not_judged(self):
        report = run(missed_calls_problem=("Jordan Ellis, L15)", "Jordan Ellis, L99)"))
        self.assertEqual(1, len(failing(report, "range")))
        quote = [c for c in report["checks"] if c["kind"] == "quote" and "L99" in c["ref"]]
        self.assertEqual([G.UNVERIFIABLE], [c["status"] for c in quote])

    def test_seeded_header_range_past_end_fails(self):
        report = run(missed_calls_problem=("Lines L7-L17", "Lines L7-L36"))
        self.assertTrue(failing(report, "range"))

    def test_seeded_wrong_speaker_on_labelled_material_fails(self):
        report = run(missed_calls_problem=("(Jordan Ellis, L15)", "(Casey Linden, L15)"))
        bad = failing(report, "speaker")
        self.assertEqual(1, len(bad))
        self.assertIn("Jordan Ellis", bad[0]["detail"])

    def test_correct_speaker_with_first_name_only_passes(self):
        report = run(missed_calls_problem=("(Jordan Ellis, L15)", "(Jordan, L15)"))
        self.assertEqual([], failing(report, "speaker"))

    def test_seeded_broken_coverage_is_detected(self):
        gap = INDEX.replace("| L18-L22 | topic 2 | |\n", "")
        self.assertEqual(1, len(failing(run(index=gap), "coverage")))  # L18-L22 declared by nobody
        wrong_topic = INDEX.replace("| L18-L22 | topic 2 |", "| L18-L22 | topic 1 |")
        self.assertTrue(failing(run(index=wrong_topic), "coverage"))  # note 1 does not claim those lines
        past_end = INDEX.replace("| L34-L35 |", "| L34-L40 |")
        self.assertTrue(failing(run(index=past_end), "coverage"))

    def test_me_them_and_unlabelled_are_unverifiable_not_invented(self):
        raw = b"Them: hello there\nMe: hi\nThem: more words\nMe: ok\nThem: again\nMe: bye\n    continued text line\n"
        index = INDEX.split("## Coverage")[0] + "## Coverage\n| Lines | Where | Note |\n|---|---|---|\n| L1-L7 | not filed | |\n"
        note = ('---\ntitle: t\n---\n## Points\n- **Jordan Ellis:** said hello (L1).\n## Key quotes\n'
                '> "hello there" (Them, L1)\n> "hi" (Jordan Ellis, L2)\n> "continued text line" (Jordan Ellis, L7)\n')
        report = G.check_package("x", raw, index, {"n.md": note})
        by_ref = {c["ref"]: c["status"] for c in report["checks"] if c["kind"] == "speaker"}
        self.assertEqual({"n.md:quote:hello there|L1": G.PASS,            # explicit "Them:" label matches a "Them" citation
                          "n.md:quote:hi|L2": G.UNVERIFIABLE,             # Me/Them cannot be turned into Jordan Ellis
                          "n.md:quote:continued text line|L7": G.UNVERIFIABLE,
                          "n.md:point:jordan ellis|L1": G.UNVERIFIABLE}, by_ref)

    def test_one_off_field_label_is_not_a_speaker(self):
        raw = b'Ann Lee\n10:31\nProduct Catch Phrase: "Done right"\n'
        src = G.Source(raw)
        self.assertEqual((None, "no explicit speaker label on this line (continuation or unlabelled)"), src.speaker_of(3))

    def test_report_is_deterministic_and_machine_readable(self):
        a = json.dumps(G.build_report([run(label="a"), run(label="b", missed_calls_problem=("it's just gone.", "x"))]), sort_keys=True)
        b = json.dumps(G.build_report([run(label="b", missed_calls_problem=("it's just gone.", "x")), run(label="a")]), sort_keys=True)
        self.assertEqual(a, b)
        self.assertEqual({"pass", "fail", "unverifiable"}, set(json.loads(a)["totals"]))

    def test_cli_package_mode_exit_codes_and_no_writes(self):
        before = sorted((p, p.stat().st_mtime_ns) for p in PKG.rglob("*") if p.is_file())
        ok = subprocess.run([sys.executable, str(ROOT / "tools/v3_grounding_check.py"), "--package", str(PKG), "--json"],
                            capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(0, ok.returncode, ok.stderr)
        self.assertEqual("not assessed", json.loads(ok.stdout)["semantic_correctness"])
        self.assertEqual(before, sorted((p, p.stat().st_mtime_ns) for p in PKG.rglob("*") if p.is_file()))
        bad = subprocess.run([sys.executable, str(ROOT / "tools/v3_grounding_check.py"), "--package", str(PKG / "nope")],
                             capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(2, bad.returncode)

    def test_no_model_or_writer_modules_are_imported(self):
        code = ("import sys; import tools.v3_grounding_check; "
                "bad=[m for m in sys.modules if m.split('.')[0] in ('anthropic','openai','requests','httpx','urllib3')]; "
                "print(bad)")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual("[]", out.stdout.strip(), out.stderr)

    def test_brain_folder_hash_check_catches_a_tampered_original(self):
        import tempfile
        from tools import source_intake
        with tempfile.TemporaryDirectory() as tmp:
            brain = Path(tmp)
            sha = G.hashlib.sha256(RAW).hexdigest()
            folder = brain / "sources/historical_calls/2026-10-14-x-155cb165"
            folder.mkdir(parents=True)
            index = INDEX.replace("155cb165d6d1b5b91cb5c08e36671932f815c7626e3cdaa2f6c3f90d67360eba", sha)
            (folder / "INDEX.md").write_text(index, encoding="utf-8")
            for name, text in NOTES.items():
                (folder / name).write_text(text, encoding="utf-8")
            rec = brain / "sources/intake/records"
            rec.mkdir(parents=True)
            payload = f"{source_intake.BYTES_BEGIN}\n{G.__dict__['hashlib'].sha256 and __import__('base64').b64encode(RAW + b'x').decode()}\n{source_intake.BYTES_END}"
            (rec / f"{sha}.md").write_text("---\n---\n" + payload, encoding="utf-8")
            report = G.load_brain_folder(brain, "sources/historical_calls/2026-10-14-x-155cb165/")
            self.assertEqual(G.FAIL, report["verdict"])
            self.assertIn("do not match source_sha256", failing(report, "source")[0]["detail"])


if __name__ == "__main__":
    unittest.main()
