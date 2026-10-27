"""Scholarly document deduplication.

Three layers of dedup, checked in order:

1. **DOI match** — strongest signal.  Two records with the same DOI are
   the same work regardless of source.
2. **Source-specific ID** — OpenAlex ID, arXiv ID, or PMCID.  Catches
   duplicates within a single source that lack DOIs.
3. **Normalized title + year** — fuzzy fallback.  Catches cross-source
   duplicates that lack all machine IDs but describe the same paper.

Content-hash dedup (SHA-256 of extracted text) is handled at the
document-insert layer via the existing ``uq_documents_tenant_sha256``
unique index and is not duplicated here.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from noplag_engine.scholarly.models import ScholarlyMetadata

logger = logging.getLogger("noplag.scholarly.dedup")


async def is_duplicate(
    session: AsyncSession,
    meta: ScholarlyMetadata,
) -> bool:
    """Return True if a document matching ``meta`` already exists.

    Checks DOI, source-specific IDs, and normalized title + year in that
    order.  Returns True on the first hit.
    """
    # Layer 1: DOI
    if meta.doi:
        exists = await _exists_by_column(session, "doi", meta.doi)
        if exists:
            logger.debug("Dedup hit: DOI %s", meta.doi)
            return True

    # Layer 2: Source-specific IDs
    if meta.openalex_id:
        exists = await _exists_by_column(session, "openalex_id", meta.openalex_id)
        if exists:
            return True
    if meta.arxiv_id:
        exists = await _exists_by_column(session, "arxiv_id", meta.arxiv_id)
        if exists:
            return True
    if meta.pmcid:
        exists = await _exists_by_column(session, "pmcid", meta.pmcid)
        if exists:
            return True

    # Layer 3: Normalized title + year
    if meta.title and meta.publication_year:
        normalized = meta.normalized_title()
        if normalized and len(normalized) > 10:
            exists = await _exists_by_title_year(
                session, normalized, meta.publication_year
            )
            if exists:
                logger.debug(
                    "Dedup hit: title+year '%s' (%d)",
                    normalized[:60], meta.publication_year,
                )
                return True

    return False


async def _exists_by_column(
    session: AsyncSession, column: str, value: str
) -> bool:
    """Check if a document with ``column = value`` exists."""
    # Column names are from a fixed set (DOI, openalex_id, etc.), never
    # user input, so f-string interpolation is safe.
    result = await session.execute(
        sql_text(f"SELECT 1 FROM documents WHERE {column} = :v LIMIT 1"),
        {"v": value},
    )
    return result.scalar() is not None


async def _exists_by_title_year(
    session: AsyncSession, normalized_title: str, year: int
) -> bool:
    """Fuzzy title + year dedup.

    Compares ``lower(replace(title, ' ', ''))`` for a tolerance of
    whitespace differences between sources. This is stricter than the
    cosine-similarity approach but avoids false positives on short titles
    like "Introduction" or "Results".
    """
    result = await session.execute(
        sql_text(
            "SELECT 1 FROM documents "
            "WHERE publication_year = :year "
            "AND lower(replace(coalesce(filename, ''), ' ', '')) = :norm "
            "LIMIT 1"
        ),
        {"year": year, "norm": normalized_title.replace(" ", "")},
    )
    return result.scalar() is not None


def normalize_doi(doi: str | None) -> str | None:
    """Normalize a DOI to lowercase, stripped form.

    Handles common prefixes: ``https://doi.org/``, ``doi:``, etc.
    """
    if not doi:
        return None
    doi = doi.strip()
    doi = re.sub(r"^https?://doi\.org/", "", doi, flags=re.IGNORECASE)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.IGNORECASE)
    return doi.lower().strip() or None
