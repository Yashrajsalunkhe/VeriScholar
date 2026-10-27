"""Scholarly corpus infrastructure for VeriScholar AI.

Modules
-------
- ``models``     — dataclasses shared across all scholarly API clients
- ``openalex``   — OpenAlex API client (discovery + metadata)
- ``arxiv``      — arXiv API client (preprint metadata + full text)
- ``crossref``   — Crossref API client (DOI metadata)
- ``pubmed``     — PubMed Central client (biomedical full text)
- ``core``       — CORE API client (open-access full text)
- ``pipeline``   — orchestrates discovery → download → ingest → index
- ``dedup``      — DOI / title / hash deduplication
"""

from __future__ import annotations
