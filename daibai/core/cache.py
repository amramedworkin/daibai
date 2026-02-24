"""
Cache manager for Redis connectivity.

Uses AZURE_REDIS_CONNECTION_STRING or REDIS_URL from .env (via config).
"""

from typing import Optional

from .config import get_redis_connection_string


class CacheManager:
    """
    Manages Redis connection for caching.
    Initializes a redis.Redis client from the connection string in .env.
    """

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
