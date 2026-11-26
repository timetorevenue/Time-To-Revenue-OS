"""Memory filing (F1, F2, F4, F5) and its importer integration. Temp-dir vaults only.

Revisit: when person hub rules or the D1 navigation policy change. · Last touched: 2026-11-06.
"""
import hashlib
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import brain_memory, memory_filing as F
from tools.memory_exchange_import import process
from tools.validate_business_brain import analyze_vault

MACHINE = ("<!-- TTROS:MACHINE:block-2-outcome-index:BEGIN -->\n## Machine-maintained verified outcomes\n\n"
           "- Outcome one.\n<!-- TTROS:MACHINE:block-2-outcome-index:END -->\n")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def note(id_, title, extra=""):
    return f'---\nid: {id_}\ntype: knowledge\ntitle: "{title}"\nobserved_at: 2026-11-04T18:03:00Z\n{extra}---\n# {title}\n\nEvidence.\n'


NOTES = {
    "cara-a.md": note("cara-a", "Cara Harcourt / Casey — beta", 'participants_text: "Casey Linden; Cara Harcourt"\nentity_key: person:cara-harcourt\nknowledge_type: historical_evidence\n'),
    "cara-b.md": note("cara-b", "Cara Harcourt / Casey — roster", "entity_key: person:cara-harcourt\n"),
    "lindqvist.md": note("lindqvist", "Cara Lindqvist pilot", 'participants_text: "Casey Linden; Cara Lindqvist"\n'),
    "theodore.md": note("theodore", "Call with Theodore", 'participants_text: "Casey Linden; Theodore (surname absent from metadata)"\nentity_key: person:theodore-pryce\n'),
    "imogen.md": note("imogen", "Imogen second call", 'participants_text: "Casey Linden; Imogen (surname inferred)"\n'),
    "prose.md": note("prose", "First CCI call", 'participants_text: "Casey Linden; CCI participants not individually attributable"\n'),
    "tokeni.md": note("tokeni", "Tokeni call", 'participants_text: "Casey Linden; Tokeni Smith"\n'),
    "career.md": note("career", "Casey career", "entity_key: person:casey-linden\n"),
}


def vault(brain, notes=NOTES, *, link_notes=True):
    write(brain / "README.md", "---\nid: readme\n---\n# Brain\n\n[[index/MEMORY_INDEX|Memory Index]]\n")
    write(brain / "index/MEMORY_INDEX.md", "---\nid: memory-index\ntype: index\n---\n# Memory Index\n\n[[README]]\n"
          + MACHINE + "\n## Historical source intake\n\n[[sources/historical_calls/INDEX|Historical calls]]\n")
    links = "".join(f"- [[sources/historical_calls/{name[:-3]}|{name}]]\n" for name in sorted(notes)) if link_notes else ""
    write(brain / "sources/historical_calls/INDEX.md",
          "---\nid: hc-index\ntype: index\n---\n# Historical Calls — Records Index\n\n" + links + "\n[[index/MEMORY_INDEX|Memory Index]]\n")
    for name, text in notes.items():
        write(brain / "sources/historical_calls" / name, text)
    write(brain / "inbox/distilled_packets/shared-submit-abc.md",
          '---\nid: shared-submit-abc\ntype: decision\nstatus: unconfirmed\ntitle: "Cara beta decision"\n'
          'source_refs: ["business_brain:sources/historical_calls/cara-a.md"]\n---\n# Cara beta decision\n')
    for name in ("app.json", "appearance.json", "core-plugins.json", "workspace.json"):
        write(brain / ".obsidian" / name, "{}\n")
    write(brain / ".obsidian/graph.json", json.dumps({"search": "-path:_backups"}) + "\n")


class Registry:
    """search_source_identities for the D1 targets: MEMORY_INDEX by path, people/ by prefix."""

    def __init__(self, people=True):
        self.people = people

    def scope_for_search_identity(self, source, path):
        if source != "business_brain":
            return None
        if path == "business_brain:index/MEMORY_INDEX.md" or (self.people and path.startswith("business_brain:people/")):
            return "global"
        return None


def ok_refresh(pointers):
    return {"status": "success"}, {"status": "success", "coverage": [{"pointer": p} for p in pointers]}


def ok_verify(plan, hubs):
    return {"passed": True, "problems": [], "hubs": [h["entity_id"] for h in hubs]}


def bad_verify(plan, hubs):
    return {"passed": False, "problems": ["person:cara-harcourt resolves to no person"], "hubs": []}


def head(brain):
    return subprocess.check_output(["git", "-C", str(brain), "rev-parse", "HEAD"], text=True).strip()


