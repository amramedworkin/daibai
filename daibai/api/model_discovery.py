"""
Model discovery for LLM providers.

Fetches available models from provider APIs. All returned strings are
ASCII-sanitized to avoid encoding errors in JSON responses.
"""

import asyncio
import json
import os
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional

# Set DAIBAI_DEBUG_MODELS=1 to print detailed flow to console
_DEBUG = os.environ.get("DAIBAI_DEBUG_MODELS", "").strip() in ("1", "true", "yes")


def _log(msg: str, *args) -> None:
    if _DEBUG:
        text = msg % args if args else msg
        print(f"[fetch-models] {text}", flush=True)


def safe_str(s: str) -> str:
    """Ensure string is ASCII-safe to avoid encoding errors in JSON responses."""
    if not isinstance(s, str):
        return str(s)
    return s.encode("ascii", "ignore").decode("ascii")


def _fetch_http(url: str, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Sync HTTP GET; run in thread pool for async compatibility.
    Raw response is decoded as UTF-8, parsed as JSON, then sanitized to ASCII
    to avoid 'ascii codec can't encode' errors downstream."""
    _log("_fetch_http: GET %s (headers: %s)", url.split("?")[0] + "?key=...", list((headers or {}).keys()))
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw_bytes = resp.read()
        _log("_fetch_http: received %d bytes", len(raw_bytes))
        body = raw_bytes.decode("utf-8", errors="replace")
        _log("_fetch_http: decoded body (first 200 chars): %r", body[:200])
        try:
            data = json.loads(body)
            _log("_fetch_http: json.loads OK, keys=%s", list(data.keys()) if isinstance(data, dict) else type(data))
        except json.JSONDecodeError as e:
            _log("_fetch_http: json.loads FAILED: %s", e)
            raise
        sanitized = _sanitize_any(data)
        _log("_fetch_http: _sanitize_any done")
        return sanitized


def _sanitize_any(obj: Any) -> Any:
    """Recursively sanitize JSON-like structures to ASCII-safe strings (values only)."""
    if isinstance(obj, str):
        return safe_str(obj)
    if isinstance(obj, dict):
        return {k: _sanitize_any(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_any(x) for x in obj]
    return obj


def _sanitize_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively sanitize result dict to ASCII-safe strings."""
    return _sanitize_any(result)


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
        _log("gemini: entry, api_key=%s", "SET" if api_key else "MISSING")
        if not api_key:
            _log("gemini: returning error (no API key)")
            return {"models": [], "error": "API key required"}
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
        try:
            _log("gemini: calling _fetch_http...")
            data = await asyncio.to_thread(_fetch_http, url)
            _log("gemini: _fetch_http returned, data.models count=%d", len(data.get("models", [])))
            raw_models = data.get("models", [])
            models = []
            for i, m in enumerate(raw_models):
                name = m.get("name", "")
                stripped = name.replace("models/", "") if name else ""
                safe = safe_str(stripped)
                if i < 5:  # log first 5
                    _log("gemini: model[%d] name=%r -> stripped=%r -> safe=%r", i, name, stripped, safe)
                models.append(safe)
            _log("gemini: extracted %d models, calling _sanitize_result", len(models))
            result = _sanitize_result({"models": models})
            _log("gemini: returning %d models", len(result["models"]))
            return result
        except urllib.error.HTTPError as e:
            _log("gemini: HTTPError %s", e.code)
            if e.code == 400:
                return {"models": [], "error": "Invalid API key"}
            return {"models": [], "error": f"HTTP {e.code}"}
        except Exception as e:
            _log("gemini: Exception %s: %s", type(e).__name__, e)
            import traceback
            _log("gemini: traceback:\n%s", traceback.format_exc())
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
