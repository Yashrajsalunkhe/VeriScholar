"""Unit tests for the shared `merge_intervals` helper."""

from __future__ import annotations

from noplag_engine.intervals import merge_intervals


def test_empty():
    assert merge_intervals([]) == []


def test_single_passthrough():
    assert merge_intervals([(10, 20)]) == [(10, 20)]


def test_disjoint_stays_separate():
    assert merge_intervals([(0, 5), (10, 20)]) == [(0, 5), (10, 20)]


def test_overlap_merges():
    assert merge_intervals([(0, 10), (5, 15)]) == [(0, 15)]


def test_contained_absorbed():
    assert merge_intervals([(0, 20), (5, 10)]) == [(0, 20)]


def test_half_open_adjoining_merges():
    # [0, 5) and [5, 10) are exactly adjacent — together they cover a
    # contiguous run [0, 10), so the merge folds them.
    assert merge_intervals([(0, 5), (5, 10)]) == [(0, 10)]


def test_out_of_order_input_handled():
    assert merge_intervals([(20, 30), (0, 10), (5, 15)]) == [(0, 15), (20, 30)]


def test_input_not_mutated():
    src = [(20, 30), (0, 10), (5, 15)]
    snapshot = list(src)
    merge_intervals(src)
    assert src == snapshot
