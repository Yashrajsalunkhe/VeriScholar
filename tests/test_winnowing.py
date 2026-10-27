from noplag_engine.fingerprinting import fingerprint

INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1


def test_empty_string_returns_empty():
    assert fingerprint("") == []


def test_whitespace_only_returns_empty():
    # Normalization collapses whitespace to "" (empty after strip).
    assert fingerprint("    \n\t  ") == []


def test_text_shorter_than_k_returns_empty():
    assert fingerprint("abc", k=5, w=8) == []
    assert fingerprint("abcd", k=5, w=8) == []


def test_text_exactly_k_yields_one_fingerprint():
    # Exactly k characters produces 1 k-gram, 1 hash, single-window degenerate.
    result = fingerprint("hello", k=5, w=8)
    assert len(result) == 1


def test_text_between_k_and_w_uses_degenerate_window():
    # 10 chars with k=5 -> 6 k-grams = 6 hashes; 6 <= w=8 -> degenerate
    # single window -> one fingerprint (the minimum of all hashes).
    result = fingerprint("a" * 10, k=5, w=8)
    assert len(result) == 1


def test_determinism():
    text = "The quick brown fox jumps over the lazy dog."
    assert fingerprint(text) == fingerprint(text)
    # Re-run several times to catch hash-randomization regressions.
    runs = [fingerprint(text) for _ in range(5)]
    assert all(r == runs[0] for r in runs)


def test_normalization_makes_input_case_and_whitespace_insensitive():
    a = fingerprint("The Quick Brown Fox Jumps Over The Lazy Dog")
    b = fingerprint("the   quick\tbrown\nfox jumps over the lazy dog")
    assert a == b


def test_substantial_overlap_produces_intersection():
    common = "the quick brown fox jumps over the lazy dog " * 5
    doc1 = "unique prefix of doc one " + common + " differing suffix one"
    doc2 = "another unique prefix doc " + common + " differing suffix two"
    f1, f2 = set(fingerprint(doc1)), set(fingerprint(doc2))
    assert len(f1 & f2) >= 5


def test_unrelated_docs_have_minimal_intersection():
    doc1 = "the quick brown fox jumps over the lazy dog. " * 3
    doc2 = "lorem ipsum dolor sit amet consectetur adipiscing elit. " * 3
    f1, f2 = set(fingerprint(doc1)), set(fingerprint(doc2))
    assert len(f1 & f2) < 5


def test_long_common_substring_produces_shared_fingerprint():
    # Schleimer matching guarantee: any common substring of length
    # >= w + k - 1 (=12 with defaults) produces at least one shared
    # fingerprint. 25-char common substring far exceeds the threshold.
    common = "the quick brown fox jumps"  # 25 chars
    doc1 = "x" * 50 + " " + common + " " + "y" * 50
    doc2 = "p" * 50 + " " + common + " " + "q" * 50
    f1, f2 = set(fingerprint(doc1)), set(fingerprint(doc2))
    assert len(f1 & f2) >= 1


def test_output_fits_in_signed_int64_range():
    text = "the quick brown fox jumps over the lazy dog. " * 50
    for h in fingerprint(text):
        assert INT64_MIN <= h <= INT64_MAX


def test_different_k_changes_output():
    text = "the quick brown fox jumps over the lazy dog"
    assert fingerprint(text, k=5, w=8) != fingerprint(text, k=6, w=8)


def test_different_w_changes_output():
    text = "the quick brown fox jumps over the lazy dog. " * 10
    short_window = fingerprint(text, k=5, w=4)
    long_window = fingerprint(text, k=5, w=12)
    assert short_window != long_window
    # A wider window selects fewer fingerprints — selection density drops
    # as the window grows.
    assert len(long_window) <= len(short_window)


# --- citation / editorial marker stripping (credibility sprint #2) ----------
from noplag_engine.fingerprinting.winnowing import _normalize  # noqa: E402


def test_citation_markers_make_pasted_text_match_marker_free_corpus():
    # A Wikipedia-style paste (numeric + editorial markers) must normalize
    # identically to the marker-free corpus copy, so fingerprints align.
    pasted = "The fact was true.[12] However it changed.[citation needed] Later[3] it held."
    corpus = "The fact was true. However it changed. Later it held."
    assert _normalize(pasted) == _normalize(corpus)
    assert fingerprint(pasted) == fingerprint(corpus)


def test_normalize_keeps_legitimate_bracketed_content():
    # Only citation-like markers are stripped; real bracketed prose survives.
    assert "[the appendix]" in _normalize("See [the appendix] for the full proof")
    assert "[redacted]" in _normalize("the value was [redacted] in the report")
