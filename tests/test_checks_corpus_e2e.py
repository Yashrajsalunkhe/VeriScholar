"""End-to-end check → corpus match through the real pipeline.

The report tests in test_checks_api.py seed `check_results` by hand. This
drives the actual flow: a fingerprinted corpus document + a real
`POST /v1/checks` submission whose text overlaps it, then asserts the
report surfaces the corpus document as a matched source. This is the
proof that the matching pipeline is wired into the user-facing check
flow end to end.
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from noplag_engine.api.deps import DEFAULT_TENANT_ID
from noplag_engine.workflows.corpus import fingerprint_document

SOURCE_TEXT = (
    "The printing press did not merely speed the copying of books; it changed "
    "what could be known and by whom. A scribe working alone might finish one "
    "manuscript in a season, while a single press turned out hundreds of "
    "identical sheets in an afternoon. Literacy stopped being the privilege of "
    "cloistered specialists and spread into workshops, markets, and homes, and "
    "ideas began to outrun the authorities that had once contained them."
)


async def _seed_fingerprinted_corpus_doc(session: AsyncSession, tenant_id):
    doc_id = uuid4()
    await session.execute(
        sql_text(
            "INSERT INTO tenants (id, name) VALUES (:t, 'e2e') "
            "ON CONFLICT DO NOTHING"
        ),
        {"t": tenant_id},
    )
    await session.execute(
        sql_text(
            "INSERT INTO documents "
            "(id, tenant_id, filename, extracted_text, char_length, source_type) "
            "VALUES (:id, :t, :f, :x, :l, "
            " CAST('user_upload' AS corpus_source_type))"
        ),
        {
            "id": doc_id,
            "t": tenant_id,
            "f": "printing-press.txt",
            "x": SOURCE_TEXT,
            "l": len(SOURCE_TEXT),
        },
    )
    await session.commit()
    await fingerprint_document(doc_id, session)
    return doc_id


async def test_check_against_fingerprinted_corpus_surfaces_matches(
    async_client, session: AsyncSession
):
    corpus_doc = await _seed_fingerprinted_corpus_doc(
        session, DEFAULT_TENANT_ID
    )

    # Submit a check whose body embeds a verbatim span of the corpus doc
    # between original filler.
    span = SOURCE_TEXT[60:340]
    filler = "Here is an original opening paragraph in my own words for the essay. "
    query_text = filler + span

    submit = await async_client.post(
        "/v1/checks",
        json={"query_text": query_text, "language": "en"},
    )
    assert submit.status_code == 202, submit.text
    cid = submit.json()["check_id"]

    # FastAPI BackgroundTasks run before the test client returns, so the
    # pipeline has completed and persisted by now.
    status = await async_client.get(f"/v1/checks/{cid}")
    assert status.json()["status"] == "complete", status.text

    report = await async_client.get(f"/v1/checks/{cid}/report")
    assert report.status_code == 200
    body = report.json()

    # Real, non-placeholder similarity against the corpus.
    assert body["overall_similarity_pct"] > 0
    source_ids = {s["source_document_id"] for s in body["sources"]}
    assert str(corpus_doc) in source_ids
    # Matched regions index into the submitted query text, inside the span.
    matched = next(
        s for s in body["sources"] if s["source_document_id"] == str(corpus_doc)
    )
    assert matched["passages"]
    assert all(p["query_start"] >= len(filler) - 5 for p in matched["passages"])
    # Enrichment: the source list reads as text, not a bare id.
    assert matched["source_filename"] == "printing-press.txt"
    assert any(p["overlap_text_preview"] for p in matched["passages"])


async def test_check_with_no_corpus_overlap_reports_zero(
    async_client, session: AsyncSession
):
    await _seed_fingerprinted_corpus_doc(session, DEFAULT_TENANT_ID)

    submit = await async_client.post(
        "/v1/checks",
        json={
            "query_text": (
                "An entirely unrelated paragraph about tide pools and the small "
                "creatures that shelter in them when the sea withdraws each day."
            ),
            "language": "en",
        },
    )
    cid = submit.json()["check_id"]
    report = await async_client.get(f"/v1/checks/{cid}/report")
    body = report.json()
    assert body["overall_similarity_pct"] == 0
    assert body["sources"] == []
