"""
Configuration system for DaiBai.

Loads configuration from:
1. daibai.yaml (or ~/.daibai/daibai.yaml) for structure
2. .env for secrets (API keys, passwords)
3. Azure Key Vault (when KEY_VAULT_URL is set)

Supports ${VAR} placeholder resolution from environment.
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Cache settings (Pydantic-validated)
# ---------------------------------------------------------------------------


class CacheConfig(BaseModel):
    """Semantic cache configuration. CACHE_THRESHOLD controls match strictness."""

    CACHE_THRESHOLD: float = Field(
        default=0.90,
        description="Cosine similarity threshold for semantic cache matches. 1.0 is exact match, 0.0 is anything goes.",
    )

    @field_validator("CACHE_THRESHOLD")
    @classmethod
    def clamp_threshold(cls, v: float) -> float:
        """Ensure CACHE_THRESHOLD stays within 0.0–1.0."""
        return max(0.0, min(1.0, v))


def get_schema_vector_limit() -> int:
    """
    Get max number of tables to send to LLM (semantic schema pruning).
    Reads SCHEMA_VECTOR_LIMIT from .env. Default 12 (supports complex multi-table JOINs).
    Clamped 1–20. Higher limit increases accuracy but also increases token cost.
    """
    import os
    from pathlib import Path
    from dotenv import load_dotenv
    env_locations = []
    try:
        env_locations.append(Path.cwd() / ".env")
    except OSError:
        pass
    env_locations.extend([Path.home() / ".daibai" / ".env"])
    for loc in env_locations:
        if loc.exists():
            load_dotenv(loc)
            break
    raw = os.environ.get("SCHEMA_VECTOR_LIMIT", "12").strip()
    try:
        val = int(raw)
        return max(1, min(20, val))
    except ValueError:
        return 12


def get_schema_refresh_interval() -> int:
    """
    Get how often (in seconds) the agent re-scans the physical database structure.
    Reads SCHEMA_REFRESH_INTERVAL from .env. Default 86400 (24 hours).
    Prevents re-indexing if the interval has not yet passed.
    """
    import os
    from pathlib import Path
    from dotenv import load_dotenv
    env_locations = []
    try:
        env_locations.append(Path.cwd() / ".env")
    except OSError:
        pass
    env_locations.extend([Path.home() / ".daibai" / ".env"])
    for loc in env_locations:
        if loc.exists():
            load_dotenv(loc)
            break
    raw = os.environ.get("SCHEMA_REFRESH_INTERVAL", "86400").strip()
    try:
        val = int(raw)
        return max(60, val)  # Minimum 1 minute
    except ValueError:
        return 86400


# Key Vault secret name -> provider_type for LLM API keys
_KEYVAULT_LLM_MAPPING = {
    "OPENAI-API-KEY": "openai",
    "GEMINI-API-KEY": "gemini",
    "ANTHROPIC-API-KEY": "anthropic",
    "AZURE-OPENAI-API-KEY": "azure",
    "DEEPSEEK-API-KEY": "deepseek",
    "MISTRAL-API-KEY": "mistral",
    "GROQ-API-KEY": "groq",
    "NVIDIA-API-KEY": "nvidia",
    "ALIBABA-API-KEY": "alibaba",
    "META-API-KEY": "meta",
}


def _fetch_secrets_from_keyvault(vault_url: str) -> Dict[str, str]:
    """Fetch secrets from Azure Key Vault. Returns dict of secret_name -> value."""
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient

        credential = DefaultAzureCredential()
        client = SecretClient(vault_url=vault_url, credential=credential)
        result = {}
        for secret_name in _KEYVAULT_LLM_MAPPING:
            try:
                secret = client.get_secret(secret_name)
                if secret and secret.value:
                    result[secret_name] = secret.value
            except Exception:
                pass
        return result
    except Exception:
        return {}


@dataclass
class DatabaseConfig:
    """Configuration for a single database connection."""
    name: str
    host: str
    port: int
    database: str
    user: str
    password: str
    ssl: bool = False
    
    def connection_string(self) -> str:
        """Return MySQL connection string."""
        ssl_param = "?ssl=true" if self.ssl else ""
        return f"mysql://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}{ssl_param}"


@dataclass
class LLMProviderConfig:
    """Configuration for a single LLM provider.
    
    Supported provider_type: gemini, openai, azure, anthropic, ollama,
    groq, deepseek, mistral, nvidia, alibaba, meta.
    API keys resolved from env via ${DEEPSEEK_API_KEY}, ${MISTRAL_API_KEY}, etc.
    """
    name: str
    provider_type: str
    model: str
    api_key: Optional[str] = None
    endpoint: Optional[str] = None
    temperature: float = 0.7
    max_tokens: int = 4096
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Config:
    """Main configuration object."""
    databases: Dict[str, DatabaseConfig] = field(default_factory=dict)
    default_database: Optional[str] = None
    
    llm_providers: Dict[str, LLMProviderConfig] = field(default_factory=dict)
    default_llm: Optional[str] = None
    
    # User preferences
    clipboard: bool = True
    exports_dir: Path = field(default_factory=lambda: Path.home() / ".daibai" / "exports")
    memory_dir: Path = field(default_factory=lambda: Path.home() / ".daibai" / "memory")
    
    def get_database(self, name: Optional[str] = None) -> DatabaseConfig:
        """Get database config by name or default."""
        db_name = name or self.default_database
        if not db_name or db_name not in self.databases:
            raise ValueError(f"Database '{db_name}' not found. Available: {list(self.databases.keys())}")
        return self.databases[db_name]
    
    def get_llm(self, name: Optional[str] = None) -> LLMProviderConfig:
        """Get LLM provider config by name or default."""
        llm_name = name or self.default_llm
        if not llm_name or llm_name not in self.llm_providers:
            raise ValueError(f"LLM provider '{llm_name}' not found. Available: {list(self.llm_providers.keys())}")
        return self.llm_providers[llm_name]
    
    def list_databases(self) -> List[str]:
        """List available database names."""
        return list(self.databases.keys())
    
    def list_llm_providers(self) -> List[str]:
        """List available LLM provider names."""
        return list(self.llm_providers.keys())

    def get_llm_provider_configs_for_ui(self) -> Dict[str, Dict[str, Any]]:
        """Return provider configs for UI pre-population (API keys masked)."""
        result = {}
        for name, cfg in self.llm_providers.items():
            result[name] = {
                "model": cfg.model or "",
                "endpoint": cfg.endpoint or "",
                "api_key": "••••••" if cfg.api_key else "",
                "deployment": cfg.extra.get("deployment", ""),
            }
        return result

    # ---------------------------------------------------------------------
    # Dual-Plane identity config accessors (Identity Plane vs Infrastructure)
    # ---------------------------------------------------------------------
    @property
    def auth_tenant_id(self) -> str:
        """The tenant ID where user identities are stored (Identity Plane)."""
        return os.environ.get("AUTH_TENANT_ID", "")

    @property
    def auth_client_id(self) -> str:
        """Client ID (App Registration) used for user authentication flows."""
        return os.environ.get("AUTH_CLIENT_ID", "")

    @property
    def azure_tenant_id(self) -> str:
        """The tenant ID where infrastructure resources live (Infrastructure Plane)."""
        return os.environ.get("AZURE_TENANT_ID", "")

    @property
    def hf_token(self) -> str:
        """Hugging Face token used for authenticated model downloads (HF_TOKEN)."""
        return os.environ.get("HF_TOKEN", "")
    def validate_auth_config(self, fail_on_error: bool = True) -> bool:
        """
        Validate Robot (app-only) credentials against the Identity Plane by requesting
        a client-credentials token for Microsoft Graph.

        Behavior:
        - If AUTH_CLIENT_ID and AUTH_CLIENT_SECRET are present,
          attempt to acquire a token via msal. If acquisition fails and fail_on_error is True,
          raise RuntimeError to fail-fast at startup.
        - If credentials are not present, this is a no-op and returns False.
        """
        tenant = os.environ.get("AUTH_TENANT_ID", "").strip()
        client_id = os.environ.get("AUTH_CLIENT_ID", "").strip()
        client_secret = os.environ.get("AUTH_CLIENT_SECRET", "").strip()
        if not (tenant and client_id and client_secret):
            # No robot credentials configured; nothing to validate here.
            return False

        try:
            import msal
        except Exception as exc:
            if fail_on_error:
                raise RuntimeError("msal is required to validate Graph credentials") from exc
            return False

        authority = f"https://login.microsoftonline.com/{tenant}"
        app = msal.ConfidentialClientApplication(client_id=client_id, client_credential=client_secret, authority=authority)
        result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
        # Successful response contains 'access_token' (cast to bool)
        ok = bool(isinstance(result, dict) and "access_token" in result and result.get("access_token"))
        if not ok and fail_on_error:
            err = result.get("error_description") if isinstance(result, dict) else str(result)
            raise RuntimeError(f"Failed to acquire Graph token for tenant {tenant}: {err}")
        return ok

    def trusted_identity_plane(self) -> Dict[str, str]:
        """
        Return a small dict describing the trusted Identity Plane.

        Keys:
        - tenant_id: AUTH_TENANT_ID
        - client_id: AUTH_CLIENT_ID
        """
        return {"tenant_id": self.auth_tenant_id, "client_id": self.auth_client_id}

    def is_auth_tenant(self, tenant_id: str) -> bool:
        """Return True if the given tenant_id matches the configured AUTH_TENANT_ID."""
        return str(tenant_id or "").strip() == str(self.auth_tenant_id or "").strip()


def _resolve_env_vars(value: Any) -> Any:
    """Resolve ${VAR} placeholders from environment."""
    if isinstance(value, str):
        pattern = r'\$\{([^}]+)\}'
        def replacer(match):
            var_name = match.group(1)
            return os.environ.get(var_name, match.group(0))
        return re.sub(pattern, replacer, value)
    elif isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_resolve_env_vars(item) for item in value]
    return value


def _find_config_file() -> Optional[Path]:
    """Find daibai.yaml in standard locations."""
    locations = []
    try:
        cwd = Path.cwd()
        locations.extend([cwd / "daibai.yaml", cwd / ".daibai.yaml"])
    except OSError:
        pass  # cwd can be invalid if directory was deleted (e.g. in some test envs)
    locations.extend([
        Path.home() / ".daibai" / "daibai.yaml",
        Path.home() / ".config" / "daibai" / "daibai.yaml",
    ])
    for loc in locations:
        if loc.exists():
            return loc
    return None


def _parse_database_config(name: str, data: Dict[str, Any]) -> DatabaseConfig:
    """Parse a database configuration entry."""
    return DatabaseConfig(
        name=name,
        host=data.get("host", "localhost"),
        port=int(data.get("port", 3306)),
        database=data.get("name", data.get("database", name)),
        user=data.get("user", "root"),
        password=data.get("password", ""),
        ssl=data.get("ssl", False),
    )


def _parse_llm_config(name: str, data: Dict[str, Any]) -> LLMProviderConfig:
    """Parse an LLM provider configuration entry."""
    # Extract standard fields
    provider_type = data.get("type", name)  # Default to name if type not specified
    model = data.get("model", "")
    api_key = data.get("api_key")
    endpoint = data.get("endpoint")
    temperature = float(data.get("temperature", 0.7))
    max_tokens = int(data.get("max_tokens", 4096))
    
    # Everything else goes to extra
    standard_keys = {"type", "model", "api_key", "endpoint", "temperature", "max_tokens"}
    extra = {k: v for k, v in data.items() if k not in standard_keys}
    
    return LLMProviderConfig(
        name=name,
        provider_type=provider_type,
        model=model,
        api_key=api_key,
        endpoint=endpoint,
        temperature=temperature,
        max_tokens=max_tokens,
        extra=extra,
    )


def load_config(config_path: Optional[Path] = None, env_path: Optional[Path] = None) -> Config:
    """
    Load configuration from YAML, environment, and optionally Azure Key Vault.
    
    When KEY_VAULT_URL is set, fetches LLM API keys (OPENAI-API-KEY, GEMINI-API-KEY, etc.)
    from Azure Key Vault and maps them to provider configs. Falls back to .env/YAML if
    Key Vault is unavailable.
    
    Args:
        config_path: Path to daibai.yaml (auto-detected if not provided)
        env_path: Path to .env file (auto-detected if not provided)
    
    Returns:
        Config object with all settings
    """
    # Load .env first so variables are available for resolution
    if env_path and env_path.exists():
        load_dotenv(env_path)
    else:
        # Try standard locations (cwd can fail if deleted, e.g. in some test envs)
        env_locations = []
        try:
            env_locations.append(Path.cwd() / ".env")
        except OSError:
            pass
        env_locations.append(Path.home() / ".daibai" / ".env")
        if config_path:
            env_locations.insert(0, config_path.parent / ".env")
        for loc in env_locations:
            if loc.exists():
                load_dotenv(loc)
                break

    # Fetch secrets from Azure Key Vault if configured
    keyvault_secrets: Dict[str, str] = {}
    vault_url = os.environ.get("KEY_VAULT_URL", "").strip()
    if vault_url:
        keyvault_secrets = _fetch_secrets_from_keyvault(vault_url)
        # Inject into env for ${VAR} resolution (e.g. OPENAI_API_KEY from OPENAI-API-KEY)
        env_mapping = {
            "OPENAI-API-KEY": "OPENAI_API_KEY",
            "GEMINI-API-KEY": "GEMINI_API_KEY",
            "ANTHROPIC-API-KEY": "ANTHROPIC_API_KEY",
            "AZURE-OPENAI-API-KEY": "AZURE_OPENAI_API_KEY",
            "DEEPSEEK-API-KEY": "DEEPSEEK_API_KEY",
            "MISTRAL-API-KEY": "MISTRAL_API_KEY",
            "GROQ-API-KEY": "GROQ_API_KEY",
            "NVIDIA-API-KEY": "NVIDIA_API_KEY",
            "ALIBABA-API-KEY": "ALIBABA_API_KEY",
            "META-API-KEY": "META_API_KEY",
        }
        for kv_name, env_name in env_mapping.items():
            if kv_name in keyvault_secrets and not os.environ.get(env_name):
                os.environ[env_name] = keyvault_secrets[kv_name]
    
    # Find and load YAML config
    yaml_path = config_path or _find_config_file()
    
    if not yaml_path or not yaml_path.exists():
        # Return empty config if no file found
        return Config()
    
    with open(yaml_path, "r", encoding="utf-8") as f:
        raw_config = yaml.safe_load(f) or {}
    
    # Resolve environment variables
    config_data = _resolve_env_vars(raw_config)
    
    # Parse databases
    databases = {}
    db_section = config_data.get("databases", {})
    for db_name, db_data in db_section.items():
        if db_name != "default" and isinstance(db_data, dict):
            databases[db_name] = _parse_database_config(db_name, db_data)
    
    # Parse LLM providers
    llm_providers = {}
    llm_section = config_data.get("llm", {}).get("providers", {})
    for llm_name, llm_data in llm_section.items():
        if isinstance(llm_data, dict):
            llm_providers[llm_name] = _parse_llm_config(llm_name, llm_data)
    
    # Apply Key Vault secrets to LLM providers when api_key is empty
    for kv_name, provider_type in _KEYVAULT_LLM_MAPPING.items():
        if kv_name not in keyvault_secrets:
            continue
        for provider_name, provider in llm_providers.items():
            if (provider.provider_type or "").lower() == provider_type.lower() and not provider.api_key:
                provider.api_key = keyvault_secrets[kv_name]
    
    # Get defaults
    default_database = db_section.get("default") or (list(databases.keys())[0] if databases else None)
    default_llm = config_data.get("llm", {}).get("default") or (list(llm_providers.keys())[0] if llm_providers else None)
    
    # Parse paths
    exports_dir = Path(config_data.get("exports_dir", Path.home() / ".daibai" / "exports"))
    memory_dir = Path(config_data.get("memory_dir", Path.home() / ".daibai" / "memory"))
    
    return Config(
        databases=databases,
        default_database=default_database,
        llm_providers=llm_providers,
        default_llm=default_llm,
        clipboard=config_data.get("clipboard", True),
        exports_dir=exports_dir,
        memory_dir=memory_dir,
    )


# User preferences (persisted separately)
_USER_PREFS_FILE = Path.home() / ".daibai" / "preferences.json"


def get_redis_connection_string() -> Optional[str]:
    """
    Get Azure Redis connection string for semantic caching.
    Loads .env from standard locations, then reads AZURE_REDIS_CONNECTION_STRING
    or REDIS_URL. Returns None if not configured.
    """
    # Ensure .env is loaded so AZURE_REDIS_CONNECTION_STRING / REDIS_URL are available
    env_locations = []
    try:
        env_locations.append(Path.cwd() / ".env")
    except OSError:
        pass
    env_locations.extend([
        Path.home() / ".daibai" / ".env",
    ])
    for loc in env_locations:
        if loc.exists():
            load_dotenv(loc)
            break

    return (
        os.environ.get("AZURE_REDIS_CONNECTION_STRING", "").strip()
        or os.environ.get("REDIS_URL", "").strip()
        or None
    )


def get_redis_entra_config() -> Optional[tuple]:
    """
    Get (host, port) for Redis when using Entra ID (secretless).
    Returns non-None when REDIS_USE_ENTRA_ID=1 and host is available from
    AZURE_REDIS_HOST or parsed from REDIS_URL. Port defaults to 6380 (Azure SSL).
    """
    use_entra = os.environ.get("REDIS_USE_ENTRA_ID", "").strip().lower() in ("1", "true", "yes")
    if not use_entra:
        return None
    host = os.environ.get("AZURE_REDIS_HOST", "").strip()
    if not host:
        url = get_redis_connection_string()
        if url and "redis" in url:
            try:
                from urllib.parse import urlparse
                parsed = urlparse(url)
                if parsed.hostname:
                    host = parsed.hostname
            except Exception:
                pass
    if not host:
        return None
    port_str = os.environ.get("AZURE_REDIS_PORT", "6380").strip()
    try:
        port = int(port_str)
    except ValueError:
        port = 6380
    return (host, port)


def get_semantic_similarity_threshold() -> float:
    """
    Get similarity threshold for semantic cache retrieval (0.0–1.0).
    Uses CacheConfig (Pydantic-validated). Reads CACHE_THRESHOLD or
    SEMANTIC_SIMILARITY_THRESHOLD from .env. Default 0.90.
    """
    # Ensure .env is loaded
    env_locations = []
    try:
        env_locations.append(Path.cwd() / ".env")
    except OSError:
        pass
    env_locations.extend([Path.home() / ".daibai" / ".env"])
    for loc in env_locations:
        if loc.exists():
            load_dotenv(loc)
            break

    raw = (
        os.environ.get("CACHE_THRESHOLD", "").strip()
        or os.environ.get("SEMANTIC_SIMILARITY_THRESHOLD", "0.90").strip()
    )
    try:
        val = float(raw)
        cfg = CacheConfig(CACHE_THRESHOLD=val)
        return cfg.CACHE_THRESHOLD
    except ValueError:
        return CacheConfig().CACHE_THRESHOLD


def load_user_preferences() -> Dict[str, Any]:
    """Load user preferences (current database, LLM, etc.)."""
    import json
    if _USER_PREFS_FILE.exists():
        try:
            with open(_USER_PREFS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"database": None, "llm": None, "mode": "sql", "clipboard": True}


def save_user_preferences(prefs: Dict[str, Any]) -> None:
    """Save user preferences."""
    import json
    _USER_PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_USER_PREFS_FILE, "w", encoding="utf-8") as f:
        json.dump(prefs, f)
