# API reference

Base URL: wherever the engine listens (compose default `http://localhost:8000`).
Everything is JSON unless noted. Interactive docs with full schemas are
served at `/docs` (Swagger UI) and `/redoc`.

No authentication — see the security model note in
[self-hosting.md](self-hosting.md).

## Checks

### POST /v1/checks

Submit plain text for checking.

```json
{ "query_text": "...", "language": "en" }
```

- `query_text`: 1–500,000 characters.
- `language`: ISO 639-1 code for sentence segmentation (default `en`).

Returns `202` with the check id and the three URLs to follow:

```json
{
  "check_id": "8b7c…",
  "status_url": "/v1/checks/8b7c…",
  "progress_url": "/v1/checks/8b7c…/progress",
  "report_url": "/v1/checks/8b7c…/report"
}
```

The check runs in the background; poll the status URL or subscribe to the
progress stream.

### POST /v1/checks/upload

Same as above for a file. Multipart form: `file` (PDF / DOCX / TXT, max
10 MiB), optional `language`. Errors: `415` unsupported type, `422` no
extractable text (e.g. a scanned PDF without a text layer), `413` too large.

### GET /v1/checks/{id}

Check metadata: `status` (`pending` / `running` / `complete` / `failed`),
in-flight `stage` (`chunking` → `fingerprinting` → `retrieving` → `aligning`
→ `assembling`), `title`, `overall_similarity_pct`, timestamps,
`error_message` on failure.

### GET /v1/checks/{id}/report

`409` until the check is `complete`. Then the full report:

- `overall_similarity_pct`, `total_matched_chars`, `query_text` (for
  rendering highlights), `word_count`, `duration_seconds`.
- `sources[]` — per matched corpus document: `source_document_id`,
  `similarity_pct`, `matched_chars`, `source_filename`, `source_url`, and
  `passages[]` with character offsets into both the query
  (`query_start`/`query_end`) and the source chunk, plus a short
  `overlap_text_preview` of the matched source text.
- `unique_passages` — merged `[start, end)` intervals over the query text
  (the deduplicated union across all sources; this is what
  `total_matched_chars` sums).
- `coverage` / `coverage_reason` / `checked_chunks` / `total_chunks` —
  `"partial"` + `"time_cap"` when the per-check time budget stopped
  retrieval early on a very large document; every match found is still
  returned.
- `corpus_sources` — estimated corpus document count the check ran against.

### GET /v1/checks/{id}/progress

Server-sent events stream of stage transitions:
`data: {"stage": "aligning", "chunks_done": 128, "chunks_total": 400}`.
Terminal event `complete` or `failed`, then the stream closes. Subscribing
after completion yields exactly one terminal event.

### GET /v1/checks

Paginated list, newest first. Query params `limit` (max 100) and `offset`.
Rows carry a 120-char `query_text_preview` plus the status/score fields.

### PATCH /v1/checks/{id}

Rename: `{ "title": "..." }`. `DELETE /v1/checks/{id}` hard-deletes the
check and its results.

### GET /v1/checks/stats

Aggregates: `total_checks`, `checks_this_month`, `avg_similarity_pct` (+ the
window size it was computed over), `total_words`, `total_sources`.

## Corpus

### POST /v1/corpus/documents

Multipart upload (`file`): extracts text, stores the document, fingerprints
it in the background. Returns `201` with metadata (`fingerprint_status`
starts `pending`, flips to `fingerprinted` when matchable). A byte-identical
re-upload returns `200` with the existing document.

### GET /v1/corpus/documents

Paginated metadata list of API-uploaded documents (`limit`/`offset`).
Bulk-loaded platform corpus rows (sample corpus, folder ingests) are not
listed here.

### GET /v1/corpus/documents/{id}

Metadata plus the full `extracted_text`.

### DELETE /v1/corpus/documents/{id}

Removes the document and (cascade) its chunks; returns `204`.

## Health

`GET /health` → `{"status": "ok"}`. No DB call — suitable for liveness
probes.
