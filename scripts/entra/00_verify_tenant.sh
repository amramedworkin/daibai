#!/bin/bash
# CI-friendly tenant check. No interactive login. Exit 0 if in DaiBai tenant, 1 otherwise.
# Usage: CI=1 ./scripts/entra/00_verify_tenant.sh
#        ./scripts/cli.sh entra verify
DAIBAI_TENANT_ID="${DAIBAI_TENANT_ID:-${AUTH_TENANT_ID:-e12adb01-a6b3-47bb-86c0-d662dacb3675}}"

# Prefer app-only verification using client credentials if available
ENV_FILE="$(dirname "$0")/../../.env"
if [ -f "$ENV_FILE" ]; then
    set -a && source "$ENV_FILE" >/dev/null 2>&1 || true
    set +a
fi
CLIENT_ID="${AUTH_CLIENT_ID:-}"
CLIENT_SECRET="${AUTH_CLIENT_SECRET:-}"

if [[ -n "$CLIENT_ID" && -n "$CLIENT_SECRET" ]]; then
    RESP=$(curl -s -X POST "https://login.microsoftonline.com/${DAIBAI_TENANT_ID}/oauth2/v2.0/token" \
      -H "Content-Type: application/x-www-form-urlencoded" \
      -d "client_id=${CLIENT_ID}&scope=https://graph.microsoft.com/.default&client_secret=${CLIENT_SECRET}&grant_type=client_credentials")
    TOKEN=$(echo "$RESP" | python3 -c "import sys,json; j=json.load(sys.stdin); print(j.get('access_token',''))" 2>/dev/null || true)
    if [[ -z "$TOKEN" ]]; then
        echo "❌ Failed to obtain token with app credentials. Response snippet:"
        echo "$RESP" | head -c 400
        echo ""
        echo "Falling back to CLI account check."
    else
        # Safely decode tid from token via python reading token from stdin
        TID=$(printf "%s" "$TOKEN" | python3 - <<'PY' 2>/dev/null
import sys, json, base64
try:
    token = sys.stdin.read().strip()
    parts = token.split('.')
    if len(parts) < 2:
        print('')
    else:
        payload = parts[1]
        payload += '=' * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload.encode()))
        print(data.get('tid',''))
except Exception:
    print('')
PY
)
        if [[ -n "$TID" && "$TID" == "$DAIBAI_TENANT_ID" ]]; then
            echo "✅ Tenant OK (app-only): $TID (DaiBai)"
            exit 0
        else
            echo "❌ App token tenant (${TID:-<none>}) does not match expected ($DAIBAI_TENANT_ID)"
            exit 1
        fi
    fi
fi

# Fallback to az CLI check (non-interactive)
CURRENT_TENANT=$(az account show --query tenantId -o tsv 2>/dev/null)
if [[ -z "$CURRENT_TENANT" ]]; then
    echo "❌ Not logged in. Run: az login"
    exit 1
fi
if [[ "$CURRENT_TENANT" == "$DAIBAI_TENANT_ID" ]]; then
    echo "✅ Tenant OK: $CURRENT_TENANT (DaiBai)"
    exit 0
fi
echo "❌ Wrong tenant: $CURRENT_TENANT (expected: $DAIBAI_TENANT_ID)"
echo "   Fix: az login --tenant $DAIBAI_TENANT_ID"
exit 1
