"""Meta (Llama) LLM Provider - OpenAI-compatible API."""

from .openai_compatible import OpenAICompatibleProvider


class MetaProvider(OpenAICompatibleProvider):
    """
    Meta Llama provider.
    
    Meta models are available via cloud partners (AWS Bedrock, Azure AI, etc.)
    or services like Groq. Set 'endpoint' in config to your provider's base URL.
    No default - endpoint is required.
    """
    DEFAULT_BASE_URL = None  # User must provide endpoint
    
    @property
    def provider_name(self) -> str:
        return "meta"
