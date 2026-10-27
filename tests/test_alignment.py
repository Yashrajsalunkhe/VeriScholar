"""Unit tests for L0 seed-and-extend text alignment."""

from __future__ import annotations

import random
import string
from itertools import pairwise
from uuid import uuid4

import pytest

from noplag_engine.alignment import AlignedPassage, align


def _alphabet() -> str:
    return string.ascii_lowercase + " "


def _random_text(length: int, seed: int) -> str:
    rng = random.Random(seed)
    return "".join(rng.choice(_alphabet()) for _ in range(length))


def test_empty_query_returns_empty():
    assert align(uuid4(), "", [(uuid4(), "anything goes here")]) == []


def test_empty_candidates_returns_empty():
    assert align(uuid4(), "the quick brown fox jumps over the lazy dog", []) == []


def test_query_shorter_than_min_seed_returns_empty():
    # 29 chars < default min_seed_len=30
    assert align(uuid4(), "x" * 29, [(uuid4(), "x" * 100)]) == []


def test_identical_query_and_candidate_full_span_score_one():
    text = "the quick brown fox jumps over the lazy dog. " * 4  # 180 chars
    qid, cid = uuid4(), uuid4()
    result = align(qid, text, [(cid, text)])
    assert len(result) == 1
    p = result[0]
    assert p.query_chunk_id == qid
    assert p.candidate_chunk_id == cid
    assert p.query_start == 0
    assert p.query_end == len(text)
    assert p.candidate_start == 0
    assert p.candidate_end == len(text)
    assert p.score == 1.0


def test_completely_different_random_text_returns_empty():
    query = _random_text(2000, seed=1)
    candidate = _random_text(2000, seed=2)
    # Independent random alphabets — Pr(any 30-char overlap) is negligible.
    assert align(uuid4(), query, [(uuid4(), candidate)]) == []


def test_exact_embedded_passage_with_filler():
    plagiarized = (
        "the rain in spain falls mainly on the plain, but it never rains "
        "after lunchtime on the weekends and never on a tuesday morning."
    )
    assert len(plagiarized) >= 100
    query = "filler1 starting prefix " + plagiarized + " filler2 trailing suffix"
    candidate = "other prefix here xx " + plagiarized + " yy other suffix here"
    q_offset = query.index(plagiarized)
    c_offset = candidate.index(plagiarized)

    result = align(uuid4(), query, [(uuid4(), candidate)])
    assert len(result) == 1
    p = result[0]
    # Extension may stretch into adjacent filler if local match ratio happens
    # to clear the tolerance; but the plagiarized region must be entirely
    # inside the emitted passage.
    assert p.query_start <= q_offset
    assert p.query_end >= q_offset + len(plagiarized)
    assert p.candidate_start <= c_offset
    assert p.candidate_end >= c_offset + len(plagiarized)
    # Same-text segments yield score 1.0 over the plagiarized core; minor
    # neighbour-extension noise can pull it down very slightly.
    assert p.score >= 0.95


def test_near_paraphrase_lands_in_expected_score_band():
    # 500-char passage; keep the first 60 chars exact so a seed anchors.
    # Then change ~10% of the remaining body at random positions.
    original = _random_text(500, seed=10)
    body = list(original[60:])
    rng = random.Random(11)
    n_changes = len(body) // 10
    for i in rng.sample(range(len(body)), n_changes):
        body[i] = "X"
    modified = original[:60] + "".join(body)
    query = "AAA filler heading text " + original + " BBB trailing words"
    candidate = "CCC different leadin " + modified + " DDD different trailing"

    result = align(uuid4(), query, [(uuid4(), candidate)])
    assert len(result) == 1
    p = result[0]
    assert 0.8 <= p.score <= 0.95


