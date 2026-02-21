"""Mistral AI LLM Provider - OpenAI-compatible API."""

from .openai_compatible import OpenAICompatibleProvider


class MistralProvider(OpenAICompatibleProvider):
    """Mistral AI provider. Uses OpenAI SDK with Mistral base URL."""
    DEFAULT_BASE_URL = "https://api.mistral.ai/v1"
    
    @property
    def provider_name(self) -> str:
        return "mistral"
