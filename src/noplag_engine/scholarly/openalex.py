"""OpenAlex API client for scholarly discovery and metadata.

OpenAlex (https://openalex.org) is prioritized as the primary discovery
source because it is:
- Freely accessible with no API key required
- Covers 250M+ works with DOIs, abstracts, and open-access metadata
- Provides structured author, institution, and topic data
- Links to full-text sources (arXiv, PMC, publisher OA pages)

Rate limits: 10 req/s unauthenticated, 100 req/s with polite email.
Passing ``polite_email`` adds the ``mailto:`` to the User-Agent header
for the polite pool (recommended by OpenAlex).

Reference: https://docs.openalex.org
"""

from __future__ import annotations

import logging
from typing import Any

from noplag_engine.scholarly.client import BaseScholarlyClient
from noplag_engine.scholarly.models import (
    AvailabilityStatus,
    ScholarlyMetadata,
    ScholarlySource,
)

logger = logging.getLogger("noplag.scholarly.openalex")

_BASE = "https://api.openalex.org"


class OpenAlexClient(BaseScholarlyClient):
    """Client for the OpenAlex REST API."""

    def __init__(
        self,
        *,
        polite_email: str = "",
        max_per_second: float = 10.0,
    ) -> None:
        super().__init__(
            max_per_second=max_per_second,
            polite_email=polite_email,
        )

    @property
    def source_name(self) -> str:
        return "OpenAlex"

    async def search_works(
        self,
        *,
        query: str | None = None,
        doi: str | None = None,
        author: str | None = None,
        topic: str | None = None,
        publication_year: int | None = None,
        from_year: int | None = None,
        to_year: int | None = None,
        is_open_access: bool | None = None,
        per_page: int = 50,
        cursor: str = "*",
    ) -> tuple[list[ScholarlyMetadata], str | None, int]:
        """Search OpenAlex works. Returns (results, next_cursor, total_count).

        Uses cursor-based pagination for efficient iteration over large
        result sets.  ``cursor="*"`` starts from the beginning.
        """
        params: dict[str, Any] = {
            "per_page": min(per_page, 200),
            "cursor": cursor,
        }

        # Build filter string
        filters: list[str] = []
        if doi:
            filters.append(f"doi:https://doi.org/{doi.strip()}")
        if publication_year:
            filters.append(f"publication_year:{publication_year}")
        if from_year:
            filters.append(f"from_publication_date:{from_year}-01-01")
        if to_year:
            filters.append(f"to_publication_date:{to_year}-12-31")
        if is_open_access is not None:
            filters.append(f"is_oa:{'true' if is_open_access else 'false'}")

        if filters:
            params["filter"] = ",".join(filters)
        if query:
            params["search"] = query
        if author:
            params["filter"] = params.get("filter", "") + (
                f",authorships.author.display_name.search:{author}"
                if "filter" in params else
                f"authorships.author.display_name.search:{author}"
            )

        data = await self._get(f"{_BASE}/works", params=params)
        total = data.get("meta", {}).get("count", 0)
        next_cursor = data.get("meta", {}).get("next_cursor")

        results = [
            _parse_work(work) for work in data.get("results", [])
        ]
        return results, next_cursor, total

    async def get_work_by_doi(self, doi: str) -> ScholarlyMetadata | None:
        """Fetch a single work by DOI."""
        try:
            data = await self._get(
                f"{_BASE}/works/https://doi.org/{doi.strip()}"
            )
            return _parse_work(data)
        except Exception:
            logger.debug("OpenAlex work not found for DOI %s", doi)
            return None

    async def get_work_by_id(self, openalex_id: str) -> ScholarlyMetadata | None:
        """Fetch a single work by OpenAlex ID (e.g. W2741809807)."""
        try:
            data = await self._get(f"{_BASE}/works/{openalex_id}")
            return _parse_work(data)
        except Exception:
            return None


def _parse_work(work: dict[str, Any]) -> ScholarlyMetadata:
    """Convert an OpenAlex work JSON to ScholarlyMetadata."""
    # Extract DOI (strip the https://doi.org/ prefix)
    doi_raw = work.get("doi") or ""
    doi = doi_raw.replace("https://doi.org/", "").strip() or None

    # Extract authors
    authors = []
    for authorship in work.get("authorships", []):
        author_obj = authorship.get("author", {})
        name = author_obj.get("display_name")
        if name:
            authors.append(name)

    # Extract abstract from inverted index (OpenAlex stores abstracts
    # as inverted indexes to save bandwidth)
    abstract = _reconstruct_abstract(work.get("abstract_inverted_index"))

    # Determine best open-access URL for full text
    oa_info = work.get("open_access", {})
    pdf_url = oa_info.get("oa_url")

    # Best available source URL
    primary_location = work.get("primary_location", {}) or {}
    source_url = (
        primary_location.get("landing_page_url")
        or work.get("doi")
        or ""
    )

    # License
    license_raw = (
        primary_location.get("license")
        or work.get("license")
        or ""
    )

    # Journal / venue
    source = primary_location.get("source", {}) or {}
    journal = source.get("display_name")

    # OpenAlex ID
    openalex_id = work.get("id", "").replace("https://openalex.org/", "")

    # Determine availability
    is_oa = oa_info.get("is_oa", False)
    availability = (
        AvailabilityStatus.PENDING if is_oa and pdf_url
        else AvailabilityStatus.ABSTRACT_ONLY if abstract
        else AvailabilityStatus.METADATA_ONLY
    )

    return ScholarlyMetadata(
        doi=doi,
        openalex_id=openalex_id or None,
        title=work.get("title"),
        authors=authors,
        abstract=abstract,
        publication_year=work.get("publication_year"),
        journal=journal,
        source=ScholarlySource.OPENALEX,
        source_url=source_url,
        pdf_url=pdf_url,
        license_id=license_raw or None,
        is_open_access=is_oa,
        full_text_available=False,  # set True after successful download
        availability=availability,
    )


def _reconstruct_abstract(
    inverted_index: dict[str, list[int]] | None,
) -> str | None:
    """Reconstruct abstract text from OpenAlex's inverted index format.

    OpenAlex stores abstracts as ``{word: [positions]}`` to reduce
    bandwidth.  We reconstruct the original text by sorting tokens by
    position.
    """
    if not inverted_index:
        return None
    # Build (position, word) pairs and sort by position
    pairs: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        for pos in positions:
            pairs.append((pos, word))
    if not pairs:
        return None
    pairs.sort(key=lambda x: x[0])
    return " ".join(word for _, word in pairs)
