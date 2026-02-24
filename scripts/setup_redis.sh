#!/bin/bash
# ============================================================================
# Azure Cache for Redis Setup
# ============================================================================
# Registers Microsoft.Cache provider, creates resource group, provisions Redis
# (Basic C0), and writes REDIS_URL to .env. Idempotent: skips components that
# already exist. Each step blocks until complete.
#
# Prerequisites: az login
# Usage: ./scripts/setup_redis.sh
# Override: REDIS_RESOURCE_GROUP, REDIS_NAME, REDIS_LOCATION
# ============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Configurable via env
REDIS_RESOURCE_GROUP="${REDIS_RESOURCE_GROUP:-daibai-rg}"
REDIS_NAME="${REDIS_NAME:-daibai-redis}"
REDIS_LOCATION="${REDIS_LOCATION:-eastus}"

echo ""
echo "============================================================================"
echo "  Azure Cache for Redis Setup"
echo "============================================================================"
echo "  Resource Group: $REDIS_RESOURCE_GROUP"
echo "  Redis Name:     $REDIS_NAME"
echo "  Location:       $REDIS_LOCATION"
echo "============================================================================"
echo ""

# 0. Register Microsoft.Cache provider (blocks until Registered)
echo "[0/4] Registering Microsoft.Cache provider..."
STATE=$(az provider show --namespace Microsoft.Cache --query "registrationState" -o tsv 2>/dev/null || echo "NotRegistered")
if [[ "$STATE" != "Registered" ]]; then
    echo "  Registering (may take 1-2 minutes)..."
    az provider register --namespace Microsoft.Cache --wait
    echo "  Done."
else
    echo "  Already registered."
fi
echo ""

# 1. Create Resource Group (idempotent)
echo "[1/4] Creating resource group (if not exists)..."
az group create --name "$REDIS_RESOURCE_GROUP" --location "$REDIS_LOCATION" -o none
echo "  Done."
echo ""

# 2. Create Azure Cache for Redis (Basic C0 - skip if already exists)
echo "[2/4] Creating Azure Cache for Redis (Basic C0)..."
if az redis show --name "$REDIS_NAME" --resource-group "$REDIS_RESOURCE_GROUP" -o none 2>/dev/null; then
    echo "  Already exists. Skipping."
else
    echo "  This may take 10-15 minutes..."
    az redis create \
        --location "$REDIS_LOCATION" \
        --name "$REDIS_NAME" \
        --resource-group "$REDIS_RESOURCE_GROUP" \
        --sku Basic \
        --vm-size c0 \
        -o none
    echo "  Done."
fi
echo ""

# 3. Retrieve hostname, port, and primary key; construct connection string
echo "[3/4] Retrieving connection string..."
HOSTNAME=$(az redis show --name "$REDIS_NAME" --resource-group "$REDIS_RESOURCE_GROUP" --query hostName -o tsv 2>/dev/null)
SSL_PORT=$(az redis show --name "$REDIS_NAME" --resource-group "$REDIS_RESOURCE_GROUP" --query sslPort -o tsv 2>/dev/null)
PRIMARY_KEY=$(az redis list-keys --name "$REDIS_NAME" --resource-group "$REDIS_RESOURCE_GROUP" --query primaryKey -o tsv 2>/dev/null)

if [[ -z "$HOSTNAME" || -z "$PRIMARY_KEY" ]]; then
    echo "[ERROR] Could not retrieve Redis connection info." >&2
    echo "  hostName: ${HOSTNAME:-<empty>}" >&2
    echo "  primaryKey: $([ -n "$PRIMARY_KEY" ] && echo '<retrieved>' || echo '<empty>')" >&2
    echo "  Run: az redis show -n $REDIS_NAME -g $REDIS_RESOURCE_GROUP" >&2
    exit 1
fi

[[ -z "$SSL_PORT" ]] && SSL_PORT=6380
PRIMARY_CS="${HOSTNAME}:${SSL_PORT},password=${PRIMARY_KEY},ssl=True"

# Convert Azure format (host:port,password=xxx,ssl=True) to redis-py URL (rediss://:pass@host:port)
REDIS_URL=$(python3 -c "
import sys
cs = sys.argv[1]
# Format: hostname:port,password=xxx,ssl=True (password may contain =)
parts = cs.split(',')
host_port = parts[0]
password = ''
for p in parts[1:]:
    if p.startswith('password='):
        password = p[9:]
        break
from urllib.parse import quote
safe_pass = quote(password, safe='') if password else ''
print(f'rediss://:{safe_pass}@{host_port}')
" "$PRIMARY_CS" 2>/dev/null || echo "")

[[ -z "$REDIS_URL" ]] && REDIS_URL="$PRIMARY_CS"

# 4. Write REDIS_URL to .env
echo "[4/4] Writing REDIS_URL to .env..."
ENV_FILE="$PROJECT_DIR/.env"
if [[ ! -f "$ENV_FILE" && -f "$PROJECT_DIR/.env.example" ]]; then
    cp "$PROJECT_DIR/.env.example" "$ENV_FILE"
fi

python3 -c "
import sys
env_path = sys.argv[1]
redis_url = sys.argv[2]
lines = []
found = False
if __import__('pathlib').Path(env_path).exists():
    with open(env_path) as f:
        for line in f:
            if line.strip().startswith('REDIS_URL='):
                lines.append(f'REDIS_URL={redis_url}\n')
                found = True
            else:
                lines.append(line)
if not found:
    if lines and not lines[-1].endswith('\n'):
        lines[-1] += '\n'
    lines.append('\n# Azure Cache for Redis (from setup_redis.sh)\n')
    lines.append(f'REDIS_URL={redis_url}\n')
with open(env_path, 'w') as f:
    f.writelines(lines)
" "$ENV_FILE" "$REDIS_URL"

echo ""
echo "============================================================================"
echo "  REDIS_URL written to $ENV_FILE"
echo "============================================================================"
echo ""
echo "  REDIS_URL=$REDIS_URL"
echo ""
