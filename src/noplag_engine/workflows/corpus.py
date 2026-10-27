"""Corpus-side orchestration: fingerprint a stored document, match it.

Ingestion lands an uploaded document in `documents` with
`extracted_text` populated and `fingerprint_status='pending'`, but nothing
consumed those rows — the check pipeline (`run_check`) matches *against*
the `chunks` table, yet nothing produced corpus chunks from a document.
This module closes that loop.

`fingerprint_document` is the producer: it reads `extracted_text`, detects
the language, chunks it, winnows each chunk, and writes `chunks` rows
(text + `fingerprints bigint[]`, GIN-indexed) — the exact shape
`retrieve_l1_candidates` queries. It owns the `fingerprint_status` state
machine:

    (ingestion) ──set 'pending'──▶ pending
    pending ──fingerprint_document success──▶ fingerprinted
    pending ──fingerprint_document raises──▶ failed
    fingerprinted ──re-run (re-upload / re-index)──▶ fingerprinted
                    (chunks for the document are deleted first, so a
                     re-run is idempotent — never duplicate chunks)

`match_against_corpus` runs a stored document's text back through the
query pipeline (`run_check`), excluding the document's own chunks, and
reshapes the report into corpus matches.

Both use `get_fingerprint_config()` so the corpus producer and the query
matcher winnow with identical (k, w) — a hard correctness requirement,
since fingerprints only match when both sides agree.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from noplag_engine.chunking import chunk_document
from noplag_engine.config import get_fingerprint_config
from noplag_engine.fingerprinting import fingerprint
from noplag_engine.ingestion.language import detect_language, pysbd_language
from noplag_engine.intervals import merge_intervals
from noplag_engine.workflows.check import run_check


@dataclass(frozen=True)
class FingerprintResult:
    document_id: UUID
    chunk_count: int
    fingerprint_count: int
    language: str


@dataclass(frozen=True)
class CorpusMatch:
    matched_document_id: UUID
    overlap_pct: float
    # Half-open [start, end) char ranges in the *queried* document's
    # extracted_text, merged and non-overlapping. The report UI renders
    # these as highlights.
    matched_regions: list[tuple[int, int]]


_INSERT_CHUNK = sql_text(
    "INSERT INTO chunks "
    "(id, document_id, tenant_id, chunk_index, char_start, char_end, "
    " sentence_count, fingerprints) "
    "VALUES (:id, :document_id, :tenant_id, :chunk_index, :char_start, "
    " :char_end, :sentence_count, :fingerprints)"
)

# Chunk text lives in the chunks_text side table so the chunks heap stays
# narrow. Written in the same transaction as the chunk row; tenant_id mirrors
# chunks (NULL for platform corpus).
_INSERT_CHUNK_TEXT = sql_text(
    "INSERT INTO chunks_text (chunk_id, tenant_id, text) "
    "VALUES (:id, :tenant_id, :text)"
)


async def ingest_platform_document(
    session: AsyncSession,
    *,
    source_type: str,
    source_url: str,
    language: str,
    filename: str,
    extracted_text: str,
    commit: bool = True,
) -> UUID | None:
    """Insert one platform-level (tenant_id NULL) corpus document.

    The non-HTTP ingestion entry point for bulk corpus sources — the
    `/v1/corpus/documents` upload endpoint is HTTP-bound and sized for
    single files, so the sample-corpus loader and the folder ingester use
    this instead. The row lands `fingerprint_status='pending'`; the caller
    fingerprints it (passing `language` as the known-language hint).

    Idempotent: `ON CONFLICT (source_type, source_url)` returns None when
    the document already exists, so a re-run skips it.

    `commit=False` leaves the transaction open so the caller owns the
    boundary — batched ingests group many documents per commit and wrap
    each in a savepoint for isolation.
    """
    doc_id = uuid4()
    row = (
        await session.execute(
            sql_text(
                "INSERT INTO documents "
                "(id, tenant_id, source_type, source_url, "
                " language, filename, extracted_text, char_length, sha256) "
                "VALUES (:id, NULL, "
                " CAST(:source_type AS corpus_source_type), :source_url, "
                " :language, :filename, :extracted_text, :char_length, :sha256) "
                "ON CONFLICT (source_type, source_url) "
                "WHERE source_url IS NOT NULL DO NOTHING "
                "RETURNING id"
            ),
            {
                "id": doc_id,
                "source_type": source_type,
                "source_url": source_url,
                "language": language,
                "filename": filename,
                "extracted_text": extracted_text,
                "char_length": len(extracted_text),
                "sha256": hashlib.sha256(extracted_text.encode("utf-8")).hexdigest(),
            },
        )
    ).one_or_none()
    if commit:
        await session.commit()
    return row.id if row is not None else None


async def fingerprint_document(
    document_id: UUID,
    session: AsyncSession,
    *,
    language: str | None = None,
    commit: bool = True,
) -> FingerprintResult:
    """Chunk + winnow a 'pending' document into `chunks`, flip its status.

    Idempotent: any existing chunks for the document are deleted first, so
    a re-run (re-upload, re-index after a config change) rebuilds cleanly
    rather than duplicating. On any failure the document is marked
    'failed' and the exception re-raised so the caller (background task /
    eval harness) can log it.

    `language` is an optional ISO 639-1 hint: when the source language is
    known (e.g. an English Wikipedia sample), pass it to skip per-document
    detection — at corpus scale that's a meaningful CPU saving. When None,
    the language is detected from the text (the upload path).

    This function does no tenant scoping of its own; it operates on the
    one document it's given.

    `commit=False` makes the function transaction-neutral: it neither
    commits on success nor runs the rollback / mark-'failed' recovery on
    error — it just raises and leaves cleanup to the caller (used by
    batched ingests, which isolate each document in a savepoint and commit
    a whole batch at once).
    """
    row = (
        await session.execute(
            sql_text(
                "SELECT tenant_id, extracted_text FROM documents WHERE id = :id"
            ),
            {"id": document_id},
        )
    ).one_or_none()
    if row is None:
        raise ValueError(f"document {document_id} not found")
    if not row.extracted_text:
        # Nothing to fingerprint — record the terminal failure rather than
        # silently leaving the row 'pending' forever.
        if commit:
            await _set_status(session, document_id, "failed")
            await session.commit()
        raise ValueError(f"document {document_id} has no extracted_text")

    try:
        cfg = get_fingerprint_config()
        if language is None:
            language = detect_language(row.extracted_text)
        chunks = chunk_document(
            row.extracted_text,
            sentences_per_chunk=cfg.sentences_per_chunk,
            overlap=cfg.chunk_overlap,
            language=pysbd_language(language),
        )

        # Idempotent rebuild: clear any prior chunks before re-inserting.
        await session.execute(
            sql_text("DELETE FROM chunks WHERE document_id = :id"),
            {"id": document_id},
        )

        total_fps = 0
        for chunk in chunks:
            fps = fingerprint(chunk.text, k=cfg.k, w=cfg.w)
            total_fps += len(fps)
            chunk_id = uuid4()
            await session.execute(
                _INSERT_CHUNK,
                {
                    "id": chunk_id,
                    "document_id": document_id,
                    "tenant_id": row.tenant_id,
                    "chunk_index": chunk.chunk_index,
                    "char_start": chunk.char_start,
                    "char_end": chunk.char_end,
                    "sentence_count": chunk.sentence_count,
                    "fingerprints": fps,
                },
            )
            await session.execute(
                _INSERT_CHUNK_TEXT,
                {"id": chunk_id, "tenant_id": row.tenant_id, "text": chunk.text},
            )

        await session.execute(
            sql_text(
                "UPDATE documents "
                "SET fingerprint_status = 'fingerprinted', language = :lang "
                "WHERE id = :id"
            ),
            {"id": document_id, "lang": language},
        )
        if commit:
            await session.commit()
    except Exception:
        # commit=False: the caller owns the transaction (savepoint) — just
        # re-raise and let it roll back; don't touch the session here.
        if not commit:
            raise
        await session.rollback()
        await _set_status(session, document_id, "failed")
        await session.commit()
        raise

    return FingerprintResult(
        document_id=document_id,
        chunk_count=len(chunks),
        fingerprint_count=total_fps,
        language=language,
    )


async def match_against_corpus(
    document_id: UUID,
    session: AsyncSession,
    *,
    tenant_id: UUID | None = None,
) -> list[CorpusMatch]:
    """Match a stored document's text against the corpus, excluding itself.

    Reuses the query pipeline (`run_check`) on the document's
    `extracted_text`, with `exclude_document_id` set so the document's own
    fingerprinted chunks don't produce a 100% self-match. Returns one
    `CorpusMatch` per matched source document, sorted by overlap
    descending.
    """
    row = (
        await session.execute(
            sql_text("SELECT extracted_text FROM documents WHERE id = :id"),
            {"id": document_id},
        )
    ).one_or_none()
    if row is None:
        raise ValueError(f"document {document_id} not found")
    if not row.extracted_text:
        return []

    report = await run_check(
        row.extracted_text,
        session,
        tenant_id=tenant_id,
        exclude_document_id=document_id,
    )
    return [
        CorpusMatch(
            matched_document_id=source.source_document_id,
            overlap_pct=source.similarity_pct,
            matched_regions=merge_intervals(
                [(p.query_start, p.query_end) for p in source.passages]
            ),
        )
        for source in report.sources
    ]


async def _set_status(
    session: AsyncSession, document_id: UUID, status: str
) -> None:
    await session.execute(
        sql_text(
            "UPDATE documents SET fingerprint_status = :s WHERE id = :id"
        ),
        {"id": document_id, "s": status},
    )
