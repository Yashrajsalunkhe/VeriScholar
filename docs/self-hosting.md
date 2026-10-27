# Self-hosting guide

The engine is two moving parts: a Postgres 16 database and one Python
process (FastAPI + background tasks in the same event loop). No queue, no
cache layer, no worker fleet.

## Prerequisites

- Docker + docker compose (the easy path), or
- Python 3.12 and Postgres 16 for a bare install.

## Compose install

```sh
git clone https://github.com/NoplagLabs/noplag-engine
cd noplag-engine
docker compose up -d
```

What happens on first start:

1. Alembic migrates the schema (`migrations/versions/0001_initial.py`).
2. The entrypoint loads the bundled Wikipedia sample corpus
   (`data/sample_corpus.jsonl.gz`, ~1,000 articles) so the demo works
   immediately. Skip with `NOPLAG_LOAD_SAMPLE_CORPUS=0`.
3. uvicorn serves the API + demo page on port 8000.

Configuration is environment variables only — see `.env.example` for the
complete annotated list. Compose reads a `.env` file next to
`docker-compose.yml`.

## Bare install

```sh
pip install -e .
export DATABASE_URL=postgresql+psycopg://user:pass@host:5432/noplag
alembic upgrade head
python scripts/load_sample_corpus.py          # optional
uvicorn noplag_engine.api.app:app --host 0.0.0.0 --port 8000
```

## Security model

There is none, on purpose: the API is an unauthenticated single-tenant
backend service. Anyone who can reach the port can run checks and manage the
corpus. Deploy it:

- on a private network / behind a VPN, or
- behind a reverse proxy that enforces your own auth (basic auth, OAuth2
  proxy, mTLS — anything), or
- as an internal service your own application calls server-to-server.

Do not expose it raw to the public internet.

## Building your corpus

The engine only matches against what you index. Sources of corpus documents:

| Path | Good for |
|---|---|
| `scripts/ingest_folder.py <dir>` | a directory tree of .txt / .md / .pdf / .docx; idempotent re-runs |
| `POST /v1/corpus/documents` | one file at a time over HTTP (multipart) |
| `ingest_platform_document()` + `fingerprint_document()` | your own bulk pipeline in Python |

Two corpus scopes exist in the schema: rows with `tenant_id` set (uploaded
through the API, manageable via `GET/DELETE /v1/corpus/documents`) and
platform rows with `tenant_id NULL` (bulk loads: the sample corpus, folder
ingests). Checks match against both.

Chunk text is stored twice on purpose: `chunks.fingerprints` (narrow,
GIN-indexed, what retrieval scans) and `chunks_text` (the text the aligner
and report previews read). Expect roughly 1.5–2× the plain-text size in
Postgres after indexing.

### Removing documents

`DELETE /v1/corpus/documents/{id}` (API-uploaded docs), or plain SQL for bulk
loads — deleting a `documents` row cascades to its chunks. Old check reports
that referenced a deleted source lose those source rows (cascade), keeping
their overall scores.

## Operating at scale

Up to a few million chunks, the defaults just work. Past that, in order:

1. **ANALYZE after every bulk load.** The L1 `&&` probe lives and dies by
   planner statistics; a freshly bulk-loaded (or restored) table is quiescent
   and autoanalyze may never fire. `python -m noplag_engine.maintenance.analyze`.
2. **Build the document-frequency table** once retrieval latency starts to
   matter (tens of millions of chunks):
   `python scripts/compute_fingerprint_df.py --floor 200`. With it present,
   retrieval probes only each chunk's rarest fingerprints, the stop-list
   filter activates, and non-discriminative all-common-phrase chunks are
   skipped. Without it, the engine transparently falls back to full-union
   retrieval — correct, just slower at scale. Re-run after each major
   ingestion.
3. **Tune the knobs** (`NOPLAG_L1_DF_BUDGET`, `NOPLAG_L1_POOL_CAP`,
   `NOPLAG_L1_NONDISCRIM_DF`, `NOPLAG_STOP_LIST_N`) against your own corpus —
   the defaults were tuned on a web-scale English corpus. All are read at
   query time; changing them needs a restart but no re-index.
4. **Per-check time budget** (`NOPLAG_CHECK_BUDGET_S`, default 60): a huge
   document that exceeds it returns `coverage: "partial"` with every match
   found so far, visited in a spread order so partial coverage is uniform
   across the document rather than front-biased.

Changing `NOPLAG_FP_K` / `NOPLAG_FP_W` / chunking parameters invalidates the
corpus — fingerprints only match when the corpus and query sides used
identical parameters. Re-ingest after changing them.

## Backups

Everything lives in Postgres (the raw upload bytes under
`CORPUS_STORAGE_DIR` are a convenience copy for future re-extraction, not
required for matching). Standard Postgres backup practice applies; after any
restore, run the ANALYZE step above.

## Scaling the process

The single process handles the API and runs checks on its own event loop,
with L0 alignment spread over a process pool sized to the machine. CPU is
the ceiling: alignment dominates a check's wall clock. Give the container
cores before RAM. If you need more than one node, note that checks are
tracked in Postgres but SSE progress events are in-process — sticky routing
per check, or polling `GET /v1/checks/{id}` (which reads the persisted
stage), keeps multi-node deployments honest.
