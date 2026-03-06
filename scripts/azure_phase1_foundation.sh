#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/infra.env"

echo "========================================================"
echo " Starting Phase 1: Production Foundation Deployment"
echo " Resource Group: $AZURE_RG_NAME ($AZURE_LOCATION)"
echo "========================================================"

# 1. Resource Group
echo "[1/5] Checking Resource Group..."
if [[ "$(az group exists --name "$AZURE_RG_NAME" 2>/dev/null)" == "true" ]]; then
    echo "  -> Resource group already exists. Skipping."
else
    echo "  -> Creating resource group..."
    az group create --name "$AZURE_RG_NAME" --location "$AZURE_LOCATION" -o none
fi

# 2. Log Analytics Workspace
echo "[2/5] Checking Log Analytics Workspace..."
if az monitor log-analytics workspace show --resource-group "$AZURE_RG_NAME" --workspace-name "$LOG_WORKSPACE_NAME" -o none 2>/dev/null; then
    echo "  -> Workspace already exists. Skipping."
else
    echo "  -> Creating workspace..."
    az monitor log-analytics workspace create --resource-group "$AZURE_RG_NAME" --workspace-name "$LOG_WORKSPACE_NAME" --location "$AZURE_LOCATION" -o none
fi

# 3. Cosmos DB Account
echo "[3/5] Checking Cosmos DB Account (Serverless)..."
if az cosmosdb show --name "$COSMOS_ACCOUNT_NAME" --resource-group "$AZURE_RG_NAME" -o none 2>/dev/null; then
    echo "  -> Cosmos DB account already exists. Skipping."
else
    echo "  -> Creating Cosmos DB account (this takes several minutes)..."
    az cosmosdb create --name "$COSMOS_ACCOUNT_NAME" --resource-group "$AZURE_RG_NAME" --capabilities EnableServerless -o none
fi

# Wait for Cosmos DB account to finish provisioning before creating databases
echo "  -> Waiting for Cosmos DB account to be ready..."
max_wait=900
elapsed=0
state=""
while [[ $elapsed -lt $max_wait ]]; do
    state=$(az cosmosdb show --name "$COSMOS_ACCOUNT_NAME" --resource-group "$AZURE_RG_NAME" --query "provisioningState" -o tsv 2>/dev/null || echo "Unknown")
    if [[ "$state" == "Succeeded" ]]; then
        echo "  -> Cosmos DB account ready."
        break
    fi
    echo "  -> Provisioning... (${elapsed}s elapsed, state=${state:-Unknown})"
    sleep 30
    elapsed=$((elapsed + 30))
done
if [[ "${state:-}" != "Succeeded" ]]; then
    echo "  -> WARNING: Cosmos DB account may still be provisioning. Re-run the script in a few minutes."
fi

# 4. Cosmos DB Databases & Containers
echo "[4/5] Checking Cosmos DB Containers..."
# Database
if az cosmosdb sql database show --account-name "$COSMOS_ACCOUNT_NAME" --resource-group "$AZURE_RG_NAME" --name "daibai-metadata" -o none 2>/dev/null; then
    echo "  -> Database 'daibai-metadata' already exists."
else
    echo "  -> Creating database 'daibai-metadata'..."
    az cosmosdb sql database create --account-name "$COSMOS_ACCOUNT_NAME" --resource-group "$AZURE_RG_NAME" --name "daibai-metadata" -o none
fi

# Containers
for container in users sessions conversations; do
    if az cosmosdb sql container show --account-name "$COSMOS_ACCOUNT_NAME" --resource-group "$AZURE_RG_NAME" --database-name "daibai-metadata" --name "$container" -o none 2>/dev/null; then
        echo "  -> Container '$container' already exists."
    else
        echo "  -> Creating container '$container'..."
        az cosmosdb sql container create --account-name "$COSMOS_ACCOUNT_NAME" --resource-group "$AZURE_RG_NAME" --database-name "daibai-metadata" --name "$container" --partition-key-path "/uid" -o none
    fi
done

# 5. Key Vault
echo "[5/5] Checking Key Vault..."
if az keyvault show --name "$KV_NAME" --resource-group "$AZURE_RG_NAME" -o none 2>/dev/null; then
    echo "  -> Key Vault already exists. Skipping."
else
    echo "  -> Creating Key Vault with RBAC authorization..."
    az keyvault create --name "$KV_NAME" --resource-group "$AZURE_RG_NAME" --location "$AZURE_LOCATION" --enable-rbac-authorization true -o none
fi

echo "========================================================"
echo " Phase 1 Complete!"
echo "========================================================"
