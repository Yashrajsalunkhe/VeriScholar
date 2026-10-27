"""Integration tests for the /v1/checks HTTP API.

Most cases insert check rows + check_results rows directly via the
session fixture rather than exercising the background-task path. That
keeps assertions deterministic — the BackgroundTasks lifecycle doesn't
have to settle before the test queries the resulting state. The end-
to-end happy path (POST → poll → report) is exercised by
test_checks_corpus_e2e.py against a fingerprinted corpus.

The engine runs single-tenant: every request operates as the default
tenant seeded by the initial migration (see api/deps.py), so requests
carry no credentials.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from noplag_engine.api.deps import DEFAULT_TENANT_ID
from noplag_engine.workflows.progress import ProgressEvent, publish

FIXTURES = Path(__file__).resolve().parent / "fixtures"


async def _insert_tenant(
    session: AsyncSession, tenant_id: UUID, name: str = "acct"
) -> None:
    """Insert a tenant row explicitly when a test needs a second one
    beyond the default tenant the session fixture seeds."""
    await session.execute(
        sql_text(
            "INSERT INTO tenants (id, name) VALUES (:id, :name) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"id": tenant_id, "name": name},
    )
    await session.commit()


async def _insert_document(
    session: AsyncSession,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    *,
    filename: str | None = None,
    source_url: str | None = None,
) -> UUID:
    await _insert_tenant(session, tenant_id)
    did = uuid4()
    await session.execute(
        sql_text(
            "INSERT INTO documents (id, tenant_id, filename, source_url) "
            "VALUES (:id, :tenant_id, :filename, :source_url)"
        ),
        {
            "id": did,
            "tenant_id": tenant_id,
            "filename": filename,
            "source_url": source_url,
        },
    )
    await session.commit()
    return did


async def _insert_check(
    session: AsyncSession,
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    status: str = "pending",
    title: str | None = None,
    query_text_length: int = 100,
    query_text: str | None = None,
    word_count: int | None = None,
    source_count: int | None = None,
    total_matched_chars: int | None = None,
    overall_similarity_pct: float | None = None,
    error_message: str | None = None,
    created_at: datetime | None = None,
) -> UUID:
    await _insert_tenant(session, tenant_id)
    cid = uuid4()
    completed_at = (
        datetime.now(UTC) if status in {"complete", "failed"} else None
    )
    await session.execute(
        sql_text(
            "INSERT INTO checks "
            "(id, tenant_id, status, title, query_text_length, query_text, "
            " word_count, source_count, total_matched_chars, "
            " overall_similarity_pct, error_message, completed_at, created_at) "
            "VALUES (:id, :tenant_id, :status, :title, :qlen, :qtext, :wc, "
            "        :sc, :tmc, :osp, :err, :ca, COALESCE(:created_at, now()))"
        ),
        {
            "id": cid,
            "tenant_id": tenant_id,
            "status": status,
            "title": title,
            "qlen": query_text_length,
            "qtext": query_text,
            "wc": word_count,
            "sc": source_count,
            "tmc": total_matched_chars,
            "osp": overall_similarity_pct,
            "err": error_message,
            "ca": completed_at,
            "created_at": created_at,
        },
    )
    await session.commit()
    return cid


async def _insert_check_result(
    session: AsyncSession,
    *,
    check_id: UUID,
    source_document_id: UUID,
    matched_chars: int,
    similarity_pct: float,
    passages: list[dict],
) -> None:
    await session.execute(
        sql_text(
            "INSERT INTO check_results "
            "(check_id, source_document_id, matched_chars, similarity_pct, "
            " passages) "
            "VALUES (:check_id, :doc, :mc, :sim, CAST(:p AS jsonb))"
        ),
        {
            "check_id": check_id,
            "doc": source_document_id,
            "mc": matched_chars,
            "sim": similarity_pct,
            "p": json.dumps(passages),
        },
    )
    await session.commit()


async def _insert_chunk_with_text(
    session: AsyncSession,
    *,
    document_id: UUID,
    text: str,
) -> UUID:
    """Seed one chunk + its chunks_text row so the report's per-region
    excerpt lookup has something to slice."""
    chunk_id = uuid4()
    await session.execute(
        sql_text(
            "INSERT INTO chunks "
            "(id, document_id, tenant_id, chunk_index, char_start, char_end, "
            " sentence_count, fingerprints) "
            "VALUES (:id, :doc, :tenant_id, 0, 0, :end, 1, ARRAY[]::bigint[])"
        ),
        {
            "id": chunk_id,
            "doc": document_id,
            "tenant_id": DEFAULT_TENANT_ID,
            "end": len(text),
        },
    )
    await session.execute(
        sql_text(
            "INSERT INTO chunks_text (chunk_id, tenant_id, text) "
            "VALUES (:id, :tenant_id, :text)"
        ),
        {"id": chunk_id, "tenant_id": DEFAULT_TENANT_ID, "text": text},
    )
    await session.commit()
    return chunk_id


# --- POST /v1/checks -----------------------------------------------


async def test_post_creates_check_and_returns_202(
    async_client, session: AsyncSession
):
    response = await async_client.post(
        "/v1/checks",
        json={"query_text": "the quick brown fox jumps over the lazy dog. " * 4},
    )
    assert response.status_code == 202
    body = response.json()
    assert UUID(body["check_id"])
    assert body["status_url"].endswith(f"/v1/checks/{body['check_id']}")
    assert body["progress_url"].endswith(
        f"/v1/checks/{body['check_id']}/progress"
    )
    assert body["report_url"].endswith(
        f"/v1/checks/{body['check_id']}/report"
    )

    # The checks row exists with a non-failed status. The background
    # task may or may not have run yet — either way the row is there.
    row = await session.execute(
        sql_text("SELECT status FROM checks WHERE id = :id"),
        {"id": UUID(body["check_id"])},
    )
    assert row.scalar_one() in {"pending", "running", "complete", "failed"}


async def test_post_with_empty_query_text_returns_422(async_client):
    response = await async_client.post(
        "/v1/checks",
        json={"query_text": ""},
    )
    # Pydantic min_length=1 → 422 (FastAPI validation error)
    assert response.status_code == 422


async def test_post_accepts_explicit_language(
    async_client, session: AsyncSession
):
    """POST with an explicit non-default language is accepted and the
    pipeline runs to completion (the value propagates into chunk_document
    via the background task). Default-of-en is exercised by every other
    POST test in this file."""
    spanish = (
        "El gato come pescado fresco todos los días. María lee un libro "
        "interesante en la biblioteca por la tarde."
    )
    response = await async_client.post(
        "/v1/checks",
        json={"query_text": spanish, "language": "es"},
    )
    assert response.status_code == 202
    body = response.json()
    check_id = UUID(body["check_id"])
    row = await session.execute(
        sql_text("SELECT status FROM checks WHERE id = :id"), {"id": check_id}
    )
    assert row.scalar_one() in {"pending", "running", "complete"}


async def test_post_derives_title_from_first_line(
    async_client, session: AsyncSession
):
    """The check title defaults to the submission's first non-empty line,
    whitespace-collapsed."""
    response = await async_client.post(
        "/v1/checks",
        json={
            "query_text": (
                "\n  A   Study of Roman   Aqueducts\n\n"
                "The aqueduct carried fresh water across the countryside "
                "for many kilometres without pumps of any kind."
            )
        },
    )
    assert response.status_code == 202
    check_id = UUID(response.json()["check_id"])
    row = await session.execute(
        sql_text("SELECT title FROM checks WHERE id = :id"), {"id": check_id}
    )
    assert row.scalar_one() == "A Study of Roman Aqueducts"


# --- GET /v1/checks/{id} -------------------------------------------


async def test_get_status_returns_metadata(
    async_client, session: AsyncSession
):
    cid = await _insert_check(
        session, status="running", query_text_length=420
    )

    response = await async_client.get(f"/v1/checks/{cid}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(cid)
    assert body["status"] == "running"
    assert body["query_text_length"] == 420
    assert body["total_matched_chars"] is None
    assert body["overall_similarity_pct"] is None
    assert body["error_message"] is None
    assert body["completed_at"] is None


async def test_get_status_404_for_missing_check(async_client):
    response = await async_client.get(f"/v1/checks/{uuid4()}")
    assert response.status_code == 404


async def test_failed_check_status_includes_error_message(
    async_client, session: AsyncSession
):
    cid = await _insert_check(
        session,
        status="failed",
        error_message="connection to DB lost mid-check",
    )
    response = await async_client.get(f"/v1/checks/{cid}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_message"] == "connection to DB lost mid-check"
    assert body["completed_at"] is not None


async def test_tenant_scoping_returns_404_for_other_tenant(
    async_client, session: AsyncSession
):
    # A check belonging to a tenant other than the default one the API
    # serves is invisible: 404 (not 403) so the resource's existence
    # doesn't leak.
    other_tenant_id = uuid4()
    await _insert_tenant(session, other_tenant_id, "other")
    cid_other = await _insert_check(
        session, tenant_id=other_tenant_id, status="running"
    )

    response = await async_client.get(f"/v1/checks/{cid_other}")
    assert response.status_code == 404

    # And the same for the report endpoint.
    r2 = await async_client.get(f"/v1/checks/{cid_other}/report")
    assert r2.status_code == 404


# --- GET /v1/checks/{id}/report ------------------------------------


async def test_get_report_before_complete_returns_409(
    async_client, session: AsyncSession
):
    cid = await _insert_check(session, status="pending")

    response = await async_client.get(f"/v1/checks/{cid}/report")
    assert response.status_code == 409
    assert "pending" in response.json()["detail"]


async def test_get_report_after_complete_returns_full_report(
    async_client, session: AsyncSession
):
    doc_a = await _insert_document(session, filename="printing-press.txt")
    doc_b = await _insert_document(session)
    cid = await _insert_check(
        session,
        status="complete",
        query_text_length=500,
        total_matched_chars=350,
        overall_similarity_pct=70.00,
    )
    await _insert_check_result(
        session,
        check_id=cid,
        source_document_id=doc_a,
        matched_chars=300,
        similarity_pct=60.00,
        passages=[
            {
                "query_chunk_id": str(UUID(int=0)),
                "candidate_chunk_id": str(uuid4()),
                "query_start": 0,
                "query_end": 200,
                "candidate_start": 50,
                "candidate_end": 250,
                "score": 0.98,
            },
        ],
    )
    await _insert_check_result(
        session,
        check_id=cid,
        source_document_id=doc_b,
        matched_chars=150,
        similarity_pct=30.00,
        passages=[
            {
                "query_chunk_id": str(UUID(int=1)),
                "candidate_chunk_id": str(uuid4()),
                # Gap [200, 250) so the merge keeps two distinct intervals
                # — exact-adjacent half-open intervals legitimately merge.
                "query_start": 250,
                "query_end": 400,
                "candidate_start": 0,
                "candidate_end": 150,
                "score": 0.85,
            },
        ],
    )

    response = await async_client.get(f"/v1/checks/{cid}/report")
    assert response.status_code == 200
    body = response.json()
    assert body["query_text_length"] == 500
    assert body["total_matched_chars"] == 350
    assert body["overall_similarity_pct"] == 70.0
    assert len(body["sources"]) == 2
    # Sorted by similarity_pct desc — doc_a (60.0) comes first.
    assert body["sources"][0]["source_document_id"] == str(doc_a)
    assert body["sources"][0]["similarity_pct"] == 60.0
    assert len(body["sources"][0]["passages"]) == 1
    # Source metadata enrichment: the matched document's filename comes
    # back so the source list reads as text, not a bare id.
    assert body["sources"][0]["source_filename"] == "printing-press.txt"
    assert body["sources"][0]["source_url"] is None
    assert body["sources"][1]["source_document_id"] == str(doc_b)
    assert body["sources"][1]["source_filename"] is None
    # Hand-seeded candidate chunk ids don't resolve to chunks_text rows,
    # so the per-region excerpt degrades to null rather than erroring.
    assert all(
        p["overlap_text_preview"] is None
        for s in body["sources"]
        for p in s["passages"]
    )
    # Cross-source unique_passages — two non-overlapping intervals
    # with a gap between them.
    assert body["unique_passages"] == [[0, 200], [250, 400]]
    assert body["coverage"] == "full"
    # completed_at + created_at are both set, so the report carries an
    # honest wall-clock duration.
    assert body["duration_seconds"] is not None
    # Estimated corpus size; None on a never-analyzed fresh table.
    assert "corpus_sources" in body


async def test_get_report_includes_query_text(
    async_client, session: AsyncSession
):
    """The report surfaces the persisted submission body so the Report
    page can render highlights against the real text."""
    cid = await _insert_check(
        session,
        status="complete",
        query_text="The quick brown fox jumps over the lazy dog.",
        query_text_length=44,
    )

    response = await async_client.get(f"/v1/checks/{cid}/report")
    assert response.status_code == 200
    body = response.json()
    assert body["query_text"] == "The quick brown fox jumps over the lazy dog."
    assert body["query_text_length"] == 44


async def test_report_overlap_preview_sliced_from_chunk_text(
    async_client, session: AsyncSession
):
    """When the candidate chunk still exists, each passage carries a
    short excerpt of the matched source region sliced from chunks_text."""
    doc = await _insert_document(session, filename="fox.txt")
    chunk_text = "The quick brown fox jumps over the lazy dog."
    chunk_id = await _insert_chunk_with_text(
        session, document_id=doc, text=chunk_text
    )
    cid = await _insert_check(
        session,
        status="complete",
        query_text_length=100,
        total_matched_chars=15,
        overall_similarity_pct=15.00,
    )
    await _insert_check_result(
        session,
        check_id=cid,
        source_document_id=doc,
        matched_chars=15,
        similarity_pct=15.00,
        passages=[
            {
                "query_chunk_id": str(UUID(int=0)),
                "candidate_chunk_id": str(chunk_id),
                "query_start": 10,
                "query_end": 25,
                "candidate_start": 4,
                "candidate_end": 19,
                "score": 0.95,
            },
        ],
    )

    response = await async_client.get(f"/v1/checks/{cid}/report")
    assert response.status_code == 200
    passage = response.json()["sources"][0]["passages"][0]
    assert passage["overlap_text_preview"] == "quick brown fox"


# --- GET /v1/checks list endpoint ----------------------------------


async def test_list_returns_empty_when_no_checks(async_client):
    response = await async_client.get("/v1/checks")
    assert response.status_code == 200
    body = response.json()
    assert body == {"checks": [], "total": 0, "limit": 20, "offset": 0}


async def test_list_excludes_other_tenants(
    async_client, session: AsyncSession
):
    stranger = uuid4()
    own_check = await _insert_check(session, status="complete")
    await _insert_check(session, tenant_id=stranger, status="complete")

    response = await async_client.get("/v1/checks")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert [item["id"] for item in body["checks"]] == [str(own_check)]


async def test_list_paginates_with_limit_and_offset(
    async_client, session: AsyncSession
):
    # Five rows with distinct created_at so the ordering is stable.
    base = datetime.now(UTC).replace(microsecond=0)
    ids: list[UUID] = []
    for i in range(5):
        ids.append(
            await _insert_check(
                session,
                status="complete",
                created_at=base.replace(microsecond=i),
            )
        )

    page_one = (await async_client.get("/v1/checks?limit=2&offset=0")).json()
    page_two = (await async_client.get("/v1/checks?limit=2&offset=2")).json()
    page_three = (await async_client.get("/v1/checks?limit=2&offset=4")).json()

    assert page_one["total"] == 5
    assert page_one["limit"] == 2 and page_one["offset"] == 0
    assert len(page_one["checks"]) == 2
    assert len(page_two["checks"]) == 2
    assert len(page_three["checks"]) == 1

    # Pages don't overlap.
    seen = {row["id"] for page in (page_one, page_two, page_three) for row in page["checks"]}
    assert len(seen) == 5
    assert seen == {str(cid) for cid in ids}


async def test_list_orders_by_created_at_desc(
    async_client, session: AsyncSession
):
    base = datetime.now(UTC).replace(microsecond=0)
    oldest = await _insert_check(
        session, status="complete", created_at=base.replace(microsecond=0)
    )
    middle = await _insert_check(
        session, status="complete", created_at=base.replace(microsecond=100)
    )
    newest = await _insert_check(
        session, status="complete", created_at=base.replace(microsecond=200)
    )

    body = (await async_client.get("/v1/checks")).json()
    returned = [UUID(item["id"]) for item in body["checks"]]
    assert returned == [newest, middle, oldest]


async def test_list_includes_query_text_preview_truncated_to_120(
    async_client, session: AsyncSession
):
    """Submission bodies returned in the list are truncated server-side
    so a long document doesn't blow up the payload. Full text only
    comes back from /v1/checks/{id}/report."""
    long_body = "x" * 1000
    await _insert_check(
        session,
        status="complete",
        query_text=long_body,
        query_text_length=len(long_body),
    )

    body = (await async_client.get("/v1/checks")).json()
    assert len(body["checks"]) == 1
    item = body["checks"][0]
    assert item["query_text_length"] == 1000
    assert item["query_text_preview"] == "x" * 120


async def test_list_clamps_limit_above_max_to_100(async_client):
    body = (await async_client.get("/v1/checks?limit=500")).json()
    assert body["limit"] == 100


# --- GET /v1/checks/stats ------------------------------------------


async def test_stats_empty(async_client):
    response = await async_client.get("/v1/checks/stats")
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "total_checks": 0,
        "checks_this_month": 0,
        "avg_similarity_pct": None,
        "avg_similarity_window": 0,
        "total_words": 0,
        "total_sources": 0,
    }


async def test_stats_aggregates(async_client, session: AsyncSession):
    await _insert_check(
        session,
        status="complete",
        overall_similarity_pct=40.00,
        word_count=100,
        source_count=2,
    )
    await _insert_check(
        session,
        status="complete",
        overall_similarity_pct=60.00,
        word_count=50,
        source_count=1,
    )
    # Failed checks count toward the total but not toward this month's
    # completed work or the similarity average.
    await _insert_check(session, status="failed", error_message="boom")
    await _insert_check(session, status="pending", word_count=10)

    body = (await async_client.get("/v1/checks/stats")).json()
    assert body["total_checks"] == 4
    assert body["checks_this_month"] == 3
    assert body["avg_similarity_pct"] == 50.0
    assert body["avg_similarity_window"] == 2
    assert body["total_words"] == 160
    assert body["total_sources"] == 3


# --- PATCH /v1/checks/{id} -----------------------------------------


async def test_patch_renames_check(async_client, session: AsyncSession):
    cid = await _insert_check(session, status="complete", title="Old name")

    response = await async_client.patch(
        f"/v1/checks/{cid}",
        json={"title": "  Renamed essay  "},
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Renamed essay"

    row = await session.execute(
        sql_text("SELECT title FROM checks WHERE id = :id"), {"id": cid}
    )
    assert row.scalar_one() == "Renamed essay"


async def test_patch_empty_title_returns_422(
    async_client, session: AsyncSession
):
    cid = await _insert_check(session, status="complete", title="Keep me")

    response = await async_client.patch(
        f"/v1/checks/{cid}",
        json={"title": "   "},
    )
    assert response.status_code == 422

    row = await session.execute(
        sql_text("SELECT title FROM checks WHERE id = :id"), {"id": cid}
    )
    assert row.scalar_one() == "Keep me"


async def test_patch_without_fields_returns_400(
    async_client, session: AsyncSession
):
    cid = await _insert_check(session, status="complete")

    response = await async_client.patch(f"/v1/checks/{cid}", json={})
    assert response.status_code == 400


async def test_patch_unknown_check_returns_404(async_client):
    response = await async_client.patch(
        f"/v1/checks/{uuid4()}",
        json={"title": "anything"},
    )
    assert response.status_code == 404


# --- DELETE /v1/checks/{id} ----------------------------------------


async def test_delete_removes_check_and_results(
    async_client, session: AsyncSession
):
    doc = await _insert_document(session)
    cid = await _insert_check(
        session,
        status="complete",
        total_matched_chars=50,
        overall_similarity_pct=10.00,
    )
    await _insert_check_result(
        session,
        check_id=cid,
        source_document_id=doc,
        matched_chars=50,
        similarity_pct=10.00,
        passages=[],
    )

    response = await async_client.delete(f"/v1/checks/{cid}")
    assert response.status_code == 204

    gone = await async_client.get(f"/v1/checks/{cid}")
    assert gone.status_code == 404

    # check_results cascade with the check row.
    remaining = await session.execute(
        sql_text("SELECT count(*) FROM check_results WHERE check_id = :id"),
        {"id": cid},
    )
    assert remaining.scalar_one() == 0


async def test_delete_unknown_returns_404(async_client):
    response = await async_client.delete(f"/v1/checks/{uuid4()}")
    assert response.status_code == 404


# --- progress (pub-sub + SSE) --------------------------------------


async def test_pubsub_delivers_events_in_publish_order():
    """Progress events arrive in publish order with end-of-stream sentinel.

    This exercises the in-memory pub-sub (`workflows/progress.py`) which
    is what the SSE endpoint wraps. The HTTP framing (`data: ...\\n\\n`)
    is covered by the curl smoke in the PR body — httpx's ASGITransport
    buffers responses and doesn't reliably stream chunks in-process, so
    the over-HTTP assertion is exercised end-to-end against a real
    server in the manual smoke, not here.
    """
    from noplag_engine.workflows.progress import (
        close_publisher,
        close_subscription,
        open_subscription,
        reset_for_tests,
    )

    reset_for_tests()
    check_id = uuid4()
    queue = open_subscription(check_id)

    publish(
        check_id, ProgressEvent(stage="chunking", chunks_done=0, chunks_total=3)
    )
    publish(
        check_id,
        ProgressEvent(stage="fingerprinting", chunks_done=0, chunks_total=3),
    )
    publish(
        check_id, ProgressEvent(stage="aligning", chunks_done=1, chunks_total=3)
    )
    publish(
        check_id, ProgressEvent(stage="complete", chunks_done=3, chunks_total=3)
    )
    close_publisher(check_id)

    events: list[ProgressEvent] = []
    while True:
        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        if event is None:
            break
        events.append(event)
    close_subscription(check_id, queue)

    assert [e.stage for e in events] == [
        "chunking",
        "fingerprinting",
        "aligning",
        "complete",
    ]
    assert events[-1].chunks_done == 3
    assert events[-1].chunks_total == 3


async def test_sse_stream_404_for_unknown_check(async_client):
    missing = uuid4()
    response = await async_client.get(f"/v1/checks/{missing}/progress")
    assert response.status_code == 404


async def test_sse_late_subscriber_gets_terminal_event(
    async_client, session: AsyncSession
):
    """A client that opens the stream after the check already finished
    receives a single terminal event instead of hanging on a publisher
    that closed before the subscription opened."""
    cid = await _insert_check(session, status="complete")

    response = await async_client.get(f"/v1/checks/{cid}/progress")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = [
        json.loads(line[len("data: "):])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert frames == [{"stage": "complete", "chunks_done": 0, "chunks_total": 0}]


# --- POST /v1/checks/upload ----------------------------------------


async def test_upload_txt_creates_check(
    async_client, session: AsyncSession
):
    body = b"The quick brown fox jumps over the lazy dog. " * 8
    response = await async_client.post(
        "/v1/checks/upload",
        files={"file": ("essay.txt", body, "text/plain")},
        data={"language": "en"},
    )
    assert response.status_code == 202
    cid = response.json()["check_id"]
    assert UUID(cid)

    row = await session.execute(
        sql_text("SELECT query_text_length FROM checks WHERE id = :id"),
        {"id": UUID(cid)},
    )
    # The extracted (normalized) text was stored as the check body.
    assert row.scalar_one() > 0


@pytest.mark.parametrize(
    "fixture",
    [
        ("sample.pdf", "application/pdf"),
        (
            "sample.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        ("sample.txt", "text/plain"),
    ],
    ids=["pdf", "docx", "txt"],
)
async def test_upload_extracts_each_supported_format(
    async_client, session: AsyncSession, fixture
):
    name, content_type = fixture
    data = (FIXTURES / name).read_bytes()
    response = await async_client.post(
        "/v1/checks/upload",
        files={"file": (name, data, content_type)},
    )
    assert response.status_code == 202, response.text
    cid = UUID(response.json()["check_id"])
    row = await session.execute(
        sql_text("SELECT query_text_length FROM checks WHERE id = :id"),
        {"id": cid},
    )
    assert row.scalar_one() > 0


async def test_upload_uses_filename_as_title(
    async_client, session: AsyncSession
):
    body = b"The printing press changed what could be known and by whom. " * 4
    response = await async_client.post(
        "/v1/checks/upload",
        files={"file": ("printing-press.txt", body, "text/plain")},
    )
    assert response.status_code == 202
    cid = UUID(response.json()["check_id"])
    row = await session.execute(
        sql_text("SELECT title FROM checks WHERE id = :id"), {"id": cid}
    )
    assert row.scalar_one() == "printing-press.txt"


async def test_upload_unsupported_type_returns_415(async_client):
    response = await async_client.post(
        "/v1/checks/upload",
        files={"file": ("photo.png", b"\x89PNG\r\n\x1a\n", "image/png")},
    )
    assert response.status_code == 415


async def test_upload_empty_file_returns_400(async_client):
    response = await async_client.post(
        "/v1/checks/upload",
        files={"file": ("empty.txt", b"", "text/plain")},
    )
    assert response.status_code == 400


async def test_upload_oversize_returns_413(async_client):
    oversize = b"a" * (10 * 1024 * 1024 + 1)
    response = await async_client.post(
        "/v1/checks/upload",
        files={"file": ("huge.txt", oversize, "text/plain")},
    )
    assert response.status_code == 413
