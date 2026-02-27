"""
Cache manager for Redis connectivity.

Connects via REDIS_URL or AZURE_REDIS_CONNECTION_STRING from environment.
"""

import hashlib
import json
from typing import List, Optional

from .config import (
    get_redis_connection_string,
    get_semantic_similarity_threshold,
)

SEMANTIC_KEY_PREFIX = "semantic:"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two vectors (0.0 to 1.0)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


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
        conn_str = self._connection_string or get_redis_connection_string()
        if not conn_str:
            return None
        import redis
        self._client = redis.Redis.from_url(conn_str, decode_responses=True)
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

    def check_semantic(
        self,
        prompt: str,
        threshold: Optional[float] = None,
    ) -> Optional[str]:
        """
        Search semantic cache for a similar prompt.
        Generates embedding for prompt, scans semantic: keys, computes cosine similarity.
        Returns cached response if a match exceeds threshold, else None.
        Threshold defaults to SEMANTIC_SIMILARITY_THRESHOLD from .env (0.90).
        """
        if threshold is None:
            threshold = get_semantic_similarity_threshold()
        query_vector = self.get_embedding(prompt)
        if query_vector is None:
            return None
        try:
            client = self._get_client()
            if client is None:
                return None
            keys = client.keys(f"{SEMANTIC_KEY_PREFIX}*")
            best_response = None
            best_sim = 0.0
            for key in keys:
                raw = client.get(key)
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                stored_vector = payload.get("vector")
                if not stored_vector or not isinstance(stored_vector, list):
                    continue
                sim = _cosine_similarity(query_vector, stored_vector)
                if sim >= threshold and sim > best_sim:
                    best_sim = sim
                    best_response = payload.get("response")
            return best_response
        except Exception:
            return None
