"""Text extraction for corpus uploads.

Pulls a plain-text layer out of an uploaded file by MIME type. Three
formats ship in the v1 baseline — the set the web app already tells
users it accepts (PDF, DOCX, TXT):

- **PDF** via `pypdf`. Pure-Python, no system libraries, fast enough for
  the upload path. It reads the embedded text layer only — a scanned PDF
  with no text layer extracts to nothing, which the caller surfaces as
  `NoExtractableText`. OCR (and a heavier layout-aware extractor like
  pdfplumber for table-dense documents) is out of scope for this extractor.
- **DOCX** via `python-docx`. Joins paragraph text with newlines.
- **TXT** by decoding bytes as UTF-8, falling back to a lossy decode so
  a stray non-UTF-8 byte never fails the whole upload.

Extraction returns *raw* text; the caller runs it through
`normalize_text` before storage. Keeping the two separate means the
normalization rules live in one place regardless of source format.
"""

from __future__ import annotations

import io

# Canonical MIME types for the v1 baseline formats.
MIME_PDF = "application/pdf"
MIME_DOCX = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
MIME_TXT = "text/plain"

SUPPORTED_MIME_TYPES = frozenset({MIME_PDF, MIME_DOCX, MIME_TXT})

# Extension → MIME fallback for when a client sends a generic or missing
# content-type (browsers and the RN fetch polyfill both do this).
_EXTENSION_MIME = {
    ".pdf": MIME_PDF,
    ".docx": MIME_DOCX,
    ".txt": MIME_TXT,
    # Markdown is close enough to plain text for corpus purposes; the
    # formatting characters winnow out as noise.
    ".md": MIME_TXT,
}


class UnsupportedMediaType(Exception):
    """The upload's MIME type / extension is not a supported format."""


class NoExtractableText(Exception):
    """Extraction produced no usable text (e.g. a scanned, OCR-less PDF)."""


class ExtractionFailed(Exception):
    """The file is the declared type but couldn't be parsed (corrupt /
    truncated). Distinct from NoExtractableText, which is a well-formed
    file that simply has no text layer."""


def resolve_mime_type(content_type: str | None, filename: str | None) -> str:
    """Pick a supported MIME type from the request's content-type or name.

    Prefers an explicit, supported `content-type`; otherwise falls back
    to the filename extension. Raises `UnsupportedMediaType` when neither
    resolves to a baseline format.
    """
    if content_type:
        # Strip any `; charset=...` parameter before matching.
        base = content_type.split(";", 1)[0].strip().lower()
        if base in SUPPORTED_MIME_TYPES:
            return base
    if filename:
        dot = filename.rfind(".")
        if dot != -1:
            ext = filename[dot:].lower()
            if ext in _EXTENSION_MIME:
                return _EXTENSION_MIME[ext]
    raise UnsupportedMediaType(
        f"unsupported upload type (content-type={content_type!r}, "
        f"filename={filename!r}); supported: PDF, DOCX, TXT"
    )


def extract_text(data: bytes, mime_type: str) -> str:
    """Extract raw text from `data` for a resolved, supported `mime_type`.

    Raises `UnsupportedMediaType` for an unexpected type and
    `NoExtractableText` when the file yields nothing (the caller maps
    these to 415 and 422 respectively).
    """
    try:
        if mime_type == MIME_PDF:
            text = _extract_pdf(data)
        elif mime_type == MIME_DOCX:
            text = _extract_docx(data)
        elif mime_type == MIME_TXT:
            text = _extract_txt(data)
        else:  # pragma: no cover - resolve_mime_type gates the call sites
            raise UnsupportedMediaType(mime_type)
    except UnsupportedMediaType:
        raise
    except Exception as exc:
        # The extraction libraries parse untrusted bytes and raise a wide
        # range of errors on a corrupt/truncated file (pypdf
        # PdfStreamError, python-docx's zipfile.BadZipFile, etc.). Map
        # them all to one domain error so the API answers 422, not 500.
        raise ExtractionFailed(
            f"could not parse the {mime_type} file (corrupt or truncated)"
        ) from exc

    if not text.strip():
        raise NoExtractableText(
            "no extractable text found; a scanned PDF without an OCR text "
            "layer cannot be checked"
        )
    return text


def _extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _extract_docx(data: bytes) -> str:
    from docx import Document

    document = Document(io.BytesIO(data))
    return "\n".join(p.text for p in document.paragraphs)


def _extract_txt(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        # A non-UTF-8 text file shouldn't fail the upload; decode lossily
        # and let NFC normalization downstream clean up what it can.
        return data.decode("utf-8", errors="replace")
