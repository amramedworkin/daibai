"""
Base class for LLM providers.

This is a minimal base for type hints only - each provider has its own
full implementation without forced abstraction.
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, AsyncIterator
from dataclasses import dataclass


@dataclass
class LLMResponse:
    """Response from an LLM provider."""
    text: str
    sql: Optional[str] = None
    tokens_used: Optional[int] = None
    model: Optional[str] = None
    raw_response: Optional[Any] = None


class BaseLLMProvider(ABC):
    """
    Minimal base class for LLM providers.
    
    Each provider implements its own specific methods and can extend
    beyond this base as needed.
    """
    
    @abstractmethod
    def generate(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> LLMResponse:
        """
        Generate a response from the LLM.
        
        Args:
            prompt: The user prompt
            context: Optional context (schema info, conversation history, etc.)
        
        Returns:
            LLMResponse with text and optional SQL
        """
        pass
    
    @abstractmethod
    async def generate_async(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> LLMResponse:
        """Async version of generate."""
        pass
    
    @abstractmethod
    def stream(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> AsyncIterator[str]:
        """Stream response tokens."""
        pass
    
    @property
    @abstractmethod
    def model_name(self) -> str:
        """Return the model name being used."""
        pass
    
    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider name (gemini, openai, etc.)."""
        pass
