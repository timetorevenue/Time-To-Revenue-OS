"""Source provenance, stage receipts and verified import success (tools/memory_exchange_import.py).

Temp-dir Brains and the fictional v3 fixture only; no live ingestion, no model call, no Drive access.
Covers: success, partial (indexing) failure and its retry, missing metadata, hash mismatch, Brain
read-back failure, duplicate replay, and the rule that prepared ChatGPT ICM never reaches Luna.

Revisit: when the provenance block or stage vocabulary changes. · Last touched: 2026-11-13.
"""
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import brain_memory, drive_watcher as w, memory_exchange_import as mei, source_interpret as si
from tests.test_memory_ingest_v3 import FIXTURE, FOLDER, RECORD, SHA, V3Base, covered

POINTERS = sorted([f"business_brain:{FOLDER}{name}.md" for name in
                   ("INDEX", "call-tracker-feedback", "free-trial-next-steps", "missed-calls-problem",
                    "referral-brother-in-law")] + [f"business_brain:{RECORD}"])
NO_EXCHANGE_ENV = {k: v for k, v in os.environ.items() if k not in {"TTROS_MEMORY_READY", "TTROS_MEMORY_IMPORTED"}}


def utc(value):
    parsed = dt.datetime.fromisoformat(value)
    assert parsed.utcoffset() == dt.timedelta(0), value
    return parsed


def partial_refresh(paths):
    """What _refresh reports when keyword Search coverage passed but embeddings were unavailable."""
    return ({"status": "pending", "expected_paths": sorted(paths), "indexed_paths": sorted(paths), "missing_paths": [],
             "embeddings": {"status": "unavailable", "reason": "embedding runtime not installed"}},
            {"status": "pending", "reason": "embedding refresh pending"})


