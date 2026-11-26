"""Isolated dated global/client corpus; no production storage or providers."""
import contextlib
import json
from pathlib import Path
from unittest.mock import patch

import aos_entity_index as entities
import aos_indexer as indexer
import shared_brain_read as reader
import brain_memory
import business_brain
from tests.business_brain_test_support import registry_data, write_registry


@contextlib.contextmanager
def corpus(root):
    vault = root / 'brain'
    db = root / 'search/os_index.db'
    rows = [
        ('decisions/current.md', '2026-11-15', 'confirmed', 'explicit_decision', 'Alex Rivera', 'Callback decision: return missed calls within one hour.', ''),
        ('decisions/proposal.md', '2026-11-15', 'unconfirmed', 'explicit_decision', 'Alex Rivera', 'Callback proposal: wait seven days. This is unconfirmed.', ''),
        ('decisions/old.md', '2026-08-15', 'superseded', 'explicit_decision', 'Alex Rivera', 'Callback old decision: wait one day.', 'business_brain:decisions/current.md'),
        ('sources/yesterday.md', '2026-11-14', 'active', 'historical_evidence', 'Alex Rivera', 'Callback meeting yesterday: review the one-hour response.', ''),
        ('sources/weeks.md', '2026-10-25', 'active', 'historical_evidence', 'Alex Rivera', 'Callback pilot started three weeks ago.', ''),
        ('sources/ambiguous.md', '2026-11-14', 'active', 'historical_evidence', 'Alex Rivers', 'Callback meeting with another Alex; different person.', ''),
        ('sources/import-only.md', '', 'active', 'historical_evidence', 'Alex Rivera', 'Callback source has no event date; imported today.', ''),
        ('clients/other.md', '2026-11-15', 'confirmed', 'explicit_decision', 'Alex Rivera', 'PRIVATE_CLIENT_SENTINEL callback decision.', ''),
    ]
    data = registry_data()
    allowed = []
    for i, (path, date, status, kind, person, body, superseded) in enumerate(rows):
        target = vault / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f'---\nid: fixture-{i}\ntitle: Callback {path}\ntype: knowledge\nknowledge_type: {kind}\nstatus: {status}\ndate: {date}\nimported_at: 2026-11-15\nparticipants_text: {person}\nentity_keys: [project:callback-pilot]\nsuperseded_by: {superseded}\nsource_refs: [https://example.org/original-{i}]\n---\n# Callback {path}\n\n{body}\n')
        if not path.startswith('clients/'):
            allowed.append('business_brain:' + path)
    data['scopes']['global']['brain_pointers'] = allowed
    data['scopes']['global']['search_source_identities'] = [{'source':'business_brain','paths':allowed}]
    data['scopes']['client-a']['brain_pointers'] = ['business_brain:clients/other.md']
    data['scopes']['client-a']['search_source_identities'] = [{'source':'business_brain','paths':['business_brain:clients/other.md']}]
    reg_path = root / 'scope.json'
    registry = write_registry(reg_path, data)
    with contextlib.ExitStack() as stack:
        for module, name, value in [
            (indexer,'LIVE_ROOT',root/'live'), (indexer,'BUSINESS_BRAIN_ROOT',vault),
            (indexer,'CLIENT_SCOPE_REGISTRY_PATH',reg_path), (indexer,'INGEST_CONFIG_PATH',root/'queue/ingest_watch.json'),
            (indexer,'runtime_db_path',lambda path=None: Path(path) if path else db),
            (entities,'BUSINESS_BRAIN_ROOT',vault), (entities,'MEMORY_EXCHANGE_RECEIPTS',root/'absent.jsonl'),
            (reader,'VAULT_ROOT',vault)]:
            stack.enter_context(patch.object(module,name,value))
        stack.enter_context(patch.object(brain_memory, 'VAULT_ROOT', vault))
        stack.enter_context(patch.object(business_brain, 'BUSINESS_BRAIN_ROOT', vault))
        indexer.scan(db, roots=[vault], registry=registry)
        entities.rebuild(db, vault=vault)
        yield vault, db, registry
