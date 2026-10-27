"""Language detection for corpus documents.

pysbd needs a language to segment sentences, and ingestion deliberately
leaves `documents.language` null rather than silently assume English. We
detect on the first fingerprinting pass and write the result back to
`documents.language` — it happens naturally once, no ingestion retrofit,
and no monolingual "en" assumption baked into storage.

Library: `langdetect` (a port of Google's language-detection). Chosen over
lingua-py / fastText because it is pure-Python, dependency-light,
returns ISO 639-1 codes that map straight onto pysbd, and — with a fixed
seed — is deterministic, which the rest of the pipeline relies on. lingua
is more accurate on short text and would be the upgrade if multilingual
corpus accuracy becomes a priority.
"""

from __future__ import annotations

from langdetect import DetectorFactory, LangDetectException, detect

# langdetect's inference is randomized; pin the seed so the same text
# always yields the same language (the winnowing pipeline is deterministic
# by design and the eval must be reproducible).
DetectorFactory.seed = 0

# ISO 639-1 codes pysbd can segment. A detected language outside this set
# still gets recorded on the document, but chunking falls back to English
# rules — good enough for fingerprinting, since k-grams are character-level.
_PYSBD_SUPPORTED = frozenset(
    {
        "en", "es", "de", "fr", "it", "pt", "ru", "ja", "zh", "nl",
        "pl", "da", "el", "ar", "fa", "hi", "mr", "my", "ur", "bg",
        "am", "hy", "kk", "sk",
    }
)

_FALLBACK = "en"


def detect_language(text: str) -> str:
    """Return the detected ISO 639-1 code for `text`, or "en" on failure.

    Empty or feature-poor text (langdetect can't decide) falls back to
    English rather than raising — a corpus document always gets *some*
    language recorded.
    """
    if not text or not text.strip():
        return _FALLBACK
    try:
        return detect(text)
    except LangDetectException:
        return _FALLBACK


def pysbd_language(code: str) -> str:
    """Map a detected ISO 639-1 code to one pysbd can segment.

    Codes pysbd doesn't support fall back to English segmentation; the
    detected code is still what gets stored on the document.
    """
    return code if code in _PYSBD_SUPPORTED else _FALLBACK
