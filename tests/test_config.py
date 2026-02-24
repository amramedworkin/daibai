"""Tests for configuration loading."""

import pytest
from pathlib import Path
import tempfile
import os

from daibai.core.config import (
    load_config,
    Config,
    CacheConfig,
    DatabaseConfig,
    LLMProviderConfig,
    get_semantic_similarity_threshold,
    get_schema_vector_limit,
    get_schema_refresh_interval,
    _resolve_env_vars,
)


def test_resolve_env_vars():
    """Test environment variable resolution."""
    os.environ["TEST_VAR"] = "test_value"
    
    result = _resolve_env_vars("Hello ${TEST_VAR}")
    assert result == "Hello test_value"
    
    result = _resolve_env_vars({"key": "${TEST_VAR}"})
    assert result == {"key": "test_value"}
    
    del os.environ["TEST_VAR"]


def test_database_config():
    """Test DatabaseConfig creation."""
    config = DatabaseConfig(
        name="test",
        host="localhost",
        port=3306,
        database="testdb",
        user="user",
        password="pass",
    )
    
    assert config.name == "test"
    assert config.host == "localhost"
    assert config.port == 3306
    assert "mysql://" in config.connection_string()


def test_llm_provider_config():
    """Test LLMProviderConfig creation."""
    config = LLMProviderConfig(
        name="test",
        provider_type="gemini",
        model="gemini-2.5-pro",
        api_key="test-key",
    )
    
    assert config.name == "test"
    assert config.provider_type == "gemini"
    assert config.model == "gemini-2.5-pro"


def test_load_config_empty():
    """Test loading config when no file exists."""
    orig_cwd = os.getcwd()
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            os.chdir(tmpdir)
            config = load_config()
            assert isinstance(config, Config)
            assert len(config.databases) == 0
            assert len(config.llm_providers) == 0
    finally:
        os.chdir(orig_cwd)


def test_load_config_from_yaml():
    """Test loading config from YAML file."""
    yaml_content = """
llm:
  default: test_llm
  providers:
    test_llm:
      type: gemini
      model: gemini-2.5-pro
      api_key: test-key

databases:
  default: test_db
  test_db:
    host: localhost
    port: 3306
    name: testdb
    user: testuser
    password: testpass
"""
    
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "daibai.yaml"
        config_path.write_text(yaml_content)
        
        config = load_config(config_path)
        
        assert "test_db" in config.databases
        assert config.default_database == "test_db"
        assert "test_llm" in config.llm_providers
        assert config.default_llm == "test_llm"


def test_config_get_database():
    """Test getting database config."""
    config = Config(
        databases={
            "db1": DatabaseConfig("db1", "host1", 3306, "db1", "user", "pass"),
            "db2": DatabaseConfig("db2", "host2", 3306, "db2", "user", "pass"),
        },
        default_database="db1",
    )
    
    db = config.get_database("db2")
    assert db.name == "db2"
    
    db = config.get_database()  # Should return default
    assert db.name == "db1"
    
    with pytest.raises(ValueError):
        config.get_database("nonexistent")


def test_config_get_llm():
    """Test getting LLM provider config."""
    config = Config(
        llm_providers={
            "llm1": LLMProviderConfig("llm1", "gemini", "model1"),
            "llm2": LLMProviderConfig("llm2", "openai", "model2"),
        },
        default_llm="llm1",
    )
    
    llm = config.get_llm("llm2")
    assert llm.name == "llm2"
    
    llm = config.get_llm()  # Should return default
    assert llm.name == "llm1"
    
    with pytest.raises(ValueError):
        config.get_llm("nonexistent")


def test_cache_config_default():
    """CacheConfig defaults to CACHE_THRESHOLD=0.90."""
    cfg = CacheConfig()
    assert cfg.CACHE_THRESHOLD == 0.90


def test_cache_config_validator_clamps():
    """CACHE_THRESHOLD validator clamps to 0.0–1.0."""
    assert CacheConfig(CACHE_THRESHOLD=0.5).CACHE_THRESHOLD == 0.5
    assert CacheConfig(CACHE_THRESHOLD=0.0).CACHE_THRESHOLD == 0.0
    assert CacheConfig(CACHE_THRESHOLD=1.0).CACHE_THRESHOLD == 1.0
    assert CacheConfig(CACHE_THRESHOLD=1.5).CACHE_THRESHOLD == 1.0
    assert CacheConfig(CACHE_THRESHOLD=-0.1).CACHE_THRESHOLD == 0.0


def test_get_semantic_similarity_threshold(monkeypatch):
    """get_semantic_similarity_threshold reads CACHE_THRESHOLD from env."""
    monkeypatch.setenv("CACHE_THRESHOLD", "0.85")
    monkeypatch.setenv("SEMANTIC_SIMILARITY_THRESHOLD", "")
    assert get_semantic_similarity_threshold() == 0.85

    monkeypatch.setenv("CACHE_THRESHOLD", "")
    monkeypatch.setenv("SEMANTIC_SIMILARITY_THRESHOLD", "0.95")
    assert get_semantic_similarity_threshold() == 0.95

    # Default when both empty (set to empty; load_dotenv won't override existing)
    monkeypatch.setenv("CACHE_THRESHOLD", "")
    monkeypatch.setenv("SEMANTIC_SIMILARITY_THRESHOLD", "")
    assert get_semantic_similarity_threshold() == 0.90


def test_get_schema_vector_limit_default(monkeypatch):
    """get_schema_vector_limit defaults to 12 when unset (supports complex JOINs)."""
    monkeypatch.delenv("SCHEMA_VECTOR_LIMIT", raising=False)
    monkeypatch.setenv("SCHEMA_VECTOR_LIMIT", "")
    assert get_schema_vector_limit() == 12


def test_get_schema_vector_limit_clamped(monkeypatch):
    """get_schema_vector_limit clamps to 1–20."""
    monkeypatch.setenv("SCHEMA_VECTOR_LIMIT", "10")
    assert get_schema_vector_limit() == 10

    monkeypatch.setenv("SCHEMA_VECTOR_LIMIT", "0")
    assert get_schema_vector_limit() == 1  # clamped to minimum 1

    monkeypatch.setenv("SCHEMA_VECTOR_LIMIT", "25")
    assert get_schema_vector_limit() == 20


def test_get_schema_refresh_interval_default(monkeypatch):
    """get_schema_refresh_interval defaults to 86400 (24h) when unset."""
    monkeypatch.delenv("SCHEMA_REFRESH_INTERVAL", raising=False)
    monkeypatch.setenv("SCHEMA_REFRESH_INTERVAL", "")
    assert get_schema_refresh_interval() == 86400


def test_get_schema_refresh_interval_minimum(monkeypatch):
    """get_schema_refresh_interval enforces minimum 60 seconds."""
    monkeypatch.setenv("SCHEMA_REFRESH_INTERVAL", "30")
    assert get_schema_refresh_interval() == 60

    monkeypatch.setenv("SCHEMA_REFRESH_INTERVAL", "3600")
    assert get_schema_refresh_interval() == 3600
