"""Scholarly ingestion pipeline orchestrator.

Coordinates the discover → dedup → download → ingest → fingerprint flow
across all scholarly API sources.  The pipeline is:

1. **Discover** — query a scholarly API (OpenAlex, arXiv, Crossref) for
   works matching the user's search criteria.
2. **Dedup** — skip works that already exist in the corpus (by DOI,
   source ID, or normalized title + year).
3. **Download** — fetch full text (PDF or plain text) when available and
   permitted by the applicable licence.
4. **Ingest** — insert the document into the ``documents`` table with
   scholarly metadata, extract text, and run the fingerprint pass.
5. **Track** — update the ``scholarly_ingestion_jobs`` table with
   progress counters and cursor state for resumability.

The pipeline is incremental and resumable: interrupted jobs store their
cursor state and can be restarted from where they left off.
"""

from __future__ import annotations

import hashlib
import logging
from uuid import UUID, uuid4

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from noplag_engine.ingestion.extract import extract_text
from noplag_engine.ingestion.normalize import normalize_text
from noplag_engine.scholarly.arxiv import ArxivClient
from noplag_engine.scholarly.crossref import CrossrefClient
from noplag_engine.scholarly.dedup import is_duplicate, normalize_doi
from noplag_engine.scholarly.models import (
    AvailabilityStatus,
    IngestionProgress,
    ScholarlyMetadata,
)
from noplag_engine.scholarly.openalex import OpenAlexClient
from noplag_engine.vs_config import ScholarlyAPIConfig
from noplag_engine.workflows.corpus import fingerprint_document

logger = logging.getLogger("noplag.scholarly.pipeline")


async def run_scholarly_ingestion(
    session: AsyncSession,
    *,
    source: str,
    query: str | None = None,
    doi: str | None = None,
    author: str | None = None,
    topic: str | None = None,
    publication_year: int | None = None,
    from_year: int | None = None,
    to_year: int | None = None,
    max_results: int = 100,
    download_full_text: bool = True,
    config: ScholarlyAPIConfig | None = None,
    job_id: UUID | None = None,
) -> IngestionProgress:
    """Run a scholarly ingestion job.

    Parameters
    ----------
    source : str
        Source to query: "openalex", "arxiv", "crossref".
    query : str | None
        Free-text search query.
    max_results : int
        Maximum number of works to process (for bounded jobs).
    download_full_text : bool
        If True, attempt to download and index full-text PDFs.
    job_id : UUID | None
        Existing job ID to resume, or None to create a new job.

    Returns
    -------
    IngestionProgress
        Counters for the completed job.
    """
    cfg = config or ScholarlyAPIConfig()
    progress = IngestionProgress()

    # Create or resume the tracking job
    if job_id is None:
        job_id = await _create_job(
            session, source=source,
            query_params={
                "query": query, "doi": doi, "author": author,
                "topic": topic, "publication_year": publication_year,
                "from_year": from_year, "to_year": to_year,
                "max_results": max_results,
            },
        )

    await _update_job_status(session, job_id, "running")

    try:
        # Phase 1: Discover works from the source
        works = await _discover(
            source=source, query=query, doi=doi, author=author,
            topic=topic, publication_year=publication_year,
            from_year=from_year, to_year=to_year,
            max_results=max_results, config=cfg,
        )
        progress.discovered = len(works)
        logger.info(
            "Discovered %d works from %s (query=%s)",
            len(works), source, query,
        )
        await _update_job_progress(session, job_id, progress)

        # Phase 2-4: Dedup, download, ingest each work
        for meta in works:
            try:
                # Normalize DOI
                meta.doi = normalize_doi(meta.doi)

                # Dedup check
                if await is_duplicate(session, meta):
                    progress.skipped += 1
                    continue

                # Download full text if available and requested
                if download_full_text and meta.pdf_url and meta.is_open_access:
                    await _download_full_text(meta, source, cfg)
                    if meta.full_text:
                        progress.downloaded += 1

                # Ingest into the corpus
                doc_id = await _ingest_scholarly_document(session, meta)
                if doc_id:
                    progress.indexed += 1
                    # Fingerprint for matching
                    try:
                        lang = "en"  # Scholarly papers are predominantly English
                        await fingerprint_document(
                            doc_id, session, language=lang
                        )
                    except Exception as exc:
                        logger.warning(
                            "Fingerprinting failed for %s: %s",
                            meta.primary_identifier(), exc,
                        )
                else:
                    progress.skipped += 1

            except Exception as exc:
                progress.failed += 1
                progress.errors.append(
                    f"{meta.primary_identifier()}: {exc!s}"
                )
                logger.warning(
                    "Failed to ingest %s: %s",
                    meta.primary_identifier(), exc,
                )

            # Periodic progress update
            total = progress.downloaded + progress.indexed + progress.skipped + progress.failed
            if total % 10 == 0:
                await _update_job_progress(session, job_id, progress)

        await _update_job_status(session, job_id, "completed")
        await _update_job_progress(session, job_id, progress)

    except Exception as exc:
        logger.exception("Ingestion job %s failed", job_id)
        await _update_job_status(
            session, job_id, "failed", error_message=str(exc)
        )
        raise

    logger.info(
        "Ingestion complete: discovered=%d downloaded=%d indexed=%d "
        "skipped=%d failed=%d",
        progress.discovered, progress.downloaded, progress.indexed,
        progress.skipped, progress.failed,
    )
    return progress


