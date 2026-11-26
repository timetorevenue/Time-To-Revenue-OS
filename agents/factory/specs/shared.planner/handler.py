"""shared.planner: one call chooses the tasks, code assembles and bounds the plan.

The model sees the request, the records and the people David supplied, and
picks from a fixed list of tasks. It never writes plan syntax. Code keeps only
records and people that were supplied, checks that each task has what it needs,
and builds the plan from fixed fragments: every plan starts by opening its
records, every drafting task is followed by the citation check, and files are
written only after that check. The plan runner checks the plan again before it
runs. The planner runs nothing, reads nothing and proposes no effect.

Revisit: when a specialist is added, renamed or re-versioned, or the plan shape changes. · Last touched: 2026-11-15.
"""

import re
from datetime import date as _date

AGENTS = {
    "retrieve": "shared.brain_retrieve@0.1.0", "verify": "shared.cite_verify@0.1.0",
    "extract": "crm.meeting_extract@0.1.0", "followup": "crm.followup_draft@0.1.0",
    "post": "marketing.linkedin_post@0.1.0", "post_check": "marketing.post_check@0.1.0",
    "synth": "research.synthesise@0.1.0", "brief": "research.brief_render@0.1.0",
    "classify": "knowledge.source_classify@0.1.0", "filing": "knowledge.filing_proposal@0.1.0",
}
MODEL_STEP_TOKENS = {"extract": 12000, "followup": 12000, "post": 12000, "synth": 14000, "classify": 12000}
MAX_TASKS = 3
SECRET = re.compile(r"(?i)\b(?:sk|pk|ghp|gho|xox[abp])-[a-z0-9_-]{6,}"
                    r"|\b(?:password|passcode|api[ _-]?key|secret|token)\b[\s:=]+\S*\d\S*")
ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
WHAT_IT_PLANS = "a meeting follow-up, a LinkedIn post, a research brief or filing a source"


def _escape(text):
    return str(text).replace("<", "&lt;").replace(">", "&gt;")