class ProvenanceBase(V3Base):
    def setUp(self):
        super().setUp()
        self.git_brain()
        env = mock.patch.dict(os.environ, NO_EXCHANGE_ENV, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def ledger(self):
        return mei.ledger_rows(self.root / mei.LEDGER)

    def run_prepared(self, **kwargs):
        return self.run_import(route="prepared_icm", **kwargs)


class SuccessTests(ProvenanceBase):
    def test_success_records_provenance_stages_and_utc_lifecycle(self):
        row = self.run_prepared()
        self.assertEqual("imported", row["outcome"], row.get("reason"))
        p = row["provenance"]
        self.assertEqual(mei.PROVENANCE_SCHEMA, p["schema"])
        self.assertEqual(SHA, p["source"]["sha256"])
        self.assertEqual(64, len(p["source"]["sha256"]))
        self.assertEqual("2026-10-14 Jordan Ellis intro call.txt", p["source"]["original_name"])
        self.assertEqual("text/plain", p["source"]["original_type"])
        self.assertEqual("derived:filename_suffix", p["basis"]["original_type"])
        self.assertEqual("chatgpt_memory_ingest_project", p["source"]["origin"])
        self.assertEqual("prepared_icm", p["processing_route"])
        # The fixture, like every real prepared manifest, declares no Drive identity: recorded as missing, not guessed.
        self.assertEqual(["external_id", "external_version", "original_location"], p["missing"])
        self.assertIsNone(p["source"]["external_id"])
        self.assertEqual("incomplete", p["metadata_status"])
        self.assertEqual(f"drive:04_IMPORTED/{FIXTURE.name}", p["package"]["location"])
        self.assertEqual("MI-jordan-ellis-intro-call-155cb165d6d1", p["package"]["package_id"])
        self.assertEqual(row["fingerprint"], p["package"]["fingerprint"])
        self.assertEqual(f"business_brain:{RECORD}", p["brain"]["record"])
        self.assertEqual([x for x in POINTERS if x != f"business_brain:{RECORD}"], p["brain"]["outputs"])

        stages = row["stages"]
        self.assertEqual({"capture", "semantic_preparation", "deterministic_import", "indexing"}, set(stages))
        self.assertEqual("success", stages["capture"]["status"])
        self.assertEqual("package_source_file", stages["capture"]["via"])
        self.assertEqual({"status": "prepared_externally", "by": "chatgpt_memory_ingest_project",
                          "ttros_model_invocations": 0, "luna": "not_called"}, stages["semantic_preparation"])
        self.assertEqual("success", stages["deterministic_import"]["status"])
        self.assertEqual({"status": "verified", "checked": 6}, stages["deterministic_import"]["verification"])
        self.assertEqual(1, stages["deterministic_import"]["attempts"])
        self.assertTrue(stages["deterministic_import"]["commit"])
        self.assertEqual("complete", stages["indexing"]["status"])
        self.assertEqual({"retryable": False, "mode": "none"}, row["retry"])
        life = row["lifecycle"]
        self.assertLessEqual(utc(life["first_attempt_at"]), utc(life["import_committed_at"]))
        self.assertLessEqual(utc(life["import_committed_at"]), utc(life["indexed_at"]))
        utc(life["last_attempt_at"])
        # The durable ledger row is the receipt.
        self.assertEqual(row, self.ledger()[-1])

    def test_declared_metadata_is_complete_and_attributed(self):
        manifest = self.manifest()
        manifest["source"].update(external_id="1AbCdEfGhIjK", external_version="rev-7", origin="google_drive",
                                  original_location="drive:Calls/2026-10-14 Jordan Ellis intro call.txt",
                                  original_type="text/plain")
        self.save(manifest)
        row = self.run_prepared()
        self.assertEqual("imported", row["outcome"], row.get("reason"))
        p = row["provenance"]
        self.assertEqual("complete", p["metadata_status"])
        self.assertEqual([], p["missing"])
        self.assertEqual("declared:source.external_id", p["basis"]["external_id"])
        self.assertEqual("rev-7", p["source"]["external_version"])
        self.assertEqual("google_drive", p["source"]["origin"])

    def test_an_undeclared_route_is_recorded_as_unrecorded_not_guessed(self):
        row = self.run_import()
        self.assertEqual("imported", row["outcome"], row.get("reason"))
        self.assertEqual("unrecorded", row["provenance"]["processing_route"])
        self.assertIn("origin", row["provenance"]["missing"])
        self.assertEqual("unrecorded", row["stages"]["semantic_preparation"]["status"])

    def test_provenance_is_a_pure_function_of_the_package(self):
        manifest = self.manifest()
        one = mei.source_provenance(manifest, "f" * 64, self.pkg, self.root, "prepared_icm")
        two = mei.source_provenance(json.loads(json.dumps(manifest)), "f" * 64, self.pkg, self.root, "prepared_icm")
        self.assertEqual(json.dumps(one, sort_keys=True), json.dumps(two, sort_keys=True))
        self.assertNotIn("_at\"", json.dumps({k: v for k, v in one.items() if k != "package"}))


class PartialFailureTests(ProvenanceBase):
    def test_restart_after_brain_commit_recovers_without_a_second_commit(self):
        first = self.run_prepared()
        self.assertEqual("imported", first["outcome"])
        intent = self.ledger()[0]
        self.assertEqual("in_progress", intent["outcome"])
        (self.root / mei.LEDGER).write_text(json.dumps(intent) + "\n")

        recovered = self.run_prepared()
        self.assertEqual("imported", recovered["outcome"], recovered.get("reason"))
        self.assertEqual(first["commit"], recovered["commit"])
        self.assertEqual(first["commit"], self.head())
        self.assertEqual("already_imported", self.run_prepared()["outcome"])

    def test_incomplete_indexing_is_reported_truthfully_and_retried(self):
        pending = self.run_prepared(refresh=partial_refresh)
        self.assertEqual("refresh_pending", pending["outcome"])
        self.assertEqual("success", pending["stages"]["deterministic_import"]["status"])
        indexing = pending["stages"]["indexing"]
        self.assertEqual("partial", indexing["status"])
        self.assertEqual("success", indexing["components"]["search"]["status"])
        self.assertEqual("unavailable", indexing["components"]["embeddings"]["status"])
        self.assertEqual("not_run", indexing["components"]["entity_index"]["status"])
        self.assertEqual("pending", indexing["components"]["graphify"]["status"])
        self.assertEqual(["embeddings", "entity_index", "graphify"], indexing["incomplete"])
        self.assertEqual("automatic_next_poll", pending["retry"]["mode"])
        self.assertIsNone(pending["lifecycle"]["indexed_at"])
        head = self.head()

        done = self.run_prepared()
        self.assertEqual("imported", done["outcome"], done.get("reason"))
        self.assertEqual(head, self.head(), "the retry rewrites nothing")
        self.assertEqual("complete", done["stages"]["indexing"]["status"])
        self.assertEqual(2, done["stages"]["indexing"]["attempts"])
        self.assertEqual(2, done["stages"]["deterministic_import"]["attempts"])
        self.assertEqual(pending["lifecycle"]["first_attempt_at"], done["lifecycle"]["first_attempt_at"])
        self.assertEqual(pending["lifecycle"]["import_committed_at"], done["lifecycle"]["import_committed_at"])
        self.assertIsNotNone(done["lifecycle"]["indexed_at"])

    def test_search_coverage_failure_is_failed_not_partial(self):
        stage = mei.indexing_stage({"status": "failed", "missing_paths": ["business_brain:x.md"]},
                                   {"status": "pending", "reason": "search refresh failed"})
        self.assertEqual("failed", stage["status"])
        self.assertEqual(["business_brain:x.md"], stage["components"]["search"]["missing_paths"])
        self.assertIn("search", stage["incomplete"])

    def test_disabled_embeddings_are_degraded_not_hidden(self):
        stage = mei.indexing_stage({"status": "success", "embeddings": {"status": "disabled"},
                                    "entity_index": {"status": "current"}}, {"status": "success"})
        self.assertEqual("complete", stage["status"])
        self.assertEqual(["embeddings"], stage["degraded"])

    def test_brain_read_back_mismatch_is_never_reported_as_imported(self):
        real = brain_memory.write_transaction

        def tampering(documents, **kwargs):
            result = real(documents, **kwargs)
            target = self.brain / FOLDER / "missed-calls-problem.md"
            target.write_text(target.read_text(encoding="utf-8") + "\nunexpected\n", encoding="utf-8")
            return result

        refreshed = []
        with mock.patch.object(brain_memory, "write_transaction", side_effect=tampering):
            row = self.run_prepared(refresh=lambda p: refreshed.append(p) or covered(p))
        self.assertEqual("verification_failed", row["outcome"])
        self.assertIn("note content differs from the package", row["reason"])
        self.assertEqual([], refreshed, "no indexing claim after a failed read-back")
        self.assertEqual("failed", row["stages"]["deterministic_import"]["status"])
        self.assertEqual("operator_review", row["retry"]["mode"])
        self.assertIsNone(row["lifecycle"]["indexed_at"])
        # The next poll re-verifies the importer's own write; it neither overwrites nor claims success.
        head = self.head()
        again = self.run_prepared()
        self.assertEqual(head, self.head())
        self.assertNotEqual("imported", again["outcome"])


class MissingMetadataTests(ProvenanceBase):
    def test_missing_original_name_is_rejected_with_provenance_recorded(self):
        manifest = self.manifest()
        del manifest["source"]["original_filename"]
        self.save(manifest)
        head = self.head()
        row = self.run_prepared()
        self.assertEqual("rejected", row["outcome"])
        self.assertIn("missing metadata: source.original_filename", row["reason"])
        self.assertEqual(head, self.head())
        self.assertFalse((self.brain / RECORD).exists())
        self.assertIsNone(row["provenance"]["source"]["original_name"])
        self.assertIn("original_name", row["provenance"]["missing"])
        self.assertEqual("failed", row["stages"]["deterministic_import"]["status"])
        self.assertEqual("new_package_required", row["retry"]["mode"])
        self.assertEqual("rejected", self.ledger()[-1]["outcome"])

    def test_unreadable_manifest_still_leaves_a_receipt(self):
        (self.pkg / "INGEST_MANIFEST.json").write_text("{not json", encoding="utf-8")
        row = mei.process_v3(self.pkg, brain=self.brain, root=self.root, refresh=covered, route="prepared_icm")
        self.assertEqual("rejected", row["outcome"])
        self.assertEqual("unreadable", row["provenance"]["metadata_status"])
        self.assertEqual("failed", row["stages"]["deterministic_import"]["status"])


class HashMismatchTests(ProvenanceBase):
    def test_source_bytes_that_do_not_match_the_declared_sha_are_refused(self):
        (self.pkg / "source/original.txt").write_bytes(b"tampered\n")
        head = self.head()
        row = self.run_prepared()
        self.assertEqual("rejected", row["outcome"])
        self.assertIn("mismatch", row["reason"])
        self.assertEqual("failed", row["stages"]["capture"]["status"])
        self.assertEqual(SHA, row["provenance"]["source"]["sha256"], "the declared hash is kept for audit")
        self.assertEqual(head, self.head())

    def write_capture(self, drive_id, data):
        path = self.brain / mei.DRIVE_CAPTURE_RELATIVE / f"{drive_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\nid: drive-source-x\ntype: source\nstatus: evidence_only\n---\n# Drive source provenance\n\n"
                        "```json\n" + json.dumps(data) + "\n```\n", encoding="utf-8")

    def test_a_capture_record_naming_another_source_hash_is_refused(self):
        self.write_capture("1AbCdEfGhIjK", {"drive_id": "1AbCdEfGhIjK", "revision": "rev-7",
                                            "source_record": "business_brain:sources/intake/records/" + "0" * 64 + ".md"})
        manifest = self.manifest()
        manifest["source"]["drive_id"] = "1AbCdEfGhIjK"
        self.save(manifest)
        row = self.run_prepared()
        self.assertEqual("rejected", row["outcome"])
        self.assertIn("hash mismatch: Drive capture record", row["reason"])
        self.assertEqual("failed", row["stages"]["capture"]["status"])

    def test_a_declared_version_that_differs_from_capture_is_refused(self):
        self.write_capture("1AbCdEfGhIjK", {"drive_id": "1AbCdEfGhIjK", "revision": "rev-7",
                                            "source_record": f"business_brain:{RECORD}"})
        manifest = self.manifest()
        manifest["source"].update(drive_id="1AbCdEfGhIjK", external_version="rev-6")
        self.save(manifest)
        row = self.run_prepared()
        self.assertEqual("rejected", row["outcome"])
        self.assertIn("source version mismatch", row["reason"])

    def test_a_matching_capture_record_is_linked_as_the_capture_stage(self):
        self.write_capture("1AbCdEfGhIjK", {"drive_id": "1AbCdEfGhIjK", "revision": "rev-7", "active": True,
                                            "source_record": f"business_brain:{RECORD}"})
        manifest = self.manifest()
        manifest["source"].update(drive_id="1AbCdEfGhIjK", external_version="rev-7")
        self.save(manifest)
        row = self.run_prepared()
        self.assertEqual("imported", row["outcome"], row.get("reason"))
        capture = row["stages"]["capture"]
        self.assertEqual("drive_watcher", capture["via"])
        self.assertEqual("business_brain:sources/intake/drive/1AbCdEfGhIjK.md", capture["capture_record"]["pointer"])

    def test_a_conflicting_capture_hint_is_refused(self):
        manifest = self.manifest()
        manifest["source"]["drive_id"] = "1AbCdEfGhIjK"
        self.save(manifest)
        row = mei.process_v3(self.pkg, brain=self.brain, root=self.root, refresh=covered, route="luna_semantic",
                             origin_hint={"external_id": "9ZzOther"})
        self.assertEqual("rejected", row["outcome"])
        self.assertIn("source identity mismatch", row["reason"])


