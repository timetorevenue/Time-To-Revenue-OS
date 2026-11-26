"""Synthetic sources and segment-cited mock interpretations for revision-3 ingestion tests (test data only).

ONE_LINE: a single-line talk transcript of more than 25,000 characters with curly quotes, dashes, accented and
non-Latin characters and an emoji, with seeded findings at the start, middle and end.
MULTILINE: a Markdown document with headings, saved with a UTF-8 BOM and CRLF line endings.

The mocks stand in for one Luna Medium output: they cite segments (``S<n>``) computed from the real segmenter, so
nothing here depends on segment numbering staying fixed. Fictional people and companies; never imported into the
live Brain.
"""
from __future__ import annotations

from tools import source_passages as sp

FILLER = (
    "The recording drifted into small talk about the drive from {city}, the weather, and a “quiet” corridor near "
    "the loading bay — nobody wrote anything down. ",
    "Someone mentioned a café near the {city} office where the croissants are très bons, then the conversation "
    "wandered on 🚚. ",
    "A side remark about the {city} trip, in the words «à bientôt», got a laugh and the room moved along without a "
    "decision. ",
    "There was a pause while the slides loaded, a joke about {city}, and an apology for the noise from the street "
    "outside – fairly long, as pauses go. ",
)
CITIES = ("Lisboa", "Kraków", "東京", "Αθήνα", "Montréal", "Reykjavík", "São Paulo", "Zürich")

# (slug, title, knowledge_type, importance, distinctive sentences)
SUBJECTS = [
    ("briefing-intro", "Who runs dispatch and owns the scheduling budget", "relationship_context", 2,
     "Marisol Ortega here, recording the Northwind field service briefing on 14 March 2026. Marisol Ortega leads "
     "dispatch operations at Northwind Services and owns the scheduling budget. "),
    ("service-call-tiers", "Three tiers of service calls", "framework", 3,
     "Every incoming service call is sorted into three tiers: emergency, scheduled and advisory. Emergency calls cover "
     "gas smells, flooding and no-heat situations. Scheduled calls are planned maintenance visits, and advisory calls "
     "are questions answered by phone without a visit. "),
    ("response-targets", "Response targets per tier", "requirement", 2,
     "An emergency call must have a technician on site within four hours. A scheduled call is booked within five "
     "business days, and an advisory call is answered the same day. "),
    ("gas-certification", "Who may take emergency gas calls", "requirement", 1,
     "Only technicians holding a gas certificate may take emergency gas calls. Certificates are renewed every two "
     "years through the regional trade board. "),
    ("van-parts-kit", "Standard van parts kit", "reference", 1,
     "Each van carries a standard parts kit restocked every Friday. Missing parts were the main reason a visit needed "
     "a second trip. "),
    ("departure-texts", "Departure text messages to customers", "reference", 1,
     "Customers receive a text message when the technician leaves the previous job. The text includes the "
     "technician's first name and a photo. "),
    ("dispatch-method", "The four-step dispatch method", "method", 3,
     "The dispatch method has four steps: triage the call, assign the nearest certified technician, confirm the "
     "arrival window by text message, and close the ticket with photos. "),
    ("missed-appointment-results", "Missed appointments after Northwind Dispatch", "attributed_claim", 3,
     "Since adopting Northwind Dispatch, missed appointments fell from 18% to 7% in six weeks. Repeat visits dropped "
     "by 22% over the same period. "),
    ("why-missed-appointments-fell", "Why missed appointments fell", "reference", 3,
     "Missed appointments dropped because customers received a confirmed arrival window by text, so they were home "
     "when the technician arrived. Shorter windows mattered more than faster driving. "),
    ("overtime-rules", "Emergency overtime rules", "decision_record", 2,
     "Overtime for emergency calls is paid at time and a half after eight hours. Marisol Ortega capped weekly "
     "overtime at ten hours per technician. "),
    ("technician-shadowing", "Shadowing for new technicians", "method", 1,
     "New technicians shadow a senior technician for three weeks before working alone. The shadowing checklist has "
     "twelve items, including safe gas isolation. "),
    ("crm-integration", "Northwind Dispatch and Vantage CRM", "architecture", 2,
     "Northwind Dispatch integrates with Vantage CRM to pull customer records before each visit. Ticket photos are "
     "stored in Vantage CRM for warranty claims. "),
    ("halifax-counterexample", "Where text confirmations did not help", "risk", 3,
     "The text confirmations did not help the Halifax depot, where technicians share vans, so arrival windows "
     "slipped anyway. Shared vans meant the parts kit was often incomplete. "),
    ("depot-next-steps", "Depot reporting and the Halifax van trial", "commitment", 2,
     "Marisol Ortega asked each depot lead to report their missed-appointment rate by 30 April 2026. The Halifax "
     "depot will trial assigned vans in May."),
]
FILLER_PER_SUBJECT = 1700


