"""Text normalization for corpus ingestion.

Extractors for different formats produce different whitespace and
encoding artifacts; this collapses them to one consistent form so the
same prose fingerprints identically regardless of where it came from.

Two rules are deliberate:

- **UTF-8 / Unicode consistency.** Text is NFC-normalized so that
  visually identical strings using different code-point sequences (e.g.
  precomposed vs. combining accents) compare and fingerprint equal.
- **No case-folding.** Casing is preserved at storage time. The
  fingerprint pass owns the folding strategy; folding here would throw
  away information the report layer wants for display.
"""

from __future__ import annotations

import re
import unicodedata

# Runs of horizontal whitespace (spaces, tabs, form feeds, NBSP, etc.)
# collapse to a single space. Newlines are handled separately so
# paragraph structure survives.
_HORIZONTAL_WS = re.compile(r"[^\S\n]+")
# Three or more newlines collapse to a paragraph break (two).
_BLANK_LINES = re.compile(r"\n{3,}")


def normalize_text(raw: str) -> str:
    """Collapse extraction whitespace and normalize encoding to NFC.

    Returns text ready for storage in `documents.extracted_text`:
    NFC-normalized, `\\n` line endings, single-spaced within lines,
    at most one blank line between paragraphs, and stripped. Casing is
    left untouched.
    """
    text = unicodedata.normalize("NFC", raw)
    # Normalize line endings before any newline-sensitive collapsing.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Collapse horizontal whitespace runs, then trim each line so a
    # collapsed run that straddles a newline doesn't leave a trailing
    # space.
    text = _HORIZONTAL_WS.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()
