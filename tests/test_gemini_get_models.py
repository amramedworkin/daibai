"""
Isolated test for Gemini Get Models request.

Run with: pytest tests/test_gemini_get_models.py -v
Or via CLI: ./scripts/cli.sh test gemini

Live test uses API key from: daibai.yaml (llm.providers.*.api_key for type=gemini),
.env, or GEMINI_API_KEY env var.
"""

import json
from unittest.mock import patch

import pytest

from daibai.api.model_discovery import fetch_provider_models
from daibai.core.config import load_config


def _get_gemini_api_key():
    """Get Gemini API key from config (yaml + .env) or GEMINI_API_KEY env."""
    config = load_config()
    for name, cfg in config.llm_providers.items():
        if (cfg.provider_type or "").lower() == "gemini" and cfg.api_key:
            return cfg.api_key
    import os
    return os.environ.get("GEMINI_API_KEY") or None


@pytest.mark.asyncio
async def test_gemini_get_models_mocked():
    """Gemini get models with mocked HTTP (no API key needed)."""
    mock_body = {
        "models": [
            {"name": "models/gemini-2.5-pro"},
            {"name": "models/gemini-2.5-flash"},
            {"name": "models/gemini-2.5-flash-🚀"},  # non-ASCII
        ],
    }

    with patch("daibai.api.model_discovery.urllib.request.urlopen") as mock_open:
        mock_open.return_value.__enter__ = lambda s: s
        mock_open.return_value.__exit__ = lambda *a: None
        mock_open.return_value.read.return_value = json.dumps(mock_body).encode("utf-8")

        result = await fetch_provider_models("gemini", api_key="test-key")

    assert "models" in result
    assert "gemini-2.5-pro" in result["models"]
    assert "gemini-2.5-flash" in result["models"]
    # Non-ASCII stripped
    assert any("gemini-2.5-flash" in m for m in result["models"])
    assert "error" not in result
    # JSON serializable (no encoding errors)
    json.dumps(result)
    print("\nModels (mocked):", ", ".join(result["models"]))


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _get_gemini_api_key(),
    reason="No Gemini API key in daibai.yaml, .env, or GEMINI_API_KEY env",
)
async def test_gemini_get_models_live():
    """Gemini get models against real API (uses key from config or env)."""
    api_key = _get_gemini_api_key()
    result = await fetch_provider_models("gemini", api_key=api_key)

    assert "models" in result
    assert "error" not in result, result.get("error", "")
    assert len(result["models"]) > 0
    # All model names must be ASCII-safe
    for m in result["models"]:
        assert m.isascii(), f"Model name has non-ASCII: {m!r}"
    json.dumps(result)
    print("\nModels returned:", len(result["models"]))
    for m in result["models"]:
        print("  -", m)