def test_multiple_separated_passages_in_one_candidate():
    passage_a = "the quick brown fox jumps over the lazy dog every single morning. "
    passage_b = "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod. "
    # `noise` is built to be unlike either passage — repeated digits won't
    # match any 30-window of alphabetic text.
    noise = "9090909090909090909090909090909090909090"
    query = passage_a * 2 + noise + passage_b * 2
    candidate = "xx " + passage_a * 2 + "0123456789" * 4 + passage_b * 2 + " yy"

    result = align(uuid4(), query, [(uuid4(), candidate)])
    assert len(result) == 2
    # Returned in (query_start, candidate_chunk_id) order; the first
    # passage covers passage_a, the second covers passage_b.
    first, second = result
    assert first.query_start < second.query_start
    assert "quick brown fox" in query[first.query_start : first.query_end]
    assert "lorem ipsum" in query[second.query_start : second.query_end]


def test_multiple_candidates_yield_per_candidate_passages():
    shared_a = "the rain in spain falls mainly on the plain and the wind blows softly. "
    shared_b = "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod. "
    shared_c = "to be or not to be that is the question, whether tis nobler in the mind. "
    query = shared_a + shared_b + shared_c
    candidates = [
        (uuid4(), "preamble xx " + shared_a + " yy postamble more text"),
        (uuid4(), "different lead-in here, then " + shared_b + " then trailing"),
        (uuid4(), "unrelated header words here " + shared_c + " unrelated footer"),
    ]
    result = align(uuid4(), query, candidates)
    assert len(result) == 3
    candidate_ids_returned = {p.candidate_chunk_id for p in result}
    assert candidate_ids_returned == {c[0] for c in candidates}


def test_below_min_seed_length_returns_empty():
    # Shared substring length min_seed_len - 1 = 29 chars
    shared = "x" * 29
    query = "aaaaa " + shared + " bbbbb"
    candidate = "ccccc " + shared + " ddddd"
    result = align(uuid4(), query, [(uuid4(), candidate)])
    assert result == []


def test_deterministic_across_runs():
    plagiarized = (
        "in the beginning god created the heaven and the earth, and the "
        "earth was without form and void, and darkness was upon the face."
    )
    query = "alpha " + plagiarized + " beta"
    candidate = "gamma " + plagiarized + " delta"
    qid, cid = uuid4(), uuid4()
    first = align(qid, query, [(cid, candidate)])
    runs = [align(qid, query, [(cid, candidate)]) for _ in range(4)]
    assert all(r == first for r in runs)


def test_char_offset_lengths_within_ten_percent():
    plagiarized = (
        "the rain in spain falls mainly on the plain. an apple a day keeps "
        "the doctor away, while a stitch in time saves nine on the weekend."
    )
    query = "filler " + plagiarized + " filler"
    candidate = "other " + plagiarized + " other"
    result = align(uuid4(), query, [(uuid4(), candidate)])
    assert len(result) == 1
    p = result[0]
    q_len = p.query_end - p.query_start
    c_len = p.candidate_end - p.candidate_start
    assert q_len > 0
    diff = abs(q_len - c_len) / max(q_len, c_len)
    assert diff <= 0.1


def test_output_sorted_by_query_start_then_candidate_chunk_id():
    passage_x = "the early bird catches the worm before the others wake up that morning. "
    passage_y = "all that glitters is not gold and all that is gold does not always glitter. "
    cid_low = uuid4()
    cid_high = uuid4()
    # Force ordering so we can assert tiebreak behaviour.
    if cid_low > cid_high:
        cid_low, cid_high = cid_high, cid_low
    query = passage_x + passage_y
    candidates = [
        (cid_high, "preamble " + passage_x + " mid " + passage_y + " end"),
        (cid_low, "other " + passage_x + " mid " + passage_y + " final"),
    ]
    result = align(uuid4(), query, candidates)
    assert len(result) == 4
    for prev, current in pairwise(result):
        assert (prev.query_start, prev.candidate_chunk_id) <= (
            current.query_start,
            current.candidate_chunk_id,
        )


def test_aligned_passage_is_frozen():
    p = AlignedPassage(
        query_chunk_id=uuid4(),
        candidate_chunk_id=uuid4(),
        query_start=0,
        query_end=10,
        candidate_start=0,
        candidate_end=10,
        score=1.0,
    )
    with pytest.raises(Exception):  # noqa: B017
        p.score = 0.5  # type: ignore[misc]
