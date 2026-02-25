#!/bin/bash
# --- AZURE CONTEXT ISOLATION ---
# Load .env to get AUTH_TENANT_ID dynamically (Identity Plane)
ENV_FILE="$(dirname "$0")/../../.env"
if [ -z "${ENTRATEST_FAKE:-}" ] && [ -f "$ENV_FILE" ]; then
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
# Try app-only client credentials first (no browser).
CLIENT_ID="${AUTH_CLIENT_ID:-}"
CLIENT_SECRET="${AUTH_CLIENT_SECRET:-}"
TOKEN=""
if [[ -n "$CLIENT_ID" && -n "$CLIENT_SECRET" ]]; then
    TOKEN=$(curl -s -X POST "https://login.microsoftonline.com/${ENTRA_TENANT}/oauth2/v2.0/token" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        -d "client_id=${CLIENT_ID}&scope=https://graph.microsoft.com/.default&client_secret=${CLIENT_SECRET}&grant_type=client_credentials" \
        | python3 -c "import sys,json; j=json.load(sys.stdin); print(j.get('access_token',''))")
fi

# CI=1 or --ci: skip interactive login attempts (testing harness)
CI_MODE=false
[[ "$1" == "--ci" || "$1" == "-n" || -n "${CI:-}" || -n "${NON_INTERACTIVE:-}" ]] && CI_MODE=true

if [[ -n "$TOKEN" ]]; then
    # Use app-only token to fetch org info
    TENANT_NAME=$(curl -s -H "Authorization: Bearer ${TOKEN}" "https://graph.microsoft.com/v1.0/organization" \
        | python3 -c "import sys,json; j=json.load(sys.stdin); print((j.get('value') or [{}])[0].get('displayName',''))")
    PRIMARY_DOMAIN=$(curl -s -H "Authorization: Bearer ${TOKEN}" "https://graph.microsoft.com/v1.0/domains" \
        | python3 -c "import sys,json; j=json.load(sys.stdin); print(next((d.get('id') for d in j.get('value',[]) if d.get('isDefault')), ''))")
    TENANT_ID="${ENTRA_TENANT}"
else
    # No app-only token available. If CI mode, do not attempt interactive login.
    if $CI_MODE; then
        CURRENT_TENANT=$(az account show --query tenantId -o tsv 2>/dev/null)
        if [[ "$CURRENT_TENANT" != "$ENTRA_TENANT" ]]; then
            echo "⚠️  CI mode: Current tenant ($CURRENT_TENANT) ≠ DaiBai tenant ($ENTRA_TENANT)"
            echo "    Provide app credentials (AUTH_CLIENT_ID/AUTH_CLIENT_SECRET) in .env for non-interactive runs."
            exit 1
        fi
        TENANT_ID=$(az account show --query tenantId -o tsv)
        TENANT_NAME=$(az rest --method get --url "https://graph.microsoft.com/v1.0/organization" --query "value[0].displayName" -o tsv)
        PRIMARY_DOMAIN=$(az rest --method get --url "https://graph.microsoft.com/v1.0/domains" --query "value[?isDefault].id" -o tsv)
    else
        # Interactive fallback: try az login (browser or device code) then use az rest
        echo "🔀 No app credentials found; falling back to interactive az login (may open a browser)..."
        if [[ -t 0 ]]; then
            az login --tenant "$ENTRA_TENANT" --allow-no-subscriptions || true
        else
            az login --tenant "$ENTRA_TENANT" --use-device-code || true
        fi
        TENANT_ID=$(az account show --query tenantId -o tsv)
        TENANT_NAME=$(az rest --method get --url "https://graph.microsoft.com/v1.0/organization" --query "value[0].displayName" -o tsv)
        PRIMARY_DOMAIN=$(az rest --method get --url "https://graph.microsoft.com/v1.0/domains" --query "value[?isDefault].id" -o tsv)
    fi
fi
# Verify the directory looks like DaiBai (name or domain contains 'daibai') — fail if not
NAME_CHECK="$(echo "${TENANT_NAME} ${PRIMARY_DOMAIN}" | tr '[:upper:]' '[:lower:]')"
if [[ "$NAME_CHECK" != *"daibai"* ]]; then
    echo "❌ Tenant does not appear to be DaiBai. Detected: ${TENANT_NAME} / ${PRIMARY_DOMAIN}"
    echo "    Aborting to avoid operating on the wrong directory."
    exit 1
fi
echo "----------------------------------------"
echo "🏢 Tenant Name:   $TENANT_NAME"
echo "🆔 Tenant ID:     $TENANT_ID"
echo "🌐 Primary Domain: $PRIMARY_DOMAIN"
echo "----------------------------------------"
