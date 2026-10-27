"""Corpus ingestion: extract text from uploads, normalize, store.

Produces fingerprint-ready `documents` rows for the fingerprint pass to
chunk and winnow. See the submodules for the moving parts:

- `extract`   — MIME-dispatched text extraction (PDF / DOCX / TXT).
- `normalize` — whitespace + Unicode normalization for consistent text.
- `storage`   — `ObjectStorage` abstraction for the raw upload bytes.
"""

from __future__ import annotations

from noplag_engine.ingestion.extract import (
    SUPPORTED_MIME_TYPES,
    ExtractionFailed,
    NoExtractableText,
    UnsupportedMediaType,
    extract_text,
    resolve_mime_type,
)
from noplag_engine.ingestion.normalize import normalize_text
from noplag_engine.ingestion.storage import (
    LocalFilesystemStorage,
    ObjectStorage,
    get_object_storage,
)

__all__ = [
    "SUPPORTED_MIME_TYPES",
    "ExtractionFailed",
    "LocalFilesystemStorage",
    "NoExtractableText",
    "ObjectStorage",
    "UnsupportedMediaType",
    "extract_text",
    "get_object_storage",
    "normalize_text",
    "resolve_mime_type",
]
