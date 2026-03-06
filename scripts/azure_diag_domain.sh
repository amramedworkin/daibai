#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/infra.env"

echo "========================================================"
echo " DIAGNOSTICS: Custom Domain & SSL Certificate State"
echo " App: daibai-api | RG: $AZURE_RG_NAME"
echo "========================================================"

az containerapp show \
  --name "daibai-api" \
  --resource-group "$AZURE_RG_NAME" \
  --query "properties.configuration.ingress.customDomains" \
  -o json
