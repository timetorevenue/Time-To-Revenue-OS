"""Explicit authenticated Factory release; uses existing authentication and authority only."""
from fastapi import APIRouter, Request
from pydantic import BaseModel
from aos_paths import aos_root, assert_authoritative_root
from aos_agents import registry
from aos_authority import authenticated_release_command
from remote_pc import require_operator

router = APIRouter(prefix="/api/agents")

class ReleaseRequest(BaseModel):
    agent: str
    state: str
    package_sha256: str

@router.post("/release")
def release(body: ReleaseRequest, request: Request):
    operator = require_operator(request, "Agent release")
    root = assert_authoritative_root(aos_root())
    command = authenticated_release_command(root, request.headers, body.agent, body.state, body.package_sha256)
    return registry.promote(body.agent, body.state, root, approved_by=operator["identity"], release_command=command)
