"""
DaiBai API module - REST API and WebSocket endpoints.
"""

from .server import app, run_server

__all__ = ["app", "run_server"]
