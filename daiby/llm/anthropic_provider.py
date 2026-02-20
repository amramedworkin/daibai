"""
Anthropic Claude LLM Provider for Daiby.

Uses the anthropic SDK directly for Claude-specific features.
"""

import re
from typing import Optional, Dict, Any, AsyncIterator

from .base import BaseLLMProvider, LLMResponse


class AnthropicProvider(BaseLLMProvider):
    """
    Anthropic Claude provider implementation.
    
    Supports:
    - Claude 3.5 Sonnet / Opus / Haiku models
    - Tool use for SQL execution
    - Extended context window (200K tokens)
    - Streaming responses
    """
    
    def __init__(
        self,
        api_key: str,
        model: str = "claude-3-5-sonnet-20241022",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs
    ):
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client = None
        self._async_client = None
    
    def _ensure_client(self):
        """Lazy initialization of Anthropic client."""
        if self._client is None:
            try:
                from anthropic import Anthropic, AsyncAnthropic
                
                self._client = Anthropic(api_key=self.api_key)
                self._async_client = AsyncAnthropic(api_key=self.api_key)
            except ImportError:
                raise ImportError(
                    "Anthropic provider requires anthropic. "
                    "Install with: pip install daiby[anthropic]"
                )
    
    def generate(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> LLMResponse:
        """Generate response using Claude."""
        self._ensure_client()
        
        system, user_message = self._build_messages(prompt, context)
        
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )
        
        text = response.content[0].text if response.content else ""
        sql = self._extract_sql(text)
        
        return LLMResponse(
            text=text,
            sql=sql,
            tokens_used=response.usage.input_tokens + response.usage.output_tokens if response.usage else None,
            model=self.model,
            raw_response=response,
        )
    
    async def generate_async(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> LLMResponse:
        """Async generation using Claude."""
        self._ensure_client()
        
        system, user_message = self._build_messages(prompt, context)
        
        response = await self._async_client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )
        
        text = response.content[0].text if response.content else ""
        sql = self._extract_sql(text)
        
        return LLMResponse(
            text=text,
            sql=sql,
            tokens_used=response.usage.input_tokens + response.usage.output_tokens if response.usage else None,
            model=self.model,
            raw_response=response,
        )
    
    async def stream(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> AsyncIterator[str]:
        """Stream response tokens from Claude."""
        self._ensure_client()
        
        system, user_message = self._build_messages(prompt, context)
        
        async with self._async_client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        ) as stream:
            async for text in stream.text_stream:
                yield text
    
    def _build_messages(self, prompt: str, context: Optional[Dict[str, Any]]) -> tuple:
        """Build system prompt and user message."""
        system_parts = []
        
        if context:
            if context.get("system_prompt"):
                system_parts.append(context["system_prompt"])
            if context.get("schema"):
                system_parts.append(f"Database Schema:\n{context['schema']}")
        
        system = "\n\n".join(system_parts) if system_parts else ""
        
        return system, prompt
    
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
        return "anthropic"
