"""Tests for configuration loading."""

import pytest
from pathlib import Path
import tempfile
import os

from daiby.core.config import (
    load_config,
    Config,
    DatabaseConfig,
    LLMProviderConfig,
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
    with tempfile.TemporaryDirectory() as tmpdir:
        os.chdir(tmpdir)
        config = load_config()
        
        assert isinstance(config, Config)
        assert len(config.databases) == 0
        assert len(config.llm_providers) == 0


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
        config_path = Path(tmpdir) / "daiby.yaml"
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
