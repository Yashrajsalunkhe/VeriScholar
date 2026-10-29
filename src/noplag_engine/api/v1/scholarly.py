"""HTTP API for scholarly corpus management.

Endpoints:

- POST   /v1/scholarly/ingest       → start a scholarly ingestion job
- GET    /v1/scholarly/jobs         → list ingestion jobs
- GET    /v1/scholarly/jobs/{id}    → job status and progress
- DELETE /v1/scholarly/jobs/{id}    → cancel a job
- GET    /v1/scholarly/stats        → corpus statistics and coverage
- GET    /v1/scholarly/search       → search the scholarly corpus
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from noplag_engine.api.deps import get_session
from noplag_engine.db import get_sessionmaker
from noplag_engine.scholarly.pipeline import get_corpus_stats, run_scholarly_ingestion
from noplag_engine.vs_config import get_verischolar_config

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Request / Response models ──────────────────────────────────────

class IngestionRequest(BaseModel):
    source: str = Field(
        description="Source API: 'openalex', 'arxiv', 'crossref'",
    )
    query: str | None = Field(
        default=None,
        description="Free-text search query",
    )
    doi: str | None = Field(
        default=None,
        description="Specific DOI to fetch",
    )
    author: str | None = Field(
        default=None,
        description="Author name to search for",
    )
    topic: str | None = Field(
        default=None,
        description="Topic or keywords to search for",
    )
    publication_year: int | None = Field(
        default=None,
        description="Exact publication year",
    )
    from_year: int | None = Field(
        default=None,
        description="Start of publication year range",
    )
    to_year: int | None = Field(
        default=None,
        description="End of publication year range",
    )
    max_results: int = Field(
        default=100,
        ge=1,
        le=10000,
        description="Maximum number of works to process",
    )
    download_full_text: bool = Field(
        default=True,
        description="Download and index full-text PDFs when available",
    )


class IngestionResponse(BaseModel):
    job_id: UUID
    status: str
    message: str


class JobStatus(BaseModel):
    id: UUID
    source: str
    status: str
    total_discovered: int
    total_downloaded: int
    total_indexed: int
    total_skipped: int
    total_failed: int
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class JobListResponse(BaseModel):
    jobs: list[JobStatus]
    total: int


class CorpusStats(BaseModel):
    total_documents: int
    full_text_count: int
    abstract_only_count: int
    metadata_only_count: int
    source_count: int
    indexed_count: int
    pending_count: int
    failed_count: int
    with_doi: int
    earliest_year: int | None
    latest_year: int | None


class ScholarlyDocumentResult(BaseModel):
    id: UUID
    title: str | None
    authors: list | None
    doi: str | None
    publication_year: int | None
    journal: str | None
    source_type: str
    source_url: str | None
    full_text_available: bool
    abstract_preview: str | None
    fingerprint_status: str


class SearchResponse(BaseModel):
    results: list[ScholarlyDocumentResult]
    total: int
    limit: int
    offset: int


# ── Endpoints ──────────────────────────────────────────────────────

@router.post(
    "/ingest",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=IngestionResponse,
)
async def start_ingestion(
    body: IngestionRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> IngestionResponse:
    """Start a scholarly ingestion job.

    The job runs in the background and can be monitored via
    GET /v1/scholarly/jobs/{id}.
    """
    valid_sources = {"openalex", "arxiv", "crossref"}
    if body.source not in valid_sources:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Invalid source '{body.source}'. Must be one of: {valid_sources}",
        )

    if not any([body.query, body.doi, body.author, body.topic]):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "At least one of query, doi, author, or topic must be provided",
        )

    # Create the job row first to return the ID immediately
    import json
    from uuid import uuid4
    job_id = uuid4()
    await session.execute(
        sql_text(
            "INSERT INTO scholarly_ingestion_jobs "
            "(id, source, query_params) "
            "VALUES (:id, :source, CAST(:params AS jsonb))"
        ),
        {
            "id": job_id,
            "source": body.source,
            "params": json.dumps(body.model_dump()),
        },
    )
    await session.commit()

    background_tasks.add_task(
        _run_ingestion_background,
        job_id=job_id,
        source=body.source,
        query=body.query,
        doi=body.doi,
        author=body.author,
        topic=body.topic,
        publication_year=body.publication_year,
        from_year=body.from_year,
        to_year=body.to_year,
        max_results=body.max_results,
        download_full_text=body.download_full_text,
    )

    return IngestionResponse(
        job_id=job_id,
        status="pending",
        message=f"Ingestion job started for {body.source}",
    )


async def _run_ingestion_background(
    *,
    job_id: UUID,
    source: str,
    query: str | None,
    doi: str | None,
    author: str | None,
    topic: str | None,
    publication_year: int | None,
    from_year: int | None,
    to_year: int | None,
    max_results: int,
    download_full_text: bool,
) -> None:
    """Background task for scholarly ingestion."""
    cfg = get_verischolar_config()
    try:
        async with get_sessionmaker()() as session:
            await run_scholarly_ingestion(
                session,
                source=source,
                query=query,
                doi=doi,
                author=author,
                topic=topic,
                publication_year=publication_year,
                from_year=from_year,
                to_year=to_year,
                max_results=max_results,
                download_full_text=download_full_text,
                config=cfg.scholarly,
                job_id=job_id,
            )
    except Exception:
        logger.exception("Scholarly ingestion job %s failed", job_id)


@router.get("/jobs", response_model=JobListResponse)
async def list_jobs(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> JobListResponse:
    """List scholarly ingestion jobs, newest first."""
    total = (
        await session.execute(
            sql_text("SELECT count(*) FROM scholarly_ingestion_jobs")
        )
    ).scalar_one()

    rows = (
        await session.execute(
            sql_text(
                "SELECT id, source, status, total_discovered, "
                "  total_downloaded, total_indexed, total_skipped, "
                "  total_failed, error_message, started_at, "
                "  completed_at, created_at "
                "FROM scholarly_ingestion_jobs "
                "ORDER BY created_at DESC "
                "LIMIT :limit OFFSET :offset"
            ),
            {"limit": limit, "offset": offset},
        )
    ).all()

    jobs = [
        JobStatus(
            id=r.id, source=r.source, status=r.status,
            total_discovered=r.total_discovered,
            total_downloaded=r.total_downloaded,
            total_indexed=r.total_indexed,
            total_skipped=r.total_skipped,
            total_failed=r.total_failed,
            error_message=r.error_message,
            started_at=r.started_at,
            completed_at=r.completed_at,
            created_at=r.created_at,
        )
        for r in rows
    ]
    return JobListResponse(jobs=jobs, total=total)


@router.get("/jobs/{job_id}", response_model=JobStatus)
async def get_job(
    job_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> JobStatus:
    """Get status and progress for an ingestion job."""
    row = (
        await session.execute(
            sql_text(
                "SELECT id, source, status, total_discovered, "
                "  total_downloaded, total_indexed, total_skipped, "
                "  total_failed, error_message, started_at, "
                "  completed_at, created_at "
                "FROM scholarly_ingestion_jobs WHERE id = :id"
            ),
            {"id": job_id},
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    return JobStatus(
        id=row.id, source=row.source, status=row.status,
        total_discovered=row.total_discovered,
        total_downloaded=row.total_downloaded,
        total_indexed=row.total_indexed,
        total_skipped=row.total_skipped,
        total_failed=row.total_failed,
        error_message=row.error_message,
        started_at=row.started_at,
        completed_at=row.completed_at,
        created_at=row.created_at,
    )


@router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_job(
    job_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Cancel a running ingestion job."""
    result = await session.execute(
        sql_text(
            "UPDATE scholarly_ingestion_jobs "
            "SET status = 'cancelled' "
            "WHERE id = :id AND status IN ('pending', 'running')"
        ),
        {"id": job_id},
    )
    if result.rowcount == 0:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "job not found or not cancellable",
        )
    await session.commit()


