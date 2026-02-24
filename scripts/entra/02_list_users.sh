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

echo "📋 Listing Active Users in DaiBai Entra ID Directory..."
az ad user list --query "[].{DisplayName:displayName, UPN:userPrincipalName, UserType:userType}" --output table
