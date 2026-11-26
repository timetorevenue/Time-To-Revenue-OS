# Local Search Index
> Revisit: after WP8 search schema changes. · Last touched: 2026-07-09

`tools/aos-indexer.py` builds a deterministic local SQLite FTS5 index at
`search/os_index.db`. It indexes Agentic OS Live plus TTROS Business Brain as a
read-only source. It does not call Hermes, Codex, Claude, embedding APIs, cloud
stores, or model providers.

Supported commands:

```bash
python3 tools/aos-indexer.py scan
python3 tools/aos-indexer.py index queue/inbox/example.md
python3 tools/aos-indexer.py watch --once
python3 tools/aos-indexer.py search carousel
python3 tools/aos-indexer.py status
```

PDF, DOCX, and XLSX files are indexed by filename only in v1.

## Hybrid Brain search (2026-11-07)

`tools/aos_embeddings.py` keeps `search/os_embeddings.db` (gitignored) beside the keyword
index: local `BAAI/bge-small-en-v1.5` embeddings (ONNX, CPU only, 4 threads) of v3 historical
topic notes, source `INDEX.md` files, and canonical Brain files (`memory/`, `decisions/`,
`prospects/`, `operating_context/`). Originals, cards and people hubs stay keyword-only.
The model lives in `~/.local/share/ttros/models/bge-small-en-v1.5` (hash-pinned), is loaded
per refresh or search and released afterwards; the module has no network code.

`aos_indexer.search(..., hybrid=True)` merges keyword order and embedding order with weighted
reciprocal rank fusion, then re-applies the ordering rules: a named person's hub first; with
`exact=True`, every exact-phrase hit next in keyword order; then canonical files (`memory/`,
`decisions/`, `prospects/`, `operating_context/`) that reached the fused top 10 (`CANONICAL_WINDOW`);
then the rest, intake originals in the lower tier. The Dashboard Memory search, the Shared Brain
connector, David's `search_history` / `search_calls` and David's context prefetch (query-search
tier, still `exact=True, path_only=True`, limit 5) all use it. `casey_only=True` keeps notes whose
`participants_text` is not "none"; `offset` pages results.

Every result carries `search_mode`: `hybrid`, or `keyword-only` when the embedding half was not
used (store missing, model failed to load, runtime missing). Search never fails because of the
embedding half.

The prefetch opens only pointers inside the registry read scope (`brain_pointers` /
`brain_pointer_prefixes`). v3 topic notes and people hubs are searchable but outside that scope;
such hits are skipped and counted in the block's `retrieval:search#mode=...#outside_read_scope=N`
source string (before 2026-11-08 one of them failed the whole assembly).

Canonical files longer than one chunk are embedded one heading section at a time; historical
notes keep fixed 1,500-character windows.

```bash
python3 tools/aos_embeddings.py rebuild --dry-run   # what would be embedded
python3 tools/aos_embeddings.py rebuild             # create/refresh the store
python3 tools/aos_embeddings.py status
```

Refresh runs after each Memory Exchange import, after `memory_filing.py --all`, after Brain
writes (brain_memory, Dashboard save) and after the nightly reindex; a search also embeds up
to 25 stale rows inline.

## Dashboard backend staleness (2026-11-08)

`/api/health` reports `code.stale` (runtime `.py` under `tools/`, `dashboard/backend/`, `hooks/`
and the repo root, compared with the snapshot the backend took at start). The user unit
`aos-backend-stale.path` (`systemd/`) fires when git moves HEAD and restarts `aos-backend` only if
it reports stale code. Check by hand: `python3 tools/runtime_code_staleness.py`.