def one_line_text() -> str:
    parts, n = [], 0
    for _slug, _title, _kt, _imp, sentences in SUBJECTS[:-1]:
        parts.append(sentences)
        size = 0
        while size < FILLER_PER_SUBJECT:
            sentence = FILLER[n % len(FILLER)].format(city=CITIES[n % len(CITIES)])
            parts.append(sentence)
            size += len(sentence)
            n += 1
    parts.append(SUBJECTS[-1][4])
    return "".join(parts)


MULTILINE_LINES = [
    "# Harbour Plumbing Pricing Playbook",
    "",
    "Prepared by Devi Raman, 2 February 2026.",
    "",
    "## Service tiers",
    "Harbour Plumbing offers three service tiers: Essential, Plus and Premier.",
    "Essential covers one annual inspection; Plus adds priority booking; Premier adds a dedicated technician.",
    "",
    "## Retainer pricing",
    "The Premier retainer costs $4,500 per month with a 30% deposit.",
    "Clients in Québec pay the same price in CAD — “no surcharge” is the rule.",
    "",
    "## Onboarding process",
    "1. Survey the site.",
    "2. Quote within 48 hours.",
    "3. Collect the 30% deposit.",
    "4. Schedule the first visit with Fieldwise.",
    "",
    "## Tools",
    "Harbour Plumbing uses Fieldwise for scheduling and Ledgerly for invoicing.",
    "",
    "## Counterexample",
    "The retainer model failed for the Dartmouth office, which served mostly one-off emergency calls.",
]


def multiline_bytes() -> bytes:
    return ("﻿" + "\r\n".join(MULTILINE_LINES) + "\r\n").encode("utf-8")


def cite(text: str, *phrases: str) -> str:
    """``S<i>-S<j>`` for each phrase: the segments that contain it, from the real segmenter."""
    segs = sp.segments(text)
    out = []
    for phrase in phrases:
        start = text.index(phrase)
        end = start + len(phrase)
        hit = [i for i, (a, b) in enumerate(segs, 1) if a < end and start < b]
        out.append(f"S{hit[0]}" + (f"-S{hit[-1]}" if hit[-1] != hit[0] else ""))
    return ", ".join(out)


def whole(text: str) -> str:
    return f"S1-S{len(sp.segments(text))}"


