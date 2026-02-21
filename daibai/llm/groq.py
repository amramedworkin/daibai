"""Groq LLM Provider - OpenAI-compatible API."""

from .openai_compatible import OpenAICompatibleProvider


class GroqProvider(OpenAICompatibleProvider):
    """Groq provider. Uses OpenAI SDK with Groq base URL."""
    DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
    
    @property
    def provider_name(self) -> str:
        return "groq"
