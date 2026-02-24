"""
Environment readiness tests.

Verifies each env component (Redis, Cosmos, Database, LLM, Key Vault) is detectable.
Uses daibai.core.env_ready logic; tests use monkeypatch for isolation.
"""

import pytest

from daibai.core.env_ready import (
    check_all,
    check_redis,
    check_cosmos,
    check_database,
    check_llm,
    check_keyvault,
)


def test_check_redis_detects_connection_string(monkeypatch):
    """REDIS_URL or AZURE_REDIS_CONNECTION_STRING sets redis ok."""
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("AZURE_REDIS_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("REDIS_USE_ENTRA_ID", raising=False)
    monkeypatch.delenv("AZURE_REDIS_HOST", raising=False)

    ok, msg = check_redis()
    assert not ok

    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    ok, msg = check_redis()
    assert ok
    assert "set" in msg

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("AZURE_REDIS_CONNECTION_STRING", "rediss://:x@host:6380")
    ok, msg = check_redis()
    assert ok


def test_check_redis_detects_entra(monkeypatch):
    """REDIS_USE_ENTRA_ID + AZURE_REDIS_HOST sets redis ok."""
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("AZURE_REDIS_CONNECTION_STRING", raising=False)
    monkeypatch.setenv("REDIS_USE_ENTRA_ID", "1")
    monkeypatch.setenv("AZURE_REDIS_HOST", "mycache.redis.cache.windows.net")

    ok, msg = check_redis()
    assert ok
    assert "Entra" in msg


def test_check_cosmos_detects_endpoint(monkeypatch):
    """COSMOS_ENDPOINT sets cosmos ok."""
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    ok, msg = check_cosmos()
    assert not ok

    monkeypatch.setenv("COSMOS_ENDPOINT", "https://x.documents.azure.com:443/")
    ok, msg = check_cosmos()
    assert ok


def test_check_llm_detects_api_key(monkeypatch):
    """At least one LLM key sets llm ok."""
    for k in ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("KEY_VAULT_URL", raising=False)

    ok, msg = check_llm()
    assert not ok

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    ok, msg = check_llm()
    assert ok
    assert "GEMINI" in msg


def test_check_keyvault_detects_url(monkeypatch):
    """KEY_VAULT_URL sets keyvault ok."""
    monkeypatch.delenv("KEY_VAULT_URL", raising=False)
    ok, msg = check_keyvault()
    assert not ok

    monkeypatch.setenv("KEY_VAULT_URL", "https://myvault.vault.azure.net/")
    ok, msg = check_keyvault()
    assert ok


def test_check_all_returns_all_components():
    """check_all returns dict with redis, cosmos, database, llm, keyvault."""
    results = check_all()
    assert set(results.keys()) == {"redis", "cosmos", "database", "llm", "keyvault"}
    for comp, (ok, msg, required) in results.items():
        assert isinstance(ok, bool)
        assert isinstance(msg, str)
        assert isinstance(required, bool)


def test_check_all_values_have_three_tuple():
    """Each component value is (ok, message, required)."""
    results = check_all()
    for comp, val in results.items():
        assert len(val) == 3
        ok, msg, req = val
        assert isinstance(ok, bool)
        assert isinstance(msg, str)
        assert isinstance(req, bool)
