"""
Environment readiness check for DaiBai.

Hardened validation: required keys, placeholder detection, at-least-one LLM.
Supports MYSQL_* (codebase standard) and daibai.yaml for database config.
"""

import os
import sys
from pathlib import Path
from typing import List, Tuple

# ---------------------------------------------------------------------------
# EnvValidator: Hardened pre-flight checks
# ---------------------------------------------------------------------------

# DB keys: check both DB_* (task spec) and MYSQL_* (codebase standard)
REQUIRED_DB_KEYS = ["DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME"]
DB_KEY_ALIASES = {
    "DB_HOST": "MYSQL_HOST",
    "DB_USER": "MYSQL_USER",
    "DB_PASSWORD": "MYSQL_PASSWORD",
    "DB_NAME": "MYSQL_DATABASE",
}

LLM_KEYS = [
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AZURE_OPENAI_KEY",
    "AZURE_OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "MISTRAL_API_KEY",
    "GROQ_API_KEY",
    "NVIDIA_API_KEY",
    "ALIBABA_API_KEY",
    "META_API_KEY",
]

INVALID_PLACEHOLDERS = frozenset({
    "",
    "your-api-key",
    "your_api_key",
    "yourpassword",
    "your_password",
    "your_user",
    "youruser",
    "yourkey",
    "your_key",
    "<insert-key-here>",
    "insert-key-here",
    "xxx",
    "changeme",
    "change-me",
    "secret",
    "password",
})


def _get_env_val(key: str, alias: str) -> str:
    """Get value for key or its alias."""
    val = os.environ.get(key)
    if not val and alias:
        val = os.environ.get(alias)
    return (val or "").strip()


def _is_invalid(val: str) -> bool:
    """True if value is empty or a placeholder."""
    if not val:
        return True
    return val.lower() in INVALID_PLACEHOLDERS


def _database_configured_via_yaml() -> bool:
    """True if database is configured via daibai.yaml (no env-based DB)."""
    if os.environ.get("MYSQL_HOST") or os.environ.get("DB_HOST"):
        return False
    if os.environ.get("MYSQL_PASSWORD") or os.environ.get("DB_PASSWORD"):
        return False
    try:
        from daibai.core.config import load_config
        cfg = load_config()
        if cfg.databases:
            for db in cfg.databases.values():
                if db.password or db.host:
                    return True
    except Exception:
        pass
    return False


class EnvValidator:
    """
    Hardened environment pre-flight validator.
    Returns (True, []) if valid, (False, list of issues) if invalid.
    """

    REQUIRED_KEYS = REQUIRED_DB_KEYS
    LLM_KEYS = LLM_KEYS
    INVALID_PLACEHOLDERS = INVALID_PLACEHOLDERS

    @classmethod
    def validate(cls) -> Tuple[bool, List[str]]:
        missing_or_invalid: List[str] = []

        # Database: require keys unless configured via daibai.yaml
        if not _database_configured_via_yaml():
            for key in cls.REQUIRED_KEYS:
                alias = DB_KEY_ALIASES.get(key)
                val = _get_env_val(key, alias)
                if _is_invalid(val):
                    missing_or_invalid.append(key)
        else:
            # DB from YAML; still reject any partial DB_*/MYSQL_* that are placeholders
            for key in cls.REQUIRED_KEYS:
                alias = DB_KEY_ALIASES.get(key)
                val = _get_env_val(key, alias)
                if val and _is_invalid(val):
                    missing_or_invalid.append(key)

        # At least one valid LLM key (or KEY_VAULT_URL)
        kv_url = os.environ.get("KEY_VAULT_URL", "").strip()
        if kv_url and not _is_invalid(kv_url):
            has_llm = True
        else:
            has_llm = False
            for key in cls.LLM_KEYS:
                val = os.environ.get(key, "").strip()
                if val and not _is_invalid(val):
                    has_llm = True
                    break

        if not has_llm:
            missing_or_invalid.append(f"AT_LEAST_ONE_OF: {', '.join(cls.LLM_KEYS[:4])}...")

        if missing_or_invalid:
            return False, missing_or_invalid
        return True, []


