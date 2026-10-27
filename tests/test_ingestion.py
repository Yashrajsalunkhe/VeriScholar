"""Unit tests for corpus text extraction + normalization.

No database — these exercise the extractors against the committed
fixtures and the normalizer against hand-built strings. The full upload
round trip through Postgres lives in test_corpus_api.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noplag_engine.ingestion import (
    ExtractionFailed,
    NoExtractableText,
    UnsupportedMediaType,
    extract_text,
    normalize_text,
    resolve_mime_type,
)
from noplag_engine.ingestion.extract import MIME_DOCX, MIME_PDF, MIME_TXT

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


# --- mime resolution ------------------------------------------------


def test_resolve_prefers_supported_content_type():
    assert resolve_mime_type("application/pdf", "x.bin") == MIME_PDF


def test_resolve_strips_charset_parameter():
    assert resolve_mime_type("text/plain; charset=utf-8", None) == MIME_TXT


def test_resolve_falls_back_to_extension():
    # Browsers / RN often send a generic or empty content-type.
    assert resolve_mime_type("application/octet-stream", "essay.docx") == MIME_DOCX
    assert resolve_mime_type(None, "notes.txt") == MIME_TXT


def test_resolve_rejects_unsupported():
    with pytest.raises(UnsupportedMediaType):
        resolve_mime_type("image/png", "scan.png")


# --- extraction per format -----------------------------------------


def test_extract_txt_marker_survives():
    text = normalize_text(extract_text(_read("sample.txt"), MIME_TXT))
    assert "TXT ingestion marker alpha." in text


def test_extract_docx_marker_survives():
    text = normalize_text(extract_text(_read("sample.docx"), MIME_DOCX))
    assert "DOCX ingestion marker bravo." in text


def test_extract_pdf_marker_survives():
    text = normalize_text(extract_text(_read("sample.pdf"), MIME_PDF))
    assert "PDF ingestion marker charlie." in text


def test_extract_empty_text_raises():
    # A PDF whose text layer yields nothing (the scanned-document case)
    # surfaces as NoExtractableText, which the API maps to 422.
    with pytest.raises(NoExtractableText):
        extract_text(b"   \n\t  ", MIME_TXT)


def test_extract_corrupt_pdf_raises_extraction_failed():
    # A file claiming to be a PDF but with garbage bytes must surface as
    # ExtractionFailed (→ 422), not leak a pypdf error as a 500.
    with pytest.raises(ExtractionFailed):
        extract_text(b"%PDF-1.4 not actually a pdf", MIME_PDF)


def test_extract_corrupt_docx_raises_extraction_failed():
    with pytest.raises(ExtractionFailed):
        extract_text(b"PK\x03\x04 not actually a docx", MIME_DOCX)


# --- normalization --------------------------------------------------


def test_normalize_collapses_horizontal_whitespace():
    assert normalize_text("a   b\t\tc") == "a b c"


def test_normalize_collapses_blank_line_runs_to_one():
    assert normalize_text("para1\n\n\n\n\npara2") == "para1\n\npara2"


def test_normalize_unifies_line_endings_and_strips():
    assert normalize_text("  line1\r\nline2  \r\n") == "line1\nline2"


def test_normalize_preserves_case():
    # Fingerprinting owns folding; storage keeps original case.
    assert normalize_text("MixedCase TEXT") == "MixedCase TEXT"


def test_normalize_nfc_unicode():
    # An NFD sequence ("e" + U+0301 combining acute) normalizes to the
    # precomposed NFC code point (U+00E9) so the two byte sequences
    # fingerprint identically.
    nfd = "caf" + "e\u0301"
    nfc = "caf\u00e9"
    assert nfd != nfc
    assert normalize_text(nfd) == nfc

