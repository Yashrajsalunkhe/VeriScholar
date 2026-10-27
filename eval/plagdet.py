"""plagdet metric for plagiarism detection evaluation.

Reference: Potthast, M., Stein, B., Barron-Cedeno, A., & Rosso, P. (2010).
An Evaluation Framework for Plagiarism Detection. COLING 2010, 997-1005.

    plagdet = F_alpha(precision, recall) / log2(1 + granularity)

precision and recall are micro-averaged passage-overlap fractions over all
suspicious documents in the eval corpus. granularity is the average number
of detections that overlap each detected truth case (1 = no penalty, >1 =
fragmented). The log2 term ensures plagdet = F_alpha when granularity = 1
and decays as fragmentation grows.

The metric operates on flat passage sequences — caller is responsible for
converting from any corpus-specific shape (PAN-PC-11 PlagiarismAnnotation,
synthetic cases, etc.) into the Passage tuple form.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import log2


@dataclass(frozen=True)
class Passage:
    """A plagiarized passage in a suspicious document."""

    doc_id: str
    start: int
    length: int

    @property
    def end(self) -> int:
        return self.start + self.length


@dataclass(frozen=True)
class PlagdetResult:
    precision: float
    recall: float
    granularity: float
    f_alpha: float
    plagdet: float


def plagdet(
    truths: Sequence[Passage],
    detections: Sequence[Passage],
    *,
    alpha: float = 1.0,
) -> PlagdetResult:
    """Compute plagdet over flat passage sequences.

    `truths` and `detections` span every suspicious document in the eval
    corpus; passages with different `doc_id` cannot overlap. `alpha` is the
    F-score weight (alpha=1.0 = F1; alpha>1 weights recall higher).

    Conventions for degenerate inputs:
        - no detections: precision = 1.0 (no false positives possible)
        - no truths:     recall = 1.0    (vacuous coverage)
        - no detected truth case: granularity = 1.0 (no penalty)

    These match the PAN evaluation framework so degenerate cases compose
    cleanly into the corpus-level micro-average.
    """
    p = _micro_precision(truths, detections)
    r = _micro_recall(truths, detections)
    g = _granularity(truths, detections)
    f = _f_alpha(p, r, alpha=alpha)
    return PlagdetResult(
        precision=p,
        recall=r,
        granularity=g,
        f_alpha=f,
        plagdet=f / log2(1 + g),
    )


def _micro_precision(truths, detections):
    if not detections:
        return 1.0
    overlap = sum(_union_overlap_length(d, truths) for d in detections)
    total = sum(d.length for d in detections)
    return overlap / total if total else 1.0


def _micro_recall(truths, detections):
    if not truths:
        return 1.0
    overlap = sum(_union_overlap_length(t, detections) for t in truths)
    total = sum(t.length for t in truths)
    return overlap / total if total else 1.0


def _granularity(truths, detections):
    counts = []
    for t in truths:
        n = sum(1 for d in detections if _intersect_length(t, d) > 0)
        if n > 0:
            counts.append(n)
    if not counts:
        return 1.0
    return sum(counts) / len(counts)


def _f_alpha(p, r, *, alpha):
    denom = alpha**2 * p + r
    if denom == 0:
        return 0.0
    return (1 + alpha**2) * p * r / denom


def _intersect_length(a, b):
    if a.doc_id != b.doc_id:
        return 0
    return max(0, min(a.end, b.end) - max(a.start, b.start))


def _union_overlap_length(target, others):
    intervals = []
    for o in others:
        if o.doc_id != target.doc_id:
            continue
        lo = max(target.start, o.start)
        hi = min(target.end, o.end)
        if lo < hi:
            intervals.append((lo, hi))
    if not intervals:
        return 0
    intervals.sort()
    merged = [intervals[0]]
    for lo, hi in intervals[1:]:
        if lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return sum(hi - lo for lo, hi in merged)
