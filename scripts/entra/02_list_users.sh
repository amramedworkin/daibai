#!/bin/bash
# --- AZURE CONTEXT ISOLATION ---
ENTRA_TENANT="e12adb01-a6b3-47bb-86c0-d662dacb3675"
ORIGINAL_SUB=$(az account show --query id -o tsv 2>/dev/null)
if [ -n "$ORIGINAL_SUB" ]; then
    trap 'echo -e "\n🔄 Restoring original Azure context..."; az account set --subscription "$ORIGINAL_SUB" > /dev/null 2>&1' EXIT
fi
echo "🔀 Temporarily switching context to Entra Directory..."
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

echo "📋 Listing Active Users in DaiBai Entra ID Directory..."
az ad user list --query "[].{DisplayName:displayName, UPN:userPrincipalName, UserType:userType}" --output table