class DuplicateReplayTests(ProvenanceBase):
    def test_replay_is_idempotent_and_reproduces_the_receipt(self):
        first = self.run_prepared()
        self.assertEqual("imported", first["outcome"], first.get("reason"))
        head, lines = self.head(), len(self.ledger())
        second = self.run_prepared()
        third = self.run_prepared()
        self.assertEqual("already_imported", second["outcome"])
        self.assertEqual(head, self.head())
        self.assertEqual(lines, len(self.ledger()), "a replay appends nothing")
        for key in ("provenance", "lifecycle"):
            self.assertEqual(first[key], second[key], key)
        self.assertEqual(json.dumps(second, sort_keys=True), json.dumps(third, sort_keys=True))
        self.assertEqual(first["stages"]["indexing"]["status"], second["stages"]["indexing"]["status"])
        self.assertEqual({"status": "verified", "checked": 6}, second["verification"])

    def test_replay_does_not_trust_a_prior_imported_label_when_the_original_is_gone(self):
        first = self.run_prepared()
        self.assertEqual("imported", first["outcome"], first.get("reason"))
        (self.brain / RECORD).unlink()
        again = self.run_prepared()
        self.assertNotEqual("already_imported", again["outcome"])
        self.assertTrue((self.brain / RECORD).exists(), "the original is restored from the package's exact bytes")
        self.assertEqual("imported", again["outcome"], again.get("reason"))

    def test_replay_after_a_different_package_id_on_the_same_targets_is_refused(self):
        self.assertEqual("imported", self.run_prepared()["outcome"])
        manifest = self.manifest()
        manifest["package_id"] = "MI-jordan-ellis-intro-call-resend"
        self.save(manifest)
        refused = self.run_prepared()
        self.assertEqual("rejected", refused["outcome"])
        self.assertEqual("new_package_required", refused["retry"]["mode"])