@router.get("/stats", response_model=CorpusStats)
async def corpus_statistics(
    session: AsyncSession = Depends(get_session),
) -> CorpusStats:
    """Get corpus statistics and coverage metrics."""
    stats = await get_corpus_stats(session)
    return CorpusStats(**stats)


@router.get("/search", response_model=SearchResponse)
async def search_corpus(
    query: str | None = Query(default=None),
    doi: str | None = Query(default=None),
    author: str | None = Query(default=None),
    year: int | None = Query(default=None),
    source_type: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> SearchResponse:
    """Search the scholarly corpus by metadata."""
    conditions = ["1=1"]
    params: dict = {"limit": limit, "offset": offset}

    if query:
        conditions.append(
            "(filename ILIKE :q OR doi ILIKE :q OR abstract ILIKE :q)"
        )
        params["q"] = f"%{query}%"
    if doi:
        conditions.append("doi = :doi")
        params["doi"] = doi
    if year:
        conditions.append("publication_year = :year")
        params["year"] = year
    if source_type:
        conditions.append("source_type = CAST(:st AS corpus_source_type)")
        params["st"] = source_type

    where = " AND ".join(conditions)

    total = (
        await session.execute(
            sql_text(f"SELECT count(*) FROM documents WHERE {where}"),
            params,
        )
    ).scalar_one()

    rows = (
        await session.execute(
            sql_text(
                f"SELECT id, filename, authors, doi, publication_year, "
                f"  journal, source_type, source_url, full_text_available, "
                f"  left(abstract, 200) as abstract_preview, "
                f"  fingerprint_status "
                f"FROM documents WHERE {where} "
                f"ORDER BY publication_year DESC NULLS LAST, created_at DESC "
                f"LIMIT :limit OFFSET :offset"
            ),
            params,
        )
    ).all()

    results = [
        ScholarlyDocumentResult(
            id=r.id,
            title=r.filename,
            authors=r.authors if isinstance(r.authors, list) else None,
            doi=r.doi,
            publication_year=r.publication_year,
            journal=r.journal,
            source_type=r.source_type,
            source_url=r.source_url,
            full_text_available=r.full_text_available,
            abstract_preview=r.abstract_preview,
            fingerprint_status=r.fingerprint_status,
        )
        for r in rows
    ]
    return SearchResponse(
        results=results, total=total, limit=limit, offset=offset
    )
