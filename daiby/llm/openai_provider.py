"""
OpenAI LLM Provider for Daiby.

Uses the openai SDK directly for OpenAI-specific features.
"""

import re
from typing import Optional, Dict, Any, AsyncIterator

from .base import BaseLLMProvider, LLMResponse


class OpenAIProvider(BaseLLMProvider):
    """
    OpenAI provider implementation.
    
    Supports:
    - GPT-4o / GPT-4 Turbo / GPT-3.5 models
    - Function/tool calling for SQL execution
    - Streaming responses
    - Token usage tracking
    """
    
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        organization: Optional[str] = None,
        **kwargs
    ):
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.organization = organization
        self._client = None
        self._async_client = None
    
    def _ensure_client(self):
        """Lazy initialization of OpenAI client."""
        if self._client is None:
            try:
                from openai import OpenAI, AsyncOpenAI
                
                self._client = OpenAI(
                    api_key=self.api_key,
                    organization=self.organization,
                )
                self._async_client = AsyncOpenAI(
                    api_key=self.api_key,
                    organization=self.organization,
                )
            except ImportError:
                raise ImportError(
                    "OpenAI provider requires openai. "
                    "Install with: pip install daiby[openai]"
                )
    
    def generate(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> LLMResponse:
        """Generate response using OpenAI."""
        self._ensure_client()
        
        messages = self._build_messages(prompt, context)
        
        response = self._client.chat.completions.create(
            model=self.model,
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
            model=self.model,
            raw_response=response,
        )
    
    async def generate_async(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> LLMResponse:
        """Async generation using OpenAI."""
        self._ensure_client()
        
        messages = self._build_messages(prompt, context)
        
        response = await self._async_client.chat.completions.create(
            model=self.model,
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
            model=self.model,
            raw_response=response,
        )
    
    async def stream(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> AsyncIterator[str]:
        """Stream response tokens from OpenAI."""
        self._ensure_client()
        
        messages = self._build_messages(prompt, context)
        
        stream = await self._async_client.chat.completions.create(
            model=self.model,
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
        
        # System message with context
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
        
        # User message
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
        return self.model
    
    @property
    def provider_name(self) -> str:
        return "openai"
