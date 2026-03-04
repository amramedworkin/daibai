#!/usr/bin/env python3
"""
Dump all secrets from Azure Key Vault.

Reads KEY_VAULT_URL from .env. Uses DefaultAzureCredential for auth (az login).
Output is formatted for easy reading.
"""
import os
import sys
from pathlib import Path

# Load .env from project and home
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


def main():
    _load_env()
    vault_url = os.environ.get("KEY_VAULT_URL", "").strip().rstrip("/")
    if not vault_url:
        print("KEY_VAULT_URL not set.", file=sys.stderr)
        print("  Add to .env: KEY_VAULT_URL=https://your-vault.vault.azure.net/", file=sys.stderr)
        print("  Or run: ./scripts/cli.sh keyvault-create", file=sys.stderr)
        sys.exit(1)

    mask = "--mask" in sys.argv or "-m" in sys.argv

    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
    except ImportError as e:
        print("Azure SDK required: pip install azure-keyvault-secrets azure-identity", file=sys.stderr)
        sys.exit(1)

    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=vault_url, credential=credential)

    # Extract vault name for display (e.g. https://myvault.vault.azure.net -> myvault)
    vault_name = vault_url.replace("https://", "").replace(".vault.azure.net", "").rstrip("/")

    print()
    print("=" * 60)
    print(f"  Azure Key Vault: {vault_name}")
    print("=" * 60)
    print(f"  URL: {vault_url}")
    print()
    if mask:
        print("  (Values masked — remove --mask to see full values)")
        print()

    count = 0
    for props in client.list_properties_of_secrets():
        name = props.name
        count += 1
        try:
            secret = client.get_secret(name)
            value = secret.value or "(empty)"
            updated = getattr(props, "updated_on", None)
            updated_str = str(updated) if updated else "(unknown)"

            if mask:
                if len(value) <= 8:
                    display = "••••••••"
                else:
                    display = f"{value[:4]}...{value[-4:]}"
            else:
                display = value

            print(f"  {name}")
            print(f"    value:   {display}")
            print(f"    updated: {updated_str}")
            print()
        except Exception as e:
            print(f"  {name}")
            print(f"    error:   {e}")
            print()

    if count == 0:
        print("  (no secrets found)")
        print()
    else:
        print("-" * 60)
        print(f"  Total: {count} secret(s)")
        print()


if __name__ == "__main__":
    main()
