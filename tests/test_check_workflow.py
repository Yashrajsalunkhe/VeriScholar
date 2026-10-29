"""End-to-end check workflow tests against the shared Postgres fixture."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import bindparam
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import BigInteger

from noplag_engine.chunking import chunk_document
from noplag_engine.fingerprinting import fingerprint
from noplag_engine.workflows import CheckReport, run_check

_INSERT_CHUNK_SQL = sql_text(
    "INSERT INTO chunks "
    "(id, document_id, tenant_id, chunk_index, char_start, char_end, "
    " sentence_count, fingerprints) "
    "VALUES (:id, :document_id, :tenant_id, :chunk_index, :char_start, "
    "        :char_end, :sentence_count, :fp)"
).bindparams(bindparam("fp", type_=ARRAY(BigInteger)))

# Chunk text lives in the chunks_text side table (the chunks heap stays
# narrow); the aligner reads candidate text from here.
_INSERT_CHUNK_TEXT_SQL = sql_text(
    "INSERT INTO chunks_text (chunk_id, tenant_id, text) "
    "VALUES (:id, :tenant_id, :text)"
)


async def _insert_tenant(session: AsyncSession, name: str = "acct") -> UUID:
    tid = uuid4()
    await session.execute(
        sql_text("INSERT INTO tenants (id, name) VALUES (:id, :name)"),
        {"id": tid, "name": name},
    )
    return tid


async def _ingest_document(
    session: AsyncSession,
    tenant_id: UUID,
    text: str,
) -> UUID:
    """Insert a document + chunk it + fingerprint each chunk + persist.

    Mirrors the production ingestion path (the real path runs uploads
    through document conversion; this is a test helper that bypasses it).
    """
    doc_id = uuid4()
    await session.execute(
        sql_text(
            "INSERT INTO documents (id, tenant_id, char_length) "
            "VALUES (:id, :tenant_id, :char_length)"
        ),
        {"id": doc_id, "tenant_id": tenant_id, "char_length": len(text)},
    )
    for chunk in chunk_document(text):
        fps = fingerprint(chunk.text)
        chunk_id = uuid4()
        await session.execute(
            _INSERT_CHUNK_SQL,
            {
                "id": chunk_id,
                "document_id": doc_id,
                "tenant_id": tenant_id,
                "chunk_index": chunk.chunk_index,
                "char_start": chunk.char_start,
                "char_end": chunk.char_end,
                "sentence_count": chunk.sentence_count,
                "fp": fps,
            },
        )
        await session.execute(
            _INSERT_CHUNK_TEXT_SQL,
            {"id": chunk_id, "tenant_id": tenant_id, "text": chunk.text},
        )
    return doc_id


_PASSAGE = (
    "The rain in Spain falls mainly on the plain. The plain is wide and "
    "flat, stretching from the mountains to the sea under an enormous sky. "
    "Farmers have worked the soil here for centuries, growing wheat and "
    "olives and grapes in long terraced rows that follow the gentle slopes. "
    "Every spring the wildflowers bloom in sheets of yellow and red, and "
    "every autumn the harvest brings villages together for festivals. "
    "Children run barefoot through the fields chasing each other and the "
    "shadows of clouds that drift across the bright midday sun."
)


async def test_empty_query_returns_zero_report(session: AsyncSession):
    report = await run_check("", session)
    assert report.query_text_length == 0
    assert report.total_matched_chars == 0
    assert report.overall_similarity_pct == 0.0
    assert report.sources == []
    assert report.unique_passages == []


async def test_no_matches_in_db_returns_empty_sources(session: AsyncSession):
    # DB is empty (truncated by the fixture). Any query yields no matches.
    report = await run_check(_PASSAGE, session)
    assert report.query_text_length == len(_PASSAGE)
    assert report.total_matched_chars == 0
    assert report.overall_similarity_pct == 0.0
    assert report.sources == []
    assert report.unique_passages == []


async def test_exact_match_yields_one_source_high_similarity(session: AsyncSession):
    tenant = await _insert_tenant(session)
    doc_id = await _ingest_document(session, tenant, _PASSAGE)

    report = await run_check(_PASSAGE, session, tenant_id=tenant)

    assert len(report.sources) == 1
    source = report.sources[0]
    assert source.source_document_id == doc_id
    assert source.matched_chars > 0
    # Per-source matched_chars merges overlapping query intervals before
    # summing, so similarity_pct is bounded by 100% even when adjacent
    # query chunks both attribute to the same source (sentence-overlap
    # region counts once).
    assert source.similarity_pct >= 90.0
    assert source.similarity_pct <= 100.0
    assert 0.0 < report.overall_similarity_pct <= 100.0
    assert report.overall_similarity_pct >= 80.0
    assert report.total_matched_chars > 0
    assert len(report.unique_passages) >= 1


async def test_two_distinct_sources_appear_sorted_desc(session: AsyncSession):
    tenant = await _insert_tenant(session)
    passage_a = _PASSAGE
    passage_b = (
        "Lorem ipsum dolor sit amet consectetur adipiscing elit sed do "
        "eiusmod tempor incididunt ut labore et dolore magna aliqua. "
        "Ut enim ad minim veniam quis nostrud exercitation ullamco laboris "
        "nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in "
        "reprehenderit in voluptate velit esse cillum dolore eu fugiat "
        "nulla pariatur excepteur sint occaecat cupidatat non proident sunt "
        "in culpa qui officia deserunt mollit anim id est laborum."
    )
    doc_a = await _ingest_document(session, tenant, passage_a)
    doc_b = await _ingest_document(session, tenant, passage_b)

    # Bias the query toward passage_a (more of it) so we can assert order.
    query = passage_a + "  " + passage_b[: len(passage_b) // 2]

    report = await run_check(query, session, tenant_id=tenant)

    assert len(report.sources) == 2
    assert {s.source_document_id for s in report.sources} == {doc_a, doc_b}
    # Sorted by similarity_pct descending — passage_a dominates the query.
    assert report.sources[0].source_document_id == doc_a
    assert report.sources[0].similarity_pct >= report.sources[1].similarity_pct
    assert all(s.passages for s in report.sources)


async def test_cross_source_dedupe_counts_overlap_once(session: AsyncSession):
    tenant = await _insert_tenant(session)
    # Two distinct source documents, both containing the same passage.
    doc_a = await _ingest_document(session, tenant, "alpha lead-in " + _PASSAGE)
    doc_b = await _ingest_document(session, tenant, "bravo lead-in " + _PASSAGE)

    report = await run_check(_PASSAGE, session, tenant_id=tenant)

    # Both sources should match the query.
    assert {s.source_document_id for s in report.sources} == {doc_a, doc_b}
    # Per-source matched_chars roughly equals the query length for each
    # (the shared passage covers the whole query). The cross-source
    # deduped total must NOT be the sum — overlap counts once.
    raw_sum = sum(s.matched_chars for s in report.sources)
    assert report.total_matched_chars < raw_sum
    # Deduped total can't exceed the query length.
    assert report.total_matched_chars <= report.query_text_length
    # Each per-source similarity stays bounded by 100% under the
    # within-source dedupe — even though both sources cover the whole
    # query, neither claims more than 100%.
    for source in report.sources:
        assert source.similarity_pct <= 100.0
    # unique_passages collapse the duplicated coverage into one set of
    # intervals — there are fewer of them than per-source passage counts.
    total_per_source_passages = sum(len(s.passages) for s in report.sources)
    assert 1 <= len(report.unique_passages) < total_per_source_passages


async def test_tenant_scoping_isolates_other_tenants(session: AsyncSession):
    tenant_a = await _insert_tenant(session, "a")
    tenant_b = await _insert_tenant(session, "b")
    doc_a = await _ingest_document(session, tenant_a, _PASSAGE)

    # Querying as tenant B sees nothing.
    report_b = await run_check(_PASSAGE, session, tenant_id=tenant_b)
    assert report_b.sources == []
    assert report_b.total_matched_chars == 0

    # Querying as tenant A sees doc_a.
    report_a = await run_check(_PASSAGE, session, tenant_id=tenant_a)
    assert len(report_a.sources) == 1
    assert report_a.sources[0].source_document_id == doc_a


async def test_determinism_repeated_calls(session: AsyncSession):
    tenant = await _insert_tenant(session)
    await _ingest_document(session, tenant, _PASSAGE)

    first = await run_check(_PASSAGE, session, tenant_id=tenant)
    runs = [
        await run_check(_PASSAGE, session, tenant_id=tenant)
        for _ in range(3)
    ]
    assert all(run == first for run in runs)


async def test_check_report_is_frozen():
    report = CheckReport(
        query_text_length=100,
        total_matched_chars=0,
        overall_similarity_pct=0.0,
        sources=[],
        unique_passages=[],
    )
    with pytest.raises(Exception):  # noqa: B017
        report.query_text_length = 999  # type: ignore[misc]


# ── L2 fuzzy layer (near-verbatim / light paraphrase) ──────────────────

# A source passage and a lightly-paraphrased version of it: synonym swaps
# and reordering, same meaning. The paraphrase still shares winnowing
# fingerprints with the source (so L1 retrieves it) but has no 30-char exact
# run (so seed-extend alone reports nothing). This is exactly the case the
# fuzzy layer exists to catch.
_FUZZY_SOURCE = (
    "Climate change poses a significant threat to global biodiversity, "
    "altering habitats and disrupting the delicate ecological balance that "
    "species depend upon for their continued survival across many regions. "
    "Rising temperatures push organisms toward the poles and up mountainsides, "
    "and those that cannot move or adapt quickly enough face local extinction."
)
# Reworded so that no 30-char exact run survives anywhere (seed-extend returns
# nothing) yet 24 winnowing fingerprints still overlap (L1 surfaces the source):
# the fuzzy layer is the *only* thing that can attribute this match.
_FUZZY_QUERY = (
    "Shifts in the climate pose a serious danger to biodiversity across the "
    "globe, reshaping habitats and upsetting the fragile ecological balance on "
    "which species rely for their lasting survival in many regions. Warming "
    "temperatures nudge organisms poleward and to higher elevations, and any "
    "that cannot relocate or adjust fast enough will meet local extinction."
)


async def test_fuzzy_layer_catches_paraphrase(session: AsyncSession):
    """A paraphrase that seed-extend misses entirely is caught by the fuzzy layer.

    The query shares winnowing fingerprints with the source (L1 retrieves it)
    but contains no long exact run, so the verbatim seed-extend stage produces
    nothing. Only the fuzzy layer can attribute the match.
    """
    from noplag_engine.config import FingerprintConfig

    tenant = await _insert_tenant(session)
    doc_id = await _ingest_document(session, tenant, _FUZZY_SOURCE)

    # Explicit threshold so the test is independent of the default's tuning.
    cfg = FingerprintConfig(fuzzy_enabled=True, fuzzy_threshold=0.35)
    report = await run_check(_FUZZY_QUERY, session, tenant_id=tenant, config=cfg)

    assert len(report.sources) == 1
    source = report.sources[0]
    assert source.source_document_id == doc_id
    # The match is attributed via fuzzy passages — there are no verbatim ones.
    match_types = {p.match_type for p in source.passages}
    assert match_types == {"fuzzy"}
    assert source.matched_chars > 0
    assert report.overall_similarity_pct > 0.0


async def test_fuzzy_disabled_reproduces_exact_only(session: AsyncSession):
    """With the fuzzy layer off, the same paraphrase yields no match —
    confirming the layer is what adds the recall, and that it can be
    switched off to recover the original exact-only behaviour."""
    from noplag_engine.config import FingerprintConfig

    tenant = await _insert_tenant(session)
    await _ingest_document(session, tenant, _FUZZY_SOURCE)

    cfg = FingerprintConfig(fuzzy_enabled=False)
    report = await run_check(_FUZZY_QUERY, session, tenant_id=tenant, config=cfg)

    assert report.sources == []
    assert report.total_matched_chars == 0


async def test_verbatim_copy_not_double_flagged_by_fuzzy(session: AsyncSession):
    """An exact copy is reported once, as verbatim — the fuzzy pass skips
    candidates seed-extend already covered, so no duplicate fuzzy passage."""
    tenant = await _insert_tenant(session)
    await _ingest_document(session, tenant, _FUZZY_SOURCE)

    report = await run_check(_FUZZY_SOURCE, session, tenant_id=tenant)

    assert len(report.sources) == 1
    source = report.sources[0]
    assert source.similarity_pct >= 90.0
    # Every chunk matched verbatim, so nothing should be left for fuzzy to
    # re-flag on the same source.
    assert all(p.match_type == "verbatim" for p in source.passages)
