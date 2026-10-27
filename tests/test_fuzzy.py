"""Tests for the fuzzy matching module."""

from noplag_engine.fuzzy import (
    combined_fuzzy_score,
    jaccard_similarity,
    lcs_ratio,
    ngram_similarity,
    tokenize,
)


def test_tokenize_removes_stopwords():
    tokens = tokenize("The quick brown fox jumps over the lazy dog")
    assert "the" not in tokens
    assert "quick" in tokens
    assert "brown" in tokens
    assert "fox" in tokens


def test_jaccard_identical():
    score = jaccard_similarity("hello world", "hello world")
    assert score == 1.0


def test_jaccard_disjoint():
    score = jaccard_similarity("hello world", "goodbye moon")
    assert score == 0.0


def test_jaccard_partial_overlap():
    score = jaccard_similarity(
        "The quick brown fox jumps",
        "The fast brown fox leaps",
    )
    assert 0.2 < score < 0.8


def test_jaccard_word_reordering():
    """Jaccard should be insensitive to word order."""
    a = "machine learning for natural language processing"
    b = "natural language processing for machine learning"
    score = jaccard_similarity(a, b)
    assert score > 0.8


def test_ngram_identical():
    score = ngram_similarity("hello world", "hello world")
    assert score == 1.0


def test_ngram_similar():
    score = ngram_similarity("plagiarism", "plagarism")  # common misspelling
    assert score > 0.6


def test_ngram_different():
    score = ngram_similarity("hello", "world")
    assert score < 0.3


def test_lcs_identical():
    score = lcs_ratio("hello world", "hello world")
    assert score == 1.0


def test_lcs_partial():
    score = lcs_ratio(
        "the cat sat on the mat",
        "the cat sat quietly on the soft mat",
    )
    assert score > 0.5


def test_combined_produces_bounded_score():
    score = combined_fuzzy_score(
        "The quick brown fox jumps over the lazy dog",
        "The fast brown fox leaps over the lazy dog",
    )
    assert 0.0 <= score <= 1.0


def test_combined_identical():
    score = combined_fuzzy_score("same text here", "same text here")
    assert score > 0.95


def test_combined_empty():
    assert combined_fuzzy_score("", "") == 0.0
    assert combined_fuzzy_score("hello", "") == 0.0
