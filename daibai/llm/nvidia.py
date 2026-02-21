"""Nvidia NIM LLM Provider - OpenAI-compatible API."""

from .openai_compatible import OpenAICompatibleProvider


class NvidiaProvider(OpenAICompatibleProvider):
    """Nvidia NIM (Nvidia Inference Microservices) provider."""
    DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
    
    @property
    def provider_name(self) -> str:
        return "nvidia"
