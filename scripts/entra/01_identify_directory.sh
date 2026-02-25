#!/bin/bash
# --- AZURE CONTEXT ISOLATION ---
# Load .env to get AUTH_TENANT_ID dynamically (Identity Plane)
ENV_FILE="$(dirname "$0")/../../.env"
if [ -f "$ENV_FILE" ]; then
    # shellcheck disable=SC1090
    set -a && source "$ENV_FILE" >/dev/null 2>&1 || true
    set +a
fi
ENTRA_TENANT="${AUTH_TENANT_ID:-e12adb01-a6b3-47bb-86c0-d662dacb3675}"
# Save the original context (Subscription ID)
ORIGINAL_SUB=$(az account show --query id -o tsv 2>/dev/null)
# Create a safety trap to ALWAYS restore the original context on exit (even on Ctrl+C or crash)
if [ -n "$ORIGINAL_SUB" ]; then
    trap 'echo -e "\n🔄 Restoring original Azure context..."; az account set --subscription "$ORIGINAL_SUB" > /dev/null 2>&1' EXIT
fi
# Switch to the Identity Plane tenant silently
echo "🔀 Temporarily switching context to Identity Plane (Tenant: $ENTRA_TENANT)..."
az login --tenant "$ENTRA_TENANT" --allow-no-subscriptions > /dev/null 2>&1
# CI=1 or --ci: skip and report only (for test harnesses)
CI_MODE=false
[[ "$1" == "--ci" || "$1" == "-n" || -n "${CI:-}" || -n "${NON_INTERACTIVE:-}" ]] && CI_MODE=true
if $CI_MODE; then
    CURRENT_TENANT=$(az account show --query tenantId -o tsv 2>/dev/null)
    if [[ "$CURRENT_TENANT" != "$ENTRA_TENANT" ]]; then
        echo "⚠️  CI mode: Current tenant ($CURRENT_TENANT) ≠ DaiBai tenant ($ENTRA_TENANT)"
        echo "    Run interactively: ./scripts/entra/01_identify_directory.sh"
        exit 1
    fi
fi

echo "🔍 Identifying DaiBai Microsoft Entra ID Directory..."
TENANT_ID=$(az account show --query tenantId -o tsv)
TENANT_NAME=$(az rest --method get --url "https://graph.microsoft.com/v1.0/organization" --query "value[0].displayName" -o tsv)
PRIMARY_DOMAIN=$(az rest --method get --url "https://graph.microsoft.com/v1.0/domains" --query "value[?isDefault].id" -o tsv)
# Verify the directory looks like DaiBai (name or domain contains 'daibai') — fail if not
NAME_CHECK="$(echo \"${TENANT_NAME} ${PRIMARY_DOMAIN}\" | tr '[:upper:]' '[:lower:]')"
if [[ \"$NAME_CHECK\" != *\"daibai\"* ]]; then
    echo \"❌ Tenant does not appear to be DaiBai. Detected: ${TENANT_NAME} / ${PRIMARY_DOMAIN}\"
    echo \"    Aborting to avoid operating on the wrong directory.\"
    exit 1
fi
echo "----------------------------------------"
echo "🏢 Tenant Name:   $TENANT_NAME"
echo "🆔 Tenant ID:     $TENANT_ID"
echo "🌐 Primary Domain: $PRIMARY_DOMAIN"
echo "----------------------------------------"
