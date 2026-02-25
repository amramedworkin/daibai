#!/bin/bash
# ============================================================================
# DaiBai Setup - Install dependencies and initialize environment
# ============================================================================
# Run this after cloning to install pip packages and (optionally) Azure services.
# ============================================================================

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo ""
echo "============================================================================"
echo "  DaiBai Setup"
echo "============================================================================"
echo ""

# 1. Create venv if missing
if [[ ! -d ".venv" ]]; then
    echo "[1/4] Creating virtual environment..."
    python3 -m venv .venv
    echo "      Created .venv"
else
    echo "[1/4] Virtual environment exists (.venv)"
fi

# 2. Install via venv pip (idempotent: pip install -e is safe to run repeatedly)
echo "[2/4] Installing dependencies (gui, dev, cache)..."
"$PROJECT_DIR/.venv/bin/pip" install -e ".[gui,dev,cache]" -q
echo "      Installed: FastAPI, pytest, rich, sentence-transformers, torch, etc."

# 3. .env from example if missing
if [[ -f ".env" ]]; then
    echo "[3/4] .env exists (skipping)"
elif [[ -f ".env.example" ]]; then
    echo "[3/4] Creating .env from .env.example..."
    cp .env.example .env
    echo "      Created .env - edit it to add your API keys and REDIS_URL"
else
    echo "[3/4] .env.example not found (skipping)"
fi

# 4. Azure CLI check (optional)
echo "[4/4] Azure CLI check..."
if command -v az &>/dev/null; then
    echo "      az CLI found. Run 'az login' to authenticate for Redis/Cosmos."
else
    echo "      az CLI not found. Install for Azure: https://docs.microsoft.com/cli/azure/install-azure-cli"
fi

echo ""
echo "============================================================================"
echo "  Setup complete"
echo "============================================================================"
echo ""
echo "Next steps:"
echo "  1. Edit .env with your API keys (GEMINI_API_KEY, etc.) and REDIS_URL"
echo "  2. Optional Azure: az login && ./scripts/cli.sh redis-create"
echo "  3. Run tests: ./test -v -s  (or: .venv/bin/python -m pytest tests/ -v -s)"
echo "  4. Start server: daibai-server"
echo ""
