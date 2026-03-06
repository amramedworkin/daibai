#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/infra.env"

echo "========================================================"
echo " Starting Phase 4: Edge Networking & Domain Migration"
echo " Domain: $CUSTOM_DOMAIN"
echo " Target Resource Group: $AZURE_RG_NAME"
echo "========================================================"

# 1. Get networking details
echo "[1/4] Fetching Container App Networking Details..."
APP_FQDN=$(az containerapp show --name "daibai-api" --resource-group "$AZURE_RG_NAME" --query properties.configuration.ingress.fqdn -o tsv)
STATIC_IP=$(az containerapp env show --name "$ACA_ENV_NAME" --resource-group "$AZURE_RG_NAME" --query properties.staticIp -o tsv)
VERIFICATION_ID=$(az containerapp show --name "daibai-api" --resource-group "$AZURE_RG_NAME" --query properties.customDomainVerificationId -o tsv)

echo "  -> Default FQDN: $APP_FQDN"
echo "  -> Environment IP: $STATIC_IP"
echo "  -> Verification ID: $VERIFICATION_ID"

# 2. Enforce DNS Zone Ownership
echo "[2/4] Enforcing DNS Zone Ownership..."
CURRENT_RG=$(az network dns zone list --query "[?name=='$CUSTOM_DOMAIN'].resourceGroup | [0]" -o tsv)
ZONE_ID=$(az network dns zone list --query "[?name=='$CUSTOM_DOMAIN'].id | [0]" -o tsv)

if [ -z "$CURRENT_RG" ]; then
    echo "  -> DNS Zone not found in subscription. Creating it in $AZURE_RG_NAME..."
    az network dns zone create -g "$AZURE_RG_NAME" -n "$CUSTOM_DOMAIN" -o none
elif [ "$CURRENT_RG" != "$AZURE_RG_NAME" ]; then
    echo "  -> DNS Zone found in wrong group ('$CURRENT_RG')."
    echo "  -> Migrating to '$AZURE_RG_NAME' (This may take a few minutes)..."
    az resource move --destination-group "$AZURE_RG_NAME" --ids "$ZONE_ID"
    echo "  -> Migration complete. Allowing Azure Control Plane to settle..."
    sleep 15
else
    echo "  -> DNS Zone is already securely located in $AZURE_RG_NAME."
fi

# 3. Configure Azure DNS
echo "[3/4] Configuring DNS Records..."

# Ensure record sets exist before adding records (Idempotent creation)
az network dns record-set a create -g "$AZURE_RG_NAME" -z "$CUSTOM_DOMAIN" -n "@" -o none 2>/dev/null || true
az network dns record-set txt create -g "$AZURE_RG_NAME" -z "$CUSTOM_DOMAIN" -n "asuid" -o none 2>/dev/null || true
az network dns record-set cname create -g "$AZURE_RG_NAME" -z "$CUSTOM_DOMAIN" -n "www" -o none 2>/dev/null || true
az network dns record-set txt create -g "$AZURE_RG_NAME" -z "$CUSTOM_DOMAIN" -n "asuid.www" -o none 2>/dev/null || true

# Apex Domain (@)
echo "  -> Applying Apex A and TXT records..."
az network dns record-set a add-record -g "$AZURE_RG_NAME" -z "$CUSTOM_DOMAIN" -n "@" -a "$STATIC_IP" -o none
az network dns record-set txt add-record -g "$AZURE_RG_NAME" -z "$CUSTOM_DOMAIN" -n "asuid" -v "$VERIFICATION_ID" -o none

# WWW Subdomain
echo "  -> Applying WWW CNAME and TXT records..."
az network dns record-set cname set-record -g "$AZURE_RG_NAME" -z "$CUSTOM_DOMAIN" -n "www" -c "$APP_FQDN" -o none
az network dns record-set txt add-record -g "$AZURE_RG_NAME" -z "$CUSTOM_DOMAIN" -n "asuid.www" -v "$VERIFICATION_ID" -o none

# 4. Bind Domains & Generate Managed Certificates
echo "[4/4] Checking existing Container App domain bindings..."
APEX_BOUND=$(az containerapp show --name "daibai-api" --resource-group "$AZURE_RG_NAME" --query "properties.configuration.ingress.customDomains[?name=='$CUSTOM_DOMAIN'].name | [0]" -o tsv)
WWW_BOUND=$(az containerapp show --name "daibai-api" --resource-group "$AZURE_RG_NAME" --query "properties.configuration.ingress.customDomains[?name=='www.$CUSTOM_DOMAIN'].name | [0]" -o tsv)

if [ "$APEX_BOUND" == "$CUSTOM_DOMAIN" ]; then
    echo "  -> Apex domain ($CUSTOM_DOMAIN) is already bound. Skipping."
else
    echo "  -> Waiting 30 seconds for DNS propagation before binding Apex..."
    sleep 30
    echo "  -> Binding $CUSTOM_DOMAIN and generating SSL certificate..."
    az containerapp hostname bind \
        --hostname "$CUSTOM_DOMAIN" \
        --resource-group "$AZURE_RG_NAME" \
        --name "daibai-api" \
        --environment "$ACA_ENV_NAME" \
        --validation-method TXT -o none
fi

if [ "$WWW_BOUND" == "www.$CUSTOM_DOMAIN" ]; then
    echo "  -> WWW subdomain (www.$CUSTOM_DOMAIN) is already bound. Skipping."
else
    echo "  -> Waiting 30 seconds for DNS propagation before binding WWW..."
    sleep 30
    echo "  -> Binding www.$CUSTOM_DOMAIN and generating SSL certificate..."
    az containerapp hostname bind \
        --hostname "www.$CUSTOM_DOMAIN" \
        --resource-group "$AZURE_RG_NAME" \
        --name "daibai-api" \
        --environment "$ACA_ENV_NAME" \
        --validation-method CNAME -o none
fi

echo "========================================================"
echo " Phase 4 Complete! Edge networking is fully configured."
echo " URL: https://$CUSTOM_DOMAIN"
echo "========================================================"
