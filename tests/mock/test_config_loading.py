import os
import pytest

from daibai.core.config import load_config, Config


def test_load_config_without_graph_env(monkeypatch, tmp_path):
    """
    Ensure load_config works when GRAPH/robot credentials are not set.
    This is a mock/unit test that should not attempt network calls.
    """
    # Ensure relevant env vars are not set
    monkeypatch.delenv("AUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("AUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("AUTH_TENANT_ID", raising=False)

    # Create an empty env file to prevent loading repository .env during the test
    env_file = tmp_path / ".env"
    env_file.write_text("")
    cfg = load_config(config_path=None, env_path=env_file)
    assert isinstance(cfg, Config)
    # validate_auth_config should be a no-op and return False when creds are absent
    assert cfg.validate_auth_config(fail_on_error=False) is False

