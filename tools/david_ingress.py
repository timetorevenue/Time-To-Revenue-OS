"""Read the existing bridge receipt and canary tombstones before new David ingress.

Adapted from main's uncommitted david_telegram_origin/david_canary work. This
reads delivery evidence, never credentials, and creates no execution authority.
There is no canary arming path in the replacement runtime.
Revisit: on bridge delivery-receipt changes. · 2026-11-15.
"""
import hashlib
import json
import re
from pathlib import Path

STATE = Path('logs/runtime/telegram_bridge_updates.json')
TESTS = Path('queue/david_canary_tests.jsonl')
MARKER = re.compile(r'FAST-DAVID-(?:AUTO|ACCEPT)-[A-Za-z0-9_-]+', re.I)


def reject_retired_test(root: Path, text: str) -> None:
    if MARKER.search(text):
        raise ValueError('Retired canary test; no execution')
    path = Path(root) / TESTS
    if not path.exists():
        return
    try:
        digest = hashlib.sha256(text.encode()).hexdigest()
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or not isinstance(row.get('marker'), str) or not row['marker']:
                raise ValueError('invalid binding')
            if row['marker'].casefold() in text.casefold() or row.get('request_sha256') == digest:
                raise ValueError('Retired canary test; no execution')
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError('Retired canary bindings refuse execution') from exc


def verify_telegram(root: Path, text: str, conversation_id: str, delivery_id: str) -> str:
    """Conversation labels/body fields alone never authorize a completion recipient."""
    if not conversation_id.startswith('telegram-'):
        return ''
    chat = conversation_id.removeprefix('telegram-')
    if not re.fullmatch(r'-?\d{1,20}', chat) or not delivery_id:
        raise ValueError('Telegram delivery provenance cannot be verified')
    key = hashlib.sha256(f'{chat}\0{delivery_id}'.encode()).hexdigest()
    try:
        row = json.loads((Path(root) / STATE).read_text())['david_deliveries'][key]
        payload = row['payload']
        if not isinstance(payload, dict):
            raise ValueError('malformed bridge delivery')
        valid = (row['chat_id'] == chat and row['delivery_id'] == delivery_id
                 and row['status'] in {'submitting', 'pending', 'ready', 'sent'}
                 and payload.get('text') == text and payload.get('conversation_id') == conversation_id
                 and payload.get('delivery_id') == delivery_id)
        if valid:
            return chat
    except (OSError, ValueError, KeyError, TypeError):
        pass
    raise ValueError('Telegram delivery provenance cannot be verified')
