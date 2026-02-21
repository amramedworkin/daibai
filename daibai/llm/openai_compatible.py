"""
OpenAI-compatible LLM Provider for DaiBai.

Generic provider for any API that follows the OpenAI chat completions format.
Used by Groq, DeepSeek, Mistral, Nvidia, Alibaba, Meta, etc.
"""

from typing import Optional

from .openai_provider import OpenAIProvider


class OpenAICompatibleProvider(OpenAIProvider):
    """
    OpenAI-compatible provider with configurable base_url.
    
    Use for any provider that exposes an OpenAI-compatible API:
    - Groq, DeepSeek, Mistral, Nvidia, Alibaba DashScope, etc.
    """
    
    DEFAULT_BASE_URL: Optional[str] = None
    
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: Optional[str] = None,
        endpoint: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs
    ):
        url = base_url or endpoint or self.DEFAULT_BASE_URL
        super().__init__(
            api_key=api_key,
            model=model,
            base_url=url,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs
        )
    
    @property
    def provider_name(self) -> str:
        return "openai_compatible"
