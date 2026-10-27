"""Unit tests for the fingerprint config + language detection."""

from __future__ import annotations

from noplag_engine.config import FingerprintConfig, get_fingerprint_config
from noplag_engine.ingestion.language import detect_language, pysbd_language


def test_config_defaults():
    cfg = get_fingerprint_config()
    assert cfg == FingerprintConfig(k=5, w=8, sentences_per_chunk=4, chunk_overlap=1)


def test_config_env_override(monkeypatch):
    monkeypatch.setenv("NOPLAG_FP_K", "7")
    monkeypatch.setenv("NOPLAG_FP_W", "12")
    monkeypatch.setenv("NOPLAG_CHUNK_SENTENCES", "6")
    monkeypatch.setenv("NOPLAG_CHUNK_OVERLAP", "2")
    cfg = get_fingerprint_config()
    assert (cfg.k, cfg.w, cfg.sentences_per_chunk, cfg.chunk_overlap) == (7, 12, 6, 2)


def test_detect_language_english():
    assert detect_language(
        "This is a clearly English sentence with enough words to classify."
    ) == "en"


def test_detect_language_empty_falls_back_to_english():
    assert detect_language("") == "en"
    assert detect_language("   \n  ") == "en"


def test_detect_language_is_deterministic():
    text = "Ceci est une phrase en français avec suffisamment de mots pour décider."
    assert detect_language(text) == detect_language(text)


def test_pysbd_language_maps_unsupported_to_english():
    assert pysbd_language("en") == "en"
    assert pysbd_language("fr") == "fr"
    # A code pysbd can't segment falls back to English rules.
    assert pysbd_language("sw") == "en"
