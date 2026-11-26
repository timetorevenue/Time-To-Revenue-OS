"""Adapters from David to the existing Brain, Jobs and Connections owners."""
from __future__ import annotations

import json
import sqlite3

import aos_jobs
from aos_automation import visible
from aos_capabilities import Capability, CapabilityError

OBJECT = {"type": "object"}


def schema(fields=(), **optional):
    return {"type": "object", "additionalProperties": False, "required": list(fields),
            "properties": {**{field: {"type": "string", "minLength": 1, "maxLength": 500}
                              for field in fields}, **optional}}


def operator_tools(david, conversation_id, owner=""):
    def find(args):
        try:
            rows = david._lookup(args["query"])
        except (OSError, sqlite3.Error) as exc:
            raise CapabilityError("Business Brain retrieval is unavailable.") from exc
        return {"records": [{key: row[key] for key in
                            ("reference", "title", "snippet", "authority", "authority_label", "source_ref", "date", "status")
                            if key in row} for row in rows[:4]]}

    def read(args):
        import shared_brain_read

        offset = args.get("offset", 0)
        try:
            result = shared_brain_read.read(args["reference"], offset=offset)
        except (OSError, sqlite3.Error) as exc:
            raise CapabilityError("The indexed Business Brain record reader is unavailable.") from exc
        if not result.get("success"):
            raise CapabilityError("That Business Brain reference is unavailable in this scope.")
        content = result.get("content", "")
        chunk = content[:1400]
        next_offset = offset + len(chunk) if len(content) > len(chunk) else result.get("next_offset")
        return {"reference": result["reference"], "authority": result.get("authority"), "content": chunk,
                "next_offset": next_offset}


    def jobs(args):
        rows = [job for job in aos_jobs.list_jobs(david.root, conversation_id=conversation_id, limit=8)
                if visible(job, owner)]
        if ident := args.get("job_id"):
            try:
                job = aos_jobs.load(david.root, ident)
            except (aos_jobs.JobError, OSError, ValueError):
                raise CapabilityError("No verified job with that ID in this conversation.") from None
            if job.get("conversation_id") != conversation_id or not visible(job, owner):
                raise CapabilityError("No verified job with that ID in this conversation.")
            rows = [job]
        return {"jobs": [{key: row[key] for key in ("id", "status", "topic", "scheduled_at", "detail", "receipts",
                                                    "reminder", "last_delivery") if key in row} for row in rows]}

    def job_result(args):
        try:
            job = aos_jobs.load(david.root, args["job_id"])
        except (aos_jobs.JobError, OSError, ValueError):
            raise CapabilityError("No verified job with that ID in this conversation.") from None
        if (job.get("conversation_id") != conversation_id
                or (job.get("executor") or {}).get("capability_owner", "") != owner):
            raise CapabilityError("No verified job with that ID in this conversation.")
        result = job.get("result") or {}
        row = {"id": job["id"], "status": job["status"], "result": result.get("output"),
               "detail": job.get("detail"), "summary": result.get("summary"), "steps": result.get("steps"),
               "artifacts": job.get("artifacts"), "receipts": job.get("receipts"), "usage": job.get("usage")}
        if len(json.dumps(row, ensure_ascii=False).encode()) > 3300 and row["result"]:
            # Keep status and verified evidence links usable when output exceeds the inline budget.
            row["result"] = {"truncated": True, "excerpt": json.dumps(row["result"], ensure_ascii=False)[:1100]}
        return row

    return [
        Capability("brain.search", "Find Business Brain evidence for company history, people, projects and decisions.",
                   "operator", "scoped Brain reader", lambda: (schema(("query",)), OBJECT), find),
        Capability("brain.read", "Open an exact indexed Business Brain reference and read the source.",
                   "operator", "scoped Brain reader", lambda: (schema(("reference",), offset={"type": "integer", "minimum": 0, "maximum": 100000}), OBJECT), read,
                   auto_inspect=True, direct=True),
        Capability("jobs.result", "Read a completed specialist or workflow Job's structured result and receipts.",
                   "conversation", "existing Jobs conversation scope", lambda: (schema(("job_id",)), OBJECT),
                   job_result, auto_inspect=True, direct=True),
        Capability("jobs.status", "List this conversation's Jobs or check pending, running or finished background runs, workflows and reminders.",
                   "conversation", "existing Jobs conversation scope",
                   lambda: (schema(job_id={"type": "string", "minLength": 1}), OBJECT), jobs),
    ]


