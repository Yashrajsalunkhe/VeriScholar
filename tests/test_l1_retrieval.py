"""Integration tests for L1 retrieval against a real Postgres.

Requires the docker-compose Postgres stack (or any DB at
`L1_TEST_DATABASE_URL` / the engine/env.py default). The shared
`session` fixture in conftest.py handles migrations + skip-when-
unreachable; this file only needs DB-fixture helpers.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import bindparam
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import BigInteger

from noplag_engine.retrieval import L1Candidate, retrieve_l1_candidates


async def _insert_tenant(session: AsyncSession, name: str = "acct") -> UUID:
    tenant_id = uuid4()
    await session.execute(
        sql_text("INSERT INTO tenants (id, name) VALUES (:id, :name)"),
        {"id": tenant_id, "name": name},
    )
    return tenant_id


async def _insert_document(session: AsyncSession, tenant_id: UUID) -> UUID:
    doc_id = uuid4()
    await session.execute(
        sql_text(
            "INSERT INTO documents (id, tenant_id) "
            "VALUES (:id, :tenant_id)"
        ),
        {"id": doc_id, "tenant_id": tenant_id},
    )
    return doc_id


_INSERT_CHUNK_SQL = sql_text(
    "INSERT INTO chunks "
    "(id, document_id, tenant_id, chunk_index, char_start, char_end, "
    " sentence_count, fingerprints) "
    "VALUES (:id, :document_id, :tenant_id, :chunk_index, 0, :char_end, "
    "        1, :fp)"
).bindparams(bindparam("fp", type_=ARRAY(BigInteger)))


async def _insert_chunk(
    session: AsyncSession,
    *,
    document_id: UUID,
    tenant_id: UUID,
    chunk_index: int,
    fingerprints: list[int],
    body: str = "fixture chunk",
) -> UUID:
    chunk_id = uuid4()
    await session.execute(
        _INSERT_CHUNK_SQL,
        {
            "id": chunk_id,
            "document_id": document_id,
            "tenant_id": tenant_id,
            "chunk_index": chunk_index,
            "char_end": len(body),
            "fp": fingerprints,
        },
    )
    return chunk_id


async def test_empty_query_returns_empty(session: AsyncSession):
    result = await retrieve_l1_candidates([], session)
    assert result == []


async def test_no_matching_chunks_returns_empty(session: AsyncSession):
    tenant = await _insert_tenant(session)
    doc = await _insert_document(session, tenant)
    await _insert_chunk(
        session,
        document_id=doc,
        tenant_id=tenant,
        chunk_index=0,
        fingerprints=[1, 2, 3],
    )
    result = await retrieve_l1_candidates([100, 200, 300], session)
    assert result == []


async def test_single_match_correct_score(session: AsyncSession):
    tenant = await _insert_tenant(session)
    doc = await _insert_document(session, tenant)
    chunk = await _insert_chunk(
        session,
        document_id=doc,
        tenant_id=tenant,
        chunk_index=0,
        fingerprints=[10, 20, 30, 40, 50],
    )
    result = await retrieve_l1_candidates([20, 40, 999], session)
    assert len(result) == 1
    candidate = result[0]
    assert candidate.chunk_id == chunk
    assert candidate.document_id == doc
    assert candidate.score == 2
    assert candidate.fingerprint_count == 5


async def test_multiple_matches_sorted_by_score_desc(session: AsyncSession):
    tenant = await _insert_tenant(session)
    doc = await _insert_document(session, tenant)
    # Three chunks with engineered overlap counts: 5 / 3 / 1.
    high = await _insert_chunk(
        session, document_id=doc, tenant_id=tenant, chunk_index=0,
        fingerprints=[1, 2, 3, 4, 5, 99],
    )
    mid = await _insert_chunk(
        session, document_id=doc, tenant_id=tenant, chunk_index=1,
        fingerprints=[1, 2, 3, 98, 97],
    )
    low = await _insert_chunk(
        session, document_id=doc, tenant_id=tenant, chunk_index=2,
        fingerprints=[1, 96, 95, 94],
    )
    result = await retrieve_l1_candidates([1, 2, 3, 4, 5], session)
    assert [c.chunk_id for c in result] == [high, mid, low]
    assert [c.score for c in result] == [5, 3, 1]


async def test_tiebreak_by_chunk_id_asc(session: AsyncSession):
    tenant = await _insert_tenant(session)
    doc = await _insert_document(session, tenant)
    # Two chunks with identical overlap (3 each); chunk_id ascending wins.
    cid_a = await _insert_chunk(
        session, document_id=doc, tenant_id=tenant, chunk_index=0,
        fingerprints=[1, 2, 3],
    )
    cid_b = await _insert_chunk(
        session, document_id=doc, tenant_id=tenant, chunk_index=1,
        fingerprints=[1, 2, 3],
    )
    result = await retrieve_l1_candidates([1, 2, 3], session)
    assert {c.chunk_id for c in result} == {cid_a, cid_b}
    assert [c.score for c in result] == [3, 3]
    expected_order = sorted([cid_a, cid_b])
    assert [c.chunk_id for c in result] == expected_order


async def test_top_k_caps_result_count(session: AsyncSession):
    tenant = await _insert_tenant(session)
    doc = await _insert_document(session, tenant)
    # Ten chunks, all overlap the query.
    for i in range(10):
        await _insert_chunk(
            session, document_id=doc, tenant_id=tenant, chunk_index=i,
            fingerprints=[1, 2, 3, 4, 5, 1000 + i],
        )
    result = await retrieve_l1_candidates([1, 2, 3, 4, 5], session, top_k=3)
    assert len(result) == 3


async def test_tenant_scoping_filters_other_tenants(session: AsyncSession):
    tenant_a = await _insert_tenant(session, "a")
    tenant_b = await _insert_tenant(session, "b")
    doc_a = await _insert_document(session, tenant_a)
    doc_b = await _insert_document(session, tenant_b)
    chunk_a = await _insert_chunk(
        session, document_id=doc_a, tenant_id=tenant_a, chunk_index=0,
        fingerprints=[1, 2, 3],
    )
    await _insert_chunk(
        session, document_id=doc_b, tenant_id=tenant_b, chunk_index=0,
        fingerprints=[1, 2, 3],
    )

    scoped = await retrieve_l1_candidates([1, 2, 3], session, tenant_id=tenant_a)
    assert len(scoped) == 1
    assert scoped[0].chunk_id == chunk_a

    unscoped = await retrieve_l1_candidates([1, 2, 3], session)
    assert len(unscoped) == 2


async def test_determinism_repeated_calls(session: AsyncSession):
    tenant = await _insert_tenant(session)
    doc = await _insert_document(session, tenant)
    for i in range(5):
        await _insert_chunk(
            session, document_id=doc, tenant_id=tenant, chunk_index=i,
            fingerprints=[1, 2, 3, 1000 + i, 2000 + i],
        )
    query = [1, 2, 3]
    first = await retrieve_l1_candidates(query, session)
    runs = [await retrieve_l1_candidates(query, session) for _ in range(4)]
    assert all(run == first for run in runs)


async def test_score_correctness_engineered_overlaps(session: AsyncSession):
    """Hand-construct three chunks with known intersection sizes 5/0/3."""
    tenant = await _insert_tenant(session)
    doc = await _insert_document(session, tenant)
    query = [10, 20, 30, 40, 50, 60, 70]
    five_overlap = await _insert_chunk(
        session, document_id=doc, tenant_id=tenant, chunk_index=0,
        fingerprints=[10, 20, 30, 40, 50, 8888],
    )
    # Zero-overlap chunk is excluded by `&&` — never reaches Python scoring.
    await _insert_chunk(
        session, document_id=doc, tenant_id=tenant, chunk_index=1,
        fingerprints=[1001, 1002, 1003],
    )
    three_overlap = await _insert_chunk(
        session, document_id=doc, tenant_id=tenant, chunk_index=2,
        fingerprints=[10, 20, 30, 7777, 6666],
    )
    result = await retrieve_l1_candidates(query, session)
    by_id = {c.chunk_id: c for c in result}
    assert by_id[five_overlap].score == 5
    assert by_id[three_overlap].score == 3
    # Two rows returned (zero-overlap chunk filtered by GIN predicate).
    assert len(result) == 2


async def test_l1_candidate_is_frozen(session: AsyncSession):
    cand = L1Candidate(
        chunk_id=uuid4(),
        document_id=uuid4(),
        score=1,
        fingerprint_count=1,
    )
    with pytest.raises(Exception):  # noqa: B017
        cand.score = 99  # type: ignore[misc]
