"""Integration tests for corpus fingerprinting + matching.

Real Postgres via the `session` fixture. Exercises `fingerprint_document`
(the producer + state machine) and `match_against_corpus` (matching a
stored document against the corpus, excluding itself) directly, plus the
upload→fingerprint loop and dedup through the HTTP surface.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text as sql_text

from noplag_engine.workflows.corpus import (
    fingerprint_document,
    match_against_corpus,
)

# Distinctive prose so winnowing fingerprints are unambiguous.
SOURCE_TEXT = (
    "The aqueduct carried fresh water across sixty kilometres of arid country, "
    "its gradient falling a few centimetres for every hundred metres so the flow "
    "never stalled. Roman engineers cut tunnels through hillsides and raised "
    "arched bridges over valleys to keep the channel almost perfectly level. "
    "Cities that had depended on cisterns and wells suddenly had running water in "
    "public fountains, bathhouses, and private homes."
)


async def _seed_document(session, tenant_id, text, *, status="pending"):
    doc_id = uuid4()
    await session.execute(
        sql_text("INSERT INTO tenants (id, name) VALUES (:t, 'fp') ON CONFLICT DO NOTHING"),
        {"t": tenant_id},
    )
    await session.execute(
        sql_text(
            "INSERT INTO documents "
            "(id, tenant_id, extracted_text, char_length, source_type, "
            " fingerprint_status) "
            "VALUES (:id, :t, :x, :l, "
            " CAST('user_upload' AS corpus_source_type), :s)"
        ),
        {"id": doc_id, "t": tenant_id, "x": text, "l": len(text), "s": status},
    )
    await session.commit()
    return doc_id


async def _status(session, doc_id):
    return (
        await session.execute(
            sql_text("SELECT fingerprint_status FROM documents WHERE id = :id"),
            {"id": doc_id},
        )
    ).scalar_one()


async def _chunk_count(session, doc_id):
    return (
        await session.execute(
            sql_text("SELECT count(*) FROM chunks WHERE document_id = :id"),
            {"id": doc_id},
        )
    ).scalar_one()


# --- fingerprint_document ------------------------------------------


async def test_fingerprint_document_produces_chunks_and_flips_status(session):
    tenant_id = uuid4()
    doc_id = await _seed_document(session, tenant_id, SOURCE_TEXT)

    result = await fingerprint_document(doc_id, session)

    assert result.chunk_count > 0
    assert result.fingerprint_count > 0
    assert await _status(session, doc_id) == "fingerprinted"
    assert await _chunk_count(session, doc_id) == result.chunk_count
    # Language detected and written back (ingestion left it null).
    lang = (
        await session.execute(
            sql_text("SELECT language FROM documents WHERE id = :id"),
            {"id": doc_id},
        )
    ).scalar_one()
    assert lang == "en"


async def test_fingerprint_document_is_idempotent(session):
    tenant_id = uuid4()
    doc_id = await _seed_document(session, tenant_id, SOURCE_TEXT)

    first = await fingerprint_document(doc_id, session)
    second = await fingerprint_document(doc_id, session)

    # Re-run rebuilds cleanly — no duplicate chunks.
    assert second.chunk_count == first.chunk_count
    assert await _chunk_count(session, doc_id) == first.chunk_count


async def test_fingerprint_document_empty_text_marks_failed(session):
    tenant_id = uuid4()
    doc_id = await _seed_document(session, tenant_id, "")
    with pytest.raises(ValueError):
        await fingerprint_document(doc_id, session)
    assert await _status(session, doc_id) == "failed"


# --- match_against_corpus ------------------------------------------


async def test_match_finds_corpus_overlap_and_excludes_self(session):
    tenant_id = uuid4()
    # Corpus document A.
    doc_a = await _seed_document(session, tenant_id, SOURCE_TEXT)
    await fingerprint_document(doc_a, session)

    # Document B embeds a verbatim span of A between original filler.
    span = SOURCE_TEXT[40:280]
    filler = (
        "Here is a passage written entirely in my own words to open the essay. "
    )
    doc_b = await _seed_document(session, tenant_id, filler + span)
    await fingerprint_document(doc_b, session)

    matches = await match_against_corpus(doc_b, session, tenant_id=tenant_id)

    matched_ids = {m.matched_document_id for m in matches}
    assert doc_a in matched_ids
    # Self-exclusion: B must not match itself despite being fingerprinted.
    assert doc_b not in matched_ids
    a_match = next(m for m in matches if m.matched_document_id == doc_a)
    assert a_match.overlap_pct > 0
    # Regions are in B's char offsets, inside the embedded span.
    assert all(start >= len(filler) - 5 for start, _ in a_match.matched_regions)


async def test_match_no_corpus_returns_empty(session):
    tenant_id = uuid4()
    doc_id = await _seed_document(session, tenant_id, SOURCE_TEXT)
    await fingerprint_document(doc_id, session)
    # Only document in the corpus — excluding itself leaves nothing.
    matches = await match_against_corpus(doc_id, session, tenant_id=tenant_id)
    assert matches == []


# --- upload loop + dedup (HTTP surface) ----------------------------


async def test_upload_triggers_background_fingerprinting(
    async_client, tmp_path, monkeypatch
):
    monkeypatch.setenv("CORPUS_STORAGE_DIR", str(tmp_path))
    # A .txt whose content is long/distinctive enough to chunk.
    res = await async_client.post(
        "/v1/corpus/documents",
        files={"file": ("aqueduct.txt", SOURCE_TEXT.encode(), "text/plain")},
    )
    assert res.status_code == 201, res.text
    doc_id = res.json()["id"]

    # FastAPI BackgroundTasks run after the response is sent; by the time
    # the test client returns, the fingerprint pass has run and committed.
    detail = await async_client.get(f"/v1/corpus/documents/{doc_id}")
    assert detail.json()["fingerprint_status"] == "fingerprinted"


async def test_duplicate_upload_is_idempotent(
    async_client, tmp_path, monkeypatch
):
    monkeypatch.setenv("CORPUS_STORAGE_DIR", str(tmp_path))
    payload = {"file": ("dup.txt", SOURCE_TEXT.encode(), "text/plain")}

    first = await async_client.post("/v1/corpus/documents", files=payload)
    assert first.status_code == 201
    second = await async_client.post(
        "/v1/corpus/documents",
        files={"file": ("dup.txt", SOURCE_TEXT.encode(), "text/plain")},
    )
    # Byte-identical re-upload returns the existing document (200), same id.
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
