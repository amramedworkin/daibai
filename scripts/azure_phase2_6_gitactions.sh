#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "========================================================"
echo " Phase 2.6: Git Commit & Push (Trigger GitHub Actions)"
echo "========================================================"

# Add the workflow, the scripts, and the safe example file
git add -A

# Commit the changes
git commit -m "Add GitHub Actions CI/CD pipeline"

# Push to trigger the workflow
git push origin main

echo "========================================================"
echo " Push complete! Check the Actions tab on GitHub."
echo "========================================================"
