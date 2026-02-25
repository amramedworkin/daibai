#!/bin/bash
# --- AZURE CONTEXT ISOLATION ---
# Load .env to get AUTH_TENANT_ID dynamically (Identity Plane)
# Skip when ENTRATEST_FAKE=1 (test harness uses fake curl)
ENV_FILE="$(dirname "$0")/../../.env"
if [ -z "${ENTRATEST_FAKE:-}" ] && [ -f "$ENV_FILE" ]; then
    set -a && source "$ENV_FILE" >/dev/null 2>&1 || true
    set +a
fi
ENTRA_TENANT="${AUTH_TENANT_ID:-e12adb01-a6b3-47bb-86c0-d662dacb3675}"

# Prefer app-only client credentials
CLIENT_ID="${AUTH_CLIENT_ID:-}"
CLIENT_SECRET="${AUTH_CLIENT_SECRET:-}"
if [[ -z "$CLIENT_ID" || -z "$CLIENT_SECRET" ]]; then
    echo "❌ Missing app-only credentials in .env. Set AUTH_CLIENT_ID and AUTH_CLIENT_SECRET to use non-interactive mode."
    exit 1
fi

TOKEN=$(curl -s -X POST "https://login.microsoftonline.com/${ENTRA_TENANT}/oauth2/v2.0/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id=${CLIENT_ID}&scope=https://graph.microsoft.com/.default&client_secret=${CLIENT_SECRET}&grant_type=client_credentials" \
  | python3 -c "import sys,json; j=json.load(sys.stdin); print(j.get('access_token',''))")
if [[ -z "$TOKEN" ]]; then
    echo "❌ Failed to obtain app-only token. Verify AUTH_CLIENT_ID/AUTH_CLIENT_SECRET and permissions."
    exit 1
fi

SOFT_DELETE=false
EXECUTE=false
CI_MODE=false

# Parse flags: --soft, --execute, --ci, --user <upn>
TARGET_USER=""
for ((i=1;i<=$#;i++)); do
    arg="${!i}"
    case "$arg" in
        --soft) SOFT_DELETE=true ;;
        --execute) EXECUTE=true ;;
        --ci) CI_MODE=true ;;
        --user)
            next=$((i+1))
            TARGET_USER="${!next}"
            ;;
        --user=*)
            TARGET_USER="${arg#*=}"
            ;;
    esac
done

if $SOFT_DELETE; then
    echo "ℹ️  Soft Delete mode enabled. Users will remain in the recycle bin for 30 days."
else
    echo "⚠️  Dry-run mode (no destructive actions). Use --execute to perform deletions."
fi

echo ""
echo "🗑️  Fetching users for deletion (app-only)..."
TMP=$(mktemp)
URL="https://graph.microsoft.com/v1.0/users?\$select=userPrincipalName"
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
UPN_LIST=$(python3 - "$TMP" <<'PY'
import sys, json
fn = sys.argv[1]
txt = open(fn).read()
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
print("\n".join(out))
PY
)
rm -f "$TMP"
mapfile -t UPN_LIST < <(printf "%s\n" "$UPN_LIST")

if [ ${#UPN_LIST[@]} -eq 0 ]; then
    echo "No users found."
    exit 0
fi

if [[ -n "$TARGET_USER" ]]; then
    USER_TO_DELETE="$TARGET_USER"
else
    echo "Select a user to delete:"
    select USER_TO_DELETE in "${UPN_LIST[@]}" "Cancel"; do
        if [[ "$USER_TO_DELETE" == "Cancel" ]]; then
            echo "Operation cancelled."
            exit 0
        elif [[ -n "$USER_TO_DELETE" ]]; then
            :
        else
            echo "Invalid selection."
            continue
        fi
        break
    done
fi

do_delete() {
    OBJ_ID=$(curl -s -H "Authorization: Bearer ${TOKEN}" \
        "https://graph.microsoft.com/v1.0/users/${USER_TO_DELETE}?\$select=id" \
        | python3 -c "import sys,json; j=json.load(sys.stdin); print(j.get('id',''))")
    if [[ -z "$OBJ_ID" ]]; then
        echo "❌ Could not resolve user id for $USER_TO_DELETE"
        return 1
    fi
    if $EXECUTE; then
        echo "1/2: Soft deleting $USER_TO_DELETE..."
        curl -s -X DELETE -H "Authorization: Bearer ${TOKEN}" "https://graph.microsoft.com/v1.0/users/${USER_TO_DELETE}"
    else
        echo "DRY-RUN: would DELETE https://graph.microsoft.com/v1.0/users/${USER_TO_DELETE}"
    fi
    if [ "$SOFT_DELETE" = false ]; then
        if $EXECUTE; then
            echo "2/2: Purging $USER_TO_DELETE from recycle bin..."
            sleep 2
            curl -s -X DELETE -H "Authorization: Bearer ${TOKEN}" "https://graph.microsoft.com/v1.0/directory/deletedItems/${OBJ_ID}"
            echo "✅ User permanently deleted."
        else
            echo "DRY-RUN: would DELETE https://graph.microsoft.com/v1.0/directory/deletedItems/${OBJ_ID}"
        fi
    else
        if $EXECUTE; then
            echo "✅ User soft-deleted."
        fi
    fi
}

if $EXECUTE; then
    while [[ -n "$USER_TO_DELETE" ]]; do
        read -p "Are you sure you want to delete $USER_TO_DELETE? (y/n) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            do_delete
            break
        else
            echo "Cancelled."
            break
        fi
    done
else
    do_delete
fi
