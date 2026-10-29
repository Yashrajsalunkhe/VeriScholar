"""Tests for scholarly dedup and data models."""

from noplag_engine.scholarly.dedup import normalize_doi
from noplag_engine.scholarly.models import (
    AvailabilityStatus,
    ScholarlyMetadata,
)
from noplag_engine.scholarly.openalex import _reconstruct_abstract


def test_normalize_doi_strips_url_prefix():
    assert normalize_doi("https://doi.org/10.1234/test.123") == "10.1234/test.123"


def test_normalize_doi_strips_doi_prefix():
    assert normalize_doi("DOI: 10.5678/abc") == "10.5678/abc"
    assert normalize_doi("doi:10.5678/abc") == "10.5678/abc"


def test_normalize_doi_lowercases():
    assert normalize_doi("10.1234/ABC") == "10.1234/abc"


def test_normalize_doi_none():
    assert normalize_doi(None) is None
    assert normalize_doi("") is None
    assert normalize_doi("  ") is None


def test_scholarly_metadata_has_identifier():
    m = ScholarlyMetadata(doi="10.1234/test")
    assert m.has_identifier()

    m2 = ScholarlyMetadata(arxiv_id="2301.07041")
    assert m2.has_identifier()

    m3 = ScholarlyMetadata()
    assert not m3.has_identifier()


def test_scholarly_metadata_normalized_title():
    m = ScholarlyMetadata(title="  The   Quick   Brown FOX  ")
    assert m.normalized_title() == "the quick brown fox"


def test_scholarly_metadata_primary_identifier():
    m1 = ScholarlyMetadata(doi="10.1234/test", arxiv_id="2301.07041")
    assert m1.primary_identifier() == "10.1234/test"  # DOI takes priority

    m2 = ScholarlyMetadata(arxiv_id="2301.07041")
    assert m2.primary_identifier() == "2301.07041"

    m3 = ScholarlyMetadata()
    assert m3.primary_identifier() == "(no id)"


def test_reconstruct_abstract():
    inv_idx = {
        "The": [0],
        "quick": [1],
        "brown": [2],
        "fox": [3],
    }
    result = _reconstruct_abstract(inv_idx)
    assert result == "The quick brown fox"


def test_reconstruct_abstract_none():
    assert _reconstruct_abstract(None) is None
    assert _reconstruct_abstract({}) is None


def test_availability_enum():
    assert AvailabilityStatus.FULL_TEXT == "full_text"
    assert AvailabilityStatus.ABSTRACT_ONLY == "abstract"
    assert AvailabilityStatus.METADATA_ONLY == "metadata"
