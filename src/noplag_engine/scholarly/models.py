"""Shared data models for the scholarly ingestion pipeline.

These dataclasses carry scholarly metadata through the pipeline stages
(discovery → download → dedup → ingest) and are persisted into the
``documents`` table's new scholarly columns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ScholarlySource(str, Enum):
    """Scholarly data sources — maps to corpus_source_type enum values."""
    OPENALEX = "openalex"
    ARXIV = "arxiv"
    CORE = "core"
    PUBMED = "pubmed"
    CROSSREF = "crossref"


class AvailabilityStatus(str, Enum):
    """Full-text availability for a scholarly record."""
    FULL_TEXT = "full_text"       # Full text retrieved and indexed
    ABSTRACT_ONLY = "abstract"    # Only abstract available
    METADATA_ONLY = "metadata"    # Only bibliographic metadata
    RESTRICTED = "restricted"     # Behind paywall / not open access
    PENDING = "pending"           # Download not yet attempted
    FAILED = "failed"             # Download attempted but failed


@dataclass
class ScholarlyMetadata:
    """Metadata for a single scholarly work, normalized across sources.

    This is the pipeline's intermediate representation — every API client
    produces these, and the ingest layer consumes them.  Fields are
    nullable because not every source provides every field.
    """
    # Identifiers
    doi: str | None = None
    openalex_id: str | None = None
    arxiv_id: str | None = None
    pmcid: str | None = None

    # Bibliographic metadata
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    abstract: str | None = None
    publication_year: int | None = None
    journal: str | None = None
    publisher: str | None = None

    # Source and access
    source: ScholarlySource | None = None
    source_url: str | None = None
    pdf_url: str | None = None
    license_id: str | None = None
    is_open_access: bool = False

    # Full text
    full_text: str | None = None
    full_text_available: bool = False
    availability: AvailabilityStatus = AvailabilityStatus.PENDING

    # Provenance
    raw_api_response: dict | None = None

    def has_identifier(self) -> bool:
        """True if the record has at least one persistent identifier."""
        return any([self.doi, self.openalex_id, self.arxiv_id, self.pmcid])

    def normalized_title(self) -> str | None:
        """Case-folded, whitespace-collapsed title for fuzzy dedup."""
        if not self.title:
            return None
        return " ".join(self.title.lower().split())

    def primary_identifier(self) -> str:
        """Best available identifier string for logging / display."""
        return self.doi or self.openalex_id or self.arxiv_id or self.pmcid or "(no id)"


@dataclass
class IngestionProgress:
    """Counters for a running ingestion job."""
    discovered: int = 0
    downloaded: int = 0
    indexed: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
