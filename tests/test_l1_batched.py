"""Batched L1 retrieval — per-chunk attribution and scoring (no DB).

The corpus query is stubbed; these cover the part batching adds on top of the
single-chunk path: one fetched row set attributed back to each query chunk and
scored against that chunk's own fingerprints.
"""

from __future__ import annotations

import uuid

from noplag_engine.retrieval.l1_winnowing import retrieve_l1_candidates_batched


class _Row:
    def __init__(self, id, document_id, fingerprints):
        self.id = id
        self.document_id = document_id
        self.fingerprints = fingerprints


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalar(self):
        # The fingerprint_df presence probe (`to_regclass`) reads .scalar();
        # None => table absent => retrieval falls back to the full-union &&,
        # which is the behaviour these batched-attribution tests assert against.
        return None


class _StubSession:
    """Returns a fixed row set for any candidate query."""

    def __init__(self, rows):
        self._rows = rows

    async def execute(self, *_args, **_kwargs):
        return _Result(self._rows)


async def test_attributes_and_scores_per_chunk():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    da, db, dc = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    rows = [_Row(a, da, [1, 2, 9]), _Row(b, db, [3, 4]), _Row(c, dc, [5, 6])]
    # chunk0 fps {1,2,3}; chunk1 fps {3,4,5}; chunk2 empty.
    res = await retrieve_l1_candidates_batched(
        [[1, 2, 3], [3, 4, 5], []], _StubSession(rows), tenant_id=uuid.uuid4()
    )
    # chunk0: a shares {1,2}=2, b shares {3}=1 -> ranked by score desc
    assert [(x.chunk_id, x.score) for x in res[0]] == [(a, 2), (b, 1)]
    # chunk1: b shares {3,4}=2, c shares {5}=1
    assert [(x.chunk_id, x.score) for x in res[1]] == [(b, 2), (c, 1)]
    # chunk2: no fingerprints -> no candidates
    assert res[2] == []


async def test_top_k_truncates_per_chunk():
    rows = [_Row(uuid.uuid4(), uuid.uuid4(), [i]) for i in range(10)]
    res = await retrieve_l1_candidates_batched(
        [list(range(10))], _StubSession(rows), top_k=3, tenant_id=uuid.uuid4()
    )
    assert len(res[0]) == 3


async def test_empty_input():
    res = await retrieve_l1_candidates_batched([], _StubSession([]), tenant_id=uuid.uuid4())
    assert res == []


# --- rarest-fingerprint probe selection (the recall fix) -----------------
from noplag_engine.retrieval.l1_winnowing import _select_probe_fps  # noqa: E402


def test_probe_prefers_low_document_frequency():
    # rarest-first by DF; 3 absent (DF 0) sorts first. Budget admits 3,7,5 but
    # the running sum stops before the common 9.
    df = {5: 100, 7: 10, 9: 5000}
    assert _select_probe_fps([5, 7, 9, 3], df, budget=200) == [3, 7, 5]


def test_probe_absent_fingerprints_sort_first_then_by_value():
    # all absent (DF 0) -> deterministic order by value, all within budget
    assert _select_probe_fps([30, 10, 20], {}, budget=15000) == [10, 20, 30]


def test_probe_budget_drops_the_pool_blowing_fingerprint():
    # a chunk of all-common fps: keep the rarest few within budget, drop the
    # DF-44k one that alone would overflow the pool (the Sheriff-chunk case).
    df = {1: 318, 2: 9422, 3: 44191, 4: 5000}
    assert _select_probe_fps([1, 2, 3, 4], df, budget=15000) == [1, 4, 2]


def test_probe_always_includes_the_rarest_even_over_budget():
    # rarest fp alone exceeds budget -> still returned (never an empty probe)
    assert _select_probe_fps([9], {9: 99999}, budget=15000) == [9]


def test_probe_respects_max_fps_cap():
    assert _select_probe_fps(list(range(50)), {}, budget=10**9, max_fps=8) == list(range(8))


# --- non-discriminative-chunk skip (the latency fix) ---------------------
from noplag_engine.retrieval.l1_winnowing import _NONDISCRIM_DF, _is_discriminative  # noqa: E402


def test_chunk_with_a_rare_fingerprint_is_discriminative():
    # one genuinely rare fp (DF below threshold) keeps the whole chunk
    df = {1: _NONDISCRIM_DF + 10, 2: 5}
    assert _is_discriminative([1, 2], df) is True


def test_all_common_chunk_is_non_discriminative():
    # every fp at/above the threshold -> skip (the posting-list-wall case)
    df = {1: _NONDISCRIM_DF, 2: _NONDISCRIM_DF * 3}
    assert _is_discriminative([1, 2], df) is False


def test_subfloor_fingerprint_keeps_chunk():
    # a fp absent from the DF table reads as DF 0 (rarer than the floor) -> kept
    df = {1: _NONDISCRIM_DF * 5}
    assert _is_discriminative([1, 2], df) is True


def test_threshold_zero_disables_skip(monkeypatch):
    # NOPLAG_L1_NONDISCRIM_DF=0 -> never skip, even an all-corpus-common chunk
    monkeypatch.setattr("noplag_engine.retrieval.l1_winnowing._NONDISCRIM_DF", 0)
    assert _is_discriminative([1, 2], {1: 10**9, 2: 10**9}) is True
