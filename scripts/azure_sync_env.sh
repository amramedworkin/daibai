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
echo "============================================================"

KEYS_TO_SYNC=(
    "DB_RUNTIME_HOST"
    "DB_RUNTIME_USER"
    "DB_RUNTIME_PASSWORD"
    "DB_RUNTIME_PORT"
    "AZURE_OPENAI_ENDPOINT"
    "AZURE_OPENAI_DEPLOYMENT"
)

echo "🔍 Fetching current live state from Azure..."
CURRENT_VARS_JSON=$(az containerapp show \
    --name "$CONTAINER_APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --query "properties.template.containers[0].env" \
    -o json)

NEEDS_UPDATE=false
UPDATE_ARGS=()

for KEY in "${KEYS_TO_SYNC[@]}"; do
    # Get local value
    LOCAL_VAL=$(grep "^${KEY}=" "$ENV_FILE" 2>/dev/null | cut -d '=' -f2- | tr -d '"' | tr -d "'" | xargs || true)

    if [[ -n "$LOCAL_VAL" ]]; then
        # Parse remote value using Python (no jq dependency)
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

        # Build arg for update (quoted to handle values with spaces)
        UPDATE_ARGS+=("${KEY}=${LOCAL_VAL}")
    fi
done

if [[ "$NEEDS_UPDATE" == "false" ]]; then
    echo ""
    echo "🎉 Success: Azure environment variables are already up to date. No container restart required."
    exit 0
fi

# Merge: --set-env-vars REPLACES all vars, so we must preserve existing (COSMOS_ENDPOINT, REDIS_URL, etc.)
# Build full env = current Azure state + our overrides
echo ""
echo "🚀 Pushing configuration updates to Azure (this will trigger a new container revision)..."
OVERRIDES_FILE=$(mktemp)
trap 'rm -f "$OVERRIDES_FILE"' EXIT
for arg in "${UPDATE_ARGS[@]}"; do
    echo "$arg"
done > "$OVERRIDES_FILE"

# Python: merge current Azure env with overrides, output KEY=value per line
# Preserves vars with secretRef using secretref:name format for az CLI
FULL_SET_STR=$(echo "$CURRENT_VARS_JSON" | python3 "$SCRIPT_DIR/azure_merge_env.py" "$OVERRIDES_FILE")

# Parse output into array (each line is KEY='value')
mapfile -t FULL_SET_ENV < <(echo "$FULL_SET_STR")
az containerapp update \
    --name "$CONTAINER_APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --set-env-vars "${FULL_SET_ENV[@]}" \
    --output none

echo "⏳ Update command finished. Verifying deployment..."

# Verify (with retries: Azure may return new revision state after a short delay)
VERIFIED=false
for attempt in 1 2 3 4 5; do
    if [[ $attempt -gt 1 ]]; then sleep 3; fi
    echo "  Verifying (attempt $attempt/5)..."
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
        break
    fi
done

if [[ "$VERIFIED" == "true" ]]; then
    echo "============================================================"
    echo "🎉 Verification Complete: All variables successfully applied!"
else
    echo "============================================================"
    echo "⚠️  Verification Failed: Azure state does not match desired state."
    exit 1
fi
