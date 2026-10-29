"""Tests for the L2 fuzzy alignment stage (chunk-level near-verbatim).

These exercise the detection gap fuzzy_align exists to close: text that
shares winnowing fingerprints with a source (so L1 surfaces it) but has no
30-char exact run for seed-extend to seed on (so the exact layer reports
nothing). The source for each paraphrase case is verified to be a real miss
for seed-extend in test_fuzzy_align_catches_what_seed_extend_misses.
"""

from __future__ import annotations

from uuid import uuid4

from noplag_engine.alignment import align
from noplag_engine.fuzzy.align import fuzzy_align

_SOURCE = (
    "Climate change poses a significant threat to global biodiversity, "
    "altering habitats and disrupting the delicate ecological balance that "
    "species depend upon for their continued survival across many regions."
)
# Synonym substitution + reordering, same meaning and length — no long exact run.
_PARAPHRASE = (
    "Climate change represents a major danger to worldwide biodiversity, "
    "changing habitats and upsetting the fragile ecological balance that "
    "organisms rely upon for their ongoing survival across numerous regions."
)


def test_fuzzy_align_flags_paraphrase():
    cid = uuid4()
    passages = fuzzy_align(uuid4(), _PARAPHRASE, [(cid, _SOURCE)])
    assert len(passages) == 1
    p = passages[0]
    assert p.match_type == "fuzzy"
    assert p.candidate_chunk_id == cid
    assert p.query_start == 0
    assert p.query_end == len(_PARAPHRASE)
    assert 0.0 < p.score <= 1.0


def test_fuzzy_align_catches_what_seed_extend_misses():
    """The whole point: seed-extend returns nothing on this pair, fuzzy does not."""
    verbatim = align(uuid4(), _PARAPHRASE, [(uuid4(), _SOURCE)])
    assert verbatim == []  # exact layer is blind to this paraphrase
    fuzzy = fuzzy_align(uuid4(), _PARAPHRASE, [(uuid4(), _SOURCE)])
    assert len(fuzzy) == 1  # fuzzy layer catches it


def test_fuzzy_align_ignores_unrelated_text():
    unrelated = (
        "The mitochondrion is the powerhouse of the cell, generating most of "
        "the chemical energy needed to power the cell's biochemical reactions "
        "through the process of oxidative phosphorylation in eukaryotes."
    )
    passages = fuzzy_align(uuid4(), _PARAPHRASE, [(uuid4(), unrelated)])
    assert passages == []


def test_fuzzy_align_respects_threshold():
    # A very high threshold rejects even a strong paraphrase.
    strict = fuzzy_align(uuid4(), _PARAPHRASE, [(uuid4(), _SOURCE)], threshold=0.99)
    assert strict == []
    # A permissive one accepts it.
    loose = fuzzy_align(uuid4(), _PARAPHRASE, [(uuid4(), _SOURCE)], threshold=0.3)
    assert len(loose) == 1


def test_fuzzy_align_skips_short_text():
    assert fuzzy_align(uuid4(), "too short", [(uuid4(), _SOURCE)]) == []
    assert fuzzy_align(uuid4(), _PARAPHRASE, [(uuid4(), "short")]) == []


def test_fuzzy_align_skips_length_mismatch():
    # Candidate ~5x longer than the query chunk: not a chunk-level paraphrase.
    long_candidate = _SOURCE * 5
    assert fuzzy_align(uuid4(), _PARAPHRASE, [(uuid4(), long_candidate)]) == []


def test_fuzzy_align_sorts_by_score_descending():
    near = _PARAPHRASE
    far = (
        "Global warming is a serious concern for the planet's many ecosystems "
        "and the varied creatures that inhabit them in different places today."
    )
    cid_near, cid_far = uuid4(), uuid4()
    passages = fuzzy_align(
        uuid4(),
        _PARAPHRASE,
        [(cid_far, far), (cid_near, near)],
        threshold=0.2,
    )
    scores = [p.score for p in passages]
    assert scores == sorted(scores, reverse=True)


def test_fuzzy_align_empty_inputs():
    assert fuzzy_align(uuid4(), "", [(uuid4(), _SOURCE)]) == []
    assert fuzzy_align(uuid4(), _PARAPHRASE, []) == []