def one_line_response(text: str) -> dict:
    """The mocked Luna output for ONE_LINE: more than 12 topics, long explanations, all four evidence outcomes."""
    by_slug = {s[0]: s for s in SUBJECTS}

    def subject_cite(slug: str) -> str:
        return cite(text, by_slug[slug][4].strip())

    def explain(slug: str) -> dict:
        return {"text": "Marisol Ortega says " + by_slug[slug][4].strip()[0].lower() + by_slug[slug][4].strip()[1:],
                "cite": subject_cite(slug)}

    topics = []
    for slug, title, ktype, importance, sentences in SUBJECTS:
        first = sentences.strip().split(". ")[0].rstrip(".") + "."
        topics.append({"title": title, "slug": slug, "knowledge_type": ktype, "importance": importance,
                       "cite": subject_cite(slug), "one_line": first, "questions": [f"What does the briefing say about {title.lower()}?"],
                       "entities": [], "explanation": [explain(slug)]})
    t = {x["slug"]: x for x in topics}
    t["briefing-intro"]["entities"] = ["Marisol Ortega", "Northwind Services"]
    t["service-call-tiers"]["quotes"] = [{"text": "Every incoming service call is sorted into three tiers: emergency, scheduled and advisory.",
                                          "speaker": "Marisol Ortega", "cite": subject_cite("service-call-tiers")}]
    t["dispatch-method"]["steps"] = [
        {"text": "Triage the call.", "cite": subject_cite("dispatch-method")},
        {"text": "Assign the nearest certified technician.", "cite": subject_cite("dispatch-method")},
        {"text": "Confirm the arrival window by text message.", "cite": subject_cite("dispatch-method")},
        {"text": "Close the ticket with photos.", "cite": subject_cite("dispatch-method")}]
    t["missed-appointment-results"].update(
        entities=["Northwind Dispatch"],
        outcomes=[{"text": "Missed appointments fell from 18% to 7% in six weeks after Northwind Dispatch was adopted.",
                   "cite": subject_cite("missed-appointment-results")}],
        claims=[{"speaker": "Marisol Ortega", "text": "Missed appointments fell to 4% once Northwind Dispatch was adopted.",
                 "cite": subject_cite("missed-appointment-results")}])                      # wrong figure -> failed
    t["why-missed-appointments-fell"]["principles"] = [
        {"text": "Customer trust depends on reliability more than raw speed.", "cite": subject_cite("why-missed-appointments-fell")}]  # -> cited
    t["overtime-rules"]["decisions"] = [{"text": "Weekly overtime is capped at ten hours per technician.", "who": "Marisol Ortega",
                                         "cite": subject_cite("overtime-rules")}]
    t["technician-shadowing"]["actions"] = [{"text": "Shadow a senior technician for three weeks.", "owner": "New technicians",
                                             "cite": "S9999"}]                                # does not resolve -> failed
    t["crm-integration"]["entities"] = ["Northwind Dispatch", "Vantage CRM"]
    t["halifax-counterexample"].update(
        entities=["Halifax depot"],
        quotes=[{"text": "where technicians share vans, so arrival windows slipped anyway", "speaker": "Marisol Ortega",
                 "cite": subject_cite("halifax-counterexample")},
                {"text": "the Halifax depot never used text confirmations", "speaker": "Marisol Ortega",
                 "cite": subject_cite("halifax-counterexample")}])                             # not in the span -> failed
    t["depot-next-steps"].update(entities=["Marisol Ortega", "Halifax depot"],
                                 open_loops=[{"text": "Each depot lead reports their missed-appointment rate by 30 April 2026.",
                                              "cite": subject_cite("depot-next-steps")}])
    # A long note citing non-adjacent segments across the whole source; its answer about Halifax is its last paragraph.
    topics.append({"title": "Field service playbook end to end", "slug": "field-service-playbook", "knowledge_type": "reference",
                   "importance": 1, "cite": ", ".join(subject_cite(s[0]) for s in SUBJECTS),
                   "one_line": "Marisol Ortega walks through tiers, targets, dispatch, results and limits.",
                   "questions": ["How does Northwind run field service dispatch end to end?"], "entities": [],
                   "explanation": [explain(s[0]) for s in SUBJECTS]})
    # A whole-source overview: its whole-source reference must never count as coverage.
    topics.append({"title": "Briefing overview", "slug": "briefing-overview", "knowledge_type": "reference", "importance": 1,
                   "cite": whole(text), "one_line": "", "questions": [], "entities": [], "explanation": []})
    intro = by_slug["briefing-intro"][4]
    return {
        "document_title": "Northwind Field Service Briefing",
        "description": "Marisol Ortega's recorded briefing on how Northwind Services triages, dispatches and measures field service calls.",
        "source_class": "reference", "document_kind": "talk transcript", "participants": ["Marisol Ortega"],
        "authors": [{"name": "Marisol Ortega", "role": "presenter", "cite": cite(text, "Marisol Ortega here"),
                     "quote": "Marisol Ortega here, recording the Northwind field service briefing"}],
        "source_date": {"text": "14 March 2026", "iso": "2026-03-14", "cite": cite(text, "14 March 2026"),
                        "quote": "recording the Northwind field service briefing on 14 March 2026"},
        "entities": [
            {"name": "Marisol Ortega", "kind": "person", "cite": cite(text, "Marisol Ortega here"), "quote": "Marisol Ortega here"},
            {"name": "Northwind Services", "kind": "organization", "cite": cite(text, "at Northwind Services"),
             "quote": "dispatch operations at Northwind Services"},
            {"name": "Northwind Dispatch", "kind": "product", "cite": cite(text, "Since adopting Northwind Dispatch"),
             "quote": "Since adopting Northwind Dispatch"},
            {"name": "Vantage CRM", "kind": "product", "cite": cite(text, "integrates with Vantage CRM"),
             "quote": "integrates with Vantage CRM"},
            {"name": "Halifax depot", "kind": "organization", "cite": cite(text, "help the Halifax depot"),
             "quote": "did not help the Halifax depot"},
            {"name": "Phantom Robotics", "kind": "organization", "cite": cite(text, "help the Halifax depot"),
             "quote": "Phantom Robotics builds vans"},                                         # unsupported -> dropped
        ],
        "relationships": [
            {"subject": "Northwind Dispatch", "predicate": "integrates_with", "object": "Vantage CRM",
             "cite": subject_cite("crm-integration"),
             "quote": "Northwind Dispatch integrates with Vantage CRM to pull customer records before each visit"},  # supported
            {"subject": "Marisol Ortega", "predicate": "recommends", "object": "Vantage CRM",
             "cite": cite(text, intro.strip()),
             "quote": "Marisol Ortega leads dispatch operations at Northwind Services"},       # exact quote, object absent -> cited
            {"subject": "Marisol Ortega", "predicate": "related_to", "object": "Northwind Services",
             "cite": cite(text, intro.strip()),
             "quote": "Marisol Ortega leads dispatch operations at Northwind Services"},       # supported but untyped
            {"subject": "Halifax depot", "predicate": "uses", "object": "Northwind Dispatch",
             "cite": subject_cite("halifax-counterexample"),
             "quote": "The Halifax depot uses Northwind Dispatch daily"},                      # not in the span -> failed
        ],
        "topics": topics,
        "uncertainty": ["The briefing does not say how the 18% baseline was measured."],
    }


