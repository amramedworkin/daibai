"""
Ollama Local LLM Provider for Daiby.

Uses the ollama API for local model inference.
"""

import re
from typing import Optional, Dict, Any, AsyncIterator

from .base import BaseLLMProvider, LLMResponse


class OllamaProvider(BaseLLMProvider):
    """
    Ollama local model provider implementation.
    
    Supports:
    - Local model inference (CodeLlama, Mistral, Llama, etc.)
    - No API key required
    - Custom model hosts
    - Streaming responses
    """
    
    def __init__(
        self,
        model: str = "codellama:13b",
        host: str = "http://localhost:11434",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs
    ):
        self.model = model
        self.host = host
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client = None
        self._async_client = None
    
    def _ensure_client(self):
        """Lazy initialization of Ollama client."""
        if self._client is None:
            try:
                import ollama
                self._client = ollama.Client(host=self.host)
                self._async_client = ollama.AsyncClient(host=self.host)
            except ImportError:
                raise ImportError(
                    "Ollama provider requires ollama. "
                    "Install with: pip install daiby[ollama]"
                )
    
    def generate(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> LLMResponse:
        """Generate response using Ollama."""
        self._ensure_client()
        
        full_prompt = self._build_prompt(prompt, context)
        
        response = self._client.generate(
            model=self.model,
            prompt=full_prompt,
            options={
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            }
        )
        
        text = response.get("response", "")
        sql = self._extract_sql(text)
        
        return LLMResponse(
            text=text,
            sql=sql,
            model=self.model,
            raw_response=response,
        )
    
    async def generate_async(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> LLMResponse:
        """Async generation using Ollama."""
        self._ensure_client()
        
        full_prompt = self._build_prompt(prompt, context)
        
        response = await self._async_client.generate(
            model=self.model,
            prompt=full_prompt,
            options={
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            }
        )
        
        text = response.get("response", "")
        sql = self._extract_sql(text)
        
        return LLMResponse(
            text=text,
            sql=sql,
            model=self.model,
            raw_response=response,
        )
    
    async def stream(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> AsyncIterator[str]:
        """Stream response tokens from Ollama."""
        self._ensure_client()
        
        full_prompt = self._build_prompt(prompt, context)
        
        async for chunk in await self._async_client.generate(
            model=self.model,
            prompt=full_prompt,
            stream=True,
            options={
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            }
        ):
            if chunk.get("response"):
                yield chunk["response"]
    
    def _build_prompt(self, prompt: str, context: Optional[Dict[str, Any]]) -> str:
        """Build full prompt with context."""
        parts = []
        
        if context:
            if context.get("system_prompt"):
                parts.append(context["system_prompt"])
            if context.get("schema"):
                parts.append(f"Database Schema:\n{context['schema']}")
        
        parts.append(prompt)
        
        return "\n\n".join(parts)
    
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
        return "ollama"