class NeverThroughLunaTests(ProvenanceBase):
    def boom(self, *_a, **_k):
        self.fail("prepared ICM must never reach Luna")

    def run_interpreter(self):
        return si.run(SHA, brain_root=self.brain, root=self.root, allow_model_call=True, llm=self.boom)

    def test_a_prepared_import_in_the_ledger_blocks_reinterpretation(self):
        self.assertEqual("imported", self.run_prepared()["outcome"])
        out = self.run_interpreter()
        self.assertEqual("held", out["prepared_icm"]["status"])
        self.assertIn("Luna reinterpretation is refused", out["prepared_icm"]["reason"])
        self.assertNotIn("interpretation", out)

    def test_a_pre_provenance_import_without_saved_luna_state_is_also_held(self):
        self.assertEqual("imported", self.run_import()["outcome"])
        rows = self.ledger()
        for row in rows:
            row.pop("provenance", None)
        (self.root / mei.LEDGER).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        out = self.run_interpreter()
        self.assertEqual("held", out["prepared_icm"]["status"])

    def test_the_prepared_package_itself_is_replayed_deterministically(self):
        from tools import source_intake
        item = source_intake.SourceItem(Path("2026-10-14 Jordan Ellis intro call.txt"),
                                        (FIXTURE / "source/original.txt").read_bytes(), SHA, "text/plain", "Earlier", None)
        brain_memory.write_transaction(
            {RECORD: source_intake.render_record(item, "2026-10-15T00:00:00Z", source_path="drive:earlier"),
             "sources/intake/INDEX.md": source_intake.render_index(self.brain, [item]),
             "index/MEMORY_INDEX.md": (self.brain / "index/MEMORY_INDEX.md").read_text() + f"\n{source_intake.INTAKE_LINK}\n"},
            source="fixture", session_id="fixture")
        cache = self.root / si.PACKAGE_RELATIVE / "prepared" / self.pkg.name
        cache.parent.mkdir(parents=True)
        shutil.copytree(self.pkg, cache)
        manifest = json.loads((cache / "INGEST_MANIFEST.json").read_text(encoding="utf-8"))
        manifest["prepared_package_folder_id"] = si.CHATGPT_PREPARED_ICM_FOLDER_ID
        manifest["source"]["drive_id"] = "1AbCdEfGhIjK"
        (cache / "INGEST_MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
        with mock.patch.object(si, "drive_provenance", return_value={"drive_id": "1AbCdEfGhIjK"}), \
                mock.patch.object(mei, "_refresh", side_effect=lambda paths, *_a, **_k: covered(paths)):
            out = self.run_interpreter()
        self.assertEqual("reused", out["prepared_icm"]["status"], out)
        self.assertEqual("imported", out["prepared_icm"]["outcome"])
        self.assertEqual("prepared_externally", out["prepared_icm"]["stages"]["semantic_preparation"])
        self.assertEqual("prepared_icm", self.ledger()[-1]["provenance"]["processing_route"])


class CaptureProvenanceTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ)  # capture_batch's no_push() sets process-wide Git settings
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_drive_capture_node_records_identity_and_replays_without_a_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            brain, runtime = base / "brain", base / "runtime"
            brain.mkdir()
            (runtime / "context").mkdir(parents=True)
            for name in ("client_scope_registry.json", "client_scope_registry.schema.json"):
                shutil.copy(w.ROOT / "context" / name, runtime / "context" / name)
            subprocess.run(["git", "init", "-q", str(brain)], check=True)
            row = {"id": "canary", "name": "synthetic.txt", "mimeType": "text/plain", "parents": ["root"],
                   "trashed": False, "modifiedTime": "2026-11-13T00:00:00.000Z", "version": "4",
                   "headRevisionId": "rev-4", "size": "40"}
            raw = b"Synthetic evidence; no customer data.\n"
            digest = hashlib.sha256(raw).hexdigest()
            inputs = [(row, "synthetic.txt", raw, digest, None)]
            from tools import brain_git_closure
            with mock.patch.object(w.brain_memory, "write_transaction", side_effect=w.brain_memory._write_transaction_inner), \
                    mock.patch.object(w.exchange, "_refresh", return_value=({"status": "success"}, {"status": "success"})), \
                    mock.patch.object(brain_git_closure, "git"):
                w.capture_batch(inputs, brain=brain, root=runtime)
                node = w.capture_node(brain / "sources/intake/drive/canary.md")
                head = subprocess.check_output(["git", "-C", str(brain), "rev-parse", "HEAD"], text=True).strip()
                w.capture_batch(inputs, brain=brain, root=runtime)
                self.assertEqual(head, subprocess.check_output(["git", "-C", str(brain), "rev-parse", "HEAD"], text=True).strip())
                self.assertEqual(node, w.capture_node(brain / "sources/intake/drive/canary.md"))
        self.assertEqual("canary", node["external_id"])
        self.assertEqual("rev-4", node["external_version"])
        self.assertEqual("synthetic.txt", node["original_name"])
        self.assertEqual("text/plain", node["original_type"])
        self.assertEqual("google_drive", node["origin"])
        self.assertEqual("drive_watcher_capture", node["processing_route"])
        self.assertEqual(digest, node["content_sha256"])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), node["source_sha256"])  # .txt normalises to itself here
        utc(node["captured_at"].replace("Z", "+00:00"))


