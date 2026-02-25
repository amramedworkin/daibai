#!/bin/bash
# Create a new user in the Identity Plane using app-only credentials.
# Usage:
#   ./scripts/entra/05_create_user.sh [--display-name "Name"] [--mail-nick nick] [--password pass] [--dry-run]

# Load .env
ENV_FILE="$(dirname "$0")/../../.env"
if [ -z "${ENTRATEST_FAKE:-}" ] && [ -f "$ENV_FILE" ]; then
    set -a && source "$ENV_FILE" >/dev/null 2>&1 || true
    set +a
fi

ENTRA_TENANT="${AUTH_TENANT_ID:-${ENTRA_TENANT:-e12adb01-a6b3-47bb-86c0-d662dacb3675}}"
CLIENT_ID="${AUTH_CLIENT_ID:-${GRAPH_CLIENT_ID:-}}"
CLIENT_SECRET="${AUTH_CLIENT_SECRET:-${GRAPH_CLIENT_SECRET:-}}"

DRY_RUN=false
DISPLAY_NAME=""
MAIL_NICK=""
TEMP_PASSWORD=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --display-name) DISPLAY_NAME="$2"; shift 2;;
    --mail-nick) MAIL_NICK="$2"; shift 2;;
    --password) TEMP_PASSWORD="$2"; shift 2;;
    --dry-run) DRY_RUN=true; shift;;
    -h|--help) echo "Usage: $0 [--display-name NAME] [--mail-nick nick] [--password pwd] [--dry-run]"; exit 0;;
    *) echo "Unknown arg: $1"; exit 1;;
  esac
done

if [[ -z "$CLIENT_ID" || -z "$CLIENT_SECRET" ]]; then
  echo "❌ Missing app credentials in .env (AUTH_CLIENT_ID/AUTH_CLIENT_SECRET or GRAPH_CLIENT_*)"
  exit 1
fi

if [[ -z "$DISPLAY_NAME" ]]; then
  read -p "Enter Display Name: " DISPLAY_NAME
fi
if [[ -z "$MAIL_NICK" ]]; then
  read -p "Enter Email Prefix (e.g., testuser): " MAIL_NICK
fi
if [[ -z "$TEMP_PASSWORD" ]]; then
  read -s -p "Enter Temporary Password: " TEMP_PASSWORD
  echo ""
fi

# Acquire token
TOKEN=$(curl -s -X POST "https://login.microsoftonline.com/${ENTRA_TENANT}/oauth2/v2.0/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id=${CLIENT_ID}&scope=https://graph.microsoft.com/.default&client_secret=${CLIENT_SECRET}&grant_type=client_credentials" \
  | python3 -c "import sys,json; j=json.load(sys.stdin); print(j.get('access_token',''))")

if [[ -z "$TOKEN" ]]; then
  echo "❌ Failed to obtain app-only token. Verify credentials and permissions."
  exit 1
fi

# Get primary domain
DOMAIN=$(curl -s -H "Authorization: Bearer ${TOKEN}" "https://graph.microsoft.com/v1.0/domains" \
  | python3 -c "import sys,json; j=json.load(sys.stdin); print(next((d.get('id') for d in j.get('value',[]) if d.get('isDefault')),''))")

if [[ -z "$DOMAIN" ]]; then
  echo "❌ Could not determine primary domain for tenant."
  exit 1
fi

USER_PRINCIPAL="${MAIL_NICK}@${DOMAIN}"

JSON_PAYLOAD=$(cat <<EOF
{
  "accountEnabled": true,
  "displayName": "${DISPLAY_NAME}",
  "mailNickname": "${MAIL_NICK}",
  "userPrincipalName": "${USER_PRINCIPAL}",
  "passwordProfile": {
    "forceChangePasswordNextSignIn": true,
    "password": "${TEMP_PASSWORD}"
  }
}
EOF
)

if $DRY_RUN; then
  echo "DRY-RUN: Would create user with payload:"
  echo "${JSON_PAYLOAD}"
  exit 0
fi

# Create user
RESP=$(curl -s -w "\n%{http_code}" -X POST "https://graph.microsoft.com/v1.0/users" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d "${JSON_PAYLOAD}")
HTTP=$(echo "$RESP" | tail -n1)
BODY=$(echo "$RESP" | sed '$d')

if [[ "$HTTP" == "201" || "$HTTP" == "200" ]]; then
  echo "✅ User created: ${USER_PRINCIPAL}"
  echo "$BODY" | python3 -c "import sys,json; j=json.load(sys.stdin); print('id:', j.get('id','')); print('displayName:', j.get('displayName','')); print('upn:', j.get('userPrincipalName',''))"
  exit 0
else
  echo "❌ Failed to create user. HTTP $HTTP"
  echo "Response: $(echo "$BODY" | head -c 400)"
  exit 1
fi