async def _discover(
    *,
    source: str,
    query: str | None,
    doi: str | None,
    author: str | None,
    topic: str | None,
    publication_year: int | None,
    from_year: int | None,
    to_year: int | None,
    max_results: int,
    config: ScholarlyAPIConfig,
) -> list[ScholarlyMetadata]:
    """Discover works from the specified source."""
    works: list[ScholarlyMetadata] = []

    if source == "openalex":
        client = OpenAlexClient(
            polite_email=config.openalex_email,
            max_per_second=config.openalex_max_per_second,
        )
        try:
            cursor = "*"
            while len(works) < max_results:
                batch, next_cursor, _total = await client.search_works(
                    query=query, doi=doi, author=author, topic=topic,
                    publication_year=publication_year,
                    from_year=from_year, to_year=to_year,
                    per_page=min(50, max_results - len(works)),
                    cursor=cursor,
                )
                works.extend(batch)
                if not next_cursor or not batch:
                    break
                cursor = next_cursor
        finally:
            await client.close()

    elif source == "arxiv":
        client = ArxivClient(
            max_per_second=config.arxiv_max_per_second,
        )
        try:
            results, _total = await client.search(
                query=query, arxiv_id=doi, author=author,
                title=topic, max_results=min(max_results, 200),
            )
            works.extend(results)
        finally:
            await client.close()

    elif source == "crossref":
        client = CrossrefClient(
            polite_email=config.crossref_email,
            max_per_second=config.crossref_max_per_second,
        )
        try:
            offset = 0
            while len(works) < max_results:
                batch, _total = await client.search_works(
                    query=query, doi=doi, author=author,
                    from_year=from_year, to_year=to_year,
                    rows=min(50, max_results - len(works)),
                    offset=offset,
                )
                works.extend(batch)
                if len(batch) < 50:
                    break
                offset += len(batch)
        finally:
            await client.close()

    else:
        raise ValueError(f"Unknown source: {source}")

    return works[:max_results]


async def _download_full_text(
    meta: ScholarlyMetadata,
    source: str,
    config: ScholarlyAPIConfig,
) -> None:
    """Attempt to download full text for a scholarly work."""
    if not meta.pdf_url:
        return

    try:
        if source == "arxiv" and meta.arxiv_id:
            client = ArxivClient(max_per_second=config.arxiv_max_per_second)
            try:
                pdf_bytes = await client.download_pdf(meta.arxiv_id)
                if pdf_bytes:
                    text = extract_text(pdf_bytes, "application/pdf")
                    meta.full_text = normalize_text(text)
                    meta.full_text_available = True
                    meta.availability = AvailabilityStatus.FULL_TEXT
            finally:
                await client.close()
        else:
            # Generic PDF download for other OA sources
            import httpx
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as http:
                resp = await http.get(meta.pdf_url)
                if resp.status_code == 200:
                    text = extract_text(resp.content, "application/pdf")
                    meta.full_text = normalize_text(text)
                    meta.full_text_available = True
                    meta.availability = AvailabilityStatus.FULL_TEXT

    except Exception as exc:
        logger.debug(
            "Full-text download failed for %s: %s",
            meta.primary_identifier(), exc,
        )
        meta.availability = AvailabilityStatus.ABSTRACT_ONLY if meta.abstract else AvailabilityStatus.METADATA_ONLY


