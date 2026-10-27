"""HTTP API for engine checks.

Endpoints:

- POST   /v1/checks                    → 202 + check_id, status/progress/report URLs
- POST   /v1/checks/upload             → same, for an uploaded PDF / DOCX / TXT
- GET    /v1/checks                    → paginated check list
- GET    /v1/checks/stats              → aggregate counts for dashboards
- GET    /v1/checks/{check_id}         → check metadata (status, similarity, etc.)
- PATCH  /v1/checks/{check_id}         → rename
- DELETE /v1/checks/{check_id}         → hard delete
- GET    /v1/checks/{check_id}/report  → full report JSON when complete
- GET    /v1/checks/{check_id}/progress → SSE stream of stage events

The POST kicks off the check pipeline via FastAPI's `BackgroundTasks`:
the engine runs single-process and the background task and the API share
an event loop. That keeps the deployment story to exactly one container
plus Postgres — no queue, no worker fleet. If you outgrow it, the
pipeline entry point (`workflows.check.run_check`) is a plain async
function you can drive from any orchestrator.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from sqlalchemy import bindparam
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from noplag_engine.api.deps import get_session, get_tenant_id
from noplag_engine.api.v1.schemas import (
    AlignedPassageJSON,
    CheckCreateRequest,
    CheckCreateResponse,
    CheckListItem,
    CheckListResponse,
    CheckReportResponse,
    CheckStatsResponse,
    CheckStatusResponse,
    CheckUpdateRequest,
    MatchedSourceJSON,
)
from noplag_engine.db import get_sessionmaker
from noplag_engine.ingestion import (
    ExtractionFailed,
    NoExtractableText,
    UnsupportedMediaType,
    extract_text,
    normalize_text,
    resolve_mime_type,
)
from noplag_engine.intervals import merge_intervals
from noplag_engine.workflows.check import run_check
from noplag_engine.workflows.progress import (
    ProgressEvent,
    close_publisher,
    close_subscription,
    open_subscription,
    publish,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=CheckCreateResponse,
)
async def create_check(
    body: CheckCreateRequest,
    background_tasks: BackgroundTasks,
    tenant_id: UUID = Depends(get_tenant_id),
    session: AsyncSession = Depends(get_session),
) -> CheckCreateResponse:
    return await _start_check(
        session=session,
        tenant_id=tenant_id,
        query_text=body.query_text,
        language=body.language,
        title=_derive_title(body.query_text),
        background_tasks=background_tasks,
    )


# Byte cap for uploaded documents. Mirrors the corpus uploader; the extracted
# text is separately capped at _MAX_QUERY_CHARS to match the text endpoint.
_MAX_UPLOAD_BYTES = 10 * 1024 * 1024
# Matches CheckCreateRequest.query_text's max_length so an uploaded document and
# a pasted one hit the same ceiling.
_MAX_QUERY_CHARS = 500_000

# Default check title length. Long enough to recognize the document, short
# enough for a row / header. Users can rename via PATCH.
_TITLE_MAX = 80


def _derive_title(text: str | None) -> str | None:
    """A human title from the document's content: the first non-empty line,
    whitespace-collapsed and capped. Returns None when there's no usable text
    (the surfaces fall back to "Untitled check")."""
    if not text:
        return None
    first = ""
    for line in text.splitlines():
        first = " ".join(line.split())
        if first:
            break
    if not first:
        return None
    if len(first) > _TITLE_MAX:
        first = first[:_TITLE_MAX].rstrip() + "…"
    return first


@router.post(
    "/upload",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=CheckCreateResponse,
)
async def create_check_upload(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    language: str = Form("en"),
    tenant_id: UUID = Depends(get_tenant_id),
    session: AsyncSession = Depends(get_session),
) -> CheckCreateResponse:
    """Run a check on an uploaded document (PDF / DOCX / TXT).

    Reuses the corpus uploader's extractor (ingestion.extract): resolve the
    MIME type, pull plain text, then hand off to the same pipeline as the
    pasted-text endpoint.
    """
    data = await file.read()
    if len(data) == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty upload")
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"upload exceeds {_MAX_UPLOAD_BYTES} bytes",
        )

    try:
        mime_type = resolve_mime_type(file.content_type, file.filename)
    except UnsupportedMediaType as exc:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)
        ) from exc

    try:
        text = normalize_text(extract_text(data, mime_type))
    except (NoExtractableText, ExtractionFailed) as exc:
        # Empty-but-well-formed and corrupt-but-typed both mean "no text" —
        # 422 on user input, never a 500.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)
        ) from exc

    if len(text) > _MAX_QUERY_CHARS:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"document text exceeds {_MAX_QUERY_CHARS} characters",
        )

    # Uploads are named by their filename — more recognizable than the first
    # line of an extracted PDF/DOCX (which is often a header or page number).
    filename_title = (file.filename or "").strip()[:_TITLE_MAX] or None

    return await _start_check(
        session=session,
        tenant_id=tenant_id,
        query_text=text,
        language=language,
        title=filename_title,
        background_tasks=background_tasks,
    )


async def _start_check(
    *,
    session: AsyncSession,
    tenant_id: UUID,
    query_text: str,
    language: str,
    title: str | None = None,
    background_tasks: BackgroundTasks,
) -> CheckCreateResponse:
    check_id = uuid4()
    await session.execute(
        sql_text(
            "INSERT INTO checks "
            "(id, tenant_id, status, title, query_text_length, word_count, "
            " query_text) "
            "VALUES (:id, :tenant_id, 'pending', :title, :qlen, :wc, :qtext)"
        ),
        {
            "id": check_id,
            "tenant_id": tenant_id,
            "title": title,
            "qlen": len(query_text),
            "wc": len(query_text.split()),
            # Persist the submission body so the report can render highlights
            # against the real text.
            "qtext": query_text,
        },
    )
    await session.commit()

    background_tasks.add_task(
        _run_check_in_background,
        check_id,
        query_text,
        tenant_id,
        language,
    )

    return CheckCreateResponse(
        check_id=check_id,
        status_url=f"/v1/checks/{check_id}",
        progress_url=f"/v1/checks/{check_id}/progress",
        report_url=f"/v1/checks/{check_id}/report",
    )


# Server-side cap on `limit` so a pathological caller can't request the
# whole table in one round-trip.
_MAX_LIST_LIMIT = 100
_DEFAULT_LIST_LIMIT = 20

# Length of the `query_text_preview` snippet returned in each list
# item. Keeps the response payload bounded — a list row only needs
# enough text to differentiate submissions visually; the full body
# lives on the report endpoint.
_PREVIEW_CHARS = 120


@router.get("", response_model=CheckListResponse)
async def list_checks(
    limit: int = _DEFAULT_LIST_LIMIT,
    offset: int = 0,
    tenant_id: UUID = Depends(get_tenant_id),
    session: AsyncSession = Depends(get_session),
) -> CheckListResponse:
    """Paginated list of checks, newest first.

    Ordered `created_at DESC, id DESC` so the secondary key breaks
    ties deterministically when two checks were inserted in the same
    server tick.
    """
    if limit < 1:
        limit = 1
    if limit > _MAX_LIST_LIMIT:
        limit = _MAX_LIST_LIMIT
    if offset < 0:
        offset = 0

    total = (
        await session.execute(
            sql_text("SELECT count(*) FROM checks WHERE tenant_id = :t"),
            {"t": tenant_id},
        )
    ).scalar_one()

    rows = (
        await session.execute(
            sql_text(
                "SELECT id, status, title, query_text_length, "
                "  word_count, source_count, query_text, "
                "  total_matched_chars, overall_similarity_pct, "
                "  created_at, completed_at "
                "FROM checks WHERE tenant_id = :t "
                "ORDER BY created_at DESC, id DESC "
                "LIMIT :limit OFFSET :offset"
            ),
            {"t": tenant_id, "limit": limit, "offset": offset},
        )
    ).all()

    items = [
        CheckListItem(
            id=row.id,
            status=row.status,
            title=row.title,
            query_text_length=row.query_text_length,
            word_count=row.word_count,
            source_count=row.source_count,
            query_text_preview=(
                row.query_text[:_PREVIEW_CHARS]
                if row.query_text is not None
                else None
            ),
            total_matched_chars=row.total_matched_chars,
            overall_similarity_pct=(
                float(row.overall_similarity_pct)
                if row.overall_similarity_pct is not None
                else None
            ),
            created_at=row.created_at,
            completed_at=row.completed_at,
        )
        for row in rows
    ]

    return CheckListResponse(
        checks=items, total=total, limit=limit, offset=offset
    )


# Declared before "/{check_id}" so FastAPI matches the literal /stats
# path rather than parsing "stats" as a check UUID (422).
@router.get("/stats", response_model=CheckStatsResponse)
async def get_check_stats(
    tenant_id: UUID = Depends(get_tenant_id),
    session: AsyncSession = Depends(get_session),
) -> CheckStatsResponse:
    """Aggregates for dashboard-style surfaces. One round-trip computes
    all counts."""
    agg = (
        await session.execute(
            sql_text(
                "SELECT "
                "  count(*) AS total, "
                "  count(*) FILTER ("
                "    WHERE status <> 'failed' "
                "    AND created_at >= date_trunc('month', now())"
                "  ) AS this_month, "
                "  avg(overall_similarity_pct) FILTER ("
                "    WHERE status = 'complete' "
                "    AND overall_similarity_pct IS NOT NULL"
                "  ) AS avg_sim, "
                "  count(*) FILTER ("
                "    WHERE status = 'complete' "
                "    AND overall_similarity_pct IS NOT NULL"
                "  ) AS sim_window, "
                "  COALESCE(sum(word_count), 0) AS words, "
                "  COALESCE(sum(source_count), 0) AS sources "
                "FROM checks WHERE tenant_id = :t"
            ),
            {"t": tenant_id},
        )
    ).one()

    return CheckStatsResponse(
        total_checks=agg.total or 0,
        checks_this_month=agg.this_month or 0,
        avg_similarity_pct=(
            float(agg.avg_sim) if agg.avg_sim is not None else None
        ),
        avg_similarity_window=agg.sim_window or 0,
        total_words=int(agg.words or 0),
        total_sources=int(agg.sources or 0),
    )


@router.get("/{check_id}", response_model=CheckStatusResponse)
async def get_check_status(
    check_id: UUID,
    tenant_id: UUID = Depends(get_tenant_id),
    session: AsyncSession = Depends(get_session),
) -> CheckStatusResponse:
    row = await _load_check(session, check_id, tenant_id)
    return _check_row_to_status(row)


@router.patch("/{check_id}", response_model=CheckStatusResponse)
async def update_check(
    check_id: UUID,
    body: CheckUpdateRequest,
    tenant_id: UUID = Depends(get_tenant_id),
    session: AsyncSession = Depends(get_session),
) -> CheckStatusResponse:
    # Partial update: apply only the fields the caller actually sent.
    fields = body.model_fields_set
    if "title" not in fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="no fields to update"
        )

    await _load_check(session, check_id, tenant_id)

    title = (body.title or "").strip()
    if not title:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="title cannot be empty",
        )

    await session.execute(
        sql_text(
            "UPDATE checks SET title = :title "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": check_id, "tenant_id": tenant_id, "title": title[:200]},
    )
    row = await _load_check(session, check_id, tenant_id)
    await session.commit()
    return _check_row_to_status(row)


@router.delete("/{check_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_check(
    check_id: UUID,
    tenant_id: UUID = Depends(get_tenant_id),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Hard-delete a check + its per-source rows (check_results cascades on
    the FK)."""
    # 404 before deleting so the caller can distinguish "gone" from "never
    # existed" — _load_check raises 404 when the row isn't visible.
    await _load_check(session, check_id, tenant_id)
    await session.execute(
        sql_text("DELETE FROM checks WHERE id = :id AND tenant_id = :tenant_id"),
        {"id": check_id, "tenant_id": tenant_id},
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{check_id}/report", response_model=CheckReportResponse)
async def get_check_report(
    check_id: UUID,
    tenant_id: UUID = Depends(get_tenant_id),
    session: AsyncSession = Depends(get_session),
) -> CheckReportResponse:
    check_row = await _load_check(session, check_id, tenant_id)
    if check_row.status != "complete":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"check is {check_row.status}; report is only available when complete",
        )
    return await _build_report_response(session, check_id, check_row)


