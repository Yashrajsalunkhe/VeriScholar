"""Crossref API client for DOI resolution and publication metadata.

Crossref is the primary DOI registration agency, covering most published
academic works.  Used for:
- DOI → metadata resolution (title, authors, journal, year)
- Enriching records discovered from other sources
- Discovering works by title search when DOI is unknown

Rate limits: 50 req/s with polite email in Mailto header.
Reference: https://api.crossref.org
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

logger = logging.getLogger("noplag.scholarly.crossref")

_BASE = "https://api.crossref.org"


class CrossrefClient(BaseScholarlyClient):
    """Client for the Crossref REST API."""

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
        return "Crossref"

    def _default_headers(self) -> dict[str, str]:
        headers = super()._default_headers()
        if self._polite_email:
            headers["Mailto"] = self._polite_email
        return headers

    async def search_works(
        self,
        *,
        query: str | None = None,
        doi: str | None = None,
        author: str | None = None,
        from_year: int | None = None,
        to_year: int | None = None,
        rows: int = 50,
        offset: int = 0,
    ) -> tuple[list[ScholarlyMetadata], int]:
        """Search Crossref works. Returns (results, total_count)."""
        if doi:
            # Direct DOI lookup
            result = await self.get_work_by_doi(doi)
            return ([result] if result else [], 1 if result else 0)

        params: dict[str, Any] = {
            "rows": min(rows, 1000),
            "offset": offset,
        }
        if query:
            params["query"] = query
        if author:
            params["query.author"] = author
        if from_year:
            params["filter"] = f"from-pub-date:{from_year}"
        if to_year:
            existing = params.get("filter", "")
            params["filter"] = (
                f"{existing},until-pub-date:{to_year}"
                if existing else f"until-pub-date:{to_year}"
            )

        data = await self._get(f"{_BASE}/works", params=params)
        msg = data.get("message", {})
        total = msg.get("total-results", 0)
        items = msg.get("items", [])

        results = [_parse_item(item) for item in items]
        return results, total

    async def get_work_by_doi(self, doi: str) -> ScholarlyMetadata | None:
        """Resolve a DOI to its metadata."""
        try:
            data = await self._get(f"{_BASE}/works/{doi.strip()}")
            return _parse_item(data.get("message", {}))
        except Exception:
            logger.debug("Crossref work not found for DOI %s", doi)
            return None


def _parse_item(item: dict[str, Any]) -> ScholarlyMetadata:
    """Convert a Crossref work item to ScholarlyMetadata."""
    doi = item.get("DOI")

    # Authors
    authors = []
    for author in item.get("author", []):
        given = author.get("given", "")
        family = author.get("family", "")
        name = f"{given} {family}".strip()
        if name:
            authors.append(name)

    # Abstract — Crossref sometimes includes it with JATS XML tags
    abstract_raw = item.get("abstract", "")
    # Strip common JATS XML wrappers
    abstract = abstract_raw
    if abstract:
        import re
        abstract = re.sub(r"<[^>]+>", "", abstract).strip()

    # Publication year
    pub_date = item.get("published-print") or item.get("published-online") or {}
    date_parts = pub_date.get("date-parts", [[]])
    year = date_parts[0][0] if date_parts and date_parts[0] else None

    # Journal
    container = item.get("container-title", [])
    journal = container[0] if container else None

    # License
    licenses = item.get("license", [])
    license_id = licenses[0].get("URL", "") if licenses else None

    # Source URL
    source_url = item.get("URL") or (f"https://doi.org/{doi}" if doi else "")

    return ScholarlyMetadata(
        doi=doi,
        title=item.get("title", [None])[0] if item.get("title") else None,
        authors=authors,
        abstract=abstract or None,
        publication_year=year,
        journal=journal,
        publisher=item.get("publisher"),
        source=ScholarlySource.CROSSREF,
        source_url=source_url,
        license_id=license_id,
        is_open_access=bool(licenses),
        availability=(
            AvailabilityStatus.ABSTRACT_ONLY if abstract
            else AvailabilityStatus.METADATA_ONLY
        ),
    )
