#!/bin/bash
# CI-friendly tenant check. No interactive login. Exit 0 if in DaiBai tenant, 1 otherwise.
# Usage: CI=1 ./scripts/entra/00_verify_tenant.sh
#        ./scripts/cli.sh entra verify
DAIBAI_TENANT_ID="${DAIBAI_TENANT_ID:-e12adb01-a6b3-47bb-86c0-d662dacb3675}"

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
