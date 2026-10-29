"""Tests for the VeriScholar configuration module."""


from noplag_engine.vs_config import (
    ScholarlyAPIConfig,
    SemanticConfig,
    VeriScholarConfig,
    get_verischolar_config,
)


def test_defaults():
    cfg = get_verischolar_config()
    assert isinstance(cfg, VeriScholarConfig)
    assert cfg.branding.product_name == "VeriScholar AI"
    assert cfg.scoring.exact_match_weight == 0.5
    assert cfg.scoring.fuzzy_match_weight == 0.25
    assert cfg.scoring.semantic_match_weight == 0.25
    assert cfg.semantic.model_name == "all-MiniLM-L6-v2"
    assert cfg.semantic.embedding_dim == 384
    assert cfg.security.auth_enabled is False


def test_env_override(monkeypatch):
    monkeypatch.setenv("VS_PRODUCT_NAME", "TestScholar")
    monkeypatch.setenv("VS_EXACT_WEIGHT", "0.8")
    monkeypatch.setenv("VS_ENABLE_FUZZY", "0")
    monkeypatch.setenv("VS_AUTH_ENABLED", "1")

    cfg = get_verischolar_config()
    assert cfg.branding.product_name == "TestScholar"
    assert cfg.scoring.exact_match_weight == 0.8
    assert cfg.scoring.enable_fuzzy is False
    assert cfg.security.auth_enabled is True


def test_scholarly_api_config():
    cfg = ScholarlyAPIConfig()
    assert cfg.openalex_max_per_second == 10.0
    assert cfg.arxiv_max_per_second == 1.0
    assert cfg.core_api_key == ""


def test_semantic_config_defaults():
    cfg = SemanticConfig()
    assert cfg.device == "cpu"
    assert cfg.faiss_index_type == "flat"
    assert cfg.semantic_threshold == 0.65
    assert cfg.use_cross_encoder is False
