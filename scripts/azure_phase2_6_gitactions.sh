#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "========================================================"
echo " Starting Phase 2.6: Triggering GitHub Actions Pipeline"
echo "========================================================"

# Add the workflow files, the safe example env, the scripts, AND the Docker files
git add -A

# Commit the changes
git commit -m "Add GitHub Actions CI/CD pipeline and Docker configuration"

# Push to the main branch to trigger the automated build
git push origin main

echo "========================================================"
echo " Code pushed!"
echo " Go check the Actions tab in your GitHub repository."
echo "========================================================"
