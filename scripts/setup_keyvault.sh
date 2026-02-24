#!/bin/bash
# ============================================================================
# Azure Key Vault Setup
# ============================================================================
# Creates resource group (if needed), provisions Key Vault with RBAC, assigns
# "Key Vault Secrets User" to the signed-in user, and writes KEY_VAULT_URL to .env.
# Idempotent: skips components that already exist.
#
# Prerequisites: az login
# Usage: ./scripts/setup_keyvault.sh
# Override: KEY_VAULT_RESOURCE_GROUP, KEY_VAULT_NAME, KEY_VAULT_LOCATION
# ============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Configurable via env (align with Redis/Cosmos for shared RG)
KEY_VAULT_RESOURCE_GROUP="${KEY_VAULT_RESOURCE_GROUP:-daibai-rg}"
KEY_VAULT_NAME="${KEY_VAULT_NAME:-daibai-kv}"
KEY_VAULT_LOCATION="${KEY_VAULT_LOCATION:-eastus}"

echo ""
echo "============================================================================"
echo "  Azure Key Vault Setup"
echo "============================================================================"
echo "  Resource Group: $KEY_VAULT_RESOURCE_GROUP"
echo "  Vault Name:     $KEY_VAULT_NAME"
echo "  Location:       $KEY_VAULT_LOCATION"
echo "============================================================================"
echo ""

# 0. Verify az login
echo "[0/4] Verifying Azure login..."
PRINCIPAL_ID=$(az ad signed-in-user show --query id -o tsv 2>/dev/null || true)
if [[ -z "$PRINCIPAL_ID" ]]; then
    echo "[ERROR] Not logged in to Azure. Run: az login" >&2
    exit 1
fi
echo "  Signed-in user principal ID: $PRINCIPAL_ID"
echo ""

# 1. Create Resource Group (idempotent)
echo "[1/4] Creating resource group (if not exists)..."
if [[ "$(az group exists --name "$KEY_VAULT_RESOURCE_GROUP" 2>/dev/null)" == "true" ]]; then
    echo "  Already exists. Skipping."
else
    az group create --name "$KEY_VAULT_RESOURCE_GROUP" --location "$KEY_VAULT_LOCATION" -o none
    echo "  Done."
fi
echo ""

# 2. Create Key Vault (RBAC mode; skip if exists)
echo "[2/4] Creating Azure Key Vault..."
if az keyvault show --name "$KEY_VAULT_NAME" --resource-group "$KEY_VAULT_RESOURCE_GROUP" -o none 2>/dev/null; then
    echo "  Already exists. Skipping."
else
    az keyvault create \
        --name "$KEY_VAULT_NAME" \
        --resource-group "$KEY_VAULT_RESOURCE_GROUP" \
        --location "$KEY_VAULT_LOCATION" \
        --enable-rbac-authorization true \
        -o none
    echo "  Done."
fi
echo ""

# 3. Assign Key Vault Secrets User role to signed-in user
echo "[3/4] Assigning Key Vault Secrets User role to signed-in user..."
VAULT_ID=$(az keyvault show --name "$KEY_VAULT_NAME" --resource-group "$KEY_VAULT_RESOURCE_GROUP" --query id -o tsv 2>/dev/null)
if [[ -z "$VAULT_ID" ]]; then
    echo "[ERROR] Could not get vault ID." >&2
    exit 1
fi

# Check if assignment already exists
EXISTING=$(az role assignment list --assignee "$PRINCIPAL_ID" --scope "$VAULT_ID" --query "[?roleDefinitionName=='Key Vault Secrets User'].id" -o tsv 2>/dev/null || true)
if [[ -n "$EXISTING" ]]; then
    echo "  Role already assigned. Skipping."
else
    az role assignment create \
        --role "Key Vault Secrets User" \
        --assignee "$PRINCIPAL_ID" \
        --scope "$VAULT_ID" \
        -o none
    echo "  Done."
fi
echo ""

# 4. Write KEY_VAULT_URL to .env
echo "[4/4] Writing KEY_VAULT_URL to .env..."
VAULT_URI=$(az keyvault show --name "$KEY_VAULT_NAME" --resource-group "$KEY_VAULT_RESOURCE_GROUP" --query properties.vaultUri -o tsv 2>/dev/null)
if [[ -z "$VAULT_URI" ]]; then
    echo "[ERROR] Could not get vault URI." >&2
    exit 1
fi

ENV_FILE="$PROJECT_DIR/.env"
python3 "$SCRIPT_DIR/update_env.py" "$ENV_FILE" \
    "KEY_VAULT_URL=$VAULT_URI" \
    "KEY_VAULT_NAME=$KEY_VAULT_NAME" \
    "KEY_VAULT_RESOURCE_GROUP=$KEY_VAULT_RESOURCE_GROUP"

echo ""
echo "============================================================================"
echo "  KEY_VAULT_URL written to $ENV_FILE"
echo "============================================================================"
echo ""
echo "  KEY_VAULT_URL=$VAULT_URI"
echo ""
echo "  Next: Add secrets (e.g. GEMINI-API-KEY) to Key Vault:"
echo "    az keyvault secret set --vault-name $KEY_VAULT_NAME --name GEMINI-API-KEY --value \"<your-key>\""
echo ""
echo "  Secret names in Key Vault match: OPENAI-API-KEY, GEMINI-API-KEY, ANTHROPIC-API-KEY, etc."
echo "  See docs/AZURE_GUIDE.md for the full mapping."
echo ""
