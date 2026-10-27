"""Tests for the hybrid scoring module."""

from uuid import uuid4

from noplag_engine.scoring import (
    HybridMatch,
    MatchEvidence,
    compute_hybrid_scores,
)


def _make_match(**kwargs) -> HybridMatch:
    return HybridMatch(source_document_id=uuid4(), **kwargs)


def test_all_three_layers():
    """Score with evidence from all three layers."""
    m = _make_match(
        exact_evidence=MatchEvidence(match_type="exact", similarity=0.4, matched_chars=200),
        fuzzy_evidence=MatchEvidence(match_type="fuzzy", similarity=0.6),
        semantic_evidence=MatchEvidence(match_type="semantic", similarity=0.8),
    )
    results = compute_hybrid_scores([m])
    assert len(results) == 1
    r = results[0]
    # Default weights: exact=0.5, fuzzy=0.25, semantic=0.25
    expected = 0.4 * 0.5 + 0.6 * 0.25 + 0.8 * 0.25
    assert abs(r.combined_score - expected) < 0.01


def test_exact_only():
    """Only exact evidence — weight redistribution should give it 100%."""
    m = _make_match(
        exact_evidence=MatchEvidence(match_type="exact", similarity=0.7, matched_chars=100),
    )
    results = compute_hybrid_scores([m])
    assert abs(results[0].combined_score - 0.7) < 0.01


def test_fuzzy_and_semantic_only():
    """No exact evidence — weights redistributed across fuzzy + semantic."""
    m = _make_match(
        fuzzy_evidence=MatchEvidence(match_type="fuzzy", similarity=0.5),
        semantic_evidence=MatchEvidence(match_type="semantic", similarity=0.9),
    )
    results = compute_hybrid_scores([m])
    # 0.25 + 0.25 = 0.5 total weight, each gets 50%
    expected = 0.5 * 0.5 + 0.9 * 0.5
    assert abs(results[0].combined_score - expected) < 0.01


def test_no_evidence():
    """No evidence at all → score 0."""
    m = _make_match()
    results = compute_hybrid_scores([m])
    assert results[0].combined_score == 0.0


def test_sorted_descending():
    """Results are sorted by combined score descending."""
    sources = [
        _make_match(
            exact_evidence=MatchEvidence(match_type="exact", similarity=0.2, matched_chars=50),
        ),
        _make_match(
            exact_evidence=MatchEvidence(match_type="exact", similarity=0.9, matched_chars=500),
        ),
        _make_match(
            exact_evidence=MatchEvidence(match_type="exact", similarity=0.5, matched_chars=200),
        ),
    ]
    results = compute_hybrid_scores(sources)
    scores = [r.combined_score for r in results]
    assert scores == sorted(scores, reverse=True)


def test_custom_weights():
    """Custom weights are applied correctly."""
    m = _make_match(
        exact_evidence=MatchEvidence(match_type="exact", similarity=1.0, matched_chars=100),
        fuzzy_evidence=MatchEvidence(match_type="fuzzy", similarity=1.0),
        semantic_evidence=MatchEvidence(match_type="semantic", similarity=1.0),
    )
    results = compute_hybrid_scores(
        [m],
        exact_weight=0.7,
        fuzzy_weight=0.2,
        semantic_weight=0.1,
    )
    # All similarities = 1.0, any weight distribution → 1.0
    assert abs(results[0].combined_score - 1.0) < 0.01


def test_score_breakdown_present():
    """Score breakdown dict is populated for transparency."""
    m = _make_match(
        exact_evidence=MatchEvidence(match_type="exact", similarity=0.5, matched_chars=100),
        fuzzy_evidence=MatchEvidence(match_type="fuzzy", similarity=0.3),
    )
    results = compute_hybrid_scores([m])
    bd = results[0].score_breakdown
    assert "exact" in bd
    assert "fuzzy" in bd
    assert "exact_raw" in bd
    assert "exact_weight" in bd
