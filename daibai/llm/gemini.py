"""
Google Gemini LLM Provider for DaiBai.

Uses the google-generativeai SDK directly for Gemini-specific features.
"""

import re
from typing import Optional, Dict, Any, AsyncIterator

from .base import BaseLLMProvider, LLMResponse


class GeminiProvider(BaseLLMProvider):
    """
    Google Gemini provider implementation.
    
    Supports:
    - Gemini 2.5 Pro / Flash models
    - Function calling for SQL execution
    - Streaming responses
    - Multi-turn conversations
    """
    
    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-pro",
        temperature: float = 0.7,
        max_tokens: int = 65536,
        **kwargs
    ):
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client = None
        self._model_instance = None
    
    def _ensure_client(self):
        """Lazy initialization of Gemini client."""
        if self._client is None:
            try:
                import google.generativeai as genai
                genai.configure(api_key=self.api_key)
                self._client = genai
                self._model_instance = genai.GenerativeModel(
                    model_name=self.model,
                    generation_config={
                        "temperature": self.temperature,
                        "max_output_tokens": self.max_tokens,
                    }
                )
            except ImportError:
                raise ImportError(
                    "Gemini provider requires google-generativeai. "
                    "Install with: pip install daibai[gemini]"
                )
    
    def generate(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> LLMResponse:
        """Generate response using Gemini."""
        self._ensure_client()
        
        # Build full prompt with context
        full_prompt = self._build_prompt(prompt, context)
        
        response = self._model_instance.generate_content(full_prompt)
        
        # Handle blocked or empty responses
        text = self._extract_text(response)
        sql = self._extract_sql(text) if text else None
        
        return LLMResponse(
            text=text,
            sql=sql,
            model=self.model,
            raw_response=response,
        )
    
    def _extract_text(self, response) -> str:
        """Safely extract text from Gemini response."""
        # Check if response has candidates
        if not response.candidates:
            return "[No response generated]"
        
        candidate = response.candidates[0]
        
        # Check finish reason (1=STOP is good, 2=MAX_TOKENS, 3=SAFETY, 4=RECITATION, 5=OTHER)
        finish_reason = getattr(candidate, 'finish_reason', None)
        if finish_reason and finish_reason != 1:
            reason_names = {2: "MAX_TOKENS", 3: "SAFETY", 4: "RECITATION", 5: "OTHER"}
            reason_name = reason_names.get(finish_reason, f"UNKNOWN({finish_reason})")
            
            # Try to get partial content if available
            if candidate.content and candidate.content.parts:
                parts_text = "".join(p.text for p in candidate.content.parts if hasattr(p, 'text'))
                if parts_text:
                    return f"{parts_text}\n\n[Response truncated: {reason_name}]"
            
            return f"[Response blocked: {reason_name}]"
        
        # Normal case - extract text from parts
        if candidate.content and candidate.content.parts:
            return "".join(p.text for p in candidate.content.parts if hasattr(p, 'text'))
        
        return "[Empty response]"
    
    async def generate_async(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> LLMResponse:
        """Async generation using Gemini."""
        self._ensure_client()
        
        full_prompt = self._build_prompt(prompt, context)
        
        response = await self._model_instance.generate_content_async(full_prompt)
        
        # Handle blocked or empty responses
        text = self._extract_text(response)
        sql = self._extract_sql(text) if text else None
        
        return LLMResponse(
            text=text,
            sql=sql,
            model=self.model,
            raw_response=response,
        )
    
    async def stream(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> AsyncIterator[str]:
        """Stream response tokens from Gemini."""
        self._ensure_client()
        
        full_prompt = self._build_prompt(prompt, context)
        
        response = await self._model_instance.generate_content_async(
            full_prompt,
            stream=True
        )
        
        async for chunk in response:
            if chunk.text:
                yield chunk.text
    
    def _build_prompt(self, prompt: str, context: Optional[Dict[str, Any]]) -> str:
        """Build full prompt with context."""
        parts = []
        
        if context:
            if context.get("schema"):
                parts.append(f"Database Schema:\n{context['schema']}\n")
            if context.get("system_prompt"):
                parts.append(context["system_prompt"])
        
        parts.append(prompt)
        
        return "\n\n".join(parts)
    
    def _extract_sql(self, text: str) -> Optional[str]:
        """Extract SQL from response text."""
        # Look for SQL in code blocks
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
        return "gemini"
