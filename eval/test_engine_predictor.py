"""Engine-backed plagdet on a synthetic PAN-style set.

Proves the real retrieval pipeline detects plagiarism — including light
in-place obfuscation — end to end, with a reproducible plagdet score.
The full PAN-PC-11 run (`run_eval.py`) needs the fetched corpus; this is
the offline, deterministic floor that runs without it.

Scope caveat: this set is verbatim / lightly-obfuscated by construction,
which is exactly what an L0/L1 engine detects. The score here says
nothing about performance on the full PAN-PC-11, whose heavy-paraphrase
cases score near zero by design — see eval/datasets/pan_pc_11/README.md,
"Interpreting the score".

Skips when Postgres isn't reachable, mirroring the engine suite, so it
never blocks a no-DB environment.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from plagdet import Passage, plagdet  # noqa: E402

DSN = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://noplag:noplag@localhost:5432/noplag"
)

# Distinctive original prose used as the corpus sources.
SOURCES = [
    (
        "The migration of monarch butterflies spans thousands of kilometres across "
        "the North American continent, a journey no single butterfly completes in "
        "its lifetime. Successive generations carry the route encoded in instinct, "
        "navigating by the position of the sun and an internal magnetic compass. "
        "Researchers have long puzzled over how such a fragile insect sustains a "
        "transcontinental passage that takes four generations to finish."
    ),
    (
        "Deep beneath the ocean surface, hydrothermal vents support ecosystems that "
        "draw no energy from sunlight. Instead, chemosynthetic bacteria convert "
        "mineral-rich water into the chemical fuel that anchors an entire food web. "
        "Giant tube worms, ghostly crabs, and translucent shrimp crowd around the "
        "scalding plumes, thriving in conditions that would be lethal almost anywhere "
        "else on the planet."
    ),
    (
        "The invention of movable type transformed the spread of knowledge in early "
        "modern Europe. Where a single manuscript once took a scribe months to copy, "
        "a print shop could produce hundreds of identical volumes in a matter of days. "
        "The resulting flood of affordable books eroded the monopoly of the few "
        "institutions that had controlled literacy, and ideas began to travel faster "
        "than any authority could contain them."
    ),
]

_FILLER_A = (
    "In this report we set out our own observations gathered over the course of the "
    "study, drawing on field notes and original measurements. "
)
_FILLER_B = (
    " What follows is our independent analysis of the implications, written without "
    "reference to any external source material whatsoever."
)


def _obfuscate(span: str, rate: int) -> str:
    """Length-preserving single-char substitution every `rate` chars."""
    if rate <= 0:
        return span
    chars = list(span)
    for j in range(0, len(chars), rate):
        c = chars[j]
        if c.isalpha():
            chars[j] = chr((ord(c.lower()) - ord("a") + 7) % 26 + ord("a"))
    return "".join(chars)


def _build_cases():
    suspicious: list[tuple[str, str]] = []
    truths: list[Passage] = []
    for i, src in enumerate(SOURCES):
        span = src[len(src) // 5 : len(src) // 5 + (len(src) * 3) // 5]
        for label, rate in [("verbatim", 0), ("light", 20), ("moderate", 9)]:
            doc_id = f"susp-{i}-{label}"
            suspicious.append((doc_id, _FILLER_A + _obfuscate(span, rate) + _FILLER_B))
            truths.append(Passage(doc_id=doc_id, start=len(_FILLER_A), length=len(span)))
    return suspicious, truths


def _postgres_reachable() -> bool:
    try:
        from sqlalchemy import create_engine
        from sqlalchemy import text as sql_text

        eng = create_engine(DSN)
        with eng.connect() as conn:
            conn.execute(sql_text("SELECT 1"))
        eng.dispose()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _postgres_reachable(), reason="Postgres not reachable")
def test_engine_detects_synthetic_plagiarism():
    import engine_predictor

    suspicious, truths = _build_cases()
    detections_map = asyncio.run(engine_predictor.evaluate(SOURCES, suspicious))
    detections = [
        Passage(doc_id=d, start=s, length=e - s)
        for d, regions in detections_map.items()
        for s, e in regions
    ]
    result = plagdet(truths, detections)

    # Default (k=5, w=8) scores ~0.862 on this set (precision 1.0, recall
    # ~0.76 — recall is aligner-bound, not winnowing-bound). Assert a
    # comfortable floor with no false positives.
    assert result.precision >= 0.95, result
    assert result.plagdet >= 0.80, result
