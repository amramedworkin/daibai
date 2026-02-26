#!/bin/bash
# --- AZURE CONTEXT ISOLATION ---
# Load .env to get AUTH_TENANT_ID dynamically (Identity Plane)
ENV_FILE="$(dirname "$0")/../../.env"
if [ -f "$ENV_FILE" ]; then
    set -a && source "$ENV_FILE" >/dev/null 2>&1 || true
    set +a
fi
ENTRA_TENANT="${AUTH_TENANT_ID:-e12adb01-a6b3-47bb-86c0-d662dacb3675}"

# Prefer app-only credentials
CLIENT_ID="${AUTH_CLIENT_ID:-}"
CLIENT_SECRET="${AUTH_CLIENT_SECRET:-}"
if [[ -z "$CLIENT_ID" || -z "$CLIENT_SECRET" ]]; then
    echo "❌ Missing app-only credentials in .env. Set AUTH_CLIENT_ID and AUTH_CLIENT_SECRET to use non-interactive mode."
    exit 1
fi

TOKEN=$(curl -s -X POST "https://login.microsoftonline.com/${ENTRA_TENANT}/oauth2/v2.0/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "client_id=${CLIENT_ID}" \
  --data-urlencode "scope=https://graph.microsoft.com/.default" \
  --data-urlencode "client_secret=${CLIENT_SECRET}" \
  --data-urlencode "grant_type=client_credentials" \
  | python3 -c "import sys,json; j=json.load(sys.stdin); print(j.get('access_token',''))")
if [[ -z "$TOKEN" ]]; then
    echo "❌ Failed to obtain app-only token. Verify AUTH_CLIENT_ID/AUTH_CLIENT_SECRET and permissions."
    exit 1
fi

USER_PREFIX="${USER_PREFIX:-daibai}"
SOFT_DELETE=false
EXECUTE=false
for a in "$@"; do
    case "$a" in
        --soft) SOFT_DELETE=true ;;
        --execute) EXECUTE=true ;;
    esac
done

if $SOFT_DELETE; then
    echo "ℹ️  Soft Delete mode enabled."
else
    echo "⚠️  Hard Delete mode (Default). Users will be PERMANENTLY destroyed."
fi
if ! $EXECUTE; then
    echo "📋 Dry-run mode (no deletions). Use --execute to perform deletions."
fi

echo ""
echo "🧹 Searching for test users starting with '$USER_PREFIX' (app-only)..."
TMP=$(mktemp)
URL="https://graph.microsoft.com/v1.0/users?\$filter=startswith(userPrincipalName,'${USER_PREFIX}')&\$select=userPrincipalName"
while [[ -n "$URL" ]]; do
  RESP=$(curl -s -w "\n%{http_code}" -H "Authorization: Bearer ${TOKEN}" "$URL")
  CODE=$(echo "$RESP" | tail -n1)
  BODY=$(echo "$RESP" | sed '$d')
  if [[ "$CODE" != "200" ]]; then
    echo "❌ Graph /users returned HTTP $CODE"
    echo "   Response snippet: $(echo "$BODY" | head -c 400 | sed 's/\"/\\\"/g')"
    rm -f "$TMP"
    exit 1
  fi
  echo "$BODY" >> "$TMP"
  echo "###JSONSEP###" >> "$TMP"
  NEXT=$(echo "$BODY" | python3 -c "import sys,json; j=json.load(sys.stdin); print(j.get('@odata.nextLink',''))")
  URL="$NEXT"
done
TEST_USERS=$(python3 - <<'PY' < "$TMP"
import sys,json
txt = sys.stdin.read()
parts = txt.split("###JSONSEP###")
out = []
for p in parts:
    p = p.strip()
    if not p:
        continue
    try:
        j = json.loads(p)
        out.extend([u.get('userPrincipalName','') for u in j.get('value',[])])
    except Exception:
        pass
print("\\n".join(out))
PY
)
rm -f "$TMP"
mapfile -t TEST_USERS < <(printf "%s\n" "$TEST_USERS")

if [ ${#TEST_USERS[@]} -eq 0 ]; then
    echo "No test users found matching prefix '$USER_PREFIX'."
    exit 0
fi

echo "Found the following test users:"
for user in "${TEST_USERS[@]}"; do echo " - $user"; done
echo ""

if $EXECUTE; then
    read -p "⚠️  Delete ALL these users? (Type 'yes' to confirm): " CONFIRM
    [[ "$CONFIRM" != "yes" ]] && echo "Bulk deletion cancelled." && exit 0
fi

for user in "${TEST_USERS[@]}"; do
        OBJ_ID=$(curl -s -H "Authorization: Bearer ${TOKEN}" "https://graph.microsoft.com/v1.0/users/${user}?\$select=id" \
            | python3 -c "import sys,json; j=json.load(sys.stdin); print(j.get('id',''))")
    if $EXECUTE; then
        echo "Deleting $user..."
        curl -s -X DELETE -H "Authorization: Bearer ${TOKEN}" "https://graph.microsoft.com/v1.0/users/${user}"
        if [ "$SOFT_DELETE" = false ]; then
            sleep 1
            curl -s -X DELETE -H "Authorization: Bearer ${TOKEN}" "https://graph.microsoft.com/v1.0/directory/deletedItems/${OBJ_ID}" || true
        fi
    else
        echo "DRY-RUN: would DELETE https://graph.microsoft.com/v1.0/users/${user}"
        if [ "$SOFT_DELETE" = false ]; then
            echo "DRY-RUN: would DELETE https://graph.microsoft.com/v1.0/directory/deletedItems/${OBJ_ID}"
        fi
    fi
done
if $EXECUTE; then
    echo "✅ Bulk deletion complete."
else
    echo "✅ Dry-run complete. No users were modified."
fi
