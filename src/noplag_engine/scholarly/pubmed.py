"""PubMed Central client for biomedical literature.

PubMed Central (PMC) is the NIH's open-access archive covering
biomedical and life-science research.  Uses NCBI E-utilities:
- ESearch for discovery (returns PMC IDs)
- EFetch for full text (returns XML)

Rate limits: 3 req/s without key, 10 req/s with API key.
An email is required by NCBI policy for the tool parameter.

Reference: https://www.ncbi.nlm.nih.gov/books/NBK25500/
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

logger = logging.getLogger("noplag.scholarly.pubmed")

_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
_ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"


class PubMedClient(BaseScholarlyClient):
    """Client for PubMed Central via NCBI E-utilities."""

    def __init__(
        self,
        *,
        email: str = "",
        api_key: str = "",
        max_per_second: float = 3.0,
    ) -> None:
        rps = 10.0 if api_key else max_per_second
        super().__init__(max_per_second=rps)
        self._email = email
        self._api_key = api_key

    @property
    def source_name(self) -> str:
        return "PubMed"

    def _base_params(self) -> dict[str, str]:
        """Common params for all E-utility calls."""
        params: dict[str, str] = {"tool": "verischolar", "retmode": "json"}
        if self._email:
            params["email"] = self._email
        if self._api_key:
            params["api_key"] = self._api_key
        return params

    async def search(
        self,
        *,
        query: str | None = None,
        author: str | None = None,
        year: int | None = None,
        max_results: int = 50,
    ) -> tuple[list[str], int]:
        """Search PubMed Central. Returns (pmcid_list, total_count).

        Returns PMC IDs (not full metadata) — call ``get_summaries``
        to fetch metadata for the results.
        """
        parts: list[str] = []
        if query:
            parts.append(query)
        if author:
            parts.append(f"{author}[Author]")
        if year:
            parts.append(f"{year}[pdat]")

        term = " AND ".join(parts) if parts else "open access[filter]"

        params = {
            **self._base_params(),
            "db": "pmc",
            "term": term,
            "retmax": str(min(max_results, 500)),
            "sort": "relevance",
        }

        data = await self._get(_ESEARCH, params=params)
        result = data.get("esearchresult", {})
        total = int(result.get("count", 0))
        id_list = result.get("idlist", [])

        return [f"PMC{pid}" for pid in id_list], total

    async def get_summaries(
        self, pmcids: list[str]
    ) -> list[ScholarlyMetadata]:
        """Fetch metadata summaries for a list of PMC IDs."""
        if not pmcids:
            return []

        # Strip "PMC" prefix for the API
        numeric_ids = [pid.replace("PMC", "") for pid in pmcids]

        params = {
            **self._base_params(),
            "db": "pmc",
            "id": ",".join(numeric_ids),
        }

        data = await self._get(_ESUMMARY, params=params)
        result_data = data.get("result", {})

        results: list[ScholarlyMetadata] = []
        for pid in numeric_ids:
            info = result_data.get(pid)
            if not info or isinstance(info, str):
                continue
            results.append(_parse_summary(info, f"PMC{pid}"))

        return results

    async def get_full_text(self, pmcid: str) -> str | None:
        """Fetch full-text XML for a PMC article and extract plain text."""
        numeric = pmcid.replace("PMC", "")
        try:
            params = {
                **self._base_params(),
                "db": "pmc",
                "id": numeric,
                "retmode": "xml",
                "rettype": "full",
            }
            # Override retmode for XML
            params["retmode"] = "xml"

            xml_text = await self._get_text(
                f"{_EFETCH}?{'&'.join(f'{k}={v}' for k, v in params.items())}"
            )
            return _extract_text_from_pmc_xml(xml_text)
        except Exception:
            logger.debug("PMC full text not available for %s", pmcid)
            return None


def _parse_summary(info: dict[str, Any], pmcid: str) -> ScholarlyMetadata:
    """Convert an ESummary result to ScholarlyMetadata."""
    # Authors
    authors = []
    for a in info.get("authors", []):
        name = a.get("name", "") if isinstance(a, dict) else str(a)
        if name:
            authors.append(name)

    # DOI from article IDs
    doi = None
    for aid in info.get("articleids", []):
        if isinstance(aid, dict) and aid.get("idtype") == "doi":
            doi = aid.get("value")
            break

    title = info.get("title", "")
    # Publication year from sortpubdate or pubdate
    pub_date = info.get("sortpubdate", "") or info.get("pubdate", "")
    year = None
    if pub_date:
        match = re.match(r"(\d{4})", pub_date)
        if match:
            year = int(match.group(1))

    journal = info.get("fulljournalname") or info.get("source")

    return ScholarlyMetadata(
        doi=doi,
        pmcid=pmcid,
        title=title or None,
        authors=authors,
        publication_year=year,
        journal=journal,
        source=ScholarlySource.PUBMED,
        source_url=f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/",
        is_open_access=True,  # PMC articles are open access
        availability=AvailabilityStatus.PENDING,
    )


def _extract_text_from_pmc_xml(xml_text: str) -> str | None:
    """Extract plain text from PMC full-text XML (JATS format).

    Walks the <body> element and concatenates all text content,
    stripping XML tags.  Returns None if parsing fails or no body found.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    # Find the body element (may be nested under <article>)
    body = root.find(".//body")
    if body is None:
        return None

    # Recursive text extraction
    parts: list[str] = []
    for elem in body.iter():
        if elem.text:
            parts.append(elem.text.strip())
        if elem.tail:
            parts.append(elem.tail.strip())

    text = " ".join(parts)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) > 50 else None
