"""
Model discovery for LLM providers.

Fetches available models from provider APIs. All returned strings are
ASCII-sanitized to avoid encoding errors in JSON responses.
"""

import asyncio
import json
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional


def safe_str(s: str) -> str:
    """Ensure string is ASCII-safe to avoid encoding errors in JSON responses."""
    if not isinstance(s, str):
        return str(s)
    return s.encode("ascii", "ignore").decode("ascii")


def _fetch_http(url: str, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Sync HTTP GET; run in thread pool for async compatibility."""
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return json.loads(body)


def _sanitize_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively sanitize result dict to ASCII-safe strings."""
    out: Dict[str, Any] = {}
    for k, v in result.items():
        if isinstance(v, str):
            out[k] = safe_str(v)
        elif isinstance(v, list):
            out[k] = [safe_str(x) if isinstance(x, str) else x for x in v]
        elif isinstance(v, dict):
            out[k] = _sanitize_result(v)
        else:
            out[k] = v
    return out


async def fetch_provider_models(
    provider: str,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Fetch available models from an LLM provider.
    Returns {models: [...], error?: str, message?: str}
    All string values are ASCII-sanitized.
    """
    provider = (provider or "").lower()

    # Azure: deployment-based, no model list API
    if provider == "azure":
        return {
            "models": [],
            "message": "Azure uses Deployment Names. Enter your deployment name in the Model field (e.g. gpt-4o).",
        }

    # Ollama: GET {base_url}/api/tags (no API key)
    if provider == "ollama":
        url = (base_url or "http://localhost:11434").rstrip("/") + "/api/tags"
        try:
            data = await asyncio.to_thread(_fetch_http, url)
            models = []
            for m in data.get("models", []):
                name = m.get("name") or m.get("model", "")
                if name:
                    models.append(safe_str(name))
            return _sanitize_result({"models": models})
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
            return {"models": [], "error": safe_str(f"HTTP {e.code}: {body[:200]}")}
        except Exception as e:
            return {"models": [], "error": safe_str(str(e))}

    # Anthropic: GET https://api.anthropic.com/v1/models, x-api-key
    if provider == "anthropic":
        if not api_key:
            return {"models": [], "error": "API key required"}
        url = "https://api.anthropic.com/v1/models"
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        try:
            data = await asyncio.to_thread(_fetch_http, url, headers)
            models = [safe_str(m["id"]) for m in data.get("data", []) if m.get("id")]
            return _sanitize_result({"models": models})
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return {"models": [], "error": "Invalid API key"}
            return {"models": [], "error": f"HTTP {e.code}"}
        except Exception as e:
            return {"models": [], "error": safe_str(str(e))}

    # Google Gemini: GET https://generativelanguage.googleapis.com/v1beta/models?key={key}
    if provider == "gemini":
        if not api_key:
            return {"models": [], "error": "API key required"}
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
        try:
            data = await asyncio.to_thread(_fetch_http, url)
            models = [
                safe_str(m.get("name", "").replace("models/", ""))
                for m in data.get("models", [])
                if m.get("name")
            ]
            return _sanitize_result({"models": models})
        except urllib.error.HTTPError as e:
            if e.code == 400:
                return {"models": [], "error": "Invalid API key"}
            return {"models": [], "error": f"HTTP {e.code}"}
        except Exception as e:
            return {"models": [], "error": safe_str(str(e))}

    # OpenAI, Groq, Mistral, DeepSeek, Nvidia, Alibaba, Meta: GET {base_url}/models, Bearer token
    openai_like = ["openai", "groq", "mistral", "deepseek", "nvidia", "alibaba", "meta"]
    if provider in openai_like:
        if not api_key:
            return {"models": [], "error": "API key required"}
        defaults = {
            "openai": "https://api.openai.com/v1",
            "groq": "https://api.groq.com/openai/v1",
            "mistral": "https://api.mistral.ai/v1",
            "deepseek": "https://api.deepseek.com/v1",
            "nvidia": "https://integrate.api.nvidia.com/v1",
            "alibaba": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            "meta": None,
        }
        base = base_url or defaults.get(provider)
        if not base:
            return {"models": [], "error": "Endpoint URL required for this provider"}
        url = base.rstrip("/") + "/models"
        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            data = await asyncio.to_thread(_fetch_http, url, headers)
            models = [safe_str(m["id"]) for m in data.get("data", []) if m.get("id")]
            return _sanitize_result({"models": models})
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return {"models": [], "error": "Invalid API key"}
            return {"models": [], "error": f"HTTP {e.code}"}
        except Exception as e:
            return {"models": [], "error": safe_str(str(e))}

    return {"models": [], "error": f"Unknown provider: {provider}"}
