"""Stage 3 capability descriptors for Stage 2's existing CapabilitySession."""
from __future__ import annotations

from pathlib import Path


def _string(maximum=300, **bounds):
    return {"type": "string", "minLength": 1, "maxLength": maximum, **bounds}


def _integer(maximum, minimum=0):
    return {"type": "integer", "minimum": minimum, "maximum": maximum}


def _object(required=(), **properties):
    return {"type": "object", "properties": properties, "required": list(required),
            "additionalProperties": False}


def capabilities(
    *, root, conversation_id, operator_scope, operator_text="", delivery_id="", verified_chat="",
    requested_at=None, connection_context=None, attachment_ids=(), read_handoff=None, on_job=None,
    artifact_scope=None, effect_key=None,
):
    """Register this source once per verified turn. All context args come from the host.

    `read_handoff(topic)` returns (reference, text) for the current conversation.
    `on_job(job_id)` submits admitted durable work to the existing Jobs runner.
    Attachment IDs must already have been authorized for this caller by ingress.
    `artifact_scope` binds the conversation to the verified Dashboard owner when applicable.
    `effect_key` is the stable server turn ID fallback for the Dashboard's empty delivery ID.
    """
    from aos_capabilities import Capability, CapabilityError
    from aos_agents.package import PackageError
    from aos_jobs import JobError
    from jsonschema import ValidationError
    from aos_david_services import Connections, Jobs

    effect_key = delivery_id or effect_key or ""

    root = Path(root)
    result = []

    def add(ident, description, scope, authorization, handler, input_schema, output_required=(), *, direct=False):
        def execute(value):
            try:
                return handler(value)
            except ValidationError:
                raise CapabilityError("Arguments did not match the underlying operation's schema.") from None
            except (PackageError, JobError, ValueError, OSError, TypeError, KeyError, AttributeError) as exc:
                from aos_david_engineering import EngineeringError
                from aos_david_files import FileError
                from aos_david_services import ServiceError
                from aos_david_web import WebError
                if isinstance(exc, (EngineeringError, FileError, ServiceError, WebError)):
                    detail = str(exc)
                else:
                    detail = "The scoped operation was unavailable or could not be verified."
                raise CapabilityError(detail) from None

        result.append(Capability(ident, description, scope, authorization,
                                 lambda: (input_schema(), {"type": "object", "required": list(output_required)}),
                                 execute, auto_inspect=direct, direct=direct))

    def operator(ident, description, handler, input_schema, output_required=(), *, direct=False):
        add(ident, description, "verified operator", "existing operator and resource scope",
            handler, input_schema, output_required, direct=direct)

    if operator_scope:
        from aos_david_engineering import Engineering
        from aos_david_files import Files, MAX_TEXT
        import aos_david_services as services
        import aos_david_web as web

        operator("web.search", "Search the public web for sources with citations and snippets.",
                 web.search, lambda: _object(("query",), query=_string(), limit=_integer(6, 1)), ("results",), direct=True)
        operator("web.retrieve", "Fetch public pages or articles and extract cited text and source metadata.",
                 web.retrieve, lambda: _object(("urls",), urls={
                     "type": "array", "minItems": 1, "maxItems": 3, "items": _string(1600)},
                     offset=_integer(768_000), length=_integer(1450, 1)), ("sources",), direct=True)

        files = Files(root, artifact_scope or conversation_id, attachment_ids, conversation_id=conversation_id)
        file_id = _string(32, pattern="^[a-f0-9]{32}$")
        offset = _integer(100_000)
        operator("files.list", "List and find authorized attachments and generated files in this conversation.",
                 files.list, lambda: _object(name=_string(120), offset=_integer(250), limit=_integer(10, 1)), ("files",))
        operator("files.read", "Read paginated text from an authorized attachment, PDF or artifact.",
                 files.read, lambda: _object(("file_id",), file_id=file_id, offset=offset, length=_integer(2000, 1)),
                 ("id", "content"))
        operator("files.search", "Search text lines in authorized conversation files.",
                 files.search, lambda: _object(("query",), query=_string(), offset=_integer(4000),
                     limit=_integer(8, 1), file_ids={"type": "array", "maxItems": 20, "items": file_id}), ("matches",))
        operator("files.write", "Create a Markdown, text, JSON, CSV or HTML artifact or edit it as a new version.",
                 files.write, lambda: _object(("filename", "content"), filename=_string(120),
                     content=_string(MAX_TEXT), replaces=file_id,
                     base_sha256=_string(64, pattern="^[a-f0-9]{64}$")), ("id", "sha256", "status"))
        operator("files.pdf", "Render a scoped Markdown file into a PDF using the TTROS report renderer.",
                 files.pdf, lambda: _object(("file_id", "filename"), file_id=file_id, filename=_string(120)), ("id", "sha256"))
        operator("files.deliver", "Open a generated artifact in Dashboard or send it to the verified operator Telegram DM.",
                 lambda args: files.delivery(args, conversation_id=conversation_id, verified_chat=verified_chat,
                                             delivery_id=delivery_id, operator_text=operator_text),
                 lambda: _object(("file_id",), file_id=file_id, channel={"enum": ["dashboard", "telegram"]}), ("id",))

        operator("brain.entity", "Find a Business Brain person or entity and retrieve linked records and relationships.",
                 services.brain_entity, lambda: _object(("entity",), entity=_string(), offset=_integer(100),
                     section={"enum": ["knowledge", "sources", "related", "timeline", "import_dated"]}), ("success",))
        workstream = _string(64, pattern="^[a-z0-9][a-z0-9-]{2,63}$")
        operator("brain.checkpoint", "Resume a named Shared Brain workstream checkpoint and its working context.",
                 lambda args: services.brain_checkpoint(args, operator_text),
                 lambda: _object(("workstream_id",), workstream_id=workstream, version=_integer(1_000_000, 1)),
                 ("checkpoint", "authority"))
        operator("brain.work_product", "Read the paginated working artifact linked to a named Shared Brain checkpoint.",
                 lambda args: services.brain_work_product(args, operator_text),
                 lambda: _object(("workstream_id",), workstream_id=workstream,
                                 version=_integer(1_000_000, 1), offset=_integer(20_000)),
                 ("reference", "content"))
        operator("brain.icm", "Read an exact Business Brain method, marketing voice or positioning, or icm:human-language-profile (Casey's writing preferences). Marketing draft sources are business_brain:memory/marketing_voice.md and business_brain:memory/positioning.md.",
                 services.brain_icm, lambda: _object(("reference",), reference=_string(300), offset=offset),
                 ("reference", "content"), direct=True)
        if read_handoff is not None:
            operator("brain.continuity", "Read prior work and saved handoff for a topic in this conversation.",
                     lambda args: services.brain_continuity(args, read_handoff),
                     lambda: _object(("topic",), topic=_string(80), offset=offset), ("reference", "content"))

        operator("crm.overview", "Read Company Home's scoped CRM pipeline and meeting follow-up priorities.",
                 lambda args: services.crm_overview(root, args), lambda: _object(limit=_integer(5, 1)),
                 ("client_scope", "counts"), direct=True)
        jobs = Jobs(root, conversation_id, verified_chat, effect_key, on_job, owner=artifact_scope)
        operator("jobs.list", "List active, finished or reminder jobs from this conversation with pagination.",
                 jobs.list, lambda: _object(state={"enum": ["all", "active", "finished", "reminders"]},
                                            offset=_integer(10_000), limit=_integer(8, 1)), ("jobs",))
        operator("jobs.evidence", "List or read a job's verified receipts and result artifacts with token usage.",
                 jobs.evidence, lambda: _object(("job_id",), job_id=_string(900), reference=_string(400), offset=offset),
                 ("id",))
        operator("jobs.schedule", "Schedule a named released workflow agent at the exact future ISO 8601 time in the request.",
                 lambda args: jobs.schedule(args, operator_text), lambda: _object(("agent", "run_at"),
                     agent=_string(160), run_at=_string(40), input={"type": "object"}), ("id", "status"))
        if verified_chat:
            operator("jobs.reminder", "Manage an explicitly requested reminder in the verified Telegram conversation.",
                     lambda args: jobs.reminder(args, operator_text, requested_at),
                     lambda: _object(("action",), action={"enum": ["create", "edit", "pause", "resume", "cancel"]}),
                     ("id", "status"))

        workbench = Engineering(root, conversation_id, verified_chat, effect_key, on_job)
        operator("engineering.projects", "List global registered engineering projects and available test or build workflows.",
                 workbench.projects, _object, ("projects",), direct=True)
        operator("forge.assess", "Assess whether an existing agent can cover a missing capability before building it.",
                 workbench.assess, lambda: _object(("name", "requirement"), name=_string(80), requirement=_string(600)),
                 ("recommendation", "components"), direct=True)
        operator("engineering.inspect", "Find and read approved tracked repository source at a verified Git commit.",
                 workbench.inspect, lambda: _object(("project",), project=_string(64), path=_string(420),
                                                   query=_string(100), offset=offset), ("project", "commit"))
        operator("engineering.preflight", "Inspect registered project Git checkout and base revision diagnostics.",
                 workbench.preflight, lambda: _object(("project",), project=_string(64)), ("project", "status"), direct=True)
        operator("engineering.workbench", "Start a requested change, test or build in a registered isolated website workbench.",
                 lambda args: workbench.workbench(args, operator_text),
                 lambda: _object(("project",), project=_string(64)), ("id", "status"))

    if connection_context is not None:
        from ttros_connections import OPERATIONS

        connections = Connections(connection_context, operator_text, effect_key)
        service = _string(60, pattern="^[a-z][a-z0-9_]+$")
        tool = _string(128, pattern="^[A-Z][A-Z0-9_]+$")

        def connection(ident, description, handler, schema, required):
            add(ident, description, "verified Connections member", "Connections Hub principal and runtime policy",
                handler, schema, required)

        connection("apps.availability", "Check connected Gmail, Calendar, Drive and other app availability.",
                   connections.availability, lambda: _object(service=service, offset=_integer(2000)), ("connections",))
        connection("apps.discover", "Discover relevant tools for one connected Gmail, Drive, Calendar or other app.",
                   connections.discover, lambda: _object(("service", "query"), service=service, query=_string(200)),
                   ("service", "tools"))
        connection("apps.schema", "Load the schema for one scoped connected app operation.",
                   connections.schema, lambda: _object(("service", "tool"), service=service, tool=tool),
                   ("tool", "input_schema"))
        connection("apps.execute", "Execute a scoped connected app read or routine write and return its provider receipt.",
                   connections.run, lambda: _object(("service", "tool"), service=service, tool=tool,
                                                    arguments={"type": "object"}), ("id", "provider_verified"))
        for name in ("create_event", "update_event"):
            connection(f"apps.calendar.{name}", f"{name.replace('_', ' ').capitalize()} for a private Google Calendar event.",
                       lambda args, name=name: connections.calendar(name, args),
                       lambda name=name: (OPERATIONS["googlecalendar"][name][1]), ("id", "provider_verified"))
    return result