class OriginalBytesTests(ProvenanceBase):
    """A normalized derivative never replaces the original: exact bytes, SHA and size stay in the Drive node."""

    def make_capture(self, original):
        from tools import source_intake
        node = {"drive_id": "1AbCdEfGhIjK", "revision": "rev-7", "drive_version": "3", "active": True,
                "source_record": f"business_brain:{RECORD}", "original_sha256": hashlib.sha256(original).hexdigest(),
                "original_size_bytes": len(original), "conversion": "normalized"}
        path = self.brain / "sources/intake/drive/1AbCdEfGhIjK.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\nid: drive-source-1AbCdEfGhIjK\n---\n```json\n" + json.dumps(node) + "\n```\n"
                        + w.original_block(original), encoding="utf-8")
        return node

    def hint(self, original):
        return {"external_id": "1AbCdEfGhIjK", "external_version": "rev-7", "drive_version": "3",
                "origin": "google_drive", "original_location": "https://drive.google.com/file/d/1AbCdEfGhIjK/view",
                "original_sha256": hashlib.sha256(original).hexdigest(), "original_size_bytes": len(original),
                "conversion": "normalized"}

    def test_original_sha_size_version_and_conversion_reach_the_receipt(self):
        original = b"original without the final newline"
        self.make_capture(original)
        row = mei.process_v3(self.pkg, brain=self.brain, root=self.root, refresh=covered, route="luna_semantic",
                             origin_hint=self.hint(original))
        self.assertEqual("imported", row["outcome"], row.get("reason"))
        p = row["provenance"]
        self.assertEqual("rev-7", p["source"]["external_version"])
        self.assertEqual("3", p["source"]["drive_version"])
        self.assertEqual("normalized", p["source"]["conversion"])
        self.assertEqual(hashlib.sha256(original).hexdigest(), p["original"]["sha256"])
        self.assertEqual(len(original), p["original"]["size_bytes"])
        self.assertEqual(SHA, p["original"]["derivative_sha256"])
        self.assertEqual("normalized_text_for_interpretation", p["original"]["derivative"])
        self.assertNotIn("external_version", p["missing"])
        self.assertEqual({"sha256": hashlib.sha256(original).hexdigest(), "size_bytes": len(original), "verified": True},
                         row["stages"]["capture"]["capture_record"]["original"])

    def test_a_capture_without_original_bytes_is_reported_missing_not_assumed(self):
        hint = self.hint(b"x")
        for key in ("original_sha256", "original_size_bytes", "conversion"):
            hint.pop(key)
        row = mei.process_v3(self.pkg, brain=self.brain, root=self.root, refresh=covered, route="luna_semantic",
                             origin_hint=hint)
        self.assertIsNone(row["provenance"]["original"])
        self.assertIn("original_sha256", row["provenance"]["missing"])

    def test_stored_bytes_that_fail_their_sha_are_refused(self):
        original = b"original bytes"
        node = self.make_capture(original)
        path = self.brain / "sources/intake/drive/1AbCdEfGhIjK.md"
        path.write_text(path.read_text().replace(json.dumps(node)[:0] + node["original_sha256"], "0" * 64, 1), encoding="utf-8")
        row = mei.process_v3(self.pkg, brain=self.brain, root=self.root, refresh=covered, route="luna_semantic",
                             origin_hint=self.hint(original))
        self.assertEqual("rejected", row["outcome"])
        self.assertIn("fail SHA-256/size verification", row["reason"])

    def test_provenance_reconciles_once_without_a_commit_or_a_second_import(self):
        first = self.run_import(route="luna_semantic")
        self.assertEqual("imported", first["outcome"], first.get("reason"))
        original = b"original without the final newline"
        self.make_capture(original)
        head, lines = self.head(), len(self.ledger())
        hint = self.hint(original)
        kw = dict(brain=self.brain, root=self.root, refresh=covered, route="luna_semantic", origin_hint=hint)
        second = mei.process_v3(self.pkg, **kw)
        third = mei.process_v3(self.pkg, **kw)
        self.assertEqual("already_imported", second["outcome"])
        self.assertTrue(second["provenance_reconciled"])
        self.assertEqual(lines + 1, len(self.ledger()), "exactly one reconciling row")
        self.assertEqual(head, self.head(), "no Brain commit")
        self.assertNotIn("provenance_reconciled", third)
        self.assertEqual(second["provenance"], third["provenance"])
        self.assertEqual(first["lifecycle"]["import_committed_at"], third["lifecycle"]["import_committed_at"])
        self.assertEqual(1, len([r for r in self.ledger() if r.get("outcome") == "imported"]))

    def test_the_watcher_stores_the_exact_original_next_to_the_derivative_link(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(os.environ):
            base = Path(temp)
            brain, runtime = base / "brain", base / "runtime"
            brain.mkdir()
            (runtime / "context").mkdir(parents=True)
            for name in ("client_scope_registry.json", "client_scope_registry.schema.json"):
                shutil.copy(w.ROOT / "context" / name, runtime / "context" / name)
            subprocess.run(["git", "init", "-q", str(brain)], check=True)
            row = {"id": "canary", "name": "synthetic.txt", "mimeType": "text/plain", "parents": ["root"],
                   "trashed": False, "modifiedTime": "2026-11-13T00:00:00.000Z", "version": "4",
                   "headRevisionId": "rev-4", "size": "39"}
            raw = b"Synthetic evidence; no customer data."          # no final newline: normalize() appends one
            digest = hashlib.sha256(raw).hexdigest()
            derived = w.drive.normalize(raw, ".txt")
            self.assertNotEqual(raw, derived)
            from tools import brain_git_closure
            with mock.patch.object(w.brain_memory, "write_transaction", side_effect=w.brain_memory._write_transaction_inner), \
                    mock.patch.object(w.exchange, "_refresh", return_value=({"status": "success"}, {"status": "success"})), \
                    mock.patch.object(brain_git_closure, "git"):
                w.capture_batch([w.CaptureRow((row, "synthetic.txt", derived, digest, None), raw)], brain=brain, root=runtime)
                path = brain / "sources/intake/drive/canary.md"
                node = w.capture_node(path)
                self.assertEqual(raw, w.preserved_original(path))
                head = subprocess.check_output(["git", "-C", str(brain), "rev-parse", "HEAD"], text=True).strip()
                # A later metadata-only tick (no download) keeps the stored original and writes nothing.
                w.capture_batch([(row, "synthetic.txt", None, digest, node["source_record"])], brain=brain, root=runtime)
                self.assertEqual(head, subprocess.check_output(["git", "-C", str(brain), "rev-parse", "HEAD"], text=True).strip())
                self.assertEqual(raw, w.preserved_original(path))
        self.assertEqual(digest, node["original_sha256"])
        self.assertEqual(len(raw), node["original_size_bytes"])
        self.assertEqual(hashlib.sha256(derived).hexdigest(), node["derivative_sha256"])
        self.assertEqual(len(derived), node["derivative_size_bytes"])
        self.assertEqual("normalized", node["conversion"])
        self.assertEqual(node["derivative_sha256"], node["source_sha256"])


if __name__ == "__main__":
    unittest.main()