async def _ingest_scholarly_document(
    session: AsyncSession,
    meta: ScholarlyMetadata,
) -> UUID | None:
    """Insert a scholarly document into the corpus.

    Returns the document ID on success, or None if the insert was
    skipped (e.g. ON CONFLICT dedup).
    """
    import json

    # Determine the text to index — full text if available, else abstract
    text_to_index = meta.full_text or meta.abstract or ""
    if not text_to_index.strip():
        logger.debug(
            "Skipping %s: no indexable text", meta.primary_identifier()
        )
        return None

    doc_id = uuid4()
    sha256 = hashlib.sha256(text_to_index.encode("utf-8")).hexdigest()

    source_type = meta.source.value if meta.source else "openalex"
    filename = meta.title[:200] if meta.title else meta.primary_identifier()

    try:
        row = (
            await session.execute(
                sql_text(
                    "INSERT INTO documents "
                    "(id, tenant_id, source_type, source_url, filename, "
                    " extracted_text, char_length, sha256, language, "
                    " doi, authors, abstract, publication_year, journal, "
                    " license_id, full_text_available, openalex_id, "
                    " arxiv_id, pmcid) "
                    "VALUES (:id, NULL, "
                    " CAST(:source_type AS corpus_source_type), :source_url, "
                    " :filename, :extracted_text, :char_length, :sha256, "
                    " 'en', :doi, CAST(:authors AS jsonb), :abstract, "
                    " :pub_year, :journal, :license_id, :full_text_available, "
                    " :openalex_id, :arxiv_id, :pmcid) "
                    "ON CONFLICT (doi) WHERE doi IS NOT NULL DO NOTHING "
                    "RETURNING id"
                ),
                {
                    "id": doc_id,
                    "source_type": source_type,
                    "source_url": meta.source_url or "",
                    "filename": filename,
                    "extracted_text": text_to_index,
                    "char_length": len(text_to_index),
                    "sha256": sha256,
                    "doi": meta.doi,
                    "authors": json.dumps(meta.authors),
                    "abstract": meta.abstract,
                    "pub_year": meta.publication_year,
                    "journal": meta.journal,
                    "license_id": meta.license_id,
                    "full_text_available": meta.full_text_available,
                    "openalex_id": meta.openalex_id,
                    "arxiv_id": meta.arxiv_id,
                    "pmcid": meta.pmcid,
                },
            )
        ).one_or_none()
        await session.commit()
        return row.id if row is not None else None

    except Exception:
        await session.rollback()
        raise


# ── Job tracking helpers ────────────────────────────────────────────

async def _create_job(
    session: AsyncSession,
    *,
    source: str,
    query_params: dict,
) -> UUID:
    import json
    job_id = uuid4()
    await session.execute(
        sql_text(
            "INSERT INTO scholarly_ingestion_jobs "
            "(id, source, query_params) "
            "VALUES (:id, :source, CAST(:params AS jsonb))"
        ),
        {"id": job_id, "source": source, "params": json.dumps(query_params)},
    )
    await session.commit()
    return job_id


async def _update_job_status(
    session: AsyncSession,
    job_id: UUID,
    status: str,
    *,
    error_message: str | None = None,
) -> None:
    if status == "running":
        await session.execute(
            sql_text(
                "UPDATE scholarly_ingestion_jobs "
                "SET status = :s, started_at = now() WHERE id = :id"
            ),
            {"id": job_id, "s": status},
        )
    elif status in ("completed", "failed"):
        await session.execute(
            sql_text(
                "UPDATE scholarly_ingestion_jobs "
                "SET status = :s, completed_at = now(), "
                "    error_message = :err WHERE id = :id"
            ),
            {"id": job_id, "s": status, "err": error_message},
        )
    else:
        await session.execute(
            sql_text(
                "UPDATE scholarly_ingestion_jobs SET status = :s WHERE id = :id"
            ),
            {"id": job_id, "s": status},
        )
    await session.commit()


async def _update_job_progress(
    session: AsyncSession,
    job_id: UUID,
    progress: IngestionProgress,
) -> None:
    await session.execute(
        sql_text(
            "UPDATE scholarly_ingestion_jobs SET "
            "  total_discovered = :d, total_downloaded = :dl, "
            "  total_indexed = :i, total_skipped = :s, "
            "  total_failed = :f WHERE id = :id"
        ),
        {
            "id": job_id,
            "d": progress.discovered,
            "dl": progress.downloaded,
            "i": progress.indexed,
            "s": progress.skipped,
            "f": progress.failed,
        },
    )
    await session.commit()


async def get_corpus_stats(session: AsyncSession) -> dict:
    """Return corpus statistics for the dashboard."""
    row = (
        await session.execute(
            sql_text(
                "SELECT "
                "  count(*) AS total_documents, "
                "  count(*) FILTER (WHERE full_text_available) AS full_text_count, "
                "  count(*) FILTER (WHERE abstract IS NOT NULL AND NOT full_text_available) AS abstract_only_count, "
                "  count(*) FILTER (WHERE abstract IS NULL AND NOT full_text_available) AS metadata_only_count, "
                "  count(DISTINCT source_type) AS source_count, "
                "  count(*) FILTER (WHERE fingerprint_status = 'fingerprinted') AS indexed_count, "
                "  count(*) FILTER (WHERE fingerprint_status = 'pending') AS pending_count, "
                "  count(*) FILTER (WHERE fingerprint_status = 'failed') AS failed_count, "
                "  count(*) FILTER (WHERE doi IS NOT NULL) AS with_doi, "
                "  min(publication_year) AS earliest_year, "
                "  max(publication_year) AS latest_year "
                "FROM documents"
            )
        )
    ).one()
    return {
        "total_documents": row.total_documents,
        "full_text_count": row.full_text_count,
        "abstract_only_count": row.abstract_only_count,
        "metadata_only_count": row.metadata_only_count,
        "source_count": row.source_count,
        "indexed_count": row.indexed_count,
        "pending_count": row.pending_count,
        "failed_count": row.failed_count,
        "with_doi": row.with_doi,
        "earliest_year": row.earliest_year,
        "latest_year": row.latest_year,
    }
