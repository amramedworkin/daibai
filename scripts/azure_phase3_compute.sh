#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/infra.env"

echo "========================================================"
echo " Starting Phase 3: Serverless Compute & Security"
echo "========================================================"

# 1. Get Log Analytics ID
echo "[1/6] Fetching Log Analytics Workspace ID..."
WORKSPACE_ID=$(az monitor log-analytics workspace show --resource-group "$AZURE_RG_NAME" --workspace-name "$LOG_WORKSPACE_NAME" --query customerId -o tsv)
WORKSPACE_KEY=$(az monitor log-analytics workspace get-shared-keys --resource-group "$AZURE_RG_NAME" --workspace-name "$LOG_WORKSPACE_NAME" --query primarySharedKey -o tsv)

# 2. Container Apps Environment
echo "[2/6] Checking Container Apps Environment ($ACA_ENV_NAME)..."
if az containerapp env show --name "$ACA_ENV_NAME" --resource-group "$AZURE_RG_NAME" -o none 2>/dev/null; then
    echo "  -> Environment already exists."
else
    echo "  -> Creating Environment..."
    az containerapp env create --name "$ACA_ENV_NAME" --resource-group "$AZURE_RG_NAME" \
        --location "$AZURE_LOCATION" \
        --logs-workspace-id "$WORKSPACE_ID" \
        --logs-workspace-key "$WORKSPACE_KEY" -o none
fi

# 3. Deploy Internal Redis Sidecar
echo "[3/6] Checking Internal Redis Container App..."
if az containerapp show --name "daibai-redis" --resource-group "$AZURE_RG_NAME" -o none 2>/dev/null; then
    echo "  -> Redis app already exists."
else
    echo "  -> Deploying Redis Stack Server (Internal only)..."
    az containerapp create \
        --name "daibai-redis" \
        --resource-group "$AZURE_RG_NAME" \
        --environment "$ACA_ENV_NAME" \
        --image redis/redis-stack-server:latest \
        --target-port 6379 \
        --ingress internal \
        --cpu 0.5 --memory 1.0Gi -o none
fi

# Fetch Redis Internal FQDN
REDIS_FQDN=$(az containerapp show --name "daibai-redis" --resource-group "$AZURE_RG_NAME" --query properties.configuration.ingress.fqdn -o tsv)
REDIS_URL="redis://$REDIS_FQDN:6379"
echo "  -> Internal Redis URL: $REDIS_URL"

# 4. Deploy DaiBai App (Initial Hello World to establish Identity)
echo "[4/6] Checking DaiBai API Container App..."
if az containerapp show --name "daibai-api" --resource-group "$AZURE_RG_NAME" -o none 2>/dev/null; then
    echo "  -> DaiBai API app already exists."
else
    echo "  -> Deploying placeholder app to establish Managed Identity..."
    az containerapp create \
        --name "daibai-api" \
        --resource-group "$AZURE_RG_NAME" \
        --environment "$ACA_ENV_NAME" \
        --image mcr.microsoft.com/k8se/quickstart:latest \
        --target-port 8000 \
        --ingress external \
        --system-assigned \
        --cpu 1.0 --memory 2.0Gi -o none
fi

# Fetch Principal ID of the App
PRINCIPAL_ID=$(az containerapp show --name "daibai-api" --resource-group "$AZURE_RG_NAME" --query identity.principalId -o tsv)
echo "  -> DaiBai API Principal ID: $PRINCIPAL_ID"

# 5. Wire up RBAC Permissions
echo "[5/6] Assigning Secretless RBAC Permissions..."

# 5a. ACR Pull
ACR_ID=$(az acr show --name "$ACR_NAME" --resource-group "$AZURE_RG_NAME" --query id -o tsv)
echo "  -> Granting AcrPull on Container Registry..."
az role assignment create --assignee "$PRINCIPAL_ID" --role AcrPull --scope "$ACR_ID" -o none 2>/dev/null || echo "     (Role likely already assigned)"

# 5b. Key Vault Secrets User
KV_ID=$(az keyvault show --name "$KV_NAME" --resource-group "$AZURE_RG_NAME" --query id -o tsv)
echo "  -> Granting Key Vault Secrets User..."
az role assignment create --assignee "$PRINCIPAL_ID" --role "Key Vault Secrets User" --scope "$KV_ID" -o none 2>/dev/null || echo "     (Role likely already assigned)"

# 5c. Cosmos DB Built-in Data Contributor (Role ID: 00000000-0000-0000-0000-000000000002)
echo "  -> Granting Cosmos DB Data Contributor..."
az cosmosdb sql role assignment create \
    --account-name "$COSMOS_ACCOUNT_NAME" \
    --resource-group "$AZURE_RG_NAME" \
    --scope "/" \
    --principal-id "$PRINCIPAL_ID" \
    --role-definition-id "00000000-0000-0000-0000-000000000002" -o none 2>/dev/null || echo "     (Role likely already assigned)"

# 6. Inject the real DaiBai Image & Environment Variables
echo "[6/6] Updating Container App with DaiBai Image and Configuration..."

# Tell Container App to authenticate to ACR using its System Identity
az containerapp registry set \
    --name "daibai-api" \
    --resource-group "$AZURE_RG_NAME" \
    --server "${ACR_NAME}.azurecr.io" \
    --identity system -o none

# Update the container with the real image and inject the secure environment variables
az containerapp update \
    --name "daibai-api" \
    --resource-group "$AZURE_RG_NAME" \
    --image "${ACR_NAME}.azurecr.io/daibai-api:latest" \
    --set-env-vars \
        "COSMOS_ENDPOINT=https://${COSMOS_ACCOUNT_NAME}.documents.azure.com:443/" \
        "COSMOS_DATABASE=daibai-metadata" \
        "KEY_VAULT_URL=https://${KV_NAME}.vault.azure.net/" \
        "REDIS_URL=$REDIS_URL" \
        "ENVIRONMENT=production" \
    -o none

DAIBAI_URL=$(az containerapp show --name "daibai-api" --resource-group "$AZURE_RG_NAME" --query properties.configuration.ingress.fqdn -o tsv)

echo "========================================================"
echo " Phase 3 Complete! Your application is LIVE."
echo " URL: https://$DAIBAI_URL"
echo "========================================================"
