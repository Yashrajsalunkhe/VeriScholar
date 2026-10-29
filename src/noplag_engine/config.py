"""Tunable parameters for the fingerprint + matching pipeline.

`k` (k-gram size) and `w` (winnow window) were previously hardcoded as
default arguments on `fingerprint()`, and the chunk window on
`chunk_document()`. They're lifted here so the corpus-side producer
(`fingerprint_document`) and the query-side matcher (`run_check`) read
the *same* values — fingerprints only match when both sides use identical
`k` and `w`, so a single source of truth is a correctness requirement,
not just tidiness.

Values are overridable by env var so the eval harness can sweep
(k, w, chunk) combinations without code changes; the defaults are the
tuned winners from the PAN-PC-11 sweep.

The hash function (blake2b, 64-bit) stays fixed in
`fingerprinting/winnowing.py` — it's not a plagdet tuning knob (any
uniform 64-bit hash performs identically), only k/w/chunk-size move the
score, so only those are exposed here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class FingerprintConfig:
    # K-gram length in characters for winnowing.
    k: int = 5
    # Winnow window size, in k-grams.
    w: int = 8
    # Sentences per chunk for the sliding-sentence chunker.
    sentences_per_chunk: int = 4
    # Sentences shared between adjacent chunks (must be < sentences_per_chunk).
    chunk_overlap: int = 1
    # ── L2 fuzzy layer ──────────────────────────────────────────────
    # Run the chunk-level fuzzy (near-verbatim) pass on L1 candidates that
    # seed-extend produced no verbatim passage for. Catches light paraphrase
    # (synonym swaps, reordering) that shares fingerprints with the source but
    # no 30-char exact run. Off reproduces the original exact-only behaviour.
    fuzzy_enabled: bool = True
    # Minimum combined fuzzy score (0–1) for such a candidate to be reported.
    # 0.4 catches moderate paraphrase while staying above the false-positive
    # floor where independent same-topic writing scores; see fuzzy/align.py.
    fuzzy_threshold: float = 0.4


def get_fingerprint_config() -> FingerprintConfig:
    """Build the config from env overrides, falling back to the tuned
    defaults. Read at call time so the eval harness can set env vars per
    sweep iteration."""
    return FingerprintConfig(
        k=int(os.environ.get("NOPLAG_FP_K", FingerprintConfig.k)),
        w=int(os.environ.get("NOPLAG_FP_W", FingerprintConfig.w)),
        sentences_per_chunk=int(
            os.environ.get("NOPLAG_CHUNK_SENTENCES", FingerprintConfig.sentences_per_chunk)
        ),
        chunk_overlap=int(
            os.environ.get("NOPLAG_CHUNK_OVERLAP", FingerprintConfig.chunk_overlap)
        ),
        fuzzy_enabled=os.environ.get(
            "NOPLAG_FUZZY_ENABLED", "1" if FingerprintConfig.fuzzy_enabled else "0"
        )
        != "0",
        fuzzy_threshold=float(
            os.environ.get("NOPLAG_FUZZY_THRESHOLD", FingerprintConfig.fuzzy_threshold)
        ),
    )
