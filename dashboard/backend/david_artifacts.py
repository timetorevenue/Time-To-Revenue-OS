"""Authenticated download of a generated artifact using the existing upload bytes."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from aos_david_files import Files, UPLOAD_ID


def artifact_router(root, authorize_scope):
    """authorize_scope(request, conversation ID) returns the host's verified file scope."""
    router = APIRouter()

    @router.get("/api/david/artifacts/{file_id}")
    def download(file_id: str, conversation_id: str, request: Request):
        scope = authorize_scope(request, conversation_id)
        if not scope:
            raise HTTPException(status_code=403, detail="Artifact access requires an authenticated operator.")
        if not UPLOAD_ID.fullmatch(file_id):
            raise HTTPException(status_code=404, detail="Artifact is unavailable in this conversation.")
        try:
            row, data = Files(root() if callable(root) else root, scope).dashboard_artifact(file_id)
        except (OSError, ValueError):
            raise HTTPException(status_code=404, detail="Artifact is unavailable in this conversation.") from None
        return Response(data, media_type="application/octet-stream", headers={
            "Content-Disposition": f'attachment; filename="{row["filename"]}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        })

    return router
