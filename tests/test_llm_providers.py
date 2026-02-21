"""Tests for LLM provider registry."""

import pytest

from daibai.llm import (
    get_provider_class,
    get_provider_classes,
    list_available_providers,
    PROVIDER_MODULES,
)
from daibai.llm.base import BaseLLMProvider

# All supported providers (existing + new)
ALL_PROVIDERS = [
    "gemini", "openai", "azure", "anthropic", "ollama",
    "groq", "deepseek", "mistral", "nvidia", "alibaba", "meta",
]


def test_list_available_providers():
    """Test listing available providers."""
    providers = list_available_providers()
    
    for p in ALL_PROVIDERS:
        assert p in providers, f"Provider {p} should be registered"


def test_provider_modules_registered():
    """Test that all providers are registered."""
    for p in ALL_PROVIDERS:
        assert p in PROVIDER_MODULES, f"Provider {p} should be in PROVIDER_MODULES"


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


# --- New provider tests (OpenAI-compatible; require openai SDK) ---

@pytest.mark.parametrize("provider_type,expected_class", [
    ("groq", "GroqProvider"),
    ("deepseek", "DeepseekProvider"),
    ("mistral", "MistralProvider"),
    ("nvidia", "NvidiaProvider"),
    ("alibaba", "AlibabaProvider"),
    ("meta", "MetaProvider"),
])
def test_new_provider_classes(provider_type, expected_class):
    """Test that each new provider class can be loaded and inherits from BaseLLMProvider."""
    try:
        provider_class = get_provider_class(provider_type)
        assert issubclass(provider_class, BaseLLMProvider)
        assert provider_class.__name__ == expected_class
    except ImportError:
        pytest.skip(f"{provider_type} dependencies not installed (pip install daibai[openai])")


def test_new_provider_instantiation():
    """Test that new providers can be instantiated with minimal config."""
    try:
        from daibai.llm import create_provider
        # Groq with dummy key - will fail on actual API call but instantiation should work
        provider = create_provider("groq", {
            "api_key": "test-key",
            "model": "llama-3.1-70b-versatile",
        })
        assert provider is not None
        assert provider.provider_name == "groq"
        assert provider.model_name == "llama-3.1-70b-versatile"
    except ImportError:
        pytest.skip("OpenAI dependencies not installed")


def test_get_provider_classes():
    """Test get_provider_classes returns dict of loaded classes."""
    classes = get_provider_classes()
    assert isinstance(classes, dict)
    for ptype in ALL_PROVIDERS:
        if ptype in classes:
            assert issubclass(classes[ptype], BaseLLMProvider)
