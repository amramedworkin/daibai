"""Tests for LLM provider registry."""

import pytest

from daibai.llm import (
    get_provider_class,
    list_available_providers,
    PROVIDER_MODULES,
)
from daibai.llm.base import BaseLLMProvider


def test_list_available_providers():
    """Test listing available providers."""
    providers = list_available_providers()
    
    assert "gemini" in providers
    assert "openai" in providers
    assert "azure" in providers
    assert "anthropic" in providers
    assert "ollama" in providers


def test_provider_modules_registered():
    """Test that all providers are registered."""
    assert "gemini" in PROVIDER_MODULES
    assert "openai" in PROVIDER_MODULES
    assert "azure" in PROVIDER_MODULES
    assert "anthropic" in PROVIDER_MODULES
    assert "ollama" in PROVIDER_MODULES


def test_get_unknown_provider():
    """Test that unknown provider raises error."""
    with pytest.raises(ValueError) as exc_info:
        get_provider_class("unknown_provider")
    
    assert "Unknown provider type" in str(exc_info.value)


def test_gemini_provider_class():
    """Test loading Gemini provider class."""
    try:
        provider_class = get_provider_class("gemini")
        assert issubclass(provider_class, BaseLLMProvider)
        assert provider_class.__name__ == "GeminiProvider"
    except ImportError:
        pytest.skip("Gemini dependencies not installed")


def test_openai_provider_class():
    """Test loading OpenAI provider class."""
    try:
        provider_class = get_provider_class("openai")
        assert issubclass(provider_class, BaseLLMProvider)
        assert provider_class.__name__ == "OpenAIProvider"
    except ImportError:
        pytest.skip("OpenAI dependencies not installed")


def test_anthropic_provider_class():
    """Test loading Anthropic provider class."""
    try:
        provider_class = get_provider_class("anthropic")
        assert issubclass(provider_class, BaseLLMProvider)
        assert provider_class.__name__ == "AnthropicProvider"
    except ImportError:
        pytest.skip("Anthropic dependencies not installed")
