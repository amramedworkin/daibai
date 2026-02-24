"""
Tests for CacheManager Redis connectivity.

Uses @patch("redis.Redis.from_url") to mock the connection and avoid hitting the cloud.
"""

import pytest
from unittest.mock import MagicMock, patch


@patch("redis.Redis.from_url")
def test_cache_manager_ping_calls_mock_and_returns_true(mock_from_url):
    """
    CacheManager.ping() uses redis.Redis.from_url and returns True when ping succeeds.
    Mocks the connection to avoid hitting the cloud during unit tests.
    """
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    mock_from_url.return_value = mock_client

    from daibai.core.cache import CacheManager

    manager = CacheManager(connection_string="rediss://:test@localhost:6380")
    result = manager.ping()

    assert result is True
    mock_from_url.assert_called_once()
    mock_client.ping.assert_called_once()


@patch("redis.Redis.from_url")
def test_cache_manager_ping_loads_connection_from_env(mock_from_url):
    """
    CacheManager can read connection string from .env via get_redis_connection_string.
    """
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    mock_from_url.return_value = mock_client

    with patch("daibai.core.cache.get_redis_connection_string", return_value="redis://localhost:6379"):
        from daibai.core.cache import CacheManager

        manager = CacheManager()  # No connection_string - uses config
        result = manager.ping()

    assert result is True
    mock_from_url.assert_called_once_with("redis://localhost:6379", decode_responses=True)
    mock_client.ping.assert_called_once()


@patch("redis.Redis.from_url")
def test_cache_manager_ping_returns_false_when_no_connection_string(mock_from_url):
    """CacheManager.ping() returns False when no connection string is configured."""
    with patch("daibai.core.cache.get_redis_connection_string", return_value=None):
        from daibai.core.cache import CacheManager

        manager = CacheManager()
        result = manager.ping()

    assert result is False
    mock_from_url.assert_not_called()


@patch("redis.Redis.from_url")
def test_cache_manager_ping_returns_false_on_redis_error(mock_from_url):
    """CacheManager.ping() returns False when Redis raises an exception."""
    mock_from_url.side_effect = ConnectionError("Connection refused")

    from daibai.core.cache import CacheManager

    manager = CacheManager(connection_string="redis://localhost:6379")
    result = manager.ping()

    assert result is False
