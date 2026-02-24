"""
Cache manager for Redis connectivity.

Uses AZURE_REDIS_CONNECTION_STRING or REDIS_URL from .env (via config).
"""

import hashlib
import json
from typing import List, Optional

from .config import get_redis_connection_string

SEMANTIC_KEY_PREFIX = "semantic:"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"


class CacheManager:
    """
    Manages Redis connection for caching.
    Initializes a redis.Redis client from the connection string in .env.
    """

    _embed_model = None  # Class-level singleton for embedding model

    def __init__(self, connection_string: Optional[str] = None):
        """
        Initialize CacheManager.
        If connection_string is None, loads from get_redis_connection_string().
        """
        self._connection_string = connection_string or get_redis_connection_string()
        self._client = None

    def _get_client(self):
        """Lazy-init Redis client from connection string."""
        if self._client is not None:
            return self._client
        if not self._connection_string:
            return None
        import redis
        self._client = redis.Redis.from_url(
            self._connection_string,
            decode_responses=True,
        )
        return self._client

    def ping(self) -> bool:
        """
        Test connectivity to Redis.
        Returns True if ping succeeds, False otherwise.
        """
        try:
            client = self._get_client()
            if client is None:
                return False
            return client.ping()
        except Exception:
            return False

    def get(self, key: str) -> Optional[str]:
        """
        Get a value by key.
        Returns the value if found, None if the key does not exist.
        """
        try:
            client = self._get_client()
            if client is None:
                return None
            return client.get(key)
        except Exception:
            return None

    def set(self, key: str, value: str, ttl: int = 3600) -> bool:
        """
        Set a key-value pair with optional TTL (time-to-live in seconds).
        Uses Redis ex parameter for TTL. Returns True on success, False on error.
        """
        try:
            client = self._get_client()
            if client is None:
                return False
            client.set(key, value, ex=ttl)
            return True
        except Exception:
            return False

    def _get_embed_model(self):
        """Lazy-init embedding model (singleton per process). Returns None on load failure."""
        if CacheManager._embed_model is not None and CacheManager._embed_model is not False:
            return CacheManager._embed_model
        if CacheManager._embed_model is False:
            return None  # Previous load failed; avoid retrying
        try:
            from sentence_transformers import SentenceTransformer

            CacheManager._embed_model = SentenceTransformer(EMBEDDING_MODEL)
            return CacheManager._embed_model
        except Exception:
            CacheManager._embed_model = False  # Sentinel: failed to load
            return None

    def get_embedding(self, text: str) -> Optional[List[float]]:
        """
        Convert text to an embedding vector.
        Uses all-MiniLM-L6-v2 (384 dimensions).
        Returns None if the model fails to load (graceful degradation).
        """
        model = self._get_embed_model()
        if model is None:
            return None
        return model.encode(text, convert_to_numpy=True).tolist()

    def set_semantic(
        self,
        text: str,
        response: str,
        vector: Optional[List[float]] = None,
        ttl: int = 3600,
    ) -> bool:
        """
        Store a semantic cache entry: text, embedding vector, and response.
        If vector is None, generates it via get_embedding(text).
        Returns False if embedding generation fails (graceful degradation).
        Key format: semantic:<hash_of_text>. Value: JSON with vector and response.
        """
        if vector is None:
            vector = self.get_embedding(text)
            if vector is None:
                return False
        try:
            client = self._get_client()
            if client is None:
                return False
            key_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
            key = f"{SEMANTIC_KEY_PREFIX}{key_hash}"
            payload = {"text": text, "vector": vector, "response": response}
            client.set(key, json.dumps(payload), ex=ttl)
            return True
        except Exception:
            return False