async def _build_report_response(
    session: AsyncSession,
    check_id: UUID,
    check_row,
) -> CheckReportResponse:
    """Assemble the CheckReportResponse for a completed check."""
    rows = (
        await session.execute(
            sql_text(
                "SELECT source_document_id, matched_chars, "
                " similarity_pct, passages "
                "FROM check_results WHERE check_id = :id "
                "ORDER BY similarity_pct DESC, source_document_id ASC"
            ),
            {"id": check_id},
        )
    ).all()

    # Enrich the report so the source list reads as text, not bare ids:
    # the matched source's filename/URL per source, and a short excerpt of
    # the matched source text per region (sliced from the candidate chunk).
    # Both come from two bounded lookups keyed off what the check_results
    # rows already reference.
    source_ids = {r.source_document_id for r in rows}
    candidate_chunk_ids = {
        UUID(p["candidate_chunk_id"]) for r in rows for p in r.passages
    }
    source_meta = await _fetch_source_metadata(session, source_ids)
    chunk_texts = await _fetch_candidate_chunk_texts(session, candidate_chunk_ids)

    sources: list[MatchedSourceJSON] = []
    unique_intervals: list[tuple[int, int]] = []
    for r in rows:
        passages = [
            AlignedPassageJSON(
                **p,
                overlap_text_preview=_region_preview(
                    chunk_texts.get(UUID(p["candidate_chunk_id"])),
                    p["candidate_start"],
                    p["candidate_end"],
                ),
            )
            for p in r.passages
        ]
        meta = source_meta.get(r.source_document_id)
        sources.append(
            MatchedSourceJSON(
                source_document_id=r.source_document_id,
                matched_chars=r.matched_chars,
                similarity_pct=float(r.similarity_pct),
                passages=passages,
                source_filename=meta[0] if meta else None,
                source_url=meta[1] if meta else None,
            )
        )
        unique_intervals.extend((p.query_start, p.query_end) for p in passages)

    duration_seconds = None
    if check_row.completed_at and check_row.created_at:
        duration_seconds = round(
            (check_row.completed_at - check_row.created_at).total_seconds(), 1
        )

    return CheckReportResponse(
        title=check_row.title,
        query_text_length=check_row.query_text_length,
        word_count=check_row.word_count,
        query_text=check_row.query_text,
        total_matched_chars=check_row.total_matched_chars or 0,
        overall_similarity_pct=float(check_row.overall_similarity_pct or 0.0),
        sources=sources,
        unique_passages=merge_intervals(unique_intervals),
        coverage=check_row.coverage or "full",
        coverage_reason=check_row.coverage_reason,
        checked_chunks=check_row.checked_chunks,
        total_chunks=check_row.total_chunks,
        duration_seconds=duration_seconds,
        corpus_sources=await _corpus_source_count(session),
    )


