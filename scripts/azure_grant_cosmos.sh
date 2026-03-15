#!/bin/bash
# Grants Cosmos DB Data Contributor roles to the local user or Azure Container App
# Dynamically resolves the active Cosmos DB from .env and the principal IDs from Azure.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$PROJECT_ROOT/.env"
INFRA_ENV_FILE="$SCRIPT_DIR/infra.env"

TARGET=$1

if [[ "$TARGET" != "local" && "$TARGET" != "app" ]]; then
    echo "Usage: $0 [local|app]"
    exit 1
fi

# Load Infrastructure variables
if [[ -f "$INFRA_ENV_FILE" ]]; then
    source "$INFRA_ENV_FILE"
fi
RESOURCE_GROUP=${RESOURCE_GROUP:-"rg-daibai-prod"}
CONTAINER_APP_NAME=${CONTAINER_APP_NAME:-"daibai-api"}

# Extract Cosmos Account Name from .env
if [[ ! -f "$ENV_FILE" ]]; then
    echo "❌ Missing .env file in project root."
    exit 1
fi

COSMOS_ENDPOINT=$(grep "^COSMOS_ENDPOINT=" "$ENV_FILE" | cut -d '=' -f2- | tr -d '"' | tr -d "'" | xargs)
if [[ -z "$COSMOS_ENDPOINT" ]]; then
    echo "❌ COSMOS_ENDPOINT not found in .env"
    exit 1
fi

# Parse account name from https://<account-name>.documents.azure.com
ACCOUNT_NAME=$(echo "$COSMOS_ENDPOINT" | sed -e 's|^https://||' -e 's|\.documents\.azure\.com.*||' -e 's|:443/||' -e 's|/$||')

if [[ -z "$ACCOUNT_NAME" ]]; then
    echo "❌ Could not parse Cosmos DB account name from endpoint: $COSMOS_ENDPOINT"
    exit 1
fi

# Cosmos DB Built-in Data Contributor Role ID
ROLE_ID="00000000-0000-0000-0000-000000000002"

if [[ "$TARGET" == "local" ]]; then
    echo "🔍 Fetching signed-in Azure user ID..."
    PRINCIPAL_ID=$(az ad signed-in-user show --query id -o tsv)
    if [[ -z "$PRINCIPAL_ID" ]]; then
        echo "❌ Could not determine signed-in user. Are you logged in with 'az login'?"
        exit 1
    fi
    echo "👤 Granting Data Contributor to Local User ($PRINCIPAL_ID)..."
    echo "   Target DB: $ACCOUNT_NAME"
    
    az cosmosdb sql role assignment create \
        --account-name "$ACCOUNT_NAME" \
        --resource-group "$RESOURCE_GROUP" \
        --scope "/" \
        --principal-id "$PRINCIPAL_ID" \
        --role-definition-id "$ROLE_ID" 2>/dev/null || echo "⚠️  Role may already exist or requires elevated subscription permissions to assign."
        
    echo "✅ Local access granted! (Note: Azure RBAC can take 1-2 minutes to propagate)"

elif [[ "$TARGET" == "app" ]]; then
    echo "🔍 Fetching Managed Identity for Container App ($CONTAINER_APP_NAME)..."
    PRINCIPAL_ID=$(az containerapp show --name "$CONTAINER_APP_NAME" --resource-group "$RESOURCE_GROUP" --query identity.principalId -o tsv)
    
    if [[ -z "$PRINCIPAL_ID" ]]; then
        echo "❌ Could not find Managed Identity for $CONTAINER_APP_NAME. Is System Assigned Identity enabled in Azure?"
        exit 1
    fi
    
    echo "🤖 Granting Data Contributor to Container App ($PRINCIPAL_ID)..."
    echo "   Target DB: $ACCOUNT_NAME"
    
    az cosmosdb sql role assignment create \
        --account-name "$ACCOUNT_NAME" \
        --resource-group "$RESOURCE_GROUP" \
        --scope "/" \
        --principal-id "$PRINCIPAL_ID" \
        --role-definition-id "$ROLE_ID" 2>/dev/null || echo "⚠️  Role may already exist or requires elevated subscription permissions to assign."
        
    echo "✅ Container App access granted! (Note: Azure RBAC can take 1-2 minutes to propagate)"
fi
