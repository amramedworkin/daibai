#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
source "$SCRIPT_DIR/infra.env"

echo "========================================================"
echo " Starting Phase 2.5: GitHub Actions Integration"
echo "========================================================"

CRED_FILE="$SCRIPT_DIR/.sp_credentials.json"
if [ ! -f "$CRED_FILE" ]; then
    echo "[ERROR] $CRED_FILE not found. Please run Phase 2 first."
    exit 1
fi

# 1. Update local .env file
echo "[1/3] Updating .env with GitHub configuration..."
python3 "$SCRIPT_DIR/update_env.py" "$PROJECT_DIR/.env" \
    "GITHUB_USERNAME=amramedworkin" \
    "GITHUB_PASSWORD=" \
    "GITHUB_REPO=daibai" \
    "GITHUB_REPO_VISIBILITY=private" \
    "GITHUB_URL=https://github.com/amramedworkin/daibai.git"

# 2. Generate the GitHub Actions YAML
echo "[2/3] Generating .github/workflows/azure-deploy.yml..."
mkdir -p "$PROJECT_DIR/.github/workflows"
YAML_FILE="$PROJECT_DIR/.github/workflows/azure-deploy.yml"

# Note: We are dynamically injecting the ACR_NAME from infra.env here
cat > "$YAML_FILE" <<EOF
name: Build and Push to Azure ACR

on:
  push:
    branches: [ "main" ]
  workflow_dispatch:

env:
  ACR_NAME: ${ACR_NAME}
  ACR_LOGIN_SERVER: ${ACR_NAME}.azurecr.io
  IMAGE_NAME: daibai-api

jobs:
  build-and-push:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Azure Login
        uses: azure/login@v1
        with:
          creds: \${{ secrets.AZURE_CREDENTIALS }}

      - name: Log in to Azure Container Registry
        run: |
          az acr login --name \${{ env.ACR_NAME }}

      - name: Build and push Docker image
        uses: docker/build-push-action@v5
        with:
          context: .
          push: true
          tags: |
            \${{ env.ACR_LOGIN_SERVER }}/\${{ env.IMAGE_NAME }}:\${{ github.sha }}
            \${{ env.ACR_LOGIN_SERVER }}/\${{ env.IMAGE_NAME }}:latest
EOF

# 3. Handle GitHub Secrets
echo "[3/3] Checking GitHub CLI for automated secret upload..."
if command -v gh &> /dev/null; then
    echo "  -> GitHub CLI (gh) detected. Attempting to upload secret automatically..."
    # Suppress error if not logged in to handle gracefully
    if gh secret set AZURE_CREDENTIALS < "$CRED_FILE" --repo amramedworkin/daibai 2>/dev/null; then
        echo "  -> Secret AZURE_CREDENTIALS uploaded successfully to GitHub!"
    else
        echo "  [!] Failed to set secret. You may need to run 'gh auth login' first."
        echo "  -> Please set it manually using the instructions below."
    fi
else
    echo "  [!] GitHub CLI (gh) not found on this system."
    echo "  -> MANUAL STEP REQUIRED:"
    echo "     1. Go to https://github.com/amramedworkin/daibai/settings/secrets/actions"
    echo "     2. Click 'New repository secret'"
    echo "     3. Name: AZURE_CREDENTIALS"
    echo "     4. Value: Paste the exact contents of scripts/.sp_credentials.json"
fi

echo "========================================================"
echo " Phase 2.5 Complete!"
echo " Next Step: Commit and push the .github directory to trigger the build."
echo "========================================================"
