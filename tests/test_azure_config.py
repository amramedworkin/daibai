"""Tests for Azure Key Vault configuration integration."""

import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from daibai.core.config import load_config, Config


def test_load_from_keyvault():
    """When KEY_VAULT_URL is set, only fetches secrets for configured providers (e.g. OPENAI-API-KEY for openai)."""
    yaml_path = Path.cwd() / "daibai_test_keyvault.yaml"
    yaml_content = """
llm:
  default: openai_provider
  providers:
    openai_provider:
      type: openai
      model: gpt-4
      api_key: ""
"""
    yaml_path.write_text(yaml_content)

    with patch("daibai.core.config._fetch_secrets_from_keyvault") as mock_fetch:
        mock_fetch.return_value = {"OPENAI-API-KEY": "test-openai-key"}
        try:
            os.environ["KEY_VAULT_URL"] = "https://test-vault.vault.azure.net/"
            config = load_config(config_path=yaml_path)
            mock_fetch.assert_called_once()
            args, kwargs = mock_fetch.call_args
            assert args[0] == "https://test-vault.vault.azure.net/"
            assert kwargs.get("secret_names") == ["OPENAI-API-KEY"]
            assert config.llm_providers["openai_provider"].api_key == "test-openai-key"
        finally:
            os.environ.pop("KEY_VAULT_URL", None)
            if yaml_path.exists():
                yaml_path.unlink()


def test_fallback_to_local(tmp_path):
    """When KEY_VAULT_URL is not set, config loads from local files without calling Azure."""
    with patch("daibai.core.config._fetch_secrets_from_keyvault") as mock_fetch:
        os.environ.pop("KEY_VAULT_URL", None)

        yaml_path = tmp_path / "daibai_test_fallback.yaml"
        yaml_content = """
llm:
  default: gemini_provider
  providers:
    gemini_provider:
      type: gemini
      model: gemini-2.5-pro
      api_key: local-key-123
"""
        yaml_path.write_text(yaml_content)
        # Use env_path without KEY_VAULT_URL so load_config does not load project .env
        env_path = tmp_path / ".env"
        env_path.write_text("# empty env for fallback test\n")

        config = load_config(yaml_path, env_path=env_path)
        mock_fetch.assert_not_called()
        assert "gemini_provider" in config.llm_providers
        assert config.llm_providers["gemini_provider"].api_key == "local-key-123"