def multiline_response(text: str) -> dict:
    """The mocked Luna output for MULTILINE (BOM + CRLF)."""
    return {
        "document_title": "Harbour Plumbing Pricing Playbook", "description": "Devi Raman's pricing and onboarding playbook for Harbour Plumbing.",
        "source_class": "reference", "document_kind": "playbook", "participants": ["Devi Raman"],
        "authors": [{"name": "Devi Raman", "role": "author", "cite": cite(text, "Prepared by Devi Raman"),
                     "quote": "Prepared by Devi Raman"}],
        "source_date": {"text": "2 February 2026", "iso": "2026-02-02", "cite": cite(text, "2 February 2026"),
                        "quote": "Prepared by Devi Raman, 2 February 2026."},
        "entities": [
            {"name": "Harbour Plumbing", "kind": "organization", "cite": cite(text, "Harbour Plumbing offers"), "quote": "Harbour Plumbing offers"},
            {"name": "Fieldwise", "kind": "product", "cite": cite(text, "uses Fieldwise"), "quote": "uses Fieldwise for scheduling"},
            {"name": "Ledgerly", "kind": "product", "cite": cite(text, "Ledgerly for invoicing"), "quote": "Ledgerly for invoicing"},
            {"name": "Dartmouth office", "kind": "organization", "cite": cite(text, "the Dartmouth office"), "quote": "the Dartmouth office"},
        ],
        "relationships": [
            {"subject": "Harbour Plumbing", "predicate": "uses", "object": "Fieldwise", "cite": cite(text, "Harbour Plumbing uses Fieldwise"),
             "quote": "Harbour Plumbing uses Fieldwise for scheduling"},
            {"subject": "Dartmouth office", "predicate": "uses", "object": "Ledgerly", "cite": cite(text, "the Dartmouth office"),
             "quote": "The retainer model failed for the Dartmouth office"},
        ],
        "topics": [
            {"title": "Service tiers", "slug": "service-tiers", "knowledge_type": "framework", "importance": 3,
             "cite": cite(text, "Harbour Plumbing offers three", "Essential covers"), "entities": ["Harbour Plumbing"],
             "one_line": "Harbour Plumbing offers three service tiers: Essential, Plus and Premier.",
             "explanation": [{"text": "Devi Raman sets out three tiers: Essential covers one annual inspection, Plus adds "
                                      "priority booking and Premier adds a dedicated technician.",
                              "cite": cite(text, "Harbour Plumbing offers three", "Essential covers")}]},
            {"title": "Premier retainer pricing", "slug": "retainer-pricing", "knowledge_type": "reference", "importance": 3,
             "cite": cite(text, "The Premier retainer", "Clients in Québec"),
             "one_line": "The Premier retainer costs $4,500 per month with a 30% deposit.",
             "claims": [{"speaker": "Devi Raman", "text": "The Premier retainer costs $5,500 per month.", "cite": cite(text, "The Premier retainer")}],
             "quotes": [{"text": "“no surcharge” is the rule", "speaker": "Devi Raman", "cite": cite(text, "Clients in Québec")}]},
            {"title": "Onboarding process", "slug": "onboarding-process", "knowledge_type": "method", "importance": 2,
             "cite": cite(text, "1. Survey", "4. Schedule"),
             "steps": [{"text": "Survey the site.", "cite": cite(text, "1. Survey")},
                       {"text": "Quote within 48 hours.", "cite": cite(text, "2. Quote")},
                       {"text": "Collect the 30% deposit.", "cite": cite(text, "3. Collect")},
                       {"text": "Schedule the first visit with Fieldwise.", "cite": cite(text, "4. Schedule")}]},
            {"title": "Tools in use", "slug": "tools-in-use", "knowledge_type": "architecture", "importance": 2,
             "cite": cite(text, "Harbour Plumbing uses Fieldwise"), "entities": ["Fieldwise", "Ledgerly"],
             "explanation": [{"text": "Harbour Plumbing uses Fieldwise for scheduling and Ledgerly for invoicing.",
                              "cite": cite(text, "Harbour Plumbing uses Fieldwise")}]},
            {"title": "Where the retainer model failed", "slug": "retainer-counterexample", "knowledge_type": "risk", "importance": 2,
             "cite": cite(text, "The retainer model failed"), "entities": ["Dartmouth office"],
             "explanation": [{"text": "Devi Raman notes the retainer model failed for the Dartmouth office, which served mostly "
                                      "one-off emergency calls.", "cite": cite(text, "The retainer model failed")}]},
        ],
        "uncertainty": [],
    }
