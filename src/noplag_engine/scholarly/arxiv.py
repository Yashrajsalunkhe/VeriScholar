"""arXiv API client for preprint metadata and full-text access.

arXiv is prioritized because:
- Preprints are freely accessible under arXiv's license terms
- Full LaTeX source and PDF are available for most papers
- Covers physics, math, CS, biology, and other STEM fields
- API is free with reasonable rate limits (1 req/3s recommended)

The client downloads PDFs for text extraction (via the existing extract
pipeline) rather than parsing LaTeX source, which is simpler and
consistent with the engine's PDF extraction path.

Reference: https://info.arxiv.org/help/api/
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from typing import Any

from noplag_engine.scholarly.client import BaseScholarlyClient
from noplag_engine.scholarly.models import (
    AvailabilityStatus,
    ScholarlyMetadata,
    ScholarlySource,
)

logger = logging.getLogger("noplag.scholarly.arxiv")

_BASE = "http://export.arxiv.org/api/query"
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
}


class ArxivClient(BaseScholarlyClient):
    """Client for the arXiv API (Atom feed)."""

    def __init__(
        self,
        *,
        max_per_second: float = 0.33,  # ~1 req per 3 seconds (arXiv TOS)
    ) -> None:
        super().__init__(max_per_second=max_per_second)

    @property
    def source_name(self) -> str:
        return "arXiv"

    async def search(
        self,
        *,
        query: str | None = None,
        arxiv_id: str | None = None,
        author: str | None = None,
        title: str | None = None,
        category: str | None = None,
        max_results: int = 50,
        start: int = 0,
    ) -> tuple[list[ScholarlyMetadata], int]:
        """Search arXiv. Returns (results, total_count).

        arXiv uses an Atom XML API, not JSON — this method parses
        the XML response into ScholarlyMetadata objects.
        """
        if arxiv_id:
            # Direct ID lookup
            result = await self.get_by_id(arxiv_id)
            return ([result] if result else [], 1 if result else 0)

        # Build search query
        parts: list[str] = []
        if query:
            parts.append(f"all:{query}")
        if author:
            parts.append(f'au:"{author}"')
        if title:
            parts.append(f'ti:"{title}"')
        if category:
            parts.append(f"cat:{category}")

        search_query = " AND ".join(parts) if parts else "all:*"

        params: dict[str, Any] = {
            "search_query": search_query,
            "start": start,
            "max_results": min(max_results, 200),
            "sortBy": "relevance",
            "sortOrder": "descending",
        }

        xml_text = await self._get_text(f"{_BASE}?{'&'.join(f'{k}={v}' for k, v in params.items())}")
        return _parse_atom_feed(xml_text)

    async def get_by_id(self, arxiv_id: str) -> ScholarlyMetadata | None:
        """Fetch a single paper by arXiv ID (e.g. 2301.07041)."""
        clean_id = arxiv_id.strip().replace("arXiv:", "")
        try:
            xml_text = await self._get_text(
                f"{_BASE}?id_list={clean_id}"
            )
            results, _ = _parse_atom_feed(xml_text)
            return results[0] if results else None
        except Exception:
            logger.debug("arXiv paper not found: %s", arxiv_id)
            return None

    async def download_pdf(self, arxiv_id: str) -> bytes | None:
        """Download the PDF for an arXiv paper.

        Returns the raw PDF bytes, or None on failure.  The caller
        passes these through ``ingestion.extract.extract_text`` for
        text extraction.
        """
        clean_id = arxiv_id.strip().replace("arXiv:", "")
        url = f"https://arxiv.org/pdf/{clean_id}.pdf"
        try:
            return await self._get_bytes(url)
        except Exception:
            logger.warning("Failed to download arXiv PDF: %s", url)
            return None


def _parse_atom_feed(xml_text: str) -> tuple[list[ScholarlyMetadata], int]:
    """Parse arXiv Atom XML response into ScholarlyMetadata list."""
    root = ET.fromstring(xml_text)

    total_str = root.findtext("opensearch:totalResults", "0", _NS)
    total = int(total_str) if total_str else 0

    results: list[ScholarlyMetadata] = []
    for entry in root.findall("atom:entry", _NS):
        meta = _parse_entry(entry)
        if meta:
            results.append(meta)

    return results, total


def _parse_entry(entry: ET.Element) -> ScholarlyMetadata | None:
    """Parse a single Atom entry into ScholarlyMetadata."""
    # arXiv ID from the <id> element (URL format)
    id_text = entry.findtext("atom:id", "", _NS)
    arxiv_id = id_text.replace("http://arxiv.org/abs/", "").strip()
    if not arxiv_id:
        return None

    # Strip version suffix for canonical ID
    arxiv_id_base = re.sub(r"v\d+$", "", arxiv_id)

    title = entry.findtext("atom:title", "", _NS)
    # arXiv titles often have embedded newlines from the XML
    title = " ".join(title.split()).strip() if title else None

    abstract = entry.findtext("atom:summary", "", _NS)
    abstract = abstract.strip() if abstract else None

    # Authors
    authors = []
    for author_elem in entry.findall("atom:author", _NS):
        name = author_elem.findtext("atom:name", "", _NS)
        if name:
            authors.append(name.strip())

    # Publication date → year
    published = entry.findtext("atom:published", "", _NS)
    year = int(published[:4]) if published and len(published) >= 4 else None

    # DOI (arXiv entries sometimes include it)
    doi = entry.findtext("arxiv:doi", None, _NS)

    # Category → journal approximation
    primary_cat = entry.find("arxiv:primary_category", _NS)
    category = primary_cat.get("term") if primary_cat is not None else None

    # PDF link
    pdf_url = f"https://arxiv.org/pdf/{arxiv_id_base}.pdf"
    abs_url = f"https://arxiv.org/abs/{arxiv_id_base}"

    return ScholarlyMetadata(
        doi=doi,
        arxiv_id=arxiv_id_base,
        title=title,
        authors=authors,
        abstract=abstract,
        publication_year=year,
        journal=f"arXiv:{category}" if category else "arXiv",
        source=ScholarlySource.ARXIV,
        source_url=abs_url,
        pdf_url=pdf_url,
        license_id="arXiv",
        is_open_access=True,
        full_text_available=False,
        availability=AvailabilityStatus.PENDING,
    )
