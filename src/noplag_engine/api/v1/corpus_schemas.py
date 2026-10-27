"""Pydantic request/response models for the /v1/corpus API."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class CorpusDocument(BaseModel):
    """Metadata for one ingested corpus document (no extracted text).

    Returned by the create endpoint and in list rows. A freshly ingested
    document is 'pending' until the fingerprint pass chunks and indexes it.
    """

    id: UUID
    source_type: str
    filename: str | None
    mime_type: str | None
    size_bytes: int | None
    sha256: str | None
    char_length: int | None
    language: str | None
    fingerprint_status: str
    created_at: datetime
    updated_at: datetime


class CorpusDocumentDetail(CorpusDocument):
    """A corpus document plus its full normalized extracted text."""

    extracted_text: str | None


class CorpusDocumentListResponse(BaseModel):
    documents: list[CorpusDocument]
    total: int
    limit: int
    offset: int
