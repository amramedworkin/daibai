#!/bin/bash
# --- AZURE CONTEXT ISOLATION ---
# Load .env to get AUTH_TENANT_ID dynamically (Identity Plane)
ENV_FILE="$(dirname "$0")/../../.env"
if [ -f "$ENV_FILE" ]; then
    set -a && source "$ENV_FILE" >/dev/null 2>&1 || true
    set +a
fi
ENTRA_TENANT="${AUTH_TENANT_ID:-e12adb01-a6b3-47bb-86c0-d662dacb3675}"
ORIGINAL_SUB=$(az account show --query id -o tsv 2>/dev/null)
if [ -n "$ORIGINAL_SUB" ]; then
    trap 'echo -e "\n🔄 Restoring original Azure context..."; az account set --subscription "$ORIGINAL_SUB" > /dev/null 2>&1' EXIT
fi
echo "🔀 Temporarily switching context to Identity Plane (Tenant: $ENTRA_TENANT)..."
az login --tenant "$ENTRA_TENANT" --allow-no-subscriptions > /dev/null 2>&1

CI_MODE=false
[[ "$1" == "--ci" || -n "${CI:-}" ]] && CI_MODE=true
if $CI_MODE; then
    CURRENT_TENANT=$(az account show --query tenantId -o tsv 2>/dev/null)
    if [[ "$CURRENT_TENANT" != "$ENTRA_TENANT" ]]; then
        echo "❌ CI: Wrong tenant ($CURRENT_TENANT). Need: $ENTRA_TENANT"
        exit 1
    fi
fi

USER_PREFIX="${USER_PREFIX:-daibai}"
SOFT_DELETE=false

if [[ "$1" == "--soft" ]]; then
    SOFT_DELETE=true
    echo "ℹ️  Soft Delete mode enabled."
else
    echo "⚠️  Hard Delete mode (Default). Users will be PERMANENTLY destroyed."
fi

echo ""
echo "🧹 Searching for test users starting with '$USER_PREFIX'..."
mapfile -t TEST_USERS < <(az ad user list --filter "startswith(userPrincipalName, '$USER_PREFIX')" --query "[].userPrincipalName" -o tsv)

if [ ${#TEST_USERS[@]} -eq 0 ]; then
    echo "No test users found matching prefix '$USER_PREFIX'."
    exit 0
fi

echo "Found the following test users:"
for user in "${TEST_USERS[@]}"; do echo " - $user"; done
echo ""
read -p "⚠️  Delete ALL these users? (Type 'yes' to confirm): " CONFIRM

if [[ "$CONFIRM" == "yes" ]]; then
    for user in "${TEST_USERS[@]}"; do
        OBJ_ID=$(az ad user show --id "$user" --query id -o tsv)
        echo "Deleting $user..."
        az ad user delete --id "$user"
        if [ "$SOFT_DELETE" = false ]; then
            sleep 2
            az rest --method DELETE --url "https://graph.microsoft.com/v1.0/directory/deletedItems/$OBJ_ID" 2>/dev/null || true
        fi
    done
    echo "✅ Bulk deletion complete."
else
    echo "Bulk deletion cancelled."
fi