class IdentityRuleTests(unittest.TestCase):
    def notes(self, rows):
        return [{"relative": f"sources/historical_calls/{k}.md", "pointer": f"business_brain:sources/historical_calls/{k}.md",
                 "keys": keys, "participants": parts} for k, keys, parts in rows]

    def test_exact_keys_owner_first_names_prose_and_indexer_exclusions(self):
        result = F.resolve_identities(self.notes([
            ("a", ["person:cara-harcourt"], "Casey Linden; Cara Harcourt"),
            ("b", [], "Casey Linden; Cara Lindqvist"),
            ("c", ["person:theodore-pryce"], "Casey Linden; Theodore (surname absent from metadata)"),
            ("d", [], "Casey Linden; Imogen (surname inferred)"),
            ("e", [], "Casey Linden; CCI participants not individually attributable"),
            ("f", [], "Casey Linden; Tokeni Smith"),
            ("g", ["person:casey-linden"], ""),
            ("h", [], "Casey Linden; Cara"),
        ]), hubs={}, people={})
        by = result["by_note"]
        self.assertEqual({"person:cara-harcourt"}, by["sources/historical_calls/a.md"])
        self.assertEqual({"person:cara-lindqvist"}, by["sources/historical_calls/b.md"], "never merged with Cara Harcourt")
        self.assertEqual({"person:theodore-pryce"}, by["sources/historical_calls/c.md"])
        self.assertNotIn("sources/historical_calls/g.md", by, "the owner gets no hub")
        self.assertFalse(any("person:casey-linden" in ids for ids in by.values()))
        needs = {(n["name"], n["reason"]) for n in result["needs"]}
        self.assertIn(("Imogen (surname inferred)", "first name only"), needs)
        self.assertIn(("Cara", "first name only"), needs, "a bare Cara is never guessed")
        self.assertIn(("Tokeni Smith", "hub path would be skipped by the search indexer"), needs)
        self.assertEqual([{"note": "business_brain:sources/historical_calls/c.md",
                           "participant": "Theodore (surname absent from metadata)"}], result["covered"])
        self.assertEqual(["CCI participants not individually attributable"], [u["segment"] for u in result["unparsed"]])
        for row in result["needs"]:
            self.assertNotRegex(row["reason"], r"(?i)token|secret|credential|password", "reasons must stay searchable")

    def test_multi_match_goes_to_needs_identity(self):
        hubs = {"person:cara-x": {"title": "Cara X", "aliases": ["Cara Harcourt"]}}
        people = {"person:cara-harcourt": {"title": "Cara Harcourt", "aliases": {"Cara Harcourt"}}}
        result = F.resolve_identities(self.notes([("a", [], "Cara Harcourt")]), hubs=hubs, people=people)
        self.assertEqual({}, result["by_note"])
        self.assertEqual("matches more than one person: person:cara-harcourt, person:cara-x", result["needs"][0]["reason"])
        single = F.resolve_identities(self.notes([("a", [], "Cara Harcourt")]), hubs={}, people=people)
        self.assertEqual({"person:cara-harcourt"}, single["by_note"]["sources/historical_calls/a.md"])

    def test_owner_is_configurable(self):
        rows = self.notes([("a", [], "Casey Linden; Cara Harcourt")])
        self.assertEqual({"person:casey-linden"}, F.resolve_identities(rows, hubs={}, people={}, owners=("Cara Harcourt",))["by_note"]["sources/historical_calls/a.md"])
        with mock.patch.dict(os.environ, {F.OWNERS_ENV: "Casey Linden; Cara Harcourt"}):
            self.assertEqual(("Casey Linden", "Cara Harcourt"), F.owner_names())
            self.assertEqual({}, F.resolve_identities(rows, hubs={}, people={}, owners=F.owner_names())["by_note"])


class FilingTransactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.brain = (self.root / "TTROS Business Brain").resolve()
        self.db = self.root / "search/os_index.db"  # absent: no entity-index persons

    def git_brain(self, **kwargs):
        vault(self.brain, **kwargs)
        subprocess.run(["git", "init", "-q", str(self.brain)], check=True)
        subprocess.run(["git", "-C", str(self.brain), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.brain), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "commit", "-qm", "fixture"], check=True)
        for patcher in (mock.patch.object(brain_memory, "VAULT_ROOT", self.brain),
                        mock.patch("tools.brain_git_closure.push_commit", return_value="local_only"),
                        mock.patch("tools.brain_git_closure.record"),
                        mock.patch("tools.aos_indexer.index_one", return_value={"status": "success"}),
                        mock.patch("tools.aos_entity_index.ensure_current"),
                        mock.patch.dict(os.environ, {F.FLAG: "1"})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def file(self, notes=None, **kwargs):
        options = dict(brain=self.brain, root=self.root, db_path=self.db, registry=Registry(),
                       refresh=ok_refresh, verifier=ok_verify)
        options.update(kwargs)
        return F.file_notes(notes, **options)

    def ledger(self):
        path = self.root / F.LEDGER
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_dry_run_proves_the_plan_on_a_copy_and_writes_nothing(self):
        self.git_brain()
        before = {p: p.read_bytes() for p in self.brain.rglob("*") if p.is_file() and ".git" not in p.parts}
        receipt = self.file(dry_run=True)
        self.assertEqual("validated", receipt["outcome"], receipt.get("reason"))
        self.assertEqual(before, {p: p.read_bytes() for p in self.brain.rglob("*") if p.is_file() and ".git" not in p.parts})
        self.assertFalse((self.root / F.LEDGER).exists(), "dry-run records nothing")
        self.assertTrue(receipt["on_copy"]["passed"])
        hubs = {h["entity_id"]: h["count"] for h in receipt["plan"]["hubs"]}
        self.assertEqual({"person:theodore-pryce": 1, "person:cara-lindqvist": 1, "person:cara-harcourt": 2}, hubs)
        self.assertTrue(any(line.startswith("+- [[people/INDEX|People]]") for line in receipt["memory_index_diff"]))

    def test_all_writes_hubs_indexes_and_memory_index_in_one_commit(self):
        self.git_brain()
        before_head = head(self.brain)
        receipt = self.file()
        self.assertEqual("filed", receipt["outcome"], receipt.get("reason"))
        changed = subprocess.check_output(["git", "-C", str(self.brain), "show", "--name-only", "--format=", "HEAD"], text=True).split()
        self.assertEqual(sorted(["index/MEMORY_INDEX.md", "people/INDEX.md", "people/NEEDS_IDENTITY.md", "people/theodore-pryce.md",
                                 "people/cara-lindqvist.md", "people/cara-harcourt.md"]), sorted(changed))
        self.assertEqual(before_head, subprocess.check_output(["git", "-C", str(self.brain), "rev-parse", "HEAD~1"], text=True).strip(),
                         "one transaction, one commit")
        hub = (self.brain / "people/cara-harcourt.md").read_text(encoding="utf-8")
        fields, _ = brain_memory.parse_frontmatter(hub)
        self.assertEqual(("person-cara-harcourt", "person", "person:cara-harcourt", "Cara Harcourt", ["Cara Harcourt"]),
                         (fields["id"], fields["type"], fields["entity_key"], fields["title"], fields["aliases"]))
        self.assertIn("- [[sources/historical_calls/cara-a|Cara Harcourt / Casey — beta]] · observed 2026-11-04 · historical_evidence\n", hub)
        self.assertIn("- [[sources/historical_calls/cara-b|Cara Harcourt / Casey — roster]] · observed 2026-11-04 · historical_evidence\n", hub)
        # D3: a submit record is a plain pointer, never a wiki-link
        self.assertIn("- `business_brain:inbox/distilled_packets/shared-submit-abc.md` · Cara beta decision", hub)
        self.assertNotIn("[[inbox/", hub)
        section = re.search(r"<!-- TTROS:HERMES:generated-navigation:BEGIN -->[\s\S]*?:END -->", hub).group(0)
        self.assertNotRegex(section, r"\d{4}-\d\d-\d\dT\d\d:", "no timestamps inside sections")
        self.assertNotIn("casey-linden", " ".join(changed))
        lindqvist = (self.brain / "people/cara-lindqvist.md").read_text(encoding="utf-8")
        self.assertIn("lindqvist|Cara Lindqvist pilot", lindqvist)
        self.assertNotIn("cara-a", lindqvist)
        index = (self.brain / "people/INDEX.md").read_text(encoding="utf-8")
        self.assertIn("- [[people/cara-harcourt|Cara Harcourt]] · 2 records · latest 2026-11-04", index)
        self.assertIn("[[people/NEEDS_IDENTITY|Needs identity]] · 2 names filing could not tie to one person", index)
        needs = (self.brain / "people/NEEDS_IDENTITY.md").read_text(encoding="utf-8")
        self.assertIn("- Imogen (surname inferred) · first name only · `business_brain:sources/historical_calls/imogen.md`", needs)
        self.assertIn("- Tokeni Smith · hub path would be skipped by the search indexer", needs)
        memory_index = (self.brain / "index/MEMORY_INDEX.md").read_text(encoding="utf-8")
        self.assertIn(MACHINE, memory_index, "machine block byte-identical")
        self.assertIn("- [[people/INDEX|People]] · 3 person hubs · 2 names needing identity", memory_index)
        self.assertIn("- [[sources/historical_calls/INDEX|Historical Calls — Records Index]] · 8 linked notes", memory_index)
        result = analyze_vault(self.brain)
        self.assertEqual("PASS", result["status"])
        self.assertTrue(receipt["verify"]["vault"]["passed"])
        self.assertEqual([], receipt["verify"]["vault"]["notes_beyond_two_hops_of_memory_index"])
        self.assertEqual(2, result["root_distances"]["people/cara-harcourt.md"])
        self.assertEqual(["filed"], [row["outcome"] for row in self.ledger()])

    def test_second_run_is_unchanged_and_writes_nothing(self):
        self.git_brain()
        self.assertEqual("filed", self.file()["outcome"])
        after = head(self.brain)
        bytes_after = {p: p.read_bytes() for p in (self.brain / "people").glob("*.md")}
        second = self.file()
        self.assertEqual("unchanged", second["outcome"])
        self.assertEqual([], second["documents"])
        self.assertEqual(after, head(self.brain))
        self.assertEqual(bytes_after, {p: p.read_bytes() for p in (self.brain / "people").glob("*.md")})
        self.assertEqual({"outcome": "nothing_pending"}, F.retry_pending(brain=self.brain, root=self.root))

    def test_dates_say_what_they_are_and_import_dates_never_count_as_latest(self):
        write(self.root / "v/x.md", '---\nid: x\nsource_date_text: "Jun 30"\nimported_at: "2026-08-17"\n---\n# X\n')
        write(self.root / "v/y.md", '---\nid: y\nimported_at: "2026-08-17"\n---\n# Y\n')
        write(self.root / "v/z.md", '---\nid: z\n---\n# Z\n')
        rows = {name: F._read_note(self.root / "v", f"{name}.md") for name in "xyz"}
        self.assertEqual(("2026-06-30", "2026-06-30"), (rows["x"]["date"], rows["x"]["date_label"]))
        self.assertEqual(("", "imported 2026-08-17"), (rows["y"]["date"], rows["y"]["date_label"]))
        self.assertEqual(("", "undated"), (rows["z"]["date"], rows["z"]["date_label"]))

    def test_render_is_deterministic(self):
        self.git_brain()
        first, second = F.build_plan(self.brain, db_path=self.db), F.build_plan(self.brain, db_path=self.db)
        self.assertEqual({k: F._strip_stamp(v) for k, v in first["documents"].items()},
                         {k: F._strip_stamp(v) for k, v in second["documents"].items()})

    def test_scope_check_refuses_targets_outside_search_identities(self):
        self.git_brain()
        before = head(self.brain)
        receipt = self.file(registry=Registry(people=False))
        self.assertEqual("filing_failed", receipt["outcome"])
        self.assertIn("not covered by search_source_identities: people/", receipt["reason"])
        self.assertEqual(before, head(self.brain))
        self.assertFalse((self.brain / "people").exists())
        with self.assertRaisesRegex(F.FilingError, "not a D1 navigation target"):
            F.check_scope(["memory/clients.md"], Registry())

    def test_post_write_gate_failure_rolls_back(self):
        self.git_brain(link_notes=False)  # the historical INDEX links no note, so none is within two hops
        before = head(self.brain)
        receipt = self.file()
        self.assertEqual("filing_failed", receipt["outcome"])
        self.assertIn("post-write vault gate failed", receipt["reason"])
        self.assertEqual(before, head(self.brain))
        self.assertFalse((self.brain / "people/cara-harcourt.md").exists())
        self.assertEqual("", subprocess.check_output(["git", "-C", str(self.brain), "status", "--porcelain"], text=True))

    def test_failed_verification_is_recorded_and_retried(self):
        self.git_brain()
        first = self.file(verifier=bad_verify)
        self.assertEqual("filing_failed", first["outcome"])
        self.assertIn("resolves to no person", first["reason"])
        self.assertIn("business_brain:sources/historical_calls/cara-a.md", F.pending_notes(self.root))
        # an identical failing retry is not re-recorded
        F.retry_pending(brain=self.brain, root=self.root, db_path=self.db, registry=Registry(), refresh=ok_refresh, verifier=bad_verify)
        F.retry_pending(brain=self.brain, root=self.root, db_path=self.db, registry=Registry(), refresh=ok_refresh, verifier=bad_verify)
        self.assertEqual(["filing_failed", "filing_failed"], [r["outcome"] for r in self.ledger()])
        retried = F.retry_pending(brain=self.brain, root=self.root, db_path=self.db, registry=Registry(), refresh=ok_refresh, verifier=ok_verify)
        self.assertEqual("filed", retried["outcome"], retried.get("reason"))
        self.assertEqual([], F.pending_notes(self.root))

    def test_refresh_pending_is_filing_pending_and_retried(self):
        self.git_brain()
        pending = self.file(refresh=lambda p: ({"status": "success"}, {"status": "pending", "reason": "Graphify build did not succeed"}))
        self.assertEqual("filing_pending", pending["outcome"])
        self.assertTrue(pending["commit"], "the navigation commit stands; only the refresh is retried")
        seen = []
        retried = F.retry_pending(brain=self.brain, root=self.root, db_path=self.db, registry=Registry(),
                                  refresh=lambda p: (seen.append(p), ok_refresh(p))[1], verifier=ok_verify)
        self.assertEqual("filed", retried["outcome"])
        self.assertIn("business_brain:people/cara-harcourt.md", seen[0], "the retry refreshes the hubs again")


V3 = "undated-tobias-programming-opinions-icm-e28011a3"
V3_NOTES = {
    f"{V3}/INDEX.md": '---\nid: v3-source-index\ntype: source_index\ntitle: "Programming opinions"\n---\n# Programming opinions\n\n'
                      f"- [[sources/historical_calls/{V3}/folder-system|Folder system]]\n- [[sources/historical_calls/{V3}/portable-brand|Portable brand]]\n",
    f"{V3}/folder-system.md": note("v3-folder", "Folder system", "entity_key: person:tobias-hartley\n"),
    f"{V3}/portable-brand.md": note("v3-brand", "Portable brand", "entity_key: person:tobias-hartley\n"),
}


class ReachabilityGateTests(FilingTransactionTests):
    """The two-hop gate: nested v3 source folders stay reachable, and unrelated older gaps never block filing."""

    def vault_with_v3(self, *, stray=True):
        self.git_brain(notes=V3_NOTES)
        # Like the live vault: unrelated notes already sit beyond two hops of the roots (Drive provenance nodes etc.).
        if stray:
            write(self.brain / "sources/intake/drive/1AbC.md", "---\nid: drive-source-1AbC\ntype: source\n---\n# Drive node\n")
            write(self.brain / "prospects/2026-07-old.md", "---\nid: prospect-old\ntype: prospect\n---\n# Old prospect\n")
            subprocess.run(["git", "-C", str(self.brain), "add", "."], check=True)
            subprocess.run(["git", "-C", str(self.brain), "-c", "user.name=Fixture", "-c", "user.email=f@example.invalid",
                            "commit", "-qm", "strays"], check=True)

    def test_nested_v3_topic_notes_and_hub_are_within_two_hops(self):
        self.vault_with_v3(stray=False)
        receipt = self.file()
        self.assertEqual("filed", receipt["outcome"], receipt.get("reason"))
        checks = receipt["verify"]["vault"]
        self.assertEqual([], checks["notes_beyond_two_hops_of_memory_index"])
        self.assertEqual([], checks["navigation_beyond_two_hops_of_memory_index"])
        hub = (self.brain / "people/tobias-hartley.md").read_text(encoding="utf-8")
        self.assertIn(f"sources/historical_calls/{V3}/folder-system", hub)
        self.assertIn(f"[[sources/historical_calls/{V3}/INDEX|source]]", hub)   # the v3 source INDEX is linked, not omitted

    def test_preexisting_unreachable_notes_are_reported_and_do_not_block_filing(self):
        self.vault_with_v3()
        self.assertEqual("FAIL", analyze_vault(self.brain)["status"])          # the vault was already failing before filing
        dry = self.file(dry_run=True)
        self.assertEqual("validated", dry["outcome"], dry.get("reason"))
        receipt = self.file()
        self.assertEqual("filed", receipt["outcome"], receipt.get("reason"))
        checks = receipt["verify"]["vault"]
        self.assertEqual("FAIL", checks["analyze_vault_raw"])
        self.assertEqual([], checks["introduced_failures"])
        # The orphan prospect is reported, never hidden; the Drive provenance node is raw evidence, not a failure.
        self.assertEqual(1, checks["preexisting_failures"], "reported, never hidden")

    def test_filing_is_idempotent_with_preexisting_gaps(self):
        self.vault_with_v3()
        self.assertEqual("filed", self.file()["outcome"])
        after = head(self.brain)
        again = self.file()
        self.assertEqual("unchanged", again["outcome"])
        self.assertEqual(after, head(self.brain))
        self.assertEqual({"outcome": "nothing_pending"}, F.retry_pending(brain=self.brain, root=self.root))
        self.assertEqual(["filed", "unchanged"], [row["outcome"] for row in self.ledger()])

    def test_a_newly_unreachable_note_still_fails_the_gate(self):
        self.vault_with_v3()
        write(self.brain / "prospects/2026-11-brand-new.md", "---\nid: prospect-new\ntype: prospect\n---\n# New\n")
        baseline = F.baseline_unreachable(self.brain)
        write(self.brain / "prospects/2026-11-newer.md", "---\nid: prospect-newer\ntype: prospect\n---\n# Newer\n")
        checks = F.vault_checks(self.brain, before_index=(self.brain / F.MEMORY_INDEX).read_text(encoding="utf-8"),
                                notes=[], documents={F.MEMORY_INDEX: ""}, baseline=baseline)
        self.assertEqual(['unreachable:"prospects/2026-11-newer.md"'], checks["introduced_failures"])
        self.assertEqual("FAIL", checks["analyze_vault"])
        self.assertFalse(checks["passed"])
        strict = F.vault_checks(self.brain, before_index=(self.brain / F.MEMORY_INDEX).read_text(encoding="utf-8"),
                                notes=[], documents={F.MEMORY_INDEX: ""})
        self.assertEqual("FAIL", strict["analyze_vault"], "without a baseline the gate stays strict")


class ZeroNoteHubTests(unittest.TestCase):
    """A hub whose notes were all removed for re-ingest is kept, marked, and only warned about."""

    setUp, git_brain, file = FilingTransactionTests.setUp, FilingTransactionTests.git_brain, FilingTransactionTests.file

    def remove_cara_notes(self):
        hc = self.brain / "sources/historical_calls/INDEX.md"
        hc.write_text("".join(line for line in hc.read_text().splitlines(keepends=True) if "cara-a" not in line and "cara-b" not in line))
        for name in ("cara-a.md", "cara-b.md"):
            (self.brain / "sources/historical_calls" / name).unlink()
        subprocess.run(["git", "-C", str(self.brain), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.brain), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "commit", "-qm", "remove cara"], check=True)

    def test_zero_note_hub_is_kept_marked_and_filed(self):
        self.git_brain()
        self.assertEqual("filed", self.file()["outcome"])
        self.remove_cara_notes()
        receipt = self.file()
        self.assertEqual("filed", receipt["outcome"], receipt.get("reason"))
        hub = (self.brain / "people/cara-harcourt.md").read_text(encoding="utf-8")
        self.assertIn(F.AWAITING, hub)
        self.assertNotIn("sources/historical_calls/cara-a", hub)
        index = (self.brain / "people/INDEX.md").read_text(encoding="utf-8")
        self.assertIn("- [[people/cara-harcourt|Cara Harcourt]] · no notes yet (awaiting re-ingest)\n", index)
        self.assertIn("- [[people/cara-lindqvist|Cara Lindqvist]] · 1 record · latest", index)
        plan = F.build_plan(self.brain, db_path=self.db)
        self.assertEqual({"person:cara-harcourt"}, {h["entity_id"] for h in plan["hubs"] if h["awaiting_reingest"]})
        self.assertEqual("PASS", analyze_vault(self.brain)["status"])
        self.assertEqual("unchanged", self.file()["outcome"], "the marked render is stable")

    def live(self, hubs, entities=None):
        graph = {"nodes": [{"kind": "note", "source_path": "business_brain:" + h["relative"], "id": h["relative"]} for h in hubs]
                 + [{"kind": "note", "source_path": n, "id": n} for h in hubs for n in h["notes"]],
                 "edges": [{"source": h["relative"], "target": n, "relation": "wiki_link"} for h in hubs for n in h["notes"]]}
        found = {"groups": {"people": [{"path": "business_brain:" + h["relative"]} for h in hubs]}}
        exact = [{"entity_id": e, "entity_type": "person", "match": "exact"} for e in (entities or [])]
        view = {"knowledge": [{"path": n} for h in hubs for n in h["notes"]], "sources": []}
        with mock.patch.object(F.entity_index, "ensure_current"), \
                mock.patch.object(F.aos_indexer, "search", return_value=found), \
                mock.patch.object(F.entity_index, "search_entities", return_value={"entities": exact, "resolution": "none"}), \
                mock.patch.object(F.entity_index, "entity_view", return_value=view):
            return F.verify_live({}, brain=self.brain, registry=Registry(), db_path=self.db, hubs=hubs, graph_loader=lambda: graph)

    def test_f5_warns_for_a_zero_note_hub_and_still_fails_a_hub_with_notes(self):
        empty = {"relative": "people/cara-harcourt.md", "title": "Cara Harcourt", "entity_id": "person:cara-harcourt", "notes": []}
        full = {"relative": "people/theodore-pryce.md", "title": "Theodore Pryce", "entity_id": "person:theodore-pryce",
                "notes": ["business_brain:sources/historical_calls/theodore.md"]}
        only_empty = self.live([empty])
        self.assertTrue(only_empty["passed"], only_empty["problems"])
        self.assertEqual(1, len(only_empty["warnings"]))
        self.assertIn("awaiting re-ingest", only_empty["warnings"][0])
        both = self.live([empty, full])
        self.assertFalse(both["passed"], "a hub with notes that resolves to no person still fails")
        self.assertEqual(["Theodore Pryce resolves to no person, not person:theodore-pryce"], both["problems"])
        wrong = self.live([empty], entities=["person:cara-lindqvist"])
        self.assertFalse(wrong["passed"], "a zero-note hub that resolves to someone else still fails")
        self.assertEqual([], wrong["warnings"])

    def test_receipt_carries_the_warning(self):
        self.git_brain()
        self.assertEqual("filed", self.file()["outcome"])
        self.remove_cara_notes()
        warn = lambda plan, hubs: {"passed": True, "problems": [], "hubs": [], "warnings": ["people/cara-harcourt.md awaiting"]}
        receipt = self.file(verifier=warn, force_verify=True)
        self.assertEqual("filed", receipt["outcome"])
        self.assertEqual(["people/cara-harcourt.md awaiting"], receipt["warnings"])


