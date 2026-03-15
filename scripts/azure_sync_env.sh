#!/bin/bash
# Idempotent sync of local .env variables to Azure Container App

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$PROJECT_ROOT/.env"
INFRA_ENV_FILE="$SCRIPT_DIR/infra.env"

# Load Infrastructure variables
if [[ -f "$INFRA_ENV_FILE" ]]; then
    source "$INFRA_ENV_FILE"
else
    echo "❌ Missing infra.env file. Cannot determine Azure targets."
    exit 1
fi

RESOURCE_GROUP="${AZURE_RG_NAME:-rg-daibai-prod}"
CONTAINER_APP_NAME="${CONTAINER_APP_NAME:-daibai-api}"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "❌ Missing .env file in project root."
    exit 1
fi

echo "============================================================"
echo " Idempotent Env Sync: $CONTAINER_APP_NAME"
echo " Reading from unified source: .env"
echo "============================================================"

KEYS_TO_SYNC=(
    "DB_RUNTIME_HOST"
    "DB_RUNTIME_USER"
    "DB_RUNTIME_PASSWORD"
    "DB_RUNTIME_PORT"
    "AZURE_OPENAI_ENDPOINT"
    "AZURE_OPENAI_DEPLOYMENT"
    "COSMOS_ENDPOINT"
    "COSMOS_DATABASE"
    "KEY_VAULT_URL"
    "REDIS_URL"
    "ENVIRONMENT"
)

echo "🔍 Fetching current live state from Azure..."
CURRENT_VARS_JSON=$(az containerapp show \
    --name "$CONTAINER_APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --query "properties.template.containers[0].env" \
    -o json)

NEEDS_UPDATE=false
UPDATE_CMD_ARGS=""

for KEY in "${KEYS_TO_SYNC[@]}"; do
    # Get local value
    LOCAL_VAL=$(grep "^${KEY}=" "$ENV_FILE" 2>/dev/null | cut -d '=' -f2- | tr -d '"' | tr -d "'" | xargs || true)

    if [[ -n "$LOCAL_VAL" ]]; then
        # Parse remote value using python
        REMOTE_VAL=$(echo "$CURRENT_VARS_JSON" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for item in data:
        if item.get('name') == '${KEY}':
            print(item.get('value', ''))
            break
    else:
        print('')
except Exception:
    print('')
" 2>/dev/null || echo "")

        if [[ "$LOCAL_VAL" != "$REMOTE_VAL" ]]; then
            echo "🔄 Drift detected for: $KEY"
            NEEDS_UPDATE=true
        else
            echo "✅ In sync: $KEY"
        fi

        UPDATE_CMD_ARGS="${UPDATE_CMD_ARGS} ${KEY}=${LOCAL_VAL}"
    fi
done

if [[ "$NEEDS_UPDATE" == "false" ]]; then
    echo ""
    echo "🎉 Success: Azure environment variables are fully up to date."
    exit 0
fi

echo ""
echo "🚀 Pushing configuration updates to Azure (this will trigger a new container revision)..."
az containerapp update \
    --name "$CONTAINER_APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --set-env-vars $UPDATE_CMD_ARGS \
    --output none

echo "⏳ Update command finished. Verifying deployment..."

# Verify
NEW_VARS_JSON=$(az containerapp show \
    --name "$CONTAINER_APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --query "properties.template.containers[0].env" \
    -o json)

VERIFIED=true
for KEY in "${KEYS_TO_SYNC[@]}"; do
    LOCAL_VAL=$(grep "^${KEY}=" "$ENV_FILE" 2>/dev/null | cut -d '=' -f2- | tr -d '"' | tr -d "'" | xargs || true)
    if [[ -n "$LOCAL_VAL" ]]; then
        REMOTE_VAL=$(echo "$NEW_VARS_JSON" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for item in data:
        if item.get('name') == '${KEY}':
            print(item.get('value', ''))
            break
    else:
        print('')
except Exception:
    print('')
" 2>/dev/null || echo "")

        if [[ "$LOCAL_VAL" != "$REMOTE_VAL" ]]; then
            echo "❌ Verification failed for: $KEY"
            VERIFIED=false
        fi
    fi
done

if [[ "$VERIFIED" == "true" ]]; then
    echo "============================================================"
    echo "🎉 Verification Complete: Variables successfully applied!"
else
    echo "============================================================"
    echo "⚠️  Verification Failed: Azure state does not match .env state."
    exit 1
fi
