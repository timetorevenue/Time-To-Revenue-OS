"""Import name kept for callers that still use ``tools.aos_orchestration`` (morning brief, Brain closure).

The queue orchestration spine that lived here was removed in the 2026-11-15 clean break.
This module re-exports the one policy implementation in aos_authority. It has no logic of its
own. Composio execution no longer goes through it: every app call uses the Connections Hub.

Revisit: when those callers import aos_authority directly. · Last touched: 2026-11-24.
"""

import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:  # imported as tools.aos_orchestration
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from aos_authority import (  # noqa: E402,F401
    NOTIFICATION_KIND_ENV,
    PROTECTED_ACTION_TOKENS,
    ROUTINE_WRITE_TOOLKITS,
    STANDING_NOTIFICATION_KINDS,
    SendError as OrchestrationError,
    action_authorization,
    load_notifications,
    routine_business_write,
)


def default_bridge_send(chat_id: str, text: str, document_paths: list[str] | None = None) -> dict:
    from aos_authority import operator_send

    return operator_send(chat_id, text, kind="operational_notification", document_paths=document_paths)
