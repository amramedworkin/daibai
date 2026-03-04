#!/usr/bin/env python3
"""
Migrate .env API keys to Azure Key Vault.

Reads KEY_VAULT_URL and LLM API keys from .env. For each key that has a value:
- If the secret does NOT exist in Key Vault → create it
- If the secret EXISTS in Key Vault → skip (unless --force)

Usage:
  python keyvault_migrate.py           # Migrate only missing secrets
  python keyvault_migrate.py --force   # Overwrite existing secrets
"""
import os
import sys
from pathlib import Path

# Key Vault secret name -> .env variable name (must match config.py env_mapping)
KV_TO_ENV = {
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

INVALID_PLACEHOLDERS = frozenset({
    "", "your-api-key", "your_api_key", "yourpassword", "your_password",
    "your_user", "youruser", "yourkey", "your_key", "<insert-key-here>",
    "insert-key-here", "xxx", "changeme", "change-me", "secret", "password",
})


def _load_env():
    root = Path(__file__).resolve().parent.parent
    for loc in [root / ".env", Path.home() / ".daibai" / ".env"]:
        if loc.exists():
            try:
                from dotenv import load_dotenv
                load_dotenv(loc)
            except ImportError:
                pass
            break


def _is_valid_value(val: str) -> bool:
    if not val or not val.strip():
        return False
    return val.strip().lower() not in INVALID_PLACEHOLDERS


def main():
    _load_env()
    force = "--force" in sys.argv or "-f" in sys.argv

    vault_url = os.environ.get("KEY_VAULT_URL", "").strip().rstrip("/")
    if not vault_url:
        print("KEY_VAULT_URL not set.", file=sys.stderr)
        print("  Add to .env or run: ./scripts/cli.sh keyvault-create", file=sys.stderr)
        sys.exit(1)

    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
        from azure.core.exceptions import ResourceNotFoundError
    except ImportError:
        print("Azure SDK required: pip install azure-keyvault-secrets azure-identity", file=sys.stderr)
        sys.exit(1)

    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=vault_url, credential=credential)
    vault_name = vault_url.replace("https://", "").replace(".vault.azure.net", "").rstrip("/")

    print()
    print("=" * 60)
    print(f"  Migrate .env → Key Vault ({vault_name})")
    print("=" * 60)
    print()
    if force:
        print("  Mode: --force (overwrite existing secrets)")
    else:
        print("  Mode: skip existing (use --force to overwrite)")
    print()

    migrated = []
    skipped_exists = []
    skipped_no_value = []

    for kv_name, env_name in KV_TO_ENV.items():
        value = os.environ.get(env_name, "").strip()
        if not _is_valid_value(value):
            skipped_no_value.append((kv_name, env_name))
            continue

        try:
            client.get_secret(kv_name)
            exists = True
        except ResourceNotFoundError:
            exists = False
        except Exception as e:
            print(f"  {kv_name}: error checking — {e}", file=sys.stderr)
            continue

        if exists and not force:
            skipped_exists.append(kv_name)
            continue

        try:
            client.set_secret(kv_name, value)
            migrated.append(kv_name)
            action = "overwritten" if exists else "created"
            print(f"  ✓ {kv_name} — {action}")
        except Exception as e:
            print(f"  ✗ {kv_name}: failed — {e}", file=sys.stderr)

    print()
    print("-" * 60)
    if migrated:
        print(f"  Migrated:  {len(migrated)}  {migrated}")
    if skipped_exists:
        print(f"  Skipped (already exists): {len(skipped_exists)}  {skipped_exists}")
        if not force:
            print("            Use --force to overwrite.")
    if skipped_no_value:
        print(f"  Skipped (no value in .env): {[e for _, e in skipped_no_value]}")
    print()


if __name__ == "__main__":
    main()
