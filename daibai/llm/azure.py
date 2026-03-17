"""
Azure OpenAI LLM Provider for DaiBai.

Uses the openai SDK with Azure-specific configuration.
"""

import re
from typing import Optional, Dict, Any, AsyncIterator

from .base import BaseLLMProvider, LLMResponse


class AzureProvider(BaseLLMProvider):
    """
    Azure OpenAI provider implementation.
    
    Supports:
    - Azure OpenAI deployments
    - API key authentication (local / dev)
    - Azure AD / Managed Identity (passwordless when api_key is empty)
    - Region-specific endpoints
    - All GPT-4 / GPT-3.5 features
    """
    
    def __init__(
        self,
        endpoint: str = "",
        deployment: str = "",
        api_key: Optional[str] = None,
        api_version: str = "2024-02-01",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs
    ):
        # Agent passes model=...; Azure uses it as deployment name
        self.deployment = (deployment or kwargs.get("model") or "").strip()
        self.api_key = (api_key or "").strip() or None
        self.endpoint = endpoint
        self.api_version = api_version
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client = None
        self._async_client = None
        self._async_credential = None  # Keep reference for async token provider
    
    def _ensure_client(self):
        """Lazy initialization of Azure OpenAI client (sync + async)."""
        if self._client is None:
            try:
                from openai import AzureOpenAI, AsyncAzureOpenAI
                
                if self.api_key:
                    # API key auth (local / dev)
                    self._client = AzureOpenAI(
                        api_key=self.api_key,
                        api_version=self.api_version,
                        azure_endpoint=self.endpoint,
                    )
                    self._async_client = AsyncAzureOpenAI(
                        api_key=self.api_key,
                        api_version=self.api_version,
                        azure_endpoint=self.endpoint,
                    )
                else:
                    # Passwordless: Managed Identity / DefaultAzureCredential
                    from azure.identity import DefaultAzureCredential, get_bearer_token_provider
                    from azure.identity.aio import AsyncDefaultAzureCredential
                    
                    credential = DefaultAzureCredential()
                    token_provider = get_bearer_token_provider(
                        credential, "https://cognitiveservices.azure.com/.default"
                    )
                    self._client = AzureOpenAI(
                        api_version=self.api_version,
                        azure_endpoint=self.endpoint,
                        azure_ad_token_provider=token_provider,
                    )
                    self._async_credential = AsyncDefaultAzureCredential()
                    async_token_provider = get_bearer_token_provider(
                        self._async_credential, "https://cognitiveservices.azure.com/.default"
                    )
                    self._async_client = AsyncAzureOpenAI(
                        api_version=self.api_version,
                        azure_endpoint=self.endpoint,
                        azure_ad_token_provider=async_token_provider,
                    )
            except Exception as e:
                import traceback
                raise RuntimeError(
                    f"Azure SDK failed to load! The packages exist but are crashing internally.\n"
                    f"Exact Error: {str(e)}\n\n"
                    f"Traceback:\n{traceback.format_exc()}"
                )
    
    def generate(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> LLMResponse:
        """Generate response using Azure OpenAI."""
        self._ensure_client()
        
        messages = self._build_messages(prompt, context)
        
        response = self._client.chat.completions.create(
            model=self.deployment,
            messages=messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        
        text = response.choices[0].message.content or ""
        sql = self._extract_sql(text)
        
        return LLMResponse(
            text=text,
            sql=sql,
            tokens_used=response.usage.total_tokens if response.usage else None,
            model=self.deployment,
            raw_response=response,
        )
    
    async def generate_async(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> LLMResponse:
        """Async generation using Azure OpenAI."""
        self._ensure_client()
        
        messages = self._build_messages(prompt, context)
        
        response = await self._async_client.chat.completions.create(
            model=self.deployment,
            messages=messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        
        text = response.choices[0].message.content or ""
        sql = self._extract_sql(text)
        
        return LLMResponse(
            text=text,
            sql=sql,
            tokens_used=response.usage.total_tokens if response.usage else None,
            model=self.deployment,
            raw_response=response,
        )
    
    async def stream(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> AsyncIterator[str]:
        """Stream response tokens from Azure OpenAI."""
        self._ensure_client()
        
        messages = self._build_messages(prompt, context)
        
        stream = await self._async_client.chat.completions.create(
            model=self.deployment,
            messages=messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            stream=True,
        )
        
        async for chunk in stream:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
    
    def _build_messages(self, prompt: str, context: Optional[Dict[str, Any]]) -> list:
        """Build message list for chat completion."""
        messages = []
        
        system_parts = []
        if context:
            if context.get("system_prompt"):
                system_parts.append(context["system_prompt"])
            if context.get("schema"):
                system_parts.append(f"Database Schema:\n{context['schema']}")
        
        if system_parts:
            messages.append({
                "role": "system",
                "content": "\n\n".join(system_parts)
            })
        
        for m in context.get("messages", []) if context else []:
            role = m.get("role")
            content = m.get("content", "")
            if role and content:
                messages.append({"role": role, "content": content})
        
        messages.append({
            "role": "user",
            "content": prompt
        })
        
        return messages
    
    def _extract_sql(self, text: str) -> Optional[str]:
        """Extract SQL from response text."""
        patterns = [
            r'```sql\s*(.*?)\s*```',
            r'```\s*(SELECT.*?)\s*```',
            r'```\s*(INSERT.*?)\s*```',
            r'```\s*(UPDATE.*?)\s*```',
            r'```\s*(DELETE.*?)\s*```',
            r'```\s*(CREATE.*?)\s*```',
            r'```\s*(ALTER.*?)\s*```',
            r'```\s*(DROP.*?)\s*```',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                return match.group(1).strip()
        
        return None
    
    @property
    def model_name(self) -> str:
        return self.deployment
    
    @property
    def provider_name(self) -> str:
        return "azure"