class ImporterIntegrationTests(unittest.TestCase):
    """Filing runs after the import transaction and refresh; it never blocks or undoes the import."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.brain = (self.root / "TTROS Business Brain").resolve()
        vault(self.brain, notes={})
        subprocess.run(["git", "init", "-q", str(self.brain)], check=True)
        subprocess.run(["git", "-C", str(self.brain), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.brain), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "commit", "-qm", "fixture"], check=True)
        for patcher in (mock.patch.object(brain_memory, "VAULT_ROOT", self.brain),
                        mock.patch("tools.brain_git_closure.push_commit", return_value="local_only"),
                        mock.patch("tools.brain_git_closure.record"),
                        mock.patch("tools.aos_indexer.index_one", return_value={"status": "success"}),
                        mock.patch("tools.aos_entity_index.ensure_current"),
                        mock.patch.dict(os.environ, {F.FLAG: "1"})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.db = self.root / "search/os_index.db"

    def package(self, participants, name="cara-new"):
        pkg = self.root / "ready" / f"MI-{name}"
        rel = f"historical/{name}.md"
        payload = note(f"icm-{name}", f"Cara Harcourt / Casey — {name}", f'participants_text: "{participants}"\n').encode()
        write(pkg / rel, payload.decode())
        manifest = {
            "schema_version": "2.0", "semantic_contract_version": "2.0.0", "reconciliation_contract_version": "2.0.0",
            "transport_mode": "directory-v1", "status": "ready_for_ttros", "package_id": pkg.name, "batch_id": name,
            "batch_fingerprint_sha256": sha(name.encode()), "package_disposition": "evidence_only", "source_count": 1,
            "sources": [{"source_id": "SRC-001", "role": "batch_input", "title": "call"}],
            "canonical_comparison": {"status": "partial"}, "has_conflicts": False, "requires_human_review": False,
            "output_count": 1, "knowledge_types_present": ["historical_evidence"],
            "package_files": [{"path": rel, "size_bytes": len(payload), "sha256": sha(payload)}],
            "outputs": [{"package_path": rel, "size_bytes": len(payload), "sha256": sha(payload), "source_ids": ["SRC-001"],
                         "requires_human_review": False, "conflict_ids": [], "operation": "historical_record",
                         "temporal_posture": "historical",
                         "target": {"namespace": "historical", "suggested_relative_path": rel,
                                    "canonical_ref": None, "expected_base_sha256": None}}],
        }
        (pkg / "INGEST_MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
        return pkg

    def filing(self, verifier=ok_verify):
        return lambda pointers, **kw: F.file_imported(pointers, **kw, db_path=self.db, registry=Registry(),
                                                      refresh=ok_refresh, verifier=verifier)

    def run_import(self, pkg, filing):
        return process(pkg, brain=self.brain, root=self.root, refresh=ok_refresh, filing=filing)

    def import_ledger(self):
        return [json.loads(l) for l in (self.root / "queue/receipts/memory_exchange_import.jsonl").read_text().splitlines()]

    def test_success_files_after_the_import_commit(self):
        row = self.run_import(self.package("Casey Linden; Cara Harcourt"), self.filing())
        self.assertEqual("imported", row["outcome"], row.get("reason"))
        self.assertEqual("filed", row["filing"]["status"], row["filing"])
        self.assertNotEqual(row["commit"], row["filing"]["commit"], "filing is its own transaction, after the import's")
        parent = subprocess.check_output(["git", "-C", str(self.brain), "rev-parse", "HEAD~1"], text=True).strip()
        self.assertEqual(row["commit"], parent)
        hub = (self.brain / "people/cara-harcourt.md").read_text(encoding="utf-8")
        self.assertIn("[[sources/historical_calls/cara-new|", hub)
        self.assertEqual("filed", self.import_ledger()[-1]["filing"]["status"])
        self.assertEqual("PASS", analyze_vault(self.brain)["status"])

    def test_failure_leaves_the_import_imported_and_the_next_poll_retries(self):
        row = self.run_import(self.package("Casey Linden; Cara Harcourt"), self.filing(verifier=bad_verify))
        self.assertEqual("imported", row["outcome"])
        self.assertEqual("pending", row["filing"]["status"])
        self.assertTrue((self.brain / "sources/historical_calls/cara-new.md").is_file(), "the import is never undone")
        self.assertEqual(["business_brain:sources/historical_calls/cara-new.md"], F.pending_notes(self.root))
        retried = F.retry_pending(brain=self.brain, root=self.root, db_path=self.db, registry=Registry(), refresh=ok_refresh, verifier=ok_verify)
        self.assertEqual("filed", retried["outcome"])
        self.assertEqual([], F.pending_notes(self.root))

    def test_a_filing_crash_is_recorded_pending_and_retried(self):
        def crash(pointers, **kw):
            raise RuntimeError("filing exploded")
        row = self.run_import(self.package("Casey Linden; Cara Harcourt"), crash)
        self.assertEqual(("imported", "pending"), (row["outcome"], row["filing"]["status"]))
        self.assertIn("filing exploded", row["filing"]["reason"])
        self.assertEqual(["business_brain:sources/historical_calls/cara-new.md"], F.pending_notes(self.root))
        retried = F.retry_pending(brain=self.brain, root=self.root, db_path=self.db, registry=Registry(), refresh=ok_refresh, verifier=ok_verify)
        self.assertEqual("filed", retried["outcome"], retried.get("reason"))

    def test_kill_switch_skips_filing(self):
        with mock.patch.dict(os.environ, {F.FLAG: "0"}):
            row = self.run_import(self.package("Casey Linden; Cara Harcourt"), self.filing())
            self.assertEqual({"status": "disabled", "reason": "TTROS_MEMORY_FILING=0"}, row["filing"])
            self.assertEqual({"outcome": "disabled"}, F.retry_pending(brain=self.brain, root=self.root))
        self.assertEqual(row["commit"], head(self.brain), "no filing commit")
        self.assertFalse((self.brain / "people").exists())
        self.assertFalse((self.root / F.LEDGER).exists())

    def test_unchanged_filing_is_a_no_op(self):
        row = self.run_import(self.package("Casey Linden; Cara Harcourt"), self.filing())
        self.assertEqual("filed", row["filing"]["status"])
        filed_head = head(self.brain)
        again = F.file_notes(["business_brain:sources/historical_calls/cara-new.md"], brain=self.brain, root=self.root,
                             db_path=self.db, registry=Registry(), refresh=ok_refresh, verifier=ok_verify)
        self.assertEqual("unchanged", again["outcome"])
        self.assertEqual(filed_head, head(self.brain))

    def test_owner_only_participants_get_no_hub(self):
        row = self.run_import(self.package("Casey Linden"), self.filing())
        self.assertEqual("filed", row["filing"]["status"])
        self.assertEqual([], sorted(p.name for p in (self.brain / "people").glob("*.md") if p.name not in {"INDEX.md", "NEEDS_IDENTITY.md"}))
        self.assertIn("· 0 person hubs ·", (self.brain / "index/MEMORY_INDEX.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
