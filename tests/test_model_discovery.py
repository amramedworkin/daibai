"""Tests for model discovery (fetching models from LLM providers)."""

import json
from unittest.mock import patch, MagicMock

import pytest

from daibai.api.model_discovery import (
    safe_str,
    _sanitize_result,
    fetch_provider_models,
)


# --- safe_str ---


def test_safe_str_ascii():
    """ASCII strings pass through unchanged."""
    assert safe_str("hello") == "hello"
    assert safe_str("models/gemini-2.5-pro") == "models/gemini-2.5-pro"


def test_safe_str_non_ascii_stripped():
    """Non-ASCII characters are stripped (avoids ordinal not in range(128))."""
    assert safe_str("gemini-2.5-pro-emoji-🚀") == "gemini-2.5-pro-emoji-"
    assert safe_str("模型名称") == ""
    assert safe_str("model™") == "model"


def test_safe_str_non_string():
    """Non-string inputs are converted to str."""
    assert safe_str(123) == "123"
    assert safe_str(None) == "None"


# --- _sanitize_result ---


def test_sanitize_result_nested_dict():
    """Nested dicts are recursively sanitized."""
    raw = {"models": ["gemini-pro", "gemini-2.5-pro-🚀"], "nested": {"name": "模型"}}
    out = _sanitize_result(raw)
    assert out["models"] == ["gemini-pro", "gemini-2.5-pro-"]
    assert out["nested"]["name"] == ""


def test_sanitize_result_preserves_non_strings():
    """Numbers and other types are preserved."""
    raw = {"count": 5, "models": ["a"], "enabled": True}
    out = _sanitize_result(raw)
    assert out["count"] == 5
    assert out["enabled"] is True


# --- fetch_provider_models (mocked HTTP) ---


@pytest.mark.asyncio
async def test_fetch_models_azure():
    """Azure returns message, no HTTP call."""
    result = await fetch_provider_models("azure")
    assert result["models"] == []
    assert "deployment" in result.get("message", "").lower()


@pytest.mark.asyncio
async def test_fetch_models_unknown_provider():
    """Unknown provider returns error."""
    result = await fetch_provider_models("unknown")
    assert result["models"] == []
    assert "Unknown provider" in result.get("error", "")


@pytest.mark.asyncio
async def test_fetch_models_gemini_non_ascii():
    """Gemini response with non-ASCII model names is sanitized (fixes encoding error)."""
    # Simulates real Gemini API returning non-ASCII in model names
    mock_body = {
        "models": [
            {"name": "models/gemini-2.5-pro"},
            {"name": "models/gemini-2.5-flash-🚀"},  # emoji
            {"name": "models/模型名称"},  # Chinese
        ],
    }

    with patch("daibai.api.model_discovery.urllib.request.urlopen") as mock_open:
        mock_open.return_value.__enter__ = lambda s: s
        mock_open.return_value.__exit__ = lambda *a: None
        mock_open.return_value.read.return_value = json.dumps(mock_body).encode("utf-8")

        result = await fetch_provider_models("gemini", api_key="test-key")

    assert result["models"] == ["gemini-2.5-pro", "gemini-2.5-flash-", ""]
    # All strings must be ASCII-safe; JSON serialization must not raise
    json_str = json.dumps(result)
    assert "gemini-2.5-pro" in json_str


@pytest.mark.asyncio
async def test_fetch_models_gemini_no_api_key():
    """Gemini without API key returns error."""
    result = await fetch_provider_models("gemini")
    assert result["models"] == []
    assert "API key" in result.get("error", "")


@pytest.mark.asyncio
async def test_fetch_models_ollama():
    """Ollama fetches from /api/tags."""
    mock_body = {"models": [{"name": "llama3.2"}, {"name": "mistral"}]}

    with patch("daibai.api.model_discovery.urllib.request.urlopen") as mock_open:
        mock_open.return_value.__enter__ = lambda s: s
        mock_open.return_value.__exit__ = lambda *a: None
        mock_open.return_value.read.return_value = json.dumps(mock_body).encode("utf-8")

        result = await fetch_provider_models("ollama", base_url="http://localhost:11434")

    assert result["models"] == ["llama3.2", "mistral"]
    mock_open.assert_called_once()
    assert "/api/tags" in mock_open.call_args[0][0].full_url


@pytest.mark.asyncio
async def test_fetch_models_anthropic():
    """Anthropic fetches from v1/models."""
    mock_body = {"data": [{"id": "claude-3-opus"}, {"id": "claude-3-sonnet"}]}

    with patch("daibai.api.model_discovery.urllib.request.urlopen") as mock_open:
        mock_open.return_value.__enter__ = lambda s: s
        mock_open.return_value.__exit__ = lambda *a: None
        mock_open.return_value.read.return_value = json.dumps(mock_body).encode("utf-8")

        result = await fetch_provider_models("anthropic", api_key="sk-ant-xxx")

    assert result["models"] == ["claude-3-opus", "claude-3-sonnet"]


@pytest.mark.asyncio
async def test_fetch_models_openai_like():
    """OpenAI-compatible providers fetch from /models."""
    mock_body = {"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]}

    with patch("daibai.api.model_discovery.urllib.request.urlopen") as mock_open:
        mock_open.return_value.__enter__ = lambda s: s
        mock_open.return_value.__exit__ = lambda *a: None
        mock_open.return_value.read.return_value = json.dumps(mock_body).encode("utf-8")

        result = await fetch_provider_models("openai", api_key="sk-xxx")

    assert result["models"] == ["gpt-4o", "gpt-4o-mini"]
