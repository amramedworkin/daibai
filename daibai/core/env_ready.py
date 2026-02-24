"""
Environment readiness check for DaiBai.

Verifies each required/optional env component is configured.
"""

import os
import sys
from pathlib import Path

# (component_id, description, required_for_core)
COMPONENTS = [
    ("redis", "Redis cache (REDIS_URL, AZURE_REDIS_CONNECTION_STRING, or REDIS_USE_ENTRA_ID+AZURE_REDIS_HOST)", False),
    ("cosmos", "Cosmos DB (COSMOS_ENDPOINT)", False),
    ("database", "Database (daibai.yaml or MYSQL_*)", True),
    ("llm", "LLM provider (GEMINI_API_KEY, OPENAI_API_KEY, etc.)", True),
    ("keyvault", "Azure Key Vault (KEY_VAULT_URL)", False),
]

_LLM_KEYS = (
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AZURE_OPENAI_KEY",
    "DEEPSEEK_API_KEY",
    "MISTRAL_API_KEY",
    "GROQ_API_KEY",
    "NVIDIA_API_KEY",
    "ALIBABA_API_KEY",
    "META_API_KEY",
)


def check_redis() -> tuple[bool, str]:
    """True if Redis is configured (connection string or Entra)."""
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


def check_cosmos() -> tuple[bool, str]:
    """True if Cosmos DB endpoint is set."""
    ep = os.environ.get("COSMOS_ENDPOINT", "").strip()
    return bool(ep), "set" if ep else "not set"


def check_database() -> tuple[bool, str]:
    """True if database is configured (daibai.yaml or MYSQL_*)."""
    if os.environ.get("MYSQL_HOST") or os.environ.get("MYSQL_PASSWORD"):
        return True, "MYSQL_*"
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


def check_llm() -> tuple[bool, str]:
    """True if at least one LLM API key is configured."""
    for key in _LLM_KEYS:
        if os.environ.get(key, "").strip():
            return True, key
    if os.environ.get("KEY_VAULT_URL", "").strip():
        return True, "KEY_VAULT_URL (keys from vault)"
    return False, "not set"


def check_keyvault() -> tuple[bool, str]:
    """True if Key Vault URL is set."""
    url = os.environ.get("KEY_VAULT_URL", "").strip()
    return bool(url), "set" if url else "not set"


def check_all() -> dict[str, tuple[bool, str, bool]]:
    """
    Check all env components. Returns dict of component -> (ok, message, required).
    """
    handlers = {
        "redis": check_redis,
        "cosmos": check_cosmos,
        "database": check_database,
        "llm": check_llm,
        "keyvault": check_keyvault,
    }
    result = {}
    for comp, _desc, required in COMPONENTS:
        ok, msg = handlers[comp]()
        result[comp] = (ok, msg, required)
    return result


def run(verbose: bool = True, strict: bool = False) -> int:
    """
    Run checks and print report. Returns 0 if ready, 1 otherwise.
    strict=True: require all components. strict=False: require only core (database, llm).
    """
    results = check_all()
    core_ok = all(results[c][0] for c, _, req in COMPONENTS if req)
    all_ok = all(r[0] for r in results.values())
    ready = all_ok if strict else core_ok

    if verbose:
        try:
            green = "\033[92m" if sys.stdout.isatty() else ""
            red = "\033[91m" if sys.stdout.isatty() else ""
            reset = "\033[0m" if sys.stdout.isatty() else ""
        except Exception:
            green = red = reset = ""

        print("")
        print("DaiBai environment readiness")
        print("-" * 50)
        for comp, _desc, required in COMPONENTS:
            ok, msg, _ = results[comp]
            sym = f"{green}✓{reset}" if ok else f"{red}✗{reset}"
            req = " (required)" if required else ""
            print(f"  {sym} {comp}: {msg}{req}")
        print("-" * 50)
        if ready:
            print(f"  {green}Ready{reset}")
        else:
            missing = [c for c, _, req in COMPONENTS if not results[c][0] and (strict or req)]
            print(f"  {red}Not ready{reset}: missing {', '.join(missing)}")
        print("")

    return 0 if ready else 1
