"""CORE API client for open-access full-text retrieval.

CORE (https://core.ac.uk) aggregates open-access research from
thousands of repositories worldwide.  Used for:
- Full-text retrieval when OpenAlex/arXiv lack it
- Harvesting from institutional repositories
- Discovering OA versions of paywalled papers

Rate limits: 10 req/s without key, higher with API key.
Reference: https://api.core.ac.uk/docs/v3
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

logger = logging.getLogger("noplag.scholarly.core")

_BASE = "https://api.core.ac.uk/v3"


class COREClient(BaseScholarlyClient):
    """Client for the CORE API v3."""

    def __init__(
        self,
        *,
        api_key: str = "",
        max_per_second: float = 5.0,
    ) -> None:
        super().__init__(max_per_second=max_per_second)
        self._api_key = api_key

    @property
    def source_name(self) -> str:
        return "CORE"

    def _default_headers(self) -> dict[str, str]:
        headers = super()._default_headers()
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def search_works(
        self,
        *,
        query: str | None = None,
        doi: str | None = None,
        year: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ScholarlyMetadata], int]:
        """Search CORE for open-access works. Returns (results, total)."""
        if doi:
            result = await self.get_by_doi(doi)
            return ([result] if result else [], 1 if result else 0)

        q = query or "*"
        params: dict[str, Any] = {
            "q": q,
            "limit": min(limit, 100),
            "offset": offset,
        }
        if year:
            params["q"] = f"{q} AND yearPublished:{year}"

        data = await self._get(f"{_BASE}/search/works", params=params)
        total = data.get("totalHits", 0)
        results = [_parse_work(w) for w in data.get("results", [])]
        return results, total

    async def get_by_doi(self, doi: str) -> ScholarlyMetadata | None:
        """Fetch a work by DOI."""
        try:
            data = await self._get(
                f"{_BASE}/search/works",
                params={"q": f"doi:{doi.strip()}", "limit": 1},
            )
            items = data.get("results", [])
            return _parse_work(items[0]) if items else None
        except Exception:
            logger.debug("CORE work not found for DOI %s", doi)
            return None

    async def get_full_text(self, core_id: str) -> str | None:
        """Download full text for a CORE work by its ID."""
        try:
            data = await self._get(f"{_BASE}/outputs/{core_id}")
            return data.get("fullText")
        except Exception:
            logger.debug("CORE full text not available for %s", core_id)
            return None


def _parse_work(work: dict[str, Any]) -> ScholarlyMetadata:
    """Convert a CORE work JSON to ScholarlyMetadata."""
    doi = None
    for ident in work.get("identifiers", []):
        if isinstance(ident, str) and ident.startswith("10."):
            doi = ident
            break

    authors = []
    for a in work.get("authors", []):
        name = a.get("name") if isinstance(a, dict) else str(a)
        if name:
            authors.append(name)

    abstract = work.get("abstract", "")
    full_text = work.get("fullText")

    has_full_text = bool(full_text and len(full_text) > 100)
    availability = (
        AvailabilityStatus.FULL_TEXT if has_full_text
        else AvailabilityStatus.ABSTRACT_ONLY if abstract
        else AvailabilityStatus.METADATA_ONLY
    )

    return ScholarlyMetadata(
        doi=doi,
        title=work.get("title"),
        authors=authors,
        abstract=abstract or None,
        publication_year=work.get("yearPublished"),
        journal=work.get("publisher"),
        source=ScholarlySource.CORE,
        source_url=work.get("downloadUrl") or work.get("sourceFulltextUrls", [None])[0] if work.get("sourceFulltextUrls") else None,
        license_id=work.get("license"),
        is_open_access=True,  # CORE only indexes OA content
        full_text=full_text if has_full_text else None,
        full_text_available=has_full_text,
        availability=availability,
    )
