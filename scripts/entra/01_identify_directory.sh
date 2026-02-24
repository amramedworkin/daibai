#!/bin/bash
# DaiBai Entra ID tenant (daibaiauth) - override via DAIBAI_TENANT_ID
# CI=1 or --ci: skip tenant switch, report only (for test harnesses)
DAIBAI_TENANT_ID="${DAIBAI_TENANT_ID:-e12adb01-a6b3-47bb-86c0-d662dacb3675}"
CI_MODE=false
[[ "$1" == "--ci" || "$1" == "-n" || -n "${CI:-}" || -n "${NON_INTERACTIVE:-}" ]] && CI_MODE=true

echo "🔍 Identifying DaiBai Microsoft Entra ID Directory..."
CURRENT_TENANT=$(az account show --query tenantId -o tsv 2>/dev/null)

if [[ -z "$CURRENT_TENANT" ]]; then
    echo "❌ Not logged in to Azure. Run: az login"
    exit 1
fi

if [[ "$CURRENT_TENANT" != "$DAIBAI_TENANT_ID" ]]; then
    if $CI_MODE; then
        echo "⚠️  CI mode: Current tenant ($CURRENT_TENANT) ≠ DaiBai tenant ($DAIBAI_TENANT_ID)"
        echo "    Run interactively: ./scripts/entra/01_identify_directory.sh"
        echo "    Or: az login --tenant $DAIBAI_TENANT_ID"
        exit 1
    fi
    echo "⚠️  Current tenant ($CURRENT_TENANT) is not the DaiBai tenant."
    echo "    Switching to DaiBai tenant ($DAIBAI_TENANT_ID)..."
    if [[ -t 0 ]]; then
        az login --tenant "$DAIBAI_TENANT_ID" || exit 1
    else
        echo "    (No TTY - using device code flow. Open the URL shown below.)"
        az login --tenant "$DAIBAI_TENANT_ID" --use-device-code || exit 1
    fi
fi

TENANT_ID=$(az account show --query tenantId -o tsv)
TENANT_NAME=$(az rest --method get --url "https://graph.microsoft.com/v1.0/organization" --query "value[0].displayName" -o tsv)
PRIMARY_DOMAIN=$(az rest --method get --url "https://graph.microsoft.com/v1.0/domains" --query "value[?isDefault].id" -o tsv)
echo "----------------------------------------"
echo "🏢 Tenant Name:   $TENANT_NAME"
echo "🆔 Tenant ID:     $TENANT_ID"
echo "🌐 Primary Domain: $PRIMARY_DOMAIN"
echo "----------------------------------------"
