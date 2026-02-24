"""
Environment readiness tests.

Verifies EnvValidator catches placeholders and missing keys.
Verifies component checks (redis, cosmos, database, llm, keyvault).
"""

import os
from unittest import mock

import pytest

from daibai.core.env_ready import (
    EnvValidator,
    check_all,
    check_cosmos,
    check_database,
    check_keyvault,
    check_llm,
    check_redis,
)


def test_env_validator_success():
    """EnvValidator passes with valid env."""
    valid_env = {
        "DB_HOST": "localhost",
        "DB_USER": "root",
        "DB_PASSWORD": "realpassword123",
        "DB_NAME": "testdb",
        "GEMINI_API_KEY": "AIzaSyRealKey",
    }
    with mock.patch.dict(os.environ, valid_env, clear=True):
        with mock.patch("daibai.core.env_ready._database_configured_via_yaml", return_value=False):
            is_valid, issues = EnvValidator.validate()
            assert is_valid is True
            assert len(issues) == 0


def test_env_validator_success_with_mysql_keys():
    """EnvValidator passes with MYSQL_* keys (alias)."""
    valid_env = {
        "MYSQL_HOST": "localhost",
        "MYSQL_USER": "root",
        "MYSQL_PASSWORD": "realpassword123",
        "MYSQL_DATABASE": "testdb",
        "GEMINI_API_KEY": "AIzaSyRealKey",
    }
    with mock.patch.dict(os.environ, valid_env, clear=True):
        # Mock daibai.yaml as not having DB config so we use env
        with mock.patch("daibai.core.env_ready._database_configured_via_yaml", return_value=False):
            is_valid, issues = EnvValidator.validate()
            assert is_valid is True
            assert len(issues) == 0


def test_env_validator_missing_db_keys():
    """EnvValidator fails when DB keys are missing."""
    invalid_env = {
        "DB_HOST": "localhost",
        "GEMINI_API_KEY": "AIzaSyRealKey",
        # Missing DB_USER, DB_PASSWORD, DB_NAME
    }
    with mock.patch.dict(os.environ, invalid_env, clear=True):
        with mock.patch("daibai.core.env_ready._database_configured_via_yaml", return_value=False):
            is_valid, issues = EnvValidator.validate()
            assert is_valid is False
            assert "DB_USER" in issues
            assert "DB_PASSWORD" in issues
            assert "DB_NAME" in issues


def test_env_validator_placeholder_detection():
    """EnvValidator fails when keys contain placeholder values."""
    placeholder_env = {
        "DB_HOST": "localhost",
        "DB_USER": "root",
        "DB_PASSWORD": "your_password",  # Invalid placeholder
        "DB_NAME": "testdb",
        "GEMINI_API_KEY": "your-api-key",  # Invalid placeholder
    }
    with mock.patch.dict(os.environ, placeholder_env, clear=True):
        with mock.patch("daibai.core.env_ready._database_configured_via_yaml", return_value=False):
            is_valid, issues = EnvValidator.validate()
            assert is_valid is False
            assert "DB_PASSWORD" in issues
            assert any("AT_LEAST_ONE_OF" in issue for issue in issues)


def test_env_validator_passes_with_daibai_yaml_db(monkeypatch):
    """EnvValidator passes when database is from daibai.yaml."""
    monkeypatch.setattr("daibai.core.env_ready._database_configured_via_yaml", lambda: True)
    with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "AIzaSyRealKey"}, clear=True):
        is_valid, issues = EnvValidator.validate()
        assert is_valid is True
        assert len(issues) == 0


def test_check_redis_detects_connection_string(monkeypatch):
    """REDIS_URL or AZURE_REDIS_CONNECTION_STRING sets redis ok."""
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("AZURE_REDIS_CONNECTION_STRING", raising=False)
    ok, msg = check_redis()
    assert not ok
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    ok, msg = check_redis()
    assert ok
    assert "set" in msg


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
