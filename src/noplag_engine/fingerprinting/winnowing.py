"""Winnowing fingerprints for L1 retrieval.

Implements robust winnowing from Schleimer, Wilkerson & Aiken (SIGMOD
2003) — "Winnowing: Local Algorithms for Document Fingerprinting". Each
document maps to a deduplicated set of selected k-gram hashes; the L1
retrieval layer queries Postgres GIN with the array-overlap
operator `&&` over these fingerprints.

Algorithm: normalize -> extract sliding-character k-grams -> hash each
to a signed 64-bit int (Postgres bigint range) -> slide a window of w
consecutive hashes -> select the rightmost minimum from each window ->
emit only when the selected position differs from the previous window's
selection (robust winnowing — guarantees that any common substring of
length >= w + k - 1 produces at least one shared fingerprint).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

# Citation / editorial markers that survive a copy-paste from a rendered page
# (Wikipedia "[12]", "[citation needed]"; academic footnote refs) but are
# stripped out by the corpus's HTML->text extraction. Removing them at
# fingerprint time lets pasted "...a fact.[12] Next sentence..." align with the
# marker-free corpus copy instead of corrupting every k-gram that straddles the
# bracket (at k=5, one "[12]" poisons ~8 consecutive grams — enough to drop a
# verbatim Wikipedia paste to single-digit similarity). Bracketed markers only;
# we don't touch parenthetical "(Smith, 2019)" style refs, which overlap with
# legitimate prose and would risk stripping real content.
_CITATION_MARKER_RE = re.compile(
    r"\[\s*(?:"
    r"\d{1,4}"  # [1] [12] [1234]
    r"|citation needed|clarification needed|verification needed|page needed"
    r"|note\s*\d*|nb\s*\d*|dead link|sic|update|edit"
    r"|by whom|according to whom|when|who|why|where"
    r")\??\s*\]",
    re.IGNORECASE,
)


def fingerprint(text: str, k: int = 5, w: int = 8) -> list[int]:
    """Compute the winnowing fingerprint of `text`.

    Returns a sorted, deduplicated list of signed 64-bit ints, each
    derived from a k-gram of the normalized input. The L1 retrieval layer
    consumes this via Postgres GIN array-overlap queries.

    Parameters
    ----------
    text : str
        Raw input document. Normalization (NFKC + lowercase + whitespace
        collapse + strip) is applied before k-gram extraction.
    k : int
        K-gram length in characters (default 5). Starting value —
        tune against PAN-PC-11.
    w : int
        Winnowing window size, in k-grams (default 8). Starting value —
        tune against PAN-PC-11.

    Returns
    -------
    list[int]
        Sorted, deduplicated fingerprints. Empty when the normalized text
        has fewer than k characters.

    Notes
    -----
    Output is deterministic across runs and machines (blake2b is keyless
    and reproducible — unlike Python's built-in `hash()` which is salted
    by PYTHONHASHSEED).
    """
    normalized = _normalize(text)
    if len(normalized) < k:
        return []

    hashes = [_hash_kgram(normalized[i : i + k]) for i in range(len(normalized) - k + 1)]
    selected = _winnow(hashes, w)
    return sorted(selected)


def _normalize(text: str) -> str:
    # NFKC folds compatibility variants (ligatures, fullwidth digits,
    # combined-form diacritics) into a stable canonical form. Lowercase
    # makes matching case-insensitive. Whitespace collapse erases
    # formatting differences without affecting word boundaries.
    # Punctuation is kept — stylistic fingerprints often live there, and
    # the L0 alignment stage handles paraphrase-level differences. Citation /
    # editorial markers are the exception: stripped so pasted reference text
    # aligns with the marker-free corpus (see _CITATION_MARKER_RE).
    nfkc = unicodedata.normalize("NFKC", text)
    de_cited = _CITATION_MARKER_RE.sub("", nfkc)
    return " ".join(de_cited.lower().split())


def _hash_kgram(s: str) -> int:
    # blake2b with digest_size=8 returns 8 bytes (64 bits); reading as
    # big-endian signed maps the value into Postgres signed bigint range
    # (-2^63 .. 2^63 - 1). Cryptographic strength is overkill but it gives
    # uniform distribution and zero implementation risk.
    digest = hashlib.blake2b(s.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)


def _winnow(hashes: list[int], w: int) -> set[int]:
    if not hashes:
        return set()
    # Degenerate case: fewer hashes than the window. Schleimer's matching
    # guarantee no longer applies, but the document still needs a fingerprint
    # for L1 retrieval — emit the minimum of what we have.
    if len(hashes) <= w:
        return {min(hashes)}

    selected_positions: set[int] = set()
    last_emitted = -1  # sentinel — no position can equal -1, so first window emits
    for window_start in range(len(hashes) - w + 1):
        # Rightmost minimum: scan window left-to-right, replace on <=
        # (not <). When two positions tie, the later one wins.
        min_pos = window_start
        for j in range(window_start + 1, window_start + w):
            if hashes[j] <= hashes[min_pos]:
                min_pos = j
        if min_pos != last_emitted:
            selected_positions.add(min_pos)
            last_emitted = min_pos

    return {hashes[i] for i in selected_positions}