class ConnectionCapabilities:
    """Resolve just the current member's accounts through Connections."""
    def __init__(self, context):
        self.context = context
        self._accounts = None
        self.dynamic: list[Capability] = []

    def accounts(self):
        if self._accounts is None:
            hub, who = self.context
            groups = {}
            for row in hub.rows(who):
                if row["enabled"] and row["access"]["mode"] == "Automatic" and row["status"] == "ACTIVE":
                    groups.setdefault(row["service"], []).append(row)
            self._accounts = {}
            for service, rows in groups.items():
                preferred = [row for row in rows if row["config"].get("source") == "project"] or rows
                if len(preferred) == 1:
                    self._accounts[service] = preferred[0]
        return self._accounts

    def execute(self, service, operation, args, **kwargs):
        account = self.accounts().get(service)
        if account is None:
            raise CapabilityError(f"No unique active Automatic connection for {service}.")
        hub, who = self.context
        return hub.execute(who, account["id"], operation, args, **kwargs)

    def capabilities(self):
        from ttros_connections import OPERATIONS, READS, is_write

        def run(service, operation, args):
            from ttros_connections import ConnectionError
            try:
                return self.execute(service, operation, args)
            except ConnectionError:
                raise CapabilityError(f"{service} {operation} failed in Connections. Check the account or try different arguments.") from None

        capabilities = []
        for service, row in self.accounts().items():
            operations = {name: (lambda definition=definition: (definition[1], OBJECT))
                          for name, definition in OPERATIONS.get(service, {}).items() if not is_write(service, name)}
            for name in READS.get(service, {}):
                operations[name] = lambda: (schema(), OBJECT)
            for definition in (row["config"].get("operations", ()) if row["kind"] != "composio" else ()):
                operations[definition["name"]] = lambda definition=definition: (definition["input_schema"], OBJECT)
            for name, schemas in operations.items():
                label = {"gmail": "Gmail email inbox message mailbox",
                         "googlecalendar": "Google Calendar meetings appointments agenda schedule",
                         "googledrive": "Google Drive files documents"}.get(service, row["name"])
                capabilities.append(Capability(f"connections.{service}.{name}", f"{name} in {label}",
                                               "workspace member", "scoped Automatic Connections access", schemas,
                                               lambda args, service=service, name=name: run(service, name, args),
                                               auto_inspect=name in READS.get(service, {}),
                                               direct=name in READS.get(service, {})))
            if row["config"].get("source") == "project":
                capabilities.append(Capability(f"connections.{service}.tools", f"Find read tools in connected {row['name']}.",
                                               "workspace member", "scoped Automatic Connections access",
                                               lambda: (schema(("query",)), OBJECT),
                                               lambda args, service=service: self.discover_tools(service, args, run)))
        return capabilities

    def discover_tools(self, service, args, run):
        receipt = run(service, "tools", args)
        hub, who = self.context
        tools = [row for row in receipt.get("result", {}).get("tools", []) if row.get("mode") == "read"]
        # Dynamic tools become candidates after provider discovery. Their full schema
        # is loaded only on inspect, under the same principal and account.
        for row in tools:
            slug = row["tool"]
            def schemas(slug=slug):
                from ttros_connections import ConnectionError
                try:
                    definition = hub.tool_definition(who, slug)
                except ConnectionError:
                    raise CapabilityError("The provider schema could not be loaded.") from None
                if definition["toolkit"] != service or definition["mode"] != "read":
                    raise CapabilityError("The provider operation is unavailable as a scoped read.")
                return definition["input"], OBJECT

            def execute(arguments, slug=slug):
                from ttros_connections import ConnectionError
                try:
                    return self.execute(service, "tool", {"tool": slug, "arguments": arguments}, expected_mode="read")
                except ConnectionError:
                    raise CapabilityError(f"{service} read failed in Connections. Check the account or arguments.") from None

            self.dynamic.append(Capability(f"connections.{service}.{slug}", row["description"], "workspace member",
                                           "scoped Automatic Connections read", schemas, execute))
        return {"id": receipt.get("id"), "tools": [{"id": f"connections.{service}.{row['tool']}",
                                                    "description": row["description"]} for row in tools]}

    def dynamic_capabilities(self):
        return self.dynamic
