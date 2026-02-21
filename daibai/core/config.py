"""
Configuration system for DaiBai.

Loads configuration from:
1. daibai.yaml (or ~/.daibai/daibai.yaml) for structure
2. .env for secrets (API keys, passwords)

Supports ${VAR} placeholder resolution from environment.
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

import yaml
from dotenv import load_dotenv


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
    locations = [
        Path.cwd() / "daibai.yaml",
        Path.cwd() / ".daibai.yaml",
        Path.home() / ".daibai" / "daibai.yaml",
        Path.home() / ".config" / "daibai" / "daibai.yaml",
    ]
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
    Load configuration from YAML and environment.
    
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
    
    # Find and load YAML config
    yaml_path = config_path or _find_config_file()
    
    if not yaml_path or not yaml_path.exists():
        # Return empty config if no file found
        return Config()
    
    with open(yaml_path, "r") as f:
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


def load_user_preferences() -> Dict[str, Any]:
    """Load user preferences (current database, LLM, etc.)."""
    import json
    if _USER_PREFS_FILE.exists():
        try:
            with open(_USER_PREFS_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"database": None, "llm": None, "mode": "sql", "clipboard": True}


def save_user_preferences(prefs: Dict[str, Any]) -> None:
    """Save user preferences."""
    import json
    _USER_PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_USER_PREFS_FILE, "w") as f:
        json.dump(prefs, f)
