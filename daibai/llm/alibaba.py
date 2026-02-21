"""Alibaba Cloud DashScope (Qwen) LLM Provider - OpenAI-compatible API."""

from .openai_compatible import OpenAICompatibleProvider


class AlibabaProvider(OpenAICompatibleProvider):
    """Alibaba DashScope/Qwen provider. Uses OpenAI SDK with DashScope compatible endpoint."""
    # Singapore region; use endpoint in config for Virginia or Beijing
    DEFAULT_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    
    @property
    def provider_name(self) -> str:
        return "alibaba"
