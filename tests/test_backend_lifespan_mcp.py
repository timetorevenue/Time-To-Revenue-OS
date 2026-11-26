"""The backend's lifespan starts what add_event_handler registers: the MCP session managers.

Regression for the 2026-11-15 cutover rollback: main.app uses a custom lifespan, which
replaces Starlette's default one, so startup handlers added by shared_brain_http.install
(and linkedin_profile_mcp.install) never ran and /api/brain/<surface>/ answered HTTP 500.
The existing MCP tests built a bare FastAPI() and could not see it.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "tools", ROOT / "dashboard" / "backend"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import main  # noqa: E402
import shared_brain_http  # noqa: E402

INITIALIZE = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                         "clientInfo": {"name": "lifespan-test", "version": "0"}}}
HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json",
           "cf-access-jwt-assertion": "fixture"}


def _no_david():
    return patch.multiple(main, start_david=lambda **_: {}, stop_david=lambda: None)


def test_main_app_runs_registered_startup_and_shutdown_handlers():
    calls: list[str] = []

    async def started():
        calls.append("startup")

    async def stopped():
        calls.append("shutdown")

    main.app.router.on_startup.append(started)
    main.app.router.on_shutdown.append(stopped)
    try:
        with _no_david(), TestClient(main.app):
            assert calls == ["startup"]
        assert calls == ["startup", "shutdown"]
    finally:
        main.app.router.on_startup.remove(started)
        main.app.router.on_shutdown.remove(stopped)


def test_shared_brain_mcp_answers_initialize_under_the_backend_lifespan():
    app = FastAPI(lifespan=main.lifespan)
    shared_brain_http.install(app)
    with _no_david(), patch.object(shared_brain_http, "verify_access_jwt", return_value="account-owner@example.invalid"), \
            TestClient(app, base_url=f"https://{shared_brain_http.BRAIN_HOST}") as client:
        for surface in ("claude", "chatgpt"):
            response = client.post(f"/api/brain/{surface}/", json=INITIALIZE, headers=HEADERS)
            assert response.status_code == 200, (surface, response.status_code, response.text[:300])
            assert response.json()["result"]["serverInfo"]["name"]
