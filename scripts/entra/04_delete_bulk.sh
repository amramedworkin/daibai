#!/bin/bash
DAIBAI_TENANT_ID="${DAIBAI_TENANT_ID:-e12adb01-a6b3-47bb-86c0-d662dacb3675}"
CI_MODE=false
[[ "$1" == "--ci" || -n "${CI:-}" ]] && CI_MODE=true

CURRENT_TENANT=$(az account show --query tenantId -o tsv 2>/dev/null)
if [[ "$CURRENT_TENANT" != "$DAIBAI_TENANT_ID" ]]; then
    if $CI_MODE; then
        echo "❌ CI: Wrong tenant ($CURRENT_TENANT). Need: $DAIBAI_TENANT_ID"
        exit 1
    fi
    [[ -t 0 ]] && az login --tenant "$DAIBAI_TENANT_ID" || az login --tenant "$DAIBAI_TENANT_ID" --use-device-code
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
