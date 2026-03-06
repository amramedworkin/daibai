#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/infra.env"

echo "========================================================"
echo " Starting Phase 2: Container Registry Deployment"
echo " Resource Group: $AZURE_RG_NAME ($AZURE_LOCATION)"
echo "========================================================"

# 1. Container Registry
echo "[1/2] Checking Azure Container Registry ($ACR_NAME)..."
if az acr show --name "$ACR_NAME" --resource-group "$AZURE_RG_NAME" -o none 2>/dev/null; then
    echo "  -> ACR already exists. Skipping."
else
    echo "  -> Creating ACR (Basic tier)..."
    az acr create --name "$ACR_NAME" --resource-group "$AZURE_RG_NAME" --sku Basic --admin-enabled false -o none
fi

# 2. Service Principal for GitHub Actions
echo "[2/2] Generating Service Principal for GitHub Actions..."
# Fetch the Azure Resource ID of the Container Registry
ACR_ID=$(az acr show --name "$ACR_NAME" --resource-group "$AZURE_RG_NAME" --query id -o tsv)
SP_NAME="sp-github-actions-$UNIQUE_SUFFIX"

echo "  -> Creating/Updating Service Principal ($SP_NAME) with AcrPush role..."
# Note: az ad sp create-for-rbac resets the password if it already exists, ensuring fresh credentials
SP_CREDENTIALS=$(az ad sp create-for-rbac --name "$SP_NAME" --scopes "$ACR_ID" --role AcrPush --json-auth 2>/dev/null || az ad sp create-for-rbac --name "$SP_NAME" --scopes "$ACR_ID" --role AcrPush --sdk-auth)

# Securely save the credentials to a local file
CRED_FILE="$SCRIPT_DIR/.sp_credentials.json"
echo "$SP_CREDENTIALS" > "$CRED_FILE"
chmod 600 "$CRED_FILE"

echo "  -> Granting SP Contributor access to Resource Group ($AZURE_RG_NAME) for CD pipeline..."
RG_ID=$(az group show --name "$AZURE_RG_NAME" --query id -o tsv)

# Give Azure AD a few seconds to propagate the newly created Service Principal globally
sleep 5
APP_ID=$(az ad sp list --display-name "$SP_NAME" --query "[0].appId" -o tsv)

az role assignment create --assignee "$APP_ID" --role "Contributor" --scope "$RG_ID" -o none 2>/dev/null || echo "     (Role likely already assigned)"

echo "========================================================"
echo " Phase 2 Complete!"
echo "========================================================"
echo "IMPORTANT NEXT STEPS FOR GITHUB ACTIONS:"
echo "The Service Principal credentials have been securely saved to:"
echo "  -> $CRED_FILE"
echo ""
echo "This file is ignored by git and locked to your user account (chmod 600)."
echo "The upcoming GitHub Actions script will read from this file automatically."
echo ""
echo "Registry Login Server: $ACR_NAME.azurecr.io"
echo "========================================================"