# ---------------------------------------------------------------------------
# Component checks (redis, cosmos, database, llm, keyvault) for status report
# ---------------------------------------------------------------------------

COMPONENTS = [
    ("redis", "Redis cache (REDIS_URL, AZURE_REDIS_CONNECTION_STRING)", False),
    ("cosmos", "Cosmos DB (COSMOS_ENDPOINT)", False),
    ("database", "Database (daibai.yaml or MYSQL_*)", True),
    ("llm", "LLM provider (GEMINI_API_KEY, OPENAI_API_KEY, etc.)", True),
    ("keyvault", "Azure Key Vault (KEY_VAULT_URL)", False),
]


def check_redis() -> Tuple[bool, str]:
    conn = (
        os.environ.get("AZURE_REDIS_CONNECTION_STRING", "").strip()
        or os.environ.get("REDIS_URL", "").strip()
    )
    if conn:
        return True, "set"
    use_entra = os.environ.get("REDIS_USE_ENTRA_ID", "").strip().lower() in ("1", "true", "yes")
    host = os.environ.get("AZURE_REDIS_HOST", "").strip()
    if use_entra and host:
        return True, "Entra ID"
    return False, "not set"


def check_cosmos() -> Tuple[bool, str]:
    ep = os.environ.get("COSMOS_ENDPOINT", "").strip()
    return bool(ep), "set" if ep else "not set"


def check_database() -> Tuple[bool, str]:
    if os.environ.get("MYSQL_HOST") or os.environ.get("MYSQL_PASSWORD") or os.environ.get("DB_HOST") or os.environ.get("DB_PASSWORD"):
        return True, "MYSQL_* / DB_*"
    try:
        from daibai.core.config import load_config
        cfg = load_config()
        if cfg.databases:
            for db in cfg.databases.values():
                if db.password or db.host:
                    return True, f"daibai.yaml ({db.name})"
    except Exception:
        pass
    return False, "not set"


def check_llm() -> Tuple[bool, str]:
    for key in LLM_KEYS:
        if os.environ.get(key, "").strip() and not _is_invalid(os.environ.get(key, "").strip()):
            return True, key
    if os.environ.get("KEY_VAULT_URL", "").strip() and not _is_invalid(os.environ.get("KEY_VAULT_URL", "").strip()):
        return True, "KEY_VAULT_URL (keys from vault)"
    return False, "not set"


def check_keyvault() -> Tuple[bool, str]:
    url = os.environ.get("KEY_VAULT_URL", "").strip()
    return bool(url) and not _is_invalid(url), "set" if url else "not set"


def check_all() -> dict:
    handlers = {
        "redis": check_redis,
        "cosmos": check_cosmos,
        "database": check_database,
        "llm": check_llm,
        "keyvault": check_keyvault,
    }
    result = {}
    for comp, _desc, req in COMPONENTS:
        ok, msg = handlers[comp]()
        result[comp] = (ok, msg, req)
    return result


def run(verbose: bool = True, strict: bool = False) -> int:
    results = check_all()
    core_ok = all(results[c][0] for c, _, req in COMPONENTS if req)
    all_ok = all(r[0] for r in results.values())
    ready = all_ok if strict else core_ok

    if verbose:
        green = "\033[92m" if (getattr(sys.stdout, "isatty", lambda: False)()) else ""
        red = "\033[91m" if (getattr(sys.stdout, "isatty", lambda: False)()) else ""
        dim = "\033[2m" if (getattr(sys.stdout, "isatty", lambda: False)()) else ""
        reset = "\033[0m"
        print("")
        print("DaiBai environment readiness")
        print("-" * 50)
        for comp, _desc, required in COMPONENTS:
            ok, msg, _ = results[comp]
            sym = f"{green}✓{reset}" if ok else (f"{red}✗{reset}" if required else f"{dim}○{reset}")
            req_str = " (required)" if required else ""
            print(f"  {sym} {comp}: {msg}{req_str}")
        print("-" * 50)
        if ready:
            print(f"  {green}Ready{reset}")
        else:
            missing = [c for c, _, req in COMPONENTS if not results[c][0] and (strict or req)]
            print(f"  {red}Not ready{reset}: missing {', '.join(missing)}")
        print("")
    return 0 if ready else 1
