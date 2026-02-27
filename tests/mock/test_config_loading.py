import pytest

from daibai.core.config import load_config, Config


def test_load_config_basic(tmp_path):
    """Ensure load_config returns a Config instance with no env file present."""
    env_file = tmp_path / ".env"
    env_file.write_text("")
    cfg = load_config(config_path=None, env_path=env_file)
    assert isinstance(cfg, Config)
