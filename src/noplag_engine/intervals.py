"""Shared interval-merge helper.

Used by:

- `workflows/check.py` — cross-source dedupe over translated query-side
  passages to produce `CheckReport.unique_passages` + `total_matched_chars`.
- `api/v1/checks.py` — reconstruct `unique_passages` on `GET
  /v1/checks/{id}/report` from the jsonb-stored per-source passages.
- Future retrieval layers (L2 / L3) — incoming overlap merge once
  those layers land.

Intervals are half-open: `[start, end)`. Adjacent intervals where one
starts exactly where the previous ends *do* merge — they describe a
contiguous run of covered query characters.
"""

from __future__ import annotations


def merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping or adjoining half-open `(start, end)` intervals.

    Sorts by `(start, end)` then folds left-to-right, extending the
    last-kept interval whenever the next one starts at or before the
    last `end`. Returns a fresh list; input is not mutated.
    """
    if not intervals:
        return []
    sorted_iv = sorted(intervals)
    merged: list[tuple[int, int]] = [sorted_iv[0]]
    for start, end in sorted_iv[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged
