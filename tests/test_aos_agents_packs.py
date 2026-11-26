"""Tests for the M5 department packs: specialists, plans, the planner and the planned job.

Zero tokens. Every model call goes to a scripted stand-in and every run uses a
temporary root. The stand-ins are rule-based readers of the real prompts: they
prove that each golden set can be satisfied and that the handlers accept a
careful reply and reject a careless one. They say nothing about how the pinned
models behave; only a live Factory build does.

Revisit: when a pack's prompt, handler, golden set or a plan changes. · Last touched: 2026-11-14.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from aos_agents import evals, executor, factory, package as packages, plans  # noqa: E402
from aos_agents.journal import StepJournal  # noqa: E402
from aos_agents.runtime import RunEnv, run_agent  # noqa: E402

TOOL_AGENTS = ("shared.brain_retrieve", "marketing.post_check", "research.brief_render", "knowledge.filing_proposal")
MODEL_AGENTS = ("crm.followup_draft", "marketing.linkedin_post", "research.synthesise", "knowledge.source_classify",
                "shared.planner")
DEPARTMENT_PLANS = ("crm.meeting_followup", "marketing.content", "research.brief", "knowledge.ingest")
INJECTION = re.compile(r"NOTE TO|ATTENTION AI|AI assistant|Assistant:|SYSTEM:|PLANNER:|to the assistant|do not share", re.I)
MONTHS = {name: number for number, name in enumerate(
    ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"), start=1)}


def reply(payload: dict, kwargs: dict, calls: int) -> dict:
    return {"status": "ok", "ok": True, "json": payload, "model": kwargs["model"], "effort": kwargs["effort"],
            "invocation_id": f"scripted-{calls}", "latency_seconds": 1.0, "failure_class": "", "detail": "",
            "ledger": {"recorded": False},
            "usage": {"available": True, "input_tokens": 7600, "output_tokens": 200, "cached_input_tokens": 0}}


def sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", text) if part.strip()]


def honest(text: str) -> list[str]:
    """The sentences of a source up to the first one that addresses the reader or holds a login."""
    kept = []
    for sentence in sentences(text):
        if INJECTION.search(sentence) or re.search(r"password|token sk-", sentence, re.I):
            break
        kept.append(sentence)
    return kept


def sources_in(prompt: str) -> list[tuple[str, str]]:
    return re.findall(r'<source id="(\w+)" ref="[^"]*"[^>]*>\n(.*?)\n</source>', prompt, flags=re.S)


def field(prompt: str, label: str) -> str:
    found = re.search(rf"^{label}: (.*)$", prompt, flags=re.M)
    return found.group(1).strip() if found else ""


# -- scripted stand-ins, one per pinned agent ---------------------------------------

def followup_model(prompt: str, **_kwargs) -> dict:
    first_name = re.search(r'start with "Hi ([^,"]+),"', prompt).group(1)
    facts = re.findall(r"^([DANPQ]\d+) \[[^\]]+\] (.*)$", re.search(r"<facts>\n(.*?)\n</facts>", prompt, flags=re.S).group(1), flags=re.M)
    chosen = [(ident, text) for ident, text in facts if ident[0] in "DAN" and not INJECTION.search(text)]
    body = f"Hi {first_name},\n\nThank you for the conversation. Here is where we landed.\n\n" + "\n".join(f"- {text}" for _, text in chosen)
    return {"subject": "Follow-up from our meeting", "body": body, "used": [ident for ident, _ in chosen]}


def careless_followup_model(prompt: str, **kwargs) -> dict:
    draft = followup_model(prompt, **kwargs)
    draft["body"] += "\n\nThe scan costs $4,999 and results are guaranteed."
    return draft


def post_model(prompt: str, **_kwargs) -> dict:
    blocked = [word for term in field(prompt, "Never name").split(";") for word in term.split() if word[:1].isupper()]
    usable = []
    for ident, text in sources_in(prompt):
        for sentence in honest(text):
            if len(sentence.split()) >= 8 and not sentence.startswith("#"):
                usable.append((ident, sentence))
    if not usable:
        return {"sufficient": False, "missing": ["an observation or lesson to build on"], "hook": "", "body": "", "cta": "",
                "hashtags": [], "claims": []}

    everything = " ".join(text for _, text in sources_in(prompt))

    def anonymous(sentence: str) -> str:
        words = sentence.split()
        first = re.sub(r"'s$|[^\w-]", "", words[0])
        is_name = first in blocked or re.search(rf"[a-z,] {re.escape(first)}\b", everything) is not None
        out = ["They" if is_name else words[0]]
        for word in words[1:]:
            bare = re.sub(r"'s$|[^\w-]", "", word)
            out.append("the client" if (bare in blocked or (bare[:1].isupper() and bare not in ("I", "AI"))) else word)
        return " ".join(out)

    lines = [anonymous(sentence) for _, sentence in usable[:5]]
    quote_from = next(((ident, s) for ident, s in usable if not any(w[:1].isupper() for w in s.split()[1:])), usable[0])
    quote = " ".join(quote_from[1].split()[1:13]).rstrip(".,;:")
    cta = field(prompt, "Call to action")
    return {"sufficient": True, "missing": [], "hook": "Slow replies cost a small firm more than it thinks.",
            "body": "\n".join(lines), "cta": cta if not cta.startswith("not given") else "Where does your team make customers wait?",
            "hashtags": ["#SmallBusiness", "Operations"],
            "claims": [{"text": anonymous(quote_from[1]), "source_id": quote_from[0], "quote": quote}]}


def naming_post_model(prompt: str, **kwargs) -> dict:
    draft = post_model(prompt, **kwargs)
    draft["body"] += "\nThanks to Harbourline Physio for letting us share this."
    return draft


def stem_overlap(question: str, text: str) -> bool:
    stems = {word[:5] for word in re.findall(r"[a-z]{5,}", question.lower())}
    return bool(stems & {word[:5] for word in re.findall(r"[a-z]{5,}", text.lower())})


def synth_model(prompt: str, **_kwargs) -> dict:
    question = field(prompt, "Question")
    findings, hours = [], 0
    for ident, text in sources_in(prompt):
        kept = [s for s in honest(text) if len(s.split()) >= 5]
        if not kept or not stem_overlap(question, " ".join(kept)):
            continue
        hours += 1 if re.search(r"\bhour", " ".join(kept)) else 0
        for sentence in kept[:4]:
            findings.append({"claim": sentence, "source_id": ident, "quote": sentence.rstrip("."),
                             "confidence": "high" if re.search(r"\d", sentence) else "medium"})
    if not findings:
        return {"sufficient": False, "missing": ["a source that bears on the question"], "summary": "", "findings": [],
                "conflicts": [], "gaps": []}
    everything = " ".join(text for _, text in sources_in(prompt)).lower()
    absent = [word for word in re.findall(r"[a-z]+-[a-z]+", question.lower()) if word not in everything]
    return {"sufficient": True, "missing": [], "summary": " ".join(row["claim"] for row in findings[:3]),
            "findings": findings[:8],
            "conflicts": ["The sources differ on whether a callback inside one hour matters more than the same day."] if hours >= 2 else [],
            "gaps": [f"The sources do not cover {word}." for word in absent[:2]]}


def careless_synth_model(prompt: str, **kwargs) -> dict:
    draft = synth_model(prompt, **kwargs)
    draft["findings"].insert(0, {"claim": "Most firms lose 80 percent of missed calls.", "source_id": draft["findings"][0]["source_id"],
                                 "quote": "eighty percent of all missed calls are lost forever", "confidence": "high"})
    return draft


def classify_model(prompt: str, **_kwargs) -> dict:
    text = re.search(r"<source ref=\"[^\"]*\">\n(.*?)\n</source>", prompt, flags=re.S).group(1)
    body = " ".join(honest(text))
    if len(re.sub(r"[^a-z]", "", re.sub(r"(?m)^[\w ]+:\s*$", "", text).lower())) < 20:
        return {"sufficient": False, "missing": ["a source with content"], "kind": "other", "summary": "", "entities": [],
                "date": "", "sensitivity": "normal", "confidence": "low"}
    rules = (("financial_record", r"INVOICE|Total due"), ("proposal_or_contract", r"Proposal for|Fee:"),
             ("email", r"From: .*Subject:"), ("call_transcript", r"transcript"), ("procedure", r"How we run|Repeat for every"),
             ("research_or_article", r"article|study"), ("marketing_content", r"LinkedIn post"),
             ("personal_or_unrelated", r"Reminder to self"))
    kind = next((name for name, pattern in rules if re.search(pattern, body, flags=re.S)), "meeting_notes")
    known = re.findall(r"^- (.+) \((?:client|prospect|person|vendor|internal)\)$", prompt, flags=re.M)
    stated = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", body)
    worded = re.search(r"\b(\d{1,2}) (" + "|".join(MONTHS) + r") (\d{4})\b", body)
    date = stated.group(0) if stated else (f"{worded.group(3)}-{MONTHS[worded.group(2)]:02d}-{int(worded.group(1)):02d}" if worded else "")
    return {"sufficient": True, "missing": [], "kind": kind, "summary": f"A {kind.replace('_', ' ')} received for filing.",
            "entities": [name for name in known if name.lower() in body.lower()], "date": date,
            "sensitivity": "confidential" if re.search(r"dollars|Total due", body) else "normal", "confidence": "high"}


def gullible_classify_model(prompt: str, **kwargs) -> dict:
    draft = classify_model(prompt, **kwargs)
    draft.update(entities=draft["entities"] + ["Example Dealership Motors"], date="2031-02-30")
    return draft


def planner_model(prompt: str, **_kwargs) -> dict:
    request = re.search(r"<request>\n(.*?)\n</request>", prompt, flags=re.S).group(1)
    records = [(ref, about) for ref, about in re.findall(r"^- (\S+)(?: : (.*))?$", re.search(r"<records>\n(.*?)\n</records>", prompt, flags=re.S).group(1), flags=re.M)]
    people = [name for name in re.findall(r"^- ([^(\n]+?)(?: \(.*\))?$", re.search(r"<people>\n(.*?)\n</people>", prompt, flags=re.S).group(1), flags=re.M)
              if not name.startswith("(")]
    empty = {"records": [], "person": "", "title": "", "date": "", "topic": "", "question": "", "cta": ""}
    if re.search(r"\b(book|delete)\b", request, re.I):
        return {"decision": "unsupported", "reason": "The request asks for an action this planner does not plan.", "missing": [], "tasks": []}
    names = set(re.findall(r"\b[A-Z][a-z]{3,}\b", request)) | ({"Acme"} if re.search(r"\bacme\b", request, re.I) else set())
    meetings = [(ref, about) for ref, about in records if re.search(r"^Notes from", about or "") and names & set(re.findall(r"[A-Z][a-z]{3,}", about))]
    voice = [ref for ref, about in records if "voice" in (about or "")]
    tasks, missing = [], []
    for kind, pattern in (("meeting_followup", r"follow-?up|f/u"), ("linkedin_post", r"linkedin|\bLI post"),
                          ("research_brief", r"\bbrief\b|how fast"), ("file_source", r"\bfile\b")):
        if not re.search(pattern, request, re.I):
            continue
        if kind == "meeting_followup":
            person = next((name for name in people if name.split()[0] in request or names & set(re.findall(r"[A-Z][a-z]{3,}", name))), people[0] if len(people) == 1 else "")
            if not meetings or not person:
                missing.append("the meeting's notes and a listed person to write to")
                continue
            ref, about = meetings[0]
            worded = re.search(r"\b(\d{1,2}) (" + "|".join(MONTHS) + r") (\d{4})\b", about)
            tasks.append({**empty, "task": kind, "records": [ref], "person": person, "title": about[:80],
                          "date": f"{worded.group(3)}-{MONTHS[worded.group(2)]:02d}-{int(worded.group(1)):02d}" if worded else ""})
        elif kind == "linkedin_post":
            if not meetings:
                missing.append("the notes the post is based on")
                continue
            cta = re.search(r"End with: (.*)$", request)
            tasks.append({**empty, "task": kind, "records": [meetings[0][0]] + voice, "topic": "What a slow reply costs a small firm",
                          "cta": cta.group(1) if cta else ""})
        elif kind == "research_brief":
            found = [ref for ref, about in records if re.search(r"study|survey", about or "")]
            if not found:
                missing.append("the sources to answer from")
                continue
            tasks.append({**empty, "task": kind, "records": found, "question": "How fast must a small service firm return a missed call?"})
        else:
            found = [ref for ref, _ in records if "inbox/" in ref]
            tasks.append({**empty, "task": kind, "records": found[:1]})
    if missing or not tasks:
        return {"decision": "needs_input", "reason": "Something the request needs was not supplied.",
                "missing": missing or ["what should be done"], "tasks": []}
    return {"decision": "plan", "reason": f"Plan {len(tasks)} task(s) as drafts for Casey.", "missing": [], "tasks": tasks}


def overreaching_planner_model(prompt: str, **kwargs) -> dict:
    draft = planner_model(prompt, **kwargs)
    if draft["decision"] == "plan":
        draft["tasks"][0]["records"].append("business_brain:clients/example_dealership_motors/HOME.md")
        draft["tasks"][0]["person"] = "Jordan Blake"
    return draft


STAND_INS = {"crm.followup_draft": followup_model, "marketing.linkedin_post": post_model, "research.synthesise": synth_model,
             "knowledge.source_classify": classify_model, "shared.planner": planner_model}


class Scripted:
    """Routes each call to the stand-in for the agent making it, and counts calls per agent."""

    def __init__(self, **overrides):
        self.models = {**STAND_INS, **overrides}
        self.calls: list[str] = []

    def __call__(self, prompt, **kwargs):
        name = kwargs["agent"].split("@", 1)[0]
        self.calls.append(name)
        return reply(self.models[name](prompt, **kwargs), kwargs, len(self.calls))


def scoped_reader(scope, pointer):
    raise PermissionError(f"Business Brain pointer is explicitly unavailable: {pointer}")


def make_root() -> Path:
    root = Path(tempfile.mkdtemp(prefix="aos-packs-"))
    shutil.copytree(ROOT / "agents", root / "agents", ignore=shutil.ignore_patterns("__pycache__", "dist"))
    (root / "queue").mkdir()
    shutil.copy(ROOT / "queue" / "model_routes.json", root / "queue" / "model_routes.json")
    return root


class PackCase(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)


class ShippedPacksTest(PackCase):
    def test_every_package_passes_gate_one_and_every_plan_loads(self):
        for name in packages.installed(self.root):
            report = evals.check(name, self.root)
            self.assertTrue(report["pass"], (name, report["problems"]))
            self.assertEqual(report["eval_set"].get("short_of", {}) if name not in (
                "crm.fit_call_brief_render", "shared.cite_verify") else {}, {}, name)
        for name in DEPARTMENT_PLANS + ("crm.fit_call_prep",):
            self.assertTrue(plans.load_plan(name, self.root)["steps"], name)

    def test_tool_agents_are_silver_from_their_specs_at_zero_calls(self):
        for name in TOOL_AGENTS:
            evidence = evals.evaluate(name, self.root, write=False, scoped_reader=scoped_reader)
            self.assertEqual((evidence["level"], evidence["totals"]["model_calls"]), ("silver", 0), name)
            self.assertEqual([row["id"] for row in evidence["cases"] if not row["passed"]], [], name)
            spec = factory.load_spec(name, self.root)
            self.assertFalse(any(factory.hand_edits(spec, {}, self.root).values()), name)
            self.assertEqual(evals.current_level(name, ROOT)["level"], "silver", name)

    def test_each_department_has_a_plan_and_two_agents(self):
        installed = packages.installed(self.root)
        for department in ("research", "marketing", "crm", "knowledge"):
            self.assertGreaterEqual(len([name for name in installed if name.startswith(department + ".")]), 2, department)
            self.assertTrue(list((self.root / "agents" / "plans").glob(f"{department}.*.json")), department)

    def test_model_agent_packages_match_their_specs(self):
        for name in MODEL_AGENTS:
            spec = factory.load_spec(name, self.root)
            self.assertFalse(any(factory.hand_edits(spec, spec.candidates[0], self.root).values()), name)
            self.assertFalse(factory.check_eval_set(spec)["problems"], name)


class GoldenSetsAreSatisfiableTest(PackCase):
    """A careful stand-in passes every case of every pinned agent; nothing here is live evidence."""

    def evaluate(self, name, model):
        return evals.evaluate(name, self.root, live=True, modelport=model, write=False, scoped_reader=scoped_reader)

    def test_every_golden_set_passes_with_a_careful_model(self):
        for name in MODEL_AGENTS:
            evidence = self.evaluate(name, Scripted())
            failed = {row["id"]: row["failures"] for row in evidence["cases"] if not row["passed"]}
            self.assertEqual(failed, {}, name)
            self.assertTrue(all(evidence["gates"][gate]["pass"] for gate in ("1", "2", "3", "4")), name)
            self.assertEqual(evidence["gates"]["2"]["repair_rate"], 0.0, name)

    def test_cases_decided_in_code_make_no_call(self):
        for name in MODEL_AGENTS:
            cases = json.loads((self.root / "agents/packages" / name / "evals/cases.json").read_text(encoding="utf-8"))["cases"]
            free = [case["id"] for case in cases if case["expect"].get("max_model_calls") == 0]
            self.assertGreaterEqual(len(free), 2, name)
            evidence = evals.evaluate(name, self.root, live=True, modelport=Scripted(), write=False, only=free, scoped_reader=scoped_reader)
            self.assertEqual(evidence["totals"]["model_calls"], 0, name)
            self.assertEqual([row["id"] for row in evidence["cases"] if not row["passed"]], [], name)

    def test_a_careless_model_fails_the_gate(self):
        for name, model, gate in (("crm.followup_draft", careless_followup_model, "2"),
                                  ("marketing.linkedin_post", naming_post_model, "2"),
                                  ("shared.planner", overreaching_planner_model, "2")):
            evidence = self.evaluate(name, Scripted(**{name: model}))
            self.assertFalse(evidence["gates"][gate]["pass"], name)
            self.assertEqual(evidence["level"], "none", name)


class HandlerGuardsTest(PackCase):
    def env(self, model, **kwargs):
        return RunEnv(root=self.root, modelport=Scripted(**model), record=False, **kwargs)

    def case_input(self, name, case_id):
        cases = json.loads((self.root / "agents/packages" / name / "evals/cases.json").read_text(encoding="utf-8"))["cases"]
        return next(case["input"] for case in cases if case["id"] == case_id)

    def test_followup_rejects_an_invented_figure_and_proposes_only_a_draft(self):
        payload = self.case_input("crm.followup_draft", "core-physio-discovery")
        bad = run_agent("crm.followup_draft", payload, self.env({"crm.followup_draft": careless_followup_model}))
        self.assertEqual((bad["status"], bad["failure_class"]), ("failed", "validation_failed"))
        self.assertIn("4,999", bad["detail"])
        good = run_agent("crm.followup_draft", payload, self.env({}))
        self.assertEqual(good["status"], "ok", good["detail"])
        self.assertEqual([(e["effect"], e["action"], e["target"]) for e in good["proposed_effects"]],
                         [("gmail.draft", "GMAIL_CREATE_EMAIL_DRAFT", "dana@harbourlinephysio.example")])
        self.assertTrue(good["output"]["body"].endswith("\n\nBest,\nCasey"))
        self.assertTrue(all(point["sources"] == ["meeting-notes:physio"] for point in good["output"]["points"]))

    def test_followup_never_shows_the_model_a_fact_holding_a_credential(self):
        payload = self.case_input("crm.followup_draft", "adv-secret-canary")
        port = Scripted()
        seen = []
        port.models["crm.followup_draft"] = lambda prompt, **kw: (seen.append(prompt), followup_model(prompt, **kw))[1]
        result = run_agent("crm.followup_draft", payload, RunEnv(root=self.root, modelport=port, record=False))
        self.assertEqual(result["status"], "ok", result["detail"])
        self.assertNotIn("CANARY", seen[0])
        self.assertNotIn("CANARY", json.dumps(result["output"]))

    def test_post_fails_closed_on_a_named_client_and_an_unquoted_claim(self):
        payload = self.case_input("marketing.linkedin_post", "core-physio-callbacks")
        named = run_agent("marketing.linkedin_post", payload, self.env({"marketing.linkedin_post": naming_post_model}))
        self.assertEqual(named["failure_class"], "validation_failed")
        self.assertIn("confidential", named["detail"])

        def paraphrasing(prompt, **kwargs):
            draft = post_model(prompt, **kwargs)
            draft["claims"][0]["quote"] = "the clinic was losing a great many patients every single month"
            return draft

        loose = run_agent("marketing.linkedin_post", payload, self.env({"marketing.linkedin_post": paraphrasing}))
        self.assertEqual(loose["failure_class"], "validation_failed")
        self.assertIn("not backed", loose["detail"])

    def test_post_quote_matches_a_markdown_source_word_for_word(self):
        # Live acceptance AOS-2026-9997: the Brain note says "a short, bounded **single-team trial**".
        source = "## Key quotes\n> \"It should be a short, bounded **single-team trial**.\" (Source document, L404)"
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "linkedin_post_handler", ROOT / "agents/packages/marketing.linkedin_post/handler.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(module._quoted_in("It should be a short, bounded single-team trial", source))
        self.assertTrue(module._quoted_in("a short, bounded **single-team trial**", source))
        self.assertFalse(module._quoted_in("It should be a controlled, measurable two-workflow pilot", source))

    def test_post_without_a_given_cta_uses_the_closing_question(self):
        # Live Luna replies (M5 eval core-gym-own-data, live acceptance AOS-2026-9997) end the body on the
        # question the prompt asks for and leave cta empty when no call to action was given.
        payload = self.case_input("marketing.linkedin_post", "core-physio-callbacks")
        payload.pop("cta", None)

        def question_in_body(prompt, **kwargs):
            draft = post_model(prompt, **kwargs)
            draft["body"], draft["cta"] = draft["body"] + "\nWhere does your team make customers wait?", ""
            return draft

        result = run_agent("marketing.linkedin_post", payload, self.env({"marketing.linkedin_post": question_in_body}))
        self.assertEqual(result["status"], "ok", result.get("detail"))
        self.assertEqual(result["output"]["cta"], "Where does your team make customers wait?")
        self.assertFalse(result["output"]["body"].endswith("?"))

        def no_question(prompt, **kwargs):
            draft = question_in_body(prompt, **kwargs)
            draft["body"] = draft["body"].rsplit("\n", 1)[0]
            return draft

        still_missing = run_agent("marketing.linkedin_post", payload, self.env({"marketing.linkedin_post": no_question}))
        self.assertEqual(still_missing["failure_class"], "validation_failed")

    def test_synthesis_drops_what_it_cannot_find_and_counts_it(self):
        payload = self.case_input("research.synthesise", "core-callbacks-three-sources")
        result = run_agent("research.synthesise", payload, self.env({"research.synthesise": careless_synth_model}))
        self.assertEqual(result["status"], "ok", result["detail"])
        self.assertEqual(result["output"]["unverified_dropped"], 1)
        self.assertNotIn("80 percent", json.dumps(result["output"]["findings"]))

    def test_classify_keeps_only_listed_entities_real_dates_and_flags_credentials_in_code(self):
        payload = self.case_input("knowledge.source_classify", "core-meeting-notes")
        result = run_agent("knowledge.source_classify", payload, self.env({"knowledge.source_classify": gullible_classify_model}))
        self.assertEqual(result["status"], "ok", result["detail"])
        self.assertEqual(result["output"]["date"], "")
        self.assertNotIn("Example Dealership", json.dumps(result["output"]["entities"]))
        self.assertEqual(len(result["output"]["caveats"]), 2)
        secret = run_agent("knowledge.source_classify", self.case_input("knowledge.source_classify", "adv-secret-canary"), self.env({}))
        self.assertEqual(secret["output"]["sensitivity"], "contains_credentials")

    def test_planner_keeps_only_supplied_records_and_people(self):
        payload = self.case_input("shared.planner", "core-followup-and-post")
        result = run_agent("shared.planner", payload, self.env({"shared.planner": overreaching_planner_model}))
        self.assertEqual(result["status"], "needs_input")       # the invented recipient is not in the people list
        self.assertIn("who the follow-up goes to", result["output"]["missing"][0])
        good = run_agent("shared.planner", payload, self.env({}))
        self.assertEqual(good["status"], "ok", good["detail"])
        self.assertNotIn("example_dealership", json.dumps(good["output"]))
        self.assertEqual(plans.check_plan(good["output"]["plan"], self.root)["ref"], "shared.planner@0.1.0")
        self.assertEqual(good["proposed_effects"], [])

    def test_planner_accepts_the_person_as_the_prompt_shows_it(self):
        # Live Luna replies copy "Name (Company)" exactly from the people list (M5 build, 2026-11-15).
        def displayed_name_model(prompt, **kwargs):
            draft = planner_model(prompt, **kwargs)
            for task in draft["tasks"]:
                if task["person"] == "Dana Reyes":
                    task["person"] = "Dana Reyes (Acme Plumbing)"
            return draft

        payload = self.case_input("shared.planner", "core-followup-only")
        result = run_agent("shared.planner", payload, self.env({"shared.planner": displayed_name_model}))
        self.assertEqual(result["status"], "ok", result.get("output"))
        recipients = [step["input"]["recipient"] for step in result["output"]["plan"]["steps"] if "recipient" in step["input"]]
        self.assertEqual(recipients, [{"name": "Dana Reyes", "company": "Acme Plumbing", "email": "dana@acmeplumbing.example"}])


MEETING_REF = "business_brain:meetings/2026-11-13-acme-plumbing.md"
VOICE_REF = "business_brain:brand/voice.md"
BRAIN = {
    MEETING_REF: ("# Acme Plumbing - follow-up call, 13 November 2026\n\nAttendees: Dana Reyes, Casey.\n\n"
                  "Dana said quotes take four to six days because the owner writes each one at night after jobs. "
                  "She said two customers this month went with another firm before the quote arrived. "
                  "We agreed to pilot a same-day quote template on bathroom jobs. "
                  "Dana will share ten recent bathroom quotes by Wednesday. "
                  "Next step: review the first template together on 19 November.\n"),
    VOICE_REF: "# Brand voice\n\nPlain words. Short sentences. No hype.\n",
    "business_brain:research/missed-calls-study.md": "Missed-call study of small service firms. 62 percent of callers who reached voicemail did not call again.\n",
    "business_brain:inbox/2026-11-12-scan-proposal.md": "Starter Scan Proposal for Acme Plumbing. Prepared 12 November 2026. Fee: 1,800 dollars, payable on acceptance.\n",
}
REQUEST = {"request": "Draft a follow-up for yesterday's Acme meeting and a LinkedIn post about what we learned.",
           "records": [{"ref": MEETING_REF, "about": "Notes from the 13 November 2026 call with Dana Reyes of Acme Plumbing"},
                       {"ref": VOICE_REF, "about": "EXCO brand voice notes"}],
           "people": [{"name": "Dana Reyes", "company": "Acme Plumbing", "email": "dana@acmeplumbing.example"}]}


def brain_read(scope, pointer):
    if pointer not in BRAIN:
        raise FileNotFoundError(pointer)
    return {"content": BRAIN[pointer], "provenance": {"path": pointer, "client_scope": scope}}


def extract_model(prompt: str, **_kwargs) -> dict:
    ident, text = sources_in(prompt)[0]
    by = {key: next(s for s in sentences(text) if marker in s) for key, marker in
          (("pain", "four to six days"), ("decision", "We agreed"), ("action", "Dana will share"), ("next", "Next step"))}
    row = lambda sentence: {"text": sentence, "source_id": ident, "quote": sentence.rstrip(".")}
    return {"sufficient": True, "missing": [], "summary": "Follow-up call about slow quotes; a same-day template pilot was agreed.",
            "decisions": [row(by["decision"])], "pain_points": [row(by["pain"])], "open_questions": [],
            "action_items": [{"task": "Share ten recent bathroom quotes", "owner": "Dana", "due": "Wednesday",
                              "source_id": ident, "quote": by["action"].rstrip(".")}],
            "next_step": row(by["next"])}


class PlannedJobTest(PackCase):
    """The cross-department example: one queue item, one planner call, one plan, one worker result."""

    def item(self, payload=None, item_id="AOS-2026-0051"):
        return {"id": item_id, "owner": "operations",
                "executor": {"kind": "agent", "planner": "shared.planner@0.1.0", "input": payload or REQUEST}}

    def run_item(self, port, **kwargs):
        return executor.run_queue_item(self.root, self.item(**kwargs), modelport=port, brain_read=brain_read)

    def port(self, **overrides):
        return Scripted(**{"crm.meeting_extract": extract_model, **overrides})

    def test_followup_and_post_complete_as_one_job(self):
        port = self.port()
        worker = self.run_item(port)
        self.assertTrue(worker["success"], worker["output"])
        result = worker["agent_result"]
        self.assertEqual([(step["id"], step["status"]) for step in result["steps"]],
                         [("plan", "ok"), ("retrieve", "ok"), ("extract", "ok"), ("post", "ok"), ("followup", "ok"),
                          ("verify", "ok"), ("post_check", "ok")])
        self.assertEqual(sorted(port.calls), ["crm.followup_draft", "crm.meeting_extract", "marketing.linkedin_post", "shared.planner"])
        self.assertEqual(result["usage"]["model_calls"], 4)
        self.assertEqual(result["usage"]["total_tokens"], 4 * 7800)
        self.assertEqual(sorted(row["path"] for row in result["artifacts"]),
                         ["results/agents/AOS-2026-0051/followup_email.md", "results/agents/AOS-2026-0051/linkedin_post.md"])
        email = (self.root / "results/agents/AOS-2026-0051/followup_email.md").read_text(encoding="utf-8")
        self.assertIn("To: Dana Reyes <dana@acmeplumbing.example>", email)
        self.assertIn("19 November", email)
        post = (self.root / "results/agents/AOS-2026-0051/linkedin_post.md").read_text(encoding="utf-8")
        self.assertNotIn("Acme", post)
        self.assertNotIn("Dana", post)
        # rules/runtime_permission_policy.md lists email drafts as routine; sending stays explicit.
        self.assertEqual([(e["effect"], e["authorization"]["disposition"]) for e in result["proposed_effects"]], [("gmail.draft", "authorized")])
        self.assertEqual(result["output"]["verify"]["verified"], True)
        self.assertIn("1 effect(s) proposed, 0 held for review", worker["output"])
        self.assertIn("Token usage: input 30400, output 800", worker["output"])

    def test_a_recovered_job_replays_the_planner_and_every_finished_step(self):
        def broken_post(prompt, **kwargs):
            return {**post_model(prompt, **kwargs), "hashtags": []}

        first = self.port(**{"marketing.linkedin_post": broken_post})
        stopped = self.run_item(first)
        self.assertFalse(stopped["success"])
        self.assertIn("step post", stopped["output"])
        self.assertEqual(sorted(first.calls), ["crm.meeting_extract", "marketing.linkedin_post", "shared.planner"])

        second = self.port()
        worker = self.run_item(second)
        self.assertTrue(worker["success"], worker["output"])
        self.assertEqual(sorted(second.calls), ["crm.followup_draft", "marketing.linkedin_post"])   # no second plan, no second extract
        replayed = {step["id"] for step in worker["agent_result"]["steps"] if step["replayed"]}
        self.assertEqual(replayed, {"plan", "retrieve", "extract"})

        third = self.port()
        again = self.run_item(third)
        self.assertTrue(again["success"])
        self.assertEqual(third.calls, [])
        self.assertEqual(len(StepJournal(self.root, "AOS-2026-0051").records()), 7)

    def test_a_request_that_cannot_be_planned_stops_before_any_step(self):
        port = self.port()
        worker = self.run_item(port, payload={**REQUEST, "records": []})
        self.assertFalse(worker["success"])
        self.assertEqual(worker["failure_class"], "agent_needs_input")
        self.assertIn("needs input", worker["output"])
        self.assertEqual(port.calls, [])

        refused = self.run_item(port, payload={**REQUEST, "request": "Book me a flight to Calgary."}, item_id="AOS-2026-0052")
        self.assertEqual(refused["failure_class"], "agent_refused")
        self.assertIn("request_unsupported", refused["output"])
        self.assertEqual(port.calls, ["shared.planner"])

    def test_an_emitted_plan_is_checked_like_a_plan_file(self):
        good = run_agent("shared.planner", REQUEST, RunEnv(root=self.root, modelport=self.port(), record=False))["output"]["plan"]
        for change, reason in ((lambda p: p["steps"].append({"id": "extra", "agent": "crm.fit_call_prep@0.1.0", "input": {}}), "not callable"),
                               (lambda p: p["steps"].__setitem__(1, {**p["steps"][1], "needs": ["verify"]}), "not defined before"),
                               (lambda p: p.update(plan="../../etc"), "department.name"),
                               (lambda p: p["steps"].extend([{"id": f"s{n}", "agent": "shared.cite_verify", "input": {}} for n in range(8)]), "needs 1 to 12 steps")):
            plan = json.loads(json.dumps(good))
            change(plan)
            ran = plans.run_plan(plan, {}, RunEnv(root=self.root, modelport=self.port(), record=False))
            self.assertEqual((ran["status"], ran["failure_class"]), ("refused", "plan_rejected"), reason)
            self.assertIn(reason, ran["detail"])
        self.assertEqual(executor.run_queue_item(self.root, {"id": "AOS-2026-0053", "executor": {
            "kind": "agent", "planner": "crm.followup_draft", "input": {}}})["agent_result"]["failure_class"], "not_a_planner")

    def test_dispatch_is_the_whole_seam(self):
        self.assertIsNone(executor.dispatch(self.root, {"id": "AOS-2026-0001", "owner": "codex", "title": "an ordinary item"}))
        self.assertIsNone(executor.dispatch(self.root, {"id": "AOS-2026-0002", "executor": {"kind": "workbench"}}))
        payload = {"claim_sets": [[{"claim": "a", "sources": ["r1"]}]], "allowed_refs": ["r1"]}
        direct = executor.dispatch(self.root, {"id": "AOS-2026-0003", "executor": {"kind": "agent", "agent": "shared.cite_verify", "input": payload}})
        self.assertTrue(direct["success"])
        order = executor.dispatch(self.root, {"id": "FACTORY-2026-0009", "executor": {
            "kind": "factory", "action": "build_agent", "spec": "marketing.post_check"}}, modelport=self.port())
        self.assertTrue(order["success"], order["output"])
        self.assertIn("packed candidate at silver", order["output"])


class DepartmentPlansTest(PackCase):
    def run_plan(self, name, payload, **overrides):
        port = Scripted(**{"crm.meeting_extract": extract_model, **overrides})
        env = RunEnv(root=self.root, job="AOS-2026-0060", modelport=port, brain_read=brain_read, record=False)
        return plans.run_plan(name, payload, env), port

    def test_crm_meeting_followup(self):
        result, port = self.run_plan("crm.meeting_followup", {
            "title": "Follow-up call - Acme Plumbing", "date": "2026-11-13", "evidence_refs": [MEETING_REF],
            "recipient": {"name": "Dana Reyes", "email": "dana@acmeplumbing.example"}})
        self.assertEqual(result["status"], "ok", result["detail"])
        self.assertEqual(port.calls, ["crm.meeting_extract", "crm.followup_draft"])
        self.assertEqual(result["output"]["verification"]["claims_checked"], len(result["output"]["draft"]["points"]))
        self.assertEqual([row["path"] for row in result["artifacts"]], ["results/agents/AOS-2026-0060/followup_email.md"])

    def test_marketing_content(self):
        result, port = self.run_plan("marketing.content", {"topic": "Why slow quotes lose jobs", "evidence_refs": [MEETING_REF, VOICE_REF],
                                                           "confidential_terms": ["Acme Plumbing", "Dana Reyes"]})
        self.assertEqual(result["status"], "ok", result["detail"])
        self.assertEqual(port.calls, ["marketing.linkedin_post"])
        self.assertEqual(result["output"]["check"]["status"], "draft_for_approval")
        self.assertEqual(result["proposed_effects"], [])

    def test_marketing_content_stops_when_the_post_names_the_client(self):
        def naming(prompt, **kwargs):
            draft = post_model(prompt, **kwargs)
            draft["hook"] = "Acme Plumbing taught us something about quotes."
            return draft

        result, _ = self.run_plan("marketing.content", {"topic": "Why slow quotes lose jobs", "evidence_refs": [MEETING_REF],
                                                        "confidential_terms": ["Acme Plumbing"]}, **{"marketing.linkedin_post": naming})
        self.assertEqual((result["status"], result["not_run"]), ("failed", ["verify", "check"]))
        self.assertEqual(result["artifacts"], [])

    def test_research_brief(self):
        result, port = self.run_plan("research.brief", {"question": "How many callers who reach voicemail call again?",
                                                        "evidence_refs": ["business_brain:research/missed-calls-study.md"]})
        self.assertEqual(result["status"], "ok", result["detail"])
        self.assertEqual(port.calls, ["research.synthesise"])
        self.assertIn("[1] business_brain:research/missed-calls-study.md", result["output"]["brief"]["markdown"])

    def test_knowledge_ingest_proposes_and_holds(self):
        result, port = self.run_plan("knowledge.ingest", {
            "title": "Fast-start scan proposal", "evidence_refs": ["business_brain:inbox/2026-11-12-scan-proposal.md"],
            "known_entities": [{"name": "Acme Plumbing", "type": "client"}]})
        self.assertEqual(result["status"], "ok", result["detail"])
        self.assertEqual(port.calls, ["knowledge.source_classify"])
        self.assertEqual(result["output"]["proposal"]["target"],
                         "business_brain:clients/acme-plumbing/commercial/2026-11-12-fast-start-scan-proposal.md")
        self.assertEqual([(e["effect"], e["authorization"]["authorized"]) for e in result["proposed_effects"]], [("brain.write_proposal", False)])

    def test_a_missing_record_stops_every_plan_before_any_call(self):
        for name, payload in (("crm.meeting_followup", {"title": "Call", "evidence_refs": ["business_brain:meetings/none.md"], "recipient": {"name": "Dana Reyes"}}),
                              ("marketing.content", {"topic": "A topic to write on", "evidence_refs": ["business_brain:meetings/none.md"]}),
                              ("research.brief", {"question": "What do the sources say?", "evidence_refs": ["business_brain:meetings/none.md"]}),
                              ("knowledge.ingest", {"evidence_refs": ["business_brain:meetings/none.md"]})):
            result, port = self.run_plan(name, payload)
            self.assertEqual((result["status"], result["failure_class"], port.calls), ("failed", "context_unavailable", []), name)

    def test_a_field_applied_to_a_list_plucks_it(self):
        results = {"extract": {"output": {"sources": [{"id": "E1", "ref": "a"}, {"id": "E2", "ref": "b"}, {"id": "E3"}]}}}
        self.assertEqual(plans._resolve("$steps.extract.output.sources.ref", {}, results), ["a", "b"])
        self.assertIsNone(plans._resolve("$steps.extract.output.missing.ref", {}, results))


if __name__ == "__main__":
    unittest.main()
