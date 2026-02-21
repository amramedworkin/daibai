"""DeepSeek LLM Provider - OpenAI-compatible API."""

from .openai_compatible import OpenAICompatibleProvider


class DeepseekProvider(OpenAICompatibleProvider):
    """DeepSeek provider. Uses OpenAI SDK with DeepSeek base URL."""
    DEFAULT_BASE_URL = "https://api.deepseek.com"
    
    @property
    def provider_name(self) -> str:
        return "deepseek"
