"""Typed MCP bindings for the existing David and Jobs boundary."""
from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any, Literal

from mcp.types import ToolAnnotations
from pydantic import Field

import aos_jobs
from aos_capabilities import CapabilityError
from aos_external_jobs import ExternalJobs
from aos_reminders import ReminderError

Workstream = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{2,63}$")]
RequestId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{8,100}$")]
CapabilityId = Annotated[str, Field(min_length=1, max_length=180)]
When = Annotated[str, Field(min_length=1, max_length=200)]
Revision = Annotated[int, Field(ge=0)]
_log = logging.getLogger("ttros.shared_brain.jobs")
READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
ADMIT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
MANAGE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)


def register(server, get_david, get_stamp):
    def run(operation, workstream, args):
        stamp = get_stamp()
        if stamp is None:
            return {"success": False, "error": "Unauthorized."}
        david = get_david()
        if david is None:
            return {"success": False, "error": "David is unavailable."}
        try:
            jobs = ExternalJobs(david, stamp["authenticated_identity"], stamp["surface"], workstream)
            result = getattr(jobs, operation)(*args)
        except (aos_jobs.JobError, CapabilityError, ReminderError) as exc:
            return {"success": False, "error": str(exc)}
        except Exception:
            _log.exception("MCP Jobs operation failed operation=%s", operation)
            return {"success": False, "error": "Jobs operation unavailable."}
        _log.info("mcp_jobs operation=%s identity=%s surface=%s workstream=%s job_id=%s",
                  operation, jobs.identity, jobs.surface, workstream, result.get("job_id"))
        return result

    async def call(operation, workstream, *args):
        return await asyncio.to_thread(run, operation, workstream, args)

    @server.tool(annotations=READ)
    async def ttros_discover(query: Annotated[str, Field(max_length=200)] = "",
                             capability: CapabilityId | None = None) -> dict[str, Any]:
        """Find relevant released specialists, workflows or David Jobs. Use an exact capability to inspect its schemas and limits."""
        return await call("discover", None, query, capability)

    @server.tool(annotations=ADMIT)
    async def ttros_submit_job(workstream_id: Workstream, request_id: RequestId, capability: CapabilityId,
                               inputs: dict[str, Any]) -> dict[str, Any]:
        """Admit an operator-requested Job. Reuse request_id on retry. Use jobs.david for scoped planning or select an exact released target."""
        return await call("submit", workstream_id, request_id, capability, inputs)

    @server.tool(annotations=READ)
    async def ttros_jobs(workstream_id: Workstream, job_id: str | None = None, include_result: bool = False,
                         limit: Annotated[int, Field(ge=1, le=50)] = 10) -> dict[str, Any]:
        """List workstream Jobs or read one status. Set include_result for settled findings, receipt and artifact references."""
        if job_id is not None:
            return await call("get", workstream_id, job_id, include_result)
        return await call("list", workstream_id, limit)

    @server.tool(annotations=READ)
    async def ttros_job_artifact(workstream_id: Workstream, job_id: str, reference: str,
                                 offset: Annotated[int, Field(ge=0)] = 0) -> dict[str, Any]:
        """Read verified text for an exact artifact returned by ttros_jobs. Use next_offset to continue."""
        return await call("artifact", workstream_id, job_id, reference, offset)

    @server.tool(annotations=ADMIT)
    async def ttros_schedule_job(workstream_id: Workstream, request_id: RequestId, capability: CapabilityId,
                                 inputs: dict[str, Any], when: When, expires_at: When | None = None) -> dict[str, Any]:
        """Schedule an operator-approved, fixed Forge target. Supply an offset-bearing ISO instant or an explicit recurring time and timezone."""
        return await call("schedule", workstream_id, request_id, capability, inputs, when, expires_at)

    @server.tool(annotations=MANAGE)
    async def ttros_manage_job(workstream_id: Workstream, job_id: str,
                               operation: Literal["cancel", "pause", "resume", "edit", "answer"],
                               revision: Revision | None = None, when: When | None = None,
                               answer: Annotated[str, Field(min_length=1, max_length=400)] | None = None,
                               evidence_refs: Annotated[list[Annotated[str, Field(max_length=300)]],
                                                        Field(max_length=8)] | None = None) -> dict[str, Any]:
        """Cancel queued or waiting work. Manage schedules with their current revision. Supply the current round when answering a waiting David Job."""
        return await call("manage", workstream_id, job_id, operation, revision, when, answer, evidence_refs)