@router.get("/{check_id}/progress")
async def stream_progress(
    check_id: UUID,
    tenant_id: UUID = Depends(get_tenant_id),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    # Verify the check exists before opening an SSE subscription.
    check_row = await _load_check(session, check_id, tenant_id)
    terminal_status = check_row.status if check_row.status in {"complete", "failed"} else None

    async def event_generator():
        # Late-subscriber short-circuit: if the check is already in a
        # terminal state, the publisher closed its sentinel before this
        # subscription opened. Emit a final event and close so the
        # client doesn't hang waiting for events that will never come.
        if terminal_status is not None:
            yield (
                "data: "
                + json.dumps(
                    {
                        "stage": terminal_status,
                        "chunks_done": 0,
                        "chunks_total": 0,
                    }
                )
                + "\n\n"
            )
            return

        queue = open_subscription(check_id)
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield f"data: {json.dumps(_event_to_dict(event))}\n\n"
        finally:
            close_subscription(check_id, queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# Max length of the per-region matched-source excerpt returned in the
# report. Long enough to read as a sentence, short enough to keep the
# payload bounded when a check matches many regions.
_REGION_PREVIEW_CHARS = 200

_SOURCE_METADATA_SQL = sql_text(
    "SELECT id, filename, source_url FROM documents WHERE id IN :ids"
).bindparams(bindparam("ids", expanding=True))

# Text lives in the chunks_text side table; chunks stays narrow.
_CANDIDATE_CHUNK_TEXTS_SQL = sql_text(
    "SELECT chunk_id AS id, text FROM chunks_text WHERE chunk_id IN :ids"
).bindparams(bindparam("ids", expanding=True))


async def _fetch_source_metadata(
    session: AsyncSession, source_ids: set[UUID]
) -> dict[UUID, tuple[str | None, str | None]]:
    """Per matched source: (filename, source_url). The report prefers the URL
    (the real page for web-sourced corpus rows) and falls back to the filename."""
    if not source_ids:
        return {}
    rows = (
        await session.execute(_SOURCE_METADATA_SQL, {"ids": list(source_ids)})
    ).all()
    return {row.id: (row.filename, row.source_url) for row in rows}


async def _corpus_source_count(session: AsyncSession) -> int | None:
    """Estimated corpus document count (the universe a check scans against).

    Uses pg_class.reltuples so it stays O(1) regardless of corpus size — an
    exact count(*) on a large corpus table would dominate report latency.
    Returns None if the estimate isn't available (fresh table, never
    analyzed)."""
    try:
        val = (
            await session.execute(
                sql_text(
                    "SELECT reltuples::bigint FROM pg_class WHERE relname = 'documents'"
                )
            )
        ).scalar()
        return int(val) if val and val > 0 else None
    except Exception:
        return None


async def _fetch_candidate_chunk_texts(
    session: AsyncSession, chunk_ids: set[UUID]
) -> dict[UUID, str]:
    if not chunk_ids:
        return {}
    rows = (
        await session.execute(
            _CANDIDATE_CHUNK_TEXTS_SQL, {"ids": list(chunk_ids)}
        )
    ).all()
    return {row.id: row.text for row in rows}


def _region_preview(
    chunk_text: str | None, start: int, end: int
) -> str | None:
    """A whitespace-collapsed excerpt of a candidate chunk's matched span.

    Returns None when the chunk is gone (e.g. re-fingerprinted corpus,
    hand-seeded test rows). Python slicing clamps out-of-range offsets, so
    a stale offset degrades to a shorter excerpt rather than raising.
    """
    if not chunk_text:
        return None
    excerpt = " ".join(chunk_text[start:end].split())
    if not excerpt:
        return None
    if len(excerpt) > _REGION_PREVIEW_CHARS:
        excerpt = excerpt[:_REGION_PREVIEW_CHARS].rstrip() + "…"
    return excerpt


async def _load_check(
    session: AsyncSession, check_id: UUID, tenant_id: UUID
):
    result = await session.execute(
        sql_text(
            "SELECT id, tenant_id, status, stage, title, query_text_length, word_count, "
            "  query_text, total_matched_chars, overall_similarity_pct, error_message, "
            "  coverage, coverage_reason, checked_chunks, total_chunks, "
            "  created_at, updated_at, completed_at "
            "FROM checks WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": check_id, "tenant_id": tenant_id},
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="check not found"
        )
    return row


def _check_row_to_status(row) -> CheckStatusResponse:
    return CheckStatusResponse(
        id=row.id,
        status=row.status,
        stage=row.stage,
        title=row.title,
        query_text_length=row.query_text_length,
        total_matched_chars=row.total_matched_chars,
        overall_similarity_pct=(
            float(row.overall_similarity_pct)
            if row.overall_similarity_pct is not None
            else None
        ),
        error_message=row.error_message,
        created_at=row.created_at,
        updated_at=row.updated_at,
        completed_at=row.completed_at,
    )


def _event_to_dict(event: ProgressEvent) -> dict:
    return {
        "stage": event.stage,
        "chunks_done": event.chunks_done,
        "chunks_total": event.chunks_total,
    }


# Per-check time budget (seconds). A large document whose retrieval exceeds
# the budget returns partial coverage rather than hanging. Env-overridable so
# self-hosters can tune it against their corpus size and hardware.
_BUDGET_S = float(os.environ.get("NOPLAG_CHECK_BUDGET_S", "60"))


# Pipeline stage order. Drives the status poller's stepper and the
# race-safe persist guard below.
_STAGE_ORDER = {
    "chunking": 1,
    "fingerprinting": 2,
    "retrieving": 3,
    "aligning": 4,
    "assembling": 5,
    "complete": 6,
}


async def _persist_stage(check_id: UUID, stage: str) -> None:
    """Write the coarse pipeline `stage` to the check row for the progress
    poller. Advisory + best-effort: a failure only logs (the durable lifecycle
    is `status`). The stage-order guard keeps a task that lands late — these are
    create_task'd from the sync progress callback — from overwriting a newer
    stage."""
    if stage not in _STAGE_ORDER:
        return
    try:
        async with get_sessionmaker()() as session:
            await session.execute(
                sql_text(
                    "UPDATE checks SET stage = :stage WHERE id = :id AND ("
                    "  stage IS NULL OR :order > CASE stage"
                    "    WHEN 'chunking' THEN 1 WHEN 'fingerprinting' THEN 2"
                    "    WHEN 'retrieving' THEN 3 WHEN 'aligning' THEN 4"
                    "    WHEN 'assembling' THEN 5 WHEN 'complete' THEN 6"
                    "    ELSE 0 END)"
                ),
                {"id": check_id, "stage": stage, "order": _STAGE_ORDER[stage]},
            )
            await session.commit()
    except Exception:
        logger.debug(
            "stage persist for %s (%s) failed", check_id, stage, exc_info=True
        )


async def _run_check_in_background(
    check_id: UUID,
    query_text: str,
    tenant_id: UUID,
    language: str,
) -> None:
    """Execute the pipeline + persist results + emit progress.

    Runs in a FastAPI BackgroundTask (post-response). Owns its own
    session so the request's session lifetime doesn't matter. Wraps
    everything in a try/except so any pipeline error lands in the row
    as `status='failed'` rather than vanishing into the event loop.
    """

    # Persist the coarse stage on change so a status poller can show ordered
    # progress without a live event stream. Fire-and-forget on the running
    # loop; references are held so the tasks aren't GC'd mid-flight, and the
    # persist itself carries a stage-order guard against out-of-order writes.
    last_stage: dict[str, str | None] = {"v": None}
    stage_tasks: set[asyncio.Task] = set()

    def progress_callback(event: ProgressEvent) -> None:
        publish(check_id, event)
        if event.stage == last_stage["v"]:
            return
        last_stage["v"] = event.stage
        task = asyncio.create_task(_persist_stage(check_id, event.stage))
        stage_tasks.add(task)
        task.add_done_callback(stage_tasks.discard)

    sessionmaker = get_sessionmaker()
    try:
        async with sessionmaker() as session:
            await _set_status(session, check_id, "running")
            report = await run_check(
                query_text,
                session,
                tenant_id=tenant_id,
                language=language,
                progress_callback=progress_callback,
                time_budget_s=_BUDGET_S,
            )

            await _persist_results(session, check_id, report)
            await session.commit()
    except Exception as exc:
        logger.exception("check %s failed", check_id)
        async with sessionmaker() as session:
            await _set_status(
                session, check_id, "failed", error_message=str(exc)
            )
            await session.commit()
    finally:
        close_publisher(check_id)


async def _set_status(
    session: AsyncSession,
    check_id: UUID,
    new_status: str,
    *,
    error_message: str | None = None,
) -> None:
    if new_status == "failed":
        await session.execute(
            sql_text(
                "UPDATE checks SET status = 'failed', error_message = :err, "
                "completed_at = :now WHERE id = :id"
            ),
            {"id": check_id, "err": error_message, "now": datetime.now(UTC)},
        )
    else:
        await session.execute(
            sql_text("UPDATE checks SET status = :s WHERE id = :id"),
            {"id": check_id, "s": new_status},
        )
    await session.commit()


async def _persist_results(session, check_id, report) -> None:
    """Write per-source rows + flip check to 'complete' in one transaction."""
    for source in report.sources:
        passages_json = [
            {
                "query_chunk_id": str(p.query_chunk_id),
                "candidate_chunk_id": str(p.candidate_chunk_id),
                "query_start": p.query_start,
                "query_end": p.query_end,
                "candidate_start": p.candidate_start,
                "candidate_end": p.candidate_end,
                "score": p.score,
                "match_type": p.match_type,
            }
            for p in source.passages
        ]
        await session.execute(
            sql_text(
                "INSERT INTO check_results "
                "(check_id, source_document_id, matched_chars, "
                " similarity_pct, passages) "
                "VALUES (:check_id, :doc, :mc, :sim, CAST(:passages AS jsonb))"
            ),
            {
                "check_id": check_id,
                "doc": source.source_document_id,
                "mc": source.matched_chars,
                "sim": source.similarity_pct,
                "passages": json.dumps(passages_json),
            },
        )
    await session.execute(
        sql_text(
            "UPDATE checks SET status = 'complete', "
            "  total_matched_chars = :tmc, "
            "  overall_similarity_pct = :osp, "
            "  source_count = :sc, "
            "  coverage = :cov, "
            "  coverage_reason = :covr, "
            "  checked_chunks = :cc, "
            "  total_chunks = :tc, "
            "  completed_at = :now "
            "WHERE id = :id"
        ),
        {
            "id": check_id,
            "tmc": report.total_matched_chars,
            "osp": report.overall_similarity_pct,
            "sc": len(report.sources),
            "cov": report.coverage,
            "covr": report.coverage_reason,
            "cc": report.checked_chunks,
            "tc": report.total_chunks,
            "now": datetime.now(UTC),
        },
    )