def _one_line(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _norm(text):
    return _one_line(text).casefold()


def _valid_date(text):
    found = ISO_DATE.match(_one_line(text))
    if not found:
        return ""
    try:
        _date(int(found.group(1)), int(found.group(2)), int(found.group(3)))
    except ValueError:
        return ""
    return found.group(0)


def run(input, ctx):
    request = SECRET.sub("[removed]", _one_line(input["request"]))
    records = {}
    for row in input.get("records") or []:
        records.setdefault(row["ref"], _one_line(row.get("about")))
    people, person_keys = {}, {}
    for row in input.get("people") or []:
        people.setdefault(_norm(row["name"]), row)
    for key, row in people.items():
        # The prompt shows a person as "Name (Company)" and asks for the name copied exactly,
        # so accept that displayed form as well as the bare name.
        person_keys.setdefault(key, row)
        if _one_line(row.get("company")):
            person_keys.setdefault(_norm(f"{row['name']} ({row['company']})"), row)
    if not records:
        return {"status": "needs_input", "detail": "no record was supplied to plan from",
                "output": {"missing": [f"the record(s) to work from, as exact Business Brain references; this planner plans {WHAT_IT_PLANS}, each from named records"]}}

    prompt = (ctx.prompt
              .replace("{{request}}", _escape(request))
              .replace("{{records}}", "\n".join(f"- {_escape(ref)}" + (f" : {_escape(SECRET.sub('[removed]', about))}" if about else "")
                                                for ref, about in records.items()))
              .replace("{{people}}", "\n".join(f"- {_escape(_one_line(row['name']))}" + (f" ({_escape(_one_line(row['company']))})" if row.get("company") else "")
                                               for row in people.values()) or "- (none supplied)"))
    draft = ctx.model(prompt, schema=ctx.schemas["model_output.json"])

    reason = SECRET.sub("[removed]", _one_line(draft["reason"]))
    if draft["decision"] == "unsupported":
        return {"status": "refused", "failure_class": "request_unsupported",
                "detail": (reason or "the request asks for something this planner does not plan")[:300] + f" This planner plans {WHAT_IT_PLANS}."}
    if draft["decision"] == "needs_input":
        missing = [_one_line(item)[:300] for item in draft["missing"] if _one_line(item)][:6] or ["what the request needs that was not supplied"]
        return {"status": "needs_input", "detail": reason[:300] or "the request cannot be planned from what was supplied",
                "output": {"missing": missing}}

    hidden = sorted({_one_line(row[key]) for row in people.values() for key in ("name", "company") if _one_line(row.get(key))})
    known_entities = [{"name": _one_line(row["name"]), "type": "person"} for row in people.values()]
    known_entities += [{"name": company, "type": "client"} for company in sorted({_one_line(row.get("company")) for row in people.values()} - {""})]

    missing, tasks, used = [], [], {}
    phase = {1: [], 2: [], 4: []}      # after retrieve; after phase 1; after the citation check
    claim_sets, verify_needs, outputs, budget_tokens, model_steps = [], [], {}, 0, 0
    for row in draft["tasks"][:MAX_TASKS]:
        kind = row["task"]
        refs = [ref for ref in dict.fromkeys(row["records"]) if ref in records]
        used[kind] = used.get(kind, 0) + 1
        tag = "" if used[kind] == 1 else f"_{used[kind]}"
        if not refs:
            missing.append(f"{kind}: which supplied record to work from")
            continue
        if kind == "meeting_followup":
            person = person_keys.get(_norm(row["person"]))
            if person is None:
                missing.append("meeting_followup: who the follow-up goes to (a person David has matched for this request)")
                continue
            recipient = {key: _one_line(person[key]) for key in ("name", "company", "email") if _one_line(person.get(key))}
            title = _one_line(row["title"])[:160]
            title = title if len(title) >= 3 else f"Meeting with {recipient['name']}"
            extract_input = {"title": title, "evidence_refs": refs}
            if _valid_date(row["date"]):
                extract_input["date"] = _valid_date(row["date"])
            steps = [f"extract{tag}", f"followup{tag}"]
            phase[1].append({"id": steps[0], "agent": AGENTS["extract"], "needs": ["retrieve"], "input": extract_input})
            phase[2].append({"id": steps[1], "agent": AGENTS["followup"], "needs": [steps[0]],
                             "input": {"recipient": recipient, "meeting": f"$steps.{steps[0]}.output",
                                       "artifact_name": f"followup_email{tag}.md"}})
            claim_sets.append(f"$steps.{steps[1]}.output.points")
            verify_needs.append(steps[1])
            outputs[steps[1]] = f"$steps.{steps[1]}.output"
            budget_tokens += MODEL_STEP_TOKENS["extract"] + MODEL_STEP_TOKENS["followup"]
            model_steps += 2
        elif kind == "linkedin_post":
            topic = _one_line(row["topic"])[:300]
            topic = topic if len(topic) >= 8 else request[:300]
            post_input = {"topic": topic, "evidence_refs": refs, "confidential_terms": hidden[:12]}
            if _one_line(row["cta"]):
                post_input["cta"] = _one_line(row["cta"])[:200]
            steps = [f"post{tag}", f"post_check{tag}"]
            phase[1].append({"id": steps[0], "agent": AGENTS["post"], "needs": ["retrieve"], "input": post_input})
            phase[4].append({"id": steps[1], "agent": AGENTS["post_check"], "needs": [steps[0], "verify"],
                             "input": {"post": f"$steps.{steps[0]}.output.post", "hashtags": f"$steps.{steps[0]}.output.hashtags",
                                       "confidential_terms": hidden[:12], "artifact_name": f"linkedin_post{tag}.md"}})
            claim_sets.append(f"$steps.{steps[0]}.output.claims")
            verify_needs.append(steps[0])
            outputs[steps[0]] = f"$steps.{steps[0]}.output"
            outputs[steps[1]] = f"$steps.{steps[1]}.output"
            budget_tokens += MODEL_STEP_TOKENS["post"]
            model_steps += 1
        elif kind == "research_brief":
            question = _one_line(row["question"])[:400]
            if len(question) < 10:
                missing.append("research_brief: the question to answer")
                continue
            steps = [f"synth{tag}", f"brief{tag}"]
            phase[1].append({"id": steps[0], "agent": AGENTS["synth"], "needs": ["retrieve"],
                             "input": {"question": question, "evidence_refs": refs[:6]}})
            phase[4].append({"id": steps[1], "agent": AGENTS["brief"], "needs": [steps[0], "verify"],
                             "input": {"synthesis": f"$steps.{steps[0]}.output", "artifact_name": f"research_brief{tag}.md"}})
            claim_sets.append(f"$steps.{steps[0]}.output.findings")
            verify_needs.append(steps[0])
            outputs[steps[1]] = f"$steps.{steps[1]}.output"
            budget_tokens += MODEL_STEP_TOKENS["synth"]
            model_steps += 1
        else:  # file_source
            refs = refs[:1]
            steps = [f"classify{tag}", f"filing{tag}"]
            classify_input = {"evidence_refs": refs}
            if known_entities:
                classify_input["known_entities"] = known_entities[:20]
            phase[1].append({"id": steps[0], "agent": AGENTS["classify"], "needs": ["retrieve"], "input": classify_input})
            phase[2].append({"id": steps[1], "agent": AGENTS["filing"], "needs": [steps[0]],
                             "input": {"classification": f"$steps.{steps[0]}.output"}})
            outputs[steps[1]] = f"$steps.{steps[1]}.output"
            budget_tokens += MODEL_STEP_TOKENS["classify"]
            model_steps += 1
        tasks.append({"task": kind, "records": refs, "steps": steps})

    if missing:
        return {"status": "needs_input", "detail": "the plan lacks something a task needs", "output": {"missing": missing[:6]}}
    if not tasks:
        return {"status": "failed", "failure_class": "validation_failed", "detail": "the model chose to plan but named no task"}

    all_refs = list(dict.fromkeys(ref for task in tasks for ref in task["records"]))[:12]
    summary = reason if len(reason) >= 10 else f"Planned {len(tasks)} task(s): " + ", ".join(task["task"] for task in tasks)
    steps = [{"id": "retrieve", "agent": AGENTS["retrieve"], "input": {"evidence_refs": all_refs}}] + phase[1] + phase[2]
    if claim_sets:
        steps.append({"id": "verify", "agent": AGENTS["verify"], "needs": ["retrieve"] + verify_needs,
                      "input": {"claim_sets": claim_sets, "allowed_refs": "$steps.retrieve.output.refs"}})
        outputs["verify"] = "$steps.verify.output"
    steps += phase[4]
    plan = {"plan": ctx.agent, "version": ctx.version, "planned": True, "summary": summary[:400],
            "input_schema": {"type": "object"},
            "budget": {"tokens": budget_tokens, "model_calls": 2 * model_steps, "seconds": 60 + 90 * model_steps},
            "steps": steps, "output": outputs}
    return {"status": "ok", "output": {"decision": "plan", "summary": summary[:400], "tasks": tasks, "plan": plan, "plan_input": {}}}
