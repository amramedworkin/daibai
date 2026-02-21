"""Tests for newly added LLM providers (Groq, DeepSeek, Mistral, Nvidia, Alibaba, Meta)."""

import pytest

from daibai.llm import get_provider_class, create_provider, list_available_providers
from daibai.llm.base import BaseLLMProvider, LLMResponse

NEW_PROVIDERS = ["groq", "deepseek", "mistral", "nvidia", "alibaba", "meta"]


def test_new_providers_registered():
    """Verify all new providers are in the registry."""
    available = list_available_providers()
    for p in NEW_PROVIDERS:
        assert p in available, f"Provider {p} should be registered"


def test_new_provider_class_inheritance():
    """Each new provider must inherit from BaseLLMProvider."""
    for provider_type in NEW_PROVIDERS:
        try:
            cls = get_provider_class(provider_type)
            assert issubclass(cls, BaseLLMProvider), f"{provider_type} must inherit BaseLLMProvider"
        except ImportError:
            pytest.skip("OpenAI SDK not installed (pip install daibai[openai])")


def test_new_provider_required_interface():
    """Each provider must implement generate, generate_async, stream, model_name, provider_name."""
    try:
        provider = create_provider("groq", {
            "api_key": "test-key",
            "model": "llama-3.1-70b-versatile",
        })
        assert hasattr(provider, "generate")
        assert hasattr(provider, "generate_async")
        assert hasattr(provider, "stream")
        assert provider.model_name == "llama-3.1-70b-versatile"
        assert provider.provider_name == "groq"
    except ImportError:
        pytest.skip("OpenAI SDK not installed")


@pytest.mark.parametrize("provider_type,model", [
    ("groq", "llama-3.1-70b-versatile"),
    ("deepseek", "deepseek-chat"),
    ("mistral", "mistral-large-latest"),
    ("nvidia", "meta/llama-3.1-70b-instruct-v2"),
    ("alibaba", "qwen-max"),
    ("meta", "llama-3.1-70b"),
])
def test_provider_instantiation(provider_type, model):
    """Test each provider can be instantiated with API key and model."""
    try:
        config = {"api_key": "test-key", "model": model}
        if provider_type == "meta":
            config["endpoint"] = "https://api.groq.com/openai/v1"  # Meta needs endpoint
        provider = create_provider(provider_type, config)
        assert provider is not None
        assert provider.model_name == model
    except ImportError:
        pytest.skip("OpenAI SDK not installed")
