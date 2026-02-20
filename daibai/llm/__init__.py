"""
LLM Provider Registry for DaiBai.

Each provider has its own specific implementation - no abstraction layer.
"""

from typing import TYPE_CHECKING, Dict, Type, Optional

if TYPE_CHECKING:
    from .base import BaseLLMProvider

# Provider registry - maps provider type to module path
PROVIDER_MODULES = {
    "gemini": "daibai.llm.gemini",
    "openai": "daibai.llm.openai_provider",
    "azure": "daibai.llm.azure",
    "anthropic": "daibai.llm.anthropic_provider",
    "ollama": "daibai.llm.ollama",
}

# Cache for loaded provider classes
_provider_cache: Dict[str, Type["BaseLLMProvider"]] = {}


def get_provider_class(provider_type: str) -> Type["BaseLLMProvider"]:
    """
    Get the provider class for a given provider type.
    
    Args:
        provider_type: One of 'gemini', 'openai', 'azure', 'anthropic', 'ollama'
    
    Returns:
        Provider class (not instantiated)
    
    Raises:
        ValueError: If provider type is unknown
        ImportError: If provider dependencies are not installed
    """
    if provider_type in _provider_cache:
        return _provider_cache[provider_type]
    
    if provider_type not in PROVIDER_MODULES:
        available = list(PROVIDER_MODULES.keys())
        raise ValueError(f"Unknown provider type '{provider_type}'. Available: {available}")
    
    module_path = PROVIDER_MODULES[provider_type]
    
    try:
        import importlib
        module = importlib.import_module(module_path)
        
        # Each module exports a class named <Type>Provider
        class_name = f"{provider_type.title()}Provider"
        if provider_type == "openai":
            class_name = "OpenAIProvider"
        elif provider_type == "anthropic":
            class_name = "AnthropicProvider"
        
        provider_class = getattr(module, class_name)
        _provider_cache[provider_type] = provider_class
        return provider_class
        
    except ImportError as e:
        raise ImportError(
            f"Provider '{provider_type}' requires additional dependencies. "
            f"Install with: pip install daibai[{provider_type}]\n"
            f"Original error: {e}"
        )


def create_provider(provider_type: str, config: dict) -> "BaseLLMProvider":
    """
    Create and configure a provider instance.
    
    Args:
        provider_type: Provider type name
        config: Configuration dict for the provider
    
    Returns:
        Configured provider instance
    """
    provider_class = get_provider_class(provider_type)
    return provider_class(**config)


def list_available_providers() -> list:
    """List all registered provider types."""
    return list(PROVIDER_MODULES.keys())


__all__ = [
    "get_provider_class",
    "create_provider",
    "list_available_providers",
    "PROVIDER_MODULES",
]
