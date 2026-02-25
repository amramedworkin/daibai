"""Tests for Azure Key Vault configuration integration."""

import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from daibai.core.config import load_config, Config


def test_load_from_keyvault():
    """When KEY_VAULT_URL is set, SecretClient.get_secret is called for expected keys."""
    mock_secret = MagicMock()
    mock_secret.value = "test-openai-key"

    mock_client = MagicMock()
    mock_client.get_secret.side_effect = lambda name: (
        mock_secret if name == "OPENAI-API-KEY" else MagicMock(value=None)
    )

    with patch("azure.identity.DefaultAzureCredential"):
        with patch("azure.keyvault.secrets.SecretClient", return_value=mock_client):
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

            try:
                os.environ["KEY_VAULT_URL"] = "https://test-vault.vault.azure.net/"
                config = load_config(yaml_path)
                mock_client.get_secret.assert_called()
                call_names = [c[0][0] for c in mock_client.get_secret.call_args_list]
                assert "OPENAI-API-KEY" in call_names
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
