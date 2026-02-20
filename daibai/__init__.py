"""
DaiBai - AI-powered natural language database assistant.

A multi-LLM text-to-SQL tool supporting Gemini, OpenAI, Azure, and Anthropic.
"""

__version__ = "0.1.0"
__author__ = "DaiBai Contributors"

from .core.agent import DaiBaiAgent
from .core.config import load_config, Config

__all__ = ["DaiBaiAgent", "load_config", "Config", "__version__"]
