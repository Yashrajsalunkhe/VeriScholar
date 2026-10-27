"""Fuzzy text matching for near-verbatim detection.

Sits between the existing L0/L1 exact pipeline and the L3 semantic layer:

- **L1** (winnowing GIN) finds candidates sharing exact fingerprints
- **L2** (this module) catches modifications that defeat fingerprinting:
  synonym substitution, word reordering, insertion/deletion of filler words
- **L3** (semantic embeddings) handles full paraphrase

Methods implemented:

1. **Token Jaccard similarity** — bag-of-words overlap after stopword
   removal.  Fast, handles word reordering.
2. **Character n-gram similarity** — Sørensen–Dice over character n-grams.
   Handles small edits (typo insertion, character substitution).
3. **Longest common subsequence ratio** — catches reordering with
   partial preservation.  More expensive; used on top-candidates only.

All methods return a 0–1 similarity score.  The hybrid scorer combines
them with configurable weights.
"""

from __future__ import annotations

import re
from collections import Counter

# ── Stopwords (English) ─────────────────────────────────────────────
# Minimal set — enough to improve Jaccard precision on common function
# words without being so aggressive that content words are lost.
_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "shall", "should", "may", "might", "can", "could", "must", "that",
    "which", "who", "whom", "this", "these", "those", "it", "its", "not",
    "no", "nor", "as", "if", "then", "than", "so", "such", "both",
    "each", "other", "into", "through", "during", "before", "after",
    "above", "below", "between", "about", "also", "very", "just", "only",
})

_WORD_RE = re.compile(r"\b\w+\b", re.UNICODE)


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens, no stopwords."""
    return [
        w for w in _WORD_RE.findall(text.lower())
        if w not in _STOPWORDS and len(w) > 1
    ]


def jaccard_similarity(text_a: str, text_b: str) -> float:
    """Token-level Jaccard similarity (bag-of-words overlap).

    Handles word reordering, insertion of filler words.
    Fast: O(n) where n = max token count.
    """
    tokens_a = set(tokenize(text_a))
    tokens_b = set(tokenize(text_b))
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


def ngram_similarity(
    text_a: str,
    text_b: str,
    n: int = 3,
) -> float:
    """Character n-gram Sørensen–Dice coefficient.

    Handles small edits: character substitution, insertion, transposition.
    More granular than token Jaccard, captures sub-word similarity.
    """
    grams_a = _char_ngrams(text_a.lower(), n)
    grams_b = _char_ngrams(text_b.lower(), n)
    if not grams_a or not grams_b:
        return 0.0
    # Dice coefficient: 2 * |intersection| / (|A| + |B|)
    # Using multiset intersection for proper counting
    counter_a = Counter(grams_a)
    counter_b = Counter(grams_b)
    intersection = sum((counter_a & counter_b).values())
    return 2 * intersection / (len(grams_a) + len(grams_b))


def lcs_ratio(text_a: str, text_b: str) -> float:
    """Longest common subsequence ratio.

    Returns LCS length / max(len_a, len_b).  Catches reordering with
    partial preservation of word order.

    Uses token-level LCS (not character-level) for performance.
    More expensive: O(n*m) where n, m = token counts.
    Reserved for top candidates after initial Jaccard/n-gram filter.
    """
    tokens_a = tokenize(text_a)
    tokens_b = tokenize(text_b)
    if not tokens_a or not tokens_b:
        return 0.0
    lcs_len = _lcs_length(tokens_a, tokens_b)
    return lcs_len / max(len(tokens_a), len(tokens_b))


def combined_fuzzy_score(
    text_a: str,
    text_b: str,
    *,
    jaccard_weight: float = 0.4,
    ngram_weight: float = 0.35,
    lcs_weight: float = 0.25,
) -> float:
    """Weighted combination of all fuzzy metrics.

    Returns a 0–1 score.  Weights are configurable and should sum to 1.0.
    """
    j = jaccard_similarity(text_a, text_b)
    ng = ngram_similarity(text_a, text_b)
    lcs = lcs_ratio(text_a, text_b)
    return j * jaccard_weight + ng * ngram_weight + lcs * lcs_weight


def _char_ngrams(text: str, n: int) -> list[str]:
    """Extract character n-grams, ignoring whitespace runs."""
    collapsed = " ".join(text.split())
    if len(collapsed) < n:
        return []
    return [collapsed[i : i + n] for i in range(len(collapsed) - n + 1)]


def _lcs_length(a: list[str], b: list[str]) -> int:
    """Standard DP LCS length (space-optimized to two rows)."""
    if len(a) > len(b):
        a, b = b, a
    prev = [0] * (len(b) + 1)
    curr = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                curr[j] = prev[j - 1] + 1
            else:
                curr[j] = max(prev[j], curr[j - 1])
        prev, curr = curr, [0] * (len(b) + 1)
    return prev[len(b)]
