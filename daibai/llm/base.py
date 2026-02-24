"""
Base class for LLM providers and semantic caching.

This is a minimal base for type hints only - each provider has its own
full implementation without forced abstraction.
"""

import json
import uuid
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, AsyncIterator, List, Callable
from dataclasses import dataclass


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class SemanticCache:
    """
    Semantic cache for LLM responses using Redis and embedding similarity.
    Stores prompt embeddings and responses; retrieves by cosine similarity > threshold.
    Gracefully degrades when Redis is unavailable.
    """

    KEY_PREFIX = "daibai:semantic_cache:"
    INDEX_KEY = "daibai:semantic_cache:index"
    SIMILARITY_THRESHOLD = 0.95

    def __init__(
        self,
        connection_string: Optional[str] = None,
        similarity_threshold: float = SIMILARITY_THRESHOLD,
        embed_fn: Optional[Callable[[str], List[float]]] = None,
    ):
        self._connection_string = connection_string
        self._similarity_threshold = similarity_threshold
        self._embed_fn = embed_fn
        self._redis = None
        self._embed_model = None
        self._available = False

    def _ensure_redis(self) -> bool:
        """Lazy init Redis connection. Supports Entra ID (secretless) or connection string."""
        if self._redis is not None:
            return self._available
        try:
            from daibai.core.config import get_redis_entra_config, get_redis_connection_string

            conn_str = self._connection_string or get_redis_connection_string()
            entra = get_redis_entra_config()

            if entra:
                host, port = entra
                try:
                    from redis import Redis
                    from redis_entraid.cred_provider import create_from_default_azure_credential

                    cred = create_from_default_azure_credential(("https://redis.azure.com/.default",))
                    self._redis = Redis(
                        host=host,
                        port=port,
                        ssl=True,
                        ssl_cert_reqs=None,
                        credential_provider=cred,
                        decode_responses=True,
                    )
                except (ImportError, Exception):
                    self._available = False
                    return False
            elif conn_str:
                import redis
                self._redis = redis.from_url(conn_str, decode_responses=True)
            else:
                self._available = False
                return False
            self._redis.ping()
            self._available = True
        except Exception:
            self._available = False
        return self._available

    def _ensure_embedding(self) -> Optional[Callable[[str], List[float]]]:
        """Lazy init embedding function. Returns embed_fn or None."""
        if self._embed_fn is not None:
            return self._embed_fn
        if self._embed_model is not None:
            return lambda t: self._embed_model.encode(t, convert_to_numpy=True).tolist()
        try:
            from sentence_transformers import SentenceTransformer
            self._embed_model = SentenceTransformer("all-MiniLM-L6-v2")
            return lambda t: self._embed_model.encode(t, convert_to_numpy=True).tolist()
        except Exception:
            return None

    def get_cached_response(self, prompt: str) -> Optional[str]:
        """
        Search cache for a similar prompt (cosine similarity > threshold).
        Returns cached response or None on miss.
        """
        if not self._ensure_redis():
            return None
        embed_fn = self._ensure_embedding()
        if not embed_fn:
            return None
        try:
            query_embedding = embed_fn(prompt)
            entry_ids = self._redis.smembers(self.INDEX_KEY)
            for eid in entry_ids:
                key = f"{self.KEY_PREFIX}entry:{eid}"
                raw = self._redis.get(key)
                if not raw:
                    continue
                entry = json.loads(raw)
                stored_embedding = entry.get("embedding")
                if not stored_embedding:
                    continue
                sim = _cosine_similarity(query_embedding, stored_embedding)
                if sim >= self._similarity_threshold:
                    return entry.get("response")
            return None
        except Exception:
            return None

    def set_cached_response(self, prompt: str, response: str) -> bool:
        """Store prompt embedding and response in cache. Returns True on success."""
        if not self._ensure_redis():
            return False
        embed_fn = self._ensure_embedding()
        if not embed_fn:
            return False
        try:
            embedding = embed_fn(prompt)
            eid = str(uuid.uuid4())
            key = f"{self.KEY_PREFIX}entry:{eid}"
            entry = {"embedding": embedding, "response": response, "prompt": prompt}
            self._redis.set(key, json.dumps(entry))
            self._redis.sadd(self.INDEX_KEY, eid)
            return True
        except Exception:
            return False

    def close(self) -> None:
        """Close Redis connection if open."""
        if self._redis:
            try:
                self._redis.close()
            except Exception:
                pass
            self._redis = None
        self._available = False


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


class CachedLLMProvider(BaseLLMProvider):
    """
    Wraps an LLM provider with SemanticCache. On generate(), checks cache first;
    on miss, calls underlying provider and stores response in cache.
    """

    def __init__(self, provider: BaseLLMProvider, cache: SemanticCache):
        self._provider = provider
        self._cache = cache

    def generate(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> LLMResponse:
        cached = self._cache.get_cached_response(prompt)
        if cached is not None:
            return LLMResponse(text=cached, model=self._provider.model_name)
        response = self._provider.generate(prompt, context)
        self._cache.set_cached_response(prompt, response.text)
        return response

    async def generate_async(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> LLMResponse:
        cached = self._cache.get_cached_response(prompt)
        if cached is not None:
            return LLMResponse(text=cached, model=self._provider.model_name)
        response = await self._provider.generate_async(prompt, context)
        self._cache.set_cached_response(prompt, response.text)
        return response

    async def stream(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> AsyncIterator[str]:
        cached = self._cache.get_cached_response(prompt)
        if cached is not None:
            yield cached
            return
        async for chunk in self._provider.stream(prompt, context):
            yield chunk
        # Note: we don't cache streamed responses (would need to collect first)

    @property
    def model_name(self) -> str:
        return self._provider.model_name

    @property
    def provider_name(self) -> str:
        return self._provider.provider_name
