"""Pydantic request/response models for the /v1/checks API."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CheckCreateRequest(BaseModel):
    query_text: str = Field(
        min_length=1,
        max_length=500_000,
        description=(
            "Plain text of the document to check. Capped at 500,000 "
            "characters (~80,000 words, longer than a doctoral thesis); "
            "documents above the cap should be split or submitted via "
            "the file-upload endpoint."
        ),
    )
    language: str = Field(
        default="en",
        description=(
            "ISO 639-1 language code for sentence segmentation. Passed "
            "through to pysbd in the chunking stage. Supported values "
            'include "en", "es", "de", "fr", "it", "pt", "ru", "ja", '
            '"zh". Wrong-language segmentation degrades chunk quality '
            "and downstream detection scores."
        ),
    )


class CheckCreateResponse(BaseModel):
    check_id: UUID
    status_url: str
    progress_url: str
    report_url: str


class CheckUpdateRequest(BaseModel):
    # Partial update — the handler applies only the fields actually sent
    # (Pydantic's model_fields_set). Rename the check; trimmed, empty/
    # whitespace rejected.
    title: str | None = Field(default=None, max_length=200)


class CheckStatusResponse(BaseModel):
    id: UUID
    status: str
    # Coarse in-flight pipeline phase for the progress poller: chunking /
    # fingerprinting / retrieving / aligning / assembling. Null before the
    # pipeline starts; the durable lifecycle is `status`.
    stage: str | None = None
    # Human title, defaulted from content on create + editable.
    title: str | None = None
    query_text_length: int
    total_matched_chars: int | None
    overall_similarity_pct: float | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class AlignedPassageJSON(BaseModel):
    query_chunk_id: UUID
    candidate_chunk_id: UUID
    # `query_*` are the target offsets (into the submitted document);
    # `candidate_*` are the source offsets (into the matched source's
    # chunk).
    query_start: int
    query_end: int
    candidate_start: int
    candidate_end: int
    score: float
    # Short excerpt of the matched source text for this region, sliced
    # from the candidate chunk. Null when the candidate chunk is no
    # longer present (hand-seeded test rows, re-fingerprinted corpus).
    # UIs show it so a source reads as text, not a bare id.
    overlap_text_preview: str | None = None
    # Match kind. Always "verbatim" for the L0/L1 cascade.
    match_type: str = "verbatim"


class MatchedSourceJSON(BaseModel):
    source_document_id: UUID
    matched_chars: int
    similarity_pct: float
    passages: list[AlignedPassageJSON]
    # Original filename of the matched corpus document, so the source list
    # is human-readable. Null for sources without a stored filename.
    source_filename: str | None = None
    # Origin URL of the matched corpus document. For web-sourced corpus
    # rows this is the real page URL — the source the user actually wants
    # to see and click. Null for uploads / rows without a URL. UIs prefer
    # this over source_filename when present.
    source_url: str | None = None


class CheckReportResponse(BaseModel):
    # Human title, defaulted from content + editable.
    title: str | None = None
    query_text_length: int
    # Persisted submission body. The report renders highlights against
    # this string.
    query_text: str | None
    total_matched_chars: int
    overall_similarity_pct: float
    # Whitespace-split token count of the submission.
    word_count: int | None = None
    sources: list[MatchedSourceJSON]
    unique_passages: list[tuple[int, int]]
    # Coverage signal. "full" unless the per-check time budget stopped
    # retrieval early on a very large document, in which case "partial" +
    # coverage_reason ("time_cap") and the checked/total_chunks counts. A
    # partial check still returns every match it found; UIs show a "scan
    # may not be exhaustive" note, never an error.
    coverage: str = "full"
    coverage_reason: str | None = None
    checked_chunks: int | None = None
    total_chunks: int | None = None
    # Wall-clock the check took (created -> completed), surfaced so a
    # report can show an honest "check time" — especially on a no-match
    # result, where a 20s scan that found nothing must not read as "0s /
    # didn't run". Null if completed_at is missing (in-flight rows).
    duration_seconds: float | None = None
    # Size of the corpus the check ran against (document count, estimated).
    # Lets a no-match card say "scanned across N sources" instead of
    # "0 sources", which otherwise reads as "compared against nothing".
    corpus_sources: int | None = None


# Row-level shape for the GET /v1/checks list endpoint. Mirrors
# CheckStatusResponse but trims the fields a list row doesn't render
# (error_message, updated_at) and adds query_text_preview — a
# server-truncated head of the body so a list can show a snippet
# without paging the whole document over the wire.
class CheckListItem(BaseModel):
    id: UUID
    status: str
    title: str | None = None
    query_text_length: int
    # Whitespace-split token count of the submission.
    word_count: int | None = None
    # Number of matched sources. Null until the check completes.
    source_count: int | None = None
    query_text_preview: str | None
    total_matched_chars: int | None
    overall_similarity_pct: float | None
    created_at: datetime
    completed_at: datetime | None


class CheckListResponse(BaseModel):
    checks: list[CheckListItem]
    total: int
    limit: int
    offset: int


class CheckStatsResponse(BaseModel):
    """Aggregates for dashboard-style surfaces.

    `avg_similarity_pct` is null when no completed check has a score yet.
    """

    total_checks: int
    checks_this_month: int
    avg_similarity_pct: float | None
    avg_similarity_window: int
    total_words: int
    total_sources: int


class ProgressEventJSON(BaseModel):
    stage: str = Field(
        description=(
            "Pipeline stage. In-flight values: 'chunking', "
            "'fingerprinting', 'retrieving', 'aligning', 'assembling'. "
            "Terminal values: 'complete' (success), 'failed' (the "
            "pipeline raised; poll /v1/checks/{id} for error_message). "
            "Late subscribers — clients that open the SSE stream after "
            "the check already reached a terminal state — receive a "
            "single event with that terminal stage and the stream "
            "closes immediately."
        ),
    )
    chunks_done: int
    chunks_total: int
