#!/bin/bash
# --- AZURE CONTEXT ISOLATION ---
# Load .env to get AUTH_TENANT_ID dynamically (Identity Plane)
ENV_FILE="$(dirname "$0")/../../.env"
if [ -z "${ENTRATEST_FAKE:-}" ] && [ -f "$ENV_FILE" ]; then
    set -a && source "$ENV_FILE" >/dev/null 2>&1 || true
    set +a
fi
ENTRA_TENANT="${AUTH_TENANT_ID:-e12adb01-a6b3-47bb-86c0-d662dacb3675}"

# Prefer app-only client credentials: AUTH_CLIENT_ID / AUTH_CLIENT_SECRET or GRAPH_CLIENT_ID / GRAPH_CLIENT_SECRET
CLIENT_ID="${AUTH_CLIENT_ID:-${GRAPH_CLIENT_ID:-}}"
CLIENT_SECRET="${AUTH_CLIENT_SECRET:-${GRAPH_CLIENT_SECRET:-}}"

if [[ -z "$CLIENT_ID" || -z "$CLIENT_SECRET" ]]; then
    echo "❌ Missing app-only credentials in .env. Set AUTH_CLIENT_ID and AUTH_CLIENT_SECRET (or GRAPH_CLIENT_*) to use non-interactive mode."
    echo "    This script will not perform an interactive browser login to the B2C tenant."
    exit 1
fi

# Acquire app-only token
TOKEN=$(curl -s -X POST "https://login.microsoftonline.com/${ENTRA_TENANT}/oauth2/v2.0/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id=${CLIENT_ID}&scope=https://graph.microsoft.com/.default&client_secret=${CLIENT_SECRET}&grant_type=client_credentials" \
  | python3 -c "import sys,json; j=json.load(sys.stdin); print(j.get('access_token',''))")

if [[ -z "$TOKEN" ]]; then
    echo "❌ Failed to obtain app-only token. Verify AUTH_CLIENT_ID/AUTH_CLIENT_SECRET and that the app has Directory.Read.All (app) permission."
    exit 1
fi

## Get organization info (tenant display name)
TENANT_NAME=$(curl -s -H "Authorization: Bearer ${TOKEN}" "https://graph.microsoft.com/v1.0/organization" \
  | python3 -c "import sys,json; j=json.load(sys.stdin); print((j.get('value') or [{}])[0].get('displayName',''))")

echo "Tenant: ${TENANT_NAME} (ID: ${ENTRA_TENANT})"
echo ""
echo "📋 Listing Active Users in ${TENANT_NAME} (app-only)..."
TMP=$(mktemp)
URL="https://graph.microsoft.com/v1.0/users?\$select=displayName,userPrincipalName,userType,accountEnabled"
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

# Merge pages into single JSON with 'value' list
TMP_PY=$(mktemp)
cat > "$TMP_PY" <<'PY'
import sys,json
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
        out.extend(j.get("value",[]))
    except Exception:
        pass
print(json.dumps({"value": out}))
PY
USERS_JSON=$(python3 "$TMP_PY" "$TMP")
rm -f "$TMP" "$TMP_PY"

## Pretty-print table with aligned columns using python (robust error handling)
TMP2=$(mktemp)
printf '%s' "$USERS_JSON" > "$TMP2"
TMP_PY2=$(mktemp)
cat > "$TMP_PY2" <<'PY'
import sys, json
fn = sys.argv[1]
try:
    data = json.load(open(fn))
    rows = data.get("value", [])
    cols = [("DisplayName", lambda r: r.get("displayName","")), ("UPN", lambda r: r.get("userPrincipalName","")), ("UserType", lambda r: r.get("userType","")), ("Status", lambda r: ("Enabled" if r.get("accountEnabled", False) else "Disabled"))]
    widths = []
    for name, func in cols:
        maxw = len(name)
        for r in rows:
            v = str(func(r) or "")
            if len(v) > maxw:
                maxw = len(v)
        widths.append(maxw)
    fmt = "  ".join("{:<%d}" % w for w in widths)
    print(fmt.format(*[c[0] for c in cols]))
    for r in rows:
        print(fmt.format(*[str(func(r) or "") for _, func in cols]))
except Exception:
    pass
PY
python3 "$TMP_PY2" "$TMP2" 2>/dev/null || true
rm -f "$TMP2" "$TMP_PY2"

# If python pretty print produced no rows (or failed), fallback to basic extraction
if ! echo "$USERS_JSON" | grep -q '"userPrincipalName"'; then
    echo "Warning: formatted output failed; using fallback listing" >&2
    echo "DisplayName  UPN  UserType"
    echo "$USERS_JSON" | grep -oP '"userPrincipalName"\s*:\s*"\K[^"]+' | while read -r upn; do
        echo " -    $upn    -"
    done
fi
