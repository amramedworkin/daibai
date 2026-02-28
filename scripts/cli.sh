#!/bin/bash
# ============================================================================
# DaiBai CLI - Non-Interactive Command-Line Interface
# ============================================================================
# Mirrors every option in menu.sh. No prompts or waits. Suitable for
# automation, CI, and scripting. Status commands output immediately.
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Source libs (actions.sh pulls in common.sh and status.sh)
source "$SCRIPT_DIR/lib/common.sh"
source "$SCRIPT_DIR/lib/status.sh"
source "$SCRIPT_DIR/lib/actions.sh"

# ============================================================================
# HELPERS
# ============================================================================

print_header() {
    echo ""
    echo "============================================================================"
    echo "$1"
    echo "============================================================================"
}

print_success() {
    echo "[SUCCESS] $1"
}

print_error() {
    echo "[ERROR] $1" >&2
}

print_info() {
    echo "[INFO] $1"
}

# Load .env from project and home (for Redis, MySQL, etc.)
load_env_for_redis() {
    [[ -f "$PROJECT_DIR/.env" ]] && set -a && source "$PROJECT_DIR/.env" 2>/dev/null && set +a
    [[ -f "$HOME/.daibai/.env" ]] && set -a && source "$HOME/.daibai/.env" 2>/dev/null && set +a
}
load_env() { load_env_for_redis; }

# Get Redis connection string: prefer Azure primary key, fallback to .env
# Uses az redis show + az redis list-keys (Authentication -> Show Access Keys -> Primary Key)
get_redis_connection() {
    load_env_for_redis
    local conn=""
    local rg="${REDIS_RESOURCE_GROUP:-daibai-rg}"
    local name="${REDIS_NAME:-daibai-redis}"

    # Try Azure: fetch hostname and primary key (Authentication -> Show Access Keys -> Primary Key)
    if command -v az &>/dev/null; then
        local host ssl_port primary_key
        host=$(az redis show --name "$name" --resource-group "$rg" --query hostName -o tsv 2>/dev/null)
        ssl_port=$(az redis show --name "$name" --resource-group "$rg" --query sslPort -o tsv 2>/dev/null)
        primary_key=$(az redis list-keys --name "$name" --resource-group "$rg" --query "primaryKey" -o tsv 2>/dev/null | tr -d '\n\r')
        if [[ -n "$host" && -n "$primary_key" ]]; then
            [[ -z "$ssl_port" ]] && ssl_port=6380
            conn=$(python3 -c "
import sys
from urllib.parse import quote
host, port, key = sys.argv[1], sys.argv[2], sys.argv[3]
safe_pass = quote(key, safe='') if key else ''
print(f'rediss://:{safe_pass}@{host}:{port}')
" "$host" "$ssl_port" "$primary_key" 2>/dev/null)
        fi
    fi

    # Fallback to .env
    [[ -z "${conn// }" ]] && conn="${AZURE_REDIS_CONNECTION_STRING:-${REDIS_URL:-}}"
    echo "$conn"
}

check_redis_cli() {
    if command -v redis-cli &>/dev/null; then
        return 0
    fi
    print_info "redis-cli not found. Installing..."
    if [[ -f /etc/os-release ]]; then
        source /etc/os-release 2>/dev/null
        if [[ "$ID" == "ubuntu" || "$ID" == "debian" ]]; then
            sudo apt-get update -qq && sudo apt-get install -y redis-tools
            return $?
        fi
    fi
    if [[ "$(uname -s)" == "Darwin" ]]; then
        brew install redis
        return $?
    fi
    print_error "Could not install redis-cli. Install manually:"
    echo "  Ubuntu/Debian: sudo apt-get install -y redis-tools"
    echo "  macOS: brew install redis"
    return 1
}

# ============================================================================
# HELP
# ============================================================================

show_help() {
    cat << EOF
DaiBai CLI - Non-Interactive Control Interface

Usage: $(basename "$0") <command> [options]

Commands (mirrors menu.sh):

  CHAT SERVICE (menu 1)
    chat-start [--open] [--debug]  Start web server (kills existing first)
                            --open  Also open browser after start
                            --debug Enable fetch-models instrumentation (see log)
    chat-stop               Stop web server (fully kill pid/sid)
    chat-restart [--open]    Restart web server (stop then start)
    chat-bounce              Restart web server and open browser (alias for chat-restart --open)
    chat-status [--json]     Show running status (no wait)
                            --json  Machine-readable output
    chat-toggle             Toggle start/stop
    server                  Run daibai-server in foreground (see logs, Ctrl+C to stop)

  INTERACTIVE CLI (menu 2)
    cli-launch              Launch interactive daibai REPL (foreground)

  COMMAND LINE (menu 3)
    cli-query <query>       Single natural-language query
    train [--database <name>]  Train schema (daibai-train)
                            --database  Train specific database

  AZURE
    cosmos-role [--principal-id ID]  Set up Cosmos DB role for signed-in user
                            Uses az ad signed-in-user show for principal-id if not given.
                            Account: daibai-metadata, RG: daibai-rg (override via env)
    cosmos-allow-ip          Whitelist your current public IP in the Cosmos DB firewall
                            Auto-detects IP, preserves existing rules, applies the update
    list-users               List all registered users from Cosmos DB (Firebase UIDs, emails, registration time)
    test-db                  Validate Cosmos DB Read/Write/Delete (Golden Ticket health check)
    test-cosmos              Cosmos DB E2E (CosmosStore lifecycle, requires COSMOS_ENDPOINT)
    verify-azure-auth        Verify secretless Cosmos access (lists containers, no COSMOS_KEY)
    redis-create             Create Azure Cache for Redis (RG + Basic C0, auto-writes REDIS_URL to .env)
    keyvault-create          Create Azure Key Vault (RG + RBAC, auto-writes KEY_VAULT_URL to .env)
    test-redis               Redis integration test (add/retrieve/delete keys, requires REDIS_URL)
    test-cache-connection    CacheManager ping (mocked + live when REDIS_URL set)
    cache-stats              Redis info stats and keyspace (requires REDIS_URL or AZURE_REDIS_CONNECTION_STRING)
    cache-monitor            Redis live monitor (requires REDIS_URL or AZURE_REDIS_CONNECTION_STRING)
    cache-info               Show parsed Redis connection info for Redis Insight
    cache-test               Test Redis connection (host, port, username, password from .env)

  SETUP (idempotent)
    setup | install        Install deps (pip), create .env, check Azure CLI. Safe to run repeatedly.

  SUPPORT & UTILITIES (menu 4)
    is-ready [--strict]      Check env components (Redis, Cosmos, DB, LLM). --strict = require all
    config-path             Show config file locations (● found ○ not found)
    config-edit             Edit daibai.yaml
    docs [file]             List docs/ or cat specific file
    docs-azure              Show Azure deployment guide (stdout, no pager)
    env-check               Check .env files (variable names only)
    env-edit                Edit .env file
    env-clean [path]        Remove duplicate keys and malformed entries from .env
    env-preferences         Show preferences path and content

  TESTS (menu 5)
    test [args]             Run unit tests (excludes cloud, ~1s). Pass-through args.
    test run [path] [args]  Run tests (default: tests/). Args: -x, -k PATTERN, -q, etc.
    test file <path>       Run specific test file (e.g. test_config.py)
    test name <pattern>    Run tests matching -k pattern
    test gemini [--live]   Run isolated Gemini get-models test
                            --live  Use real API (requires GEMINI_API_KEY)
    test list              List test files
    test collect           List all test names (--collect-only)
    test coverage          Run with coverage report
    test full              Run full suite including cloud (REDIS_URL, COSMOS_ENDPOINT for live tests)
    test-guardrails        Run SQL guardrail tests (30 mock + 8 live; live use daibai config or MYSQL_*)

  META
    status                  Alias for chat-status
    help                    Show this help

Examples:
    $(basename "$0") chat-status
    $(basename "$0") chat-bounce
    $(basename "$0") chat-start --open
    $(basename "$0") server
    $(basename "$0") cli-query "How many users are in the database?"
    $(basename "$0") train --database suitecrm
    $(basename "$0") is-ready
    $(basename "$0") config-path
    $(basename "$0") cosmos-role
    $(basename "$0") cosmos-role --principal-id <object-id>
    $(basename "$0") list-users
    $(basename "$0") test-db
    $(basename "$0") test-cosmos
    $(basename "$0") verify-azure-auth
    $(basename "$0") redis-create
    $(basename "$0") keyvault-create
    $(basename "$0") test-redis
    $(basename "$0") test-cache-connection
    $(basename "$0") cache-stats
    $(basename "$0") cache-monitor
    $(basename "$0") cache-info
    $(basename "$0") cache-test
    $(basename "$0") test
    $(basename "$0") test -x
    $(basename "$0") test file test_config.py
    $(basename "$0") test name provider
    $(basename "$0") test gemini
    $(basename "$0") test gemini --live
    $(basename "$0") test coverage
    $(basename "$0") test-guardrails

EOF
}

# ============================================================================
# CHAT SERVICE COMMANDS
# ============================================================================

cmd_chat_start() {
    local open_browser=false
    local debug=false
    while [[ -n "$1" ]]; do
        case "$1" in
            --open)  open_browser=true ;;
            --debug) debug=true ;;
        esac
        shift
    done

    if $debug; then
        start_chat_service_background --debug
    else
        start_chat_service_background
    fi
    if [[ $? -eq 0 ]] && $open_browser; then
        open_chat_browser
    fi
}

cmd_chat_stop() {
    stop_chat_service
}

cmd_chat_restart() {
    local open_browser=false
    [[ "$1" == "--open" ]] && open_browser=true
    if restart_chat_service; then
        $open_browser && open_chat_browser
    fi
}

cmd_chat_bounce() {
    if restart_chat_service; then
        open_chat_browser
    fi
}

cmd_chat_status() {
    local json=false
    [[ "$1" == "--json" ]] && json=true

    if $json; then
        local port="${DAIBAI_PORT:-8080}"
        local running=false
        if chat_service_is_running; then
            running=true
        fi
        echo "{\"running\": $running, \"port\": $port, \"url\": \"http://localhost:$port\"}"
    else
        print_header "Chat Service Status"
        show_chat_service_status_bar --legend
        if chat_service_is_running; then
            echo "  URL: http://localhost:${DAIBAI_PORT:-8080}"
        fi
        echo ""
    fi
}

cmd_server() {
    run_daibai_server "$@"
}

cmd_chat_toggle() {
    if chat_service_is_running; then
        stop_chat_service
    else
        start_chat_service_background
    fi
}

# ============================================================================
# INTERACTIVE CLI
# ============================================================================

cmd_cli_launch() {
    run_daibai
}

# ============================================================================
# COMMAND LINE
# ============================================================================

cmd_cli_query() {
    if [[ -z "$1" ]]; then
        print_error "Query required"
        echo "Usage: $(basename "$0") cli-query \"your natural language question\""
        exit 1
    fi
    run_daibai "$@"
}

cmd_train() {
    local db=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --database)
                db="$2"
                shift 2
                ;;
            *)
                shift
                ;;
        esac
    done
    if [[ -n "$db" ]]; then
        run_daibai_train --database "$db"
    else
        run_daibai_train
    fi
}

# ============================================================================
# SUPPORT & UTILITIES
# ============================================================================

cmd_is_ready() {
    load_env
    local py
    if [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
        py="$PROJECT_DIR/.venv/bin/python"
    else
        py="$(command -v python3 python 2>/dev/null | head -1)"
    fi
    [[ -z "$py" ]] && { print_error "Python not found"; exit 1; }
    local strict=""
    [[ "$1" == "--strict" ]] && strict="--strict"
    "$py" "$PROJECT_DIR/scripts/check_env_ready.py" $strict
}

cmd_config_path() {
    print_header "Configuration File Search Order"
    local locs=(
        "$PROJECT_DIR/daibai.yaml"
        "$PROJECT_DIR/.daibai.yaml"
        "$HOME/.daibai/daibai.yaml"
        "$HOME/.config/daibai/daibai.yaml"
    )
    for loc in "${locs[@]}"; do
        if [[ -f "$loc" ]]; then
            echo -e "  ${GREEN}●${NC} $loc"
        else
            echo -e "  ${DIM}○${NC} $loc"
        fi
    done
    echo ""
}

cmd_config_edit() {
    local config
    config=$(find_config_file)
    if [[ -z "$config" ]]; then
        config="$PROJECT_DIR/daibai.yaml"
        if [[ -f "$PROJECT_DIR/daibai.yaml.example" ]]; then
            print_info "No config found. Creating from example..."
            cp "$PROJECT_DIR/daibai.yaml.example" "$config"
        fi
    fi
    "${EDITOR:-nano}" "$config"
}

cmd_docs() {
    if [[ ! -d "$PROJECT_DIR/docs" ]]; then
        print_error "docs/ directory not found"
        exit 1
    fi
    if [[ -n "$1" && -f "$PROJECT_DIR/docs/$1" ]]; then
        cat "$PROJECT_DIR/docs/$1"
    else
        print_header "Documentation"
        ls -la "$PROJECT_DIR/docs/"
    fi
}

cmd_docs_azure() {
    if [[ -f "$PROJECT_DIR/docs/AZURE_GUIDE.md" ]]; then
        cat "$PROJECT_DIR/docs/AZURE_GUIDE.md"
    else
        print_error "docs/AZURE_GUIDE.md not found"
        exit 1
    fi
}

cmd_env_check() {
    print_header ".env File Check"
    for loc in "$PROJECT_DIR/.env" "$HOME/.daibai/.env"; do
        if [[ -f "$loc" ]]; then
            echo -e "${GREEN}Found: $loc${NC}"
            echo ""
            echo -e "${DIM}(variable names only, not values)${NC}"
            grep -E '^[A-Z_]+=' "$loc" 2>/dev/null | cut -d= -f1 || true
        else
            echo -e "${DIM}○ $loc${NC}"
        fi
    done
    echo ""
}

cmd_env_edit() {
    local env_file="$PROJECT_DIR/.env"
    [[ ! -f "$env_file" && -f "$PROJECT_DIR/.env.example" ]] && cp "$PROJECT_DIR/.env.example" "$env_file"
    "${EDITOR:-nano}" "$env_file"
}

cmd_env_clean() {
    local env_file="${1:-$PROJECT_DIR/.env}"
    print_header "Clean .env (remove duplicates, malformed entries)"
    if [[ -f "$env_file" ]]; then
        python3 "$SCRIPT_DIR/clean_env.py" "$env_file"
        print_success "Done. Run env-check to verify."
    else
        print_error ".env not found: $env_file"
        exit 1
    fi
}

cmd_setup() {
    if [[ -x "$SCRIPT_DIR/setup.sh" ]]; then
        bash "$SCRIPT_DIR/setup.sh"
    else
        print_error "scripts/setup.sh not found"
        exit 1
    fi
}

# ============================================================================
# AZURE COMMANDS
# ============================================================================

cmd_test_db() {
    local py
    if [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
        py="$PROJECT_DIR/.venv/bin/python"
    else
        py="$(command -v python3 python 2>/dev/null | head -1)"
        if [[ -n "$py" ]]; then
            print_error "No .venv found. Create one and install deps first:"
            echo ""
            echo "  python3 -m venv .venv"
            echo "  source .venv/bin/activate"
            echo "  pip install -e ."
            echo ""
            echo "  Then run: ./scripts/cli.sh test-db"
            exit 1
        fi
    fi
    [[ -z "$py" ]] && { print_error "Python not found"; exit 1; }
    print_header "Cosmos DB Validation (Golden Ticket)"
    "$py" "$PROJECT_DIR/test_cosmos.py"
}

cmd_test_cosmos() {
    load_env
    if [[ -z "${COSMOS_ENDPOINT:-}" ]]; then
        print_error "COSMOS_ENDPOINT not set. Add to environment:"
        echo ""
        echo '  export COSMOS_ENDPOINT="https://daibai-metadata.documents.azure.com:443/"'
        echo ""
        exit 1
    fi
    print_header "Cosmos DB E2E (CosmosStore lifecycle)"
    run_pytest tests/test_cosmos_store.py -v -s
}

cmd_verify_azure_auth() {
    load_env
    print_header "Verify Secretless Azure Auth (Cosmos DB)"
    local py
    if [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
        py="$PROJECT_DIR/.venv/bin/python"
    else
        py="$(command -v python3 python 2>/dev/null | head -1)"
    fi
    [[ -z "$py" ]] && { print_error "Python not found"; exit 1; }
    "$py" "$PROJECT_DIR/scripts/verify_azure_auth.py"
}


cmd_redis_create() {
    print_header "Azure Cache for Redis Setup"
    bash "$SCRIPT_DIR/setup_redis.sh"
}

cmd_keyvault_create() {
    print_header "Azure Key Vault Setup"
    bash "$SCRIPT_DIR/setup_keyvault.sh"
}

# -----------------------------------------------------------------------------
# Cosmos DB Environment Sync
# -----------------------------------------------------------------------------
sync_cosmos_env() {
    echo "Pulling Cosmos DB configuration from Azure..."
    local COSMOS_ACCOUNT
    COSMOS_ACCOUNT=$(az cosmosdb list --query "[0].name" -o tsv 2>/dev/null || true)
    COSMOS_RG=$(az cosmosdb list --query "[0].resourceGroup" -o tsv 2>/dev/null || true)
    COSMOS_ENDPOINT=$(az cosmosdb list --query "[0].documentEndpoint" -o tsv 2>/dev/null || true)
    if [ -z "$COSMOS_ACCOUNT" ]; then
        echo "Error: No Cosmos DB account found."
        return 1
    fi

    local COSMOS_DATABASE="daibai-metadata"
    local COSMOS_CONTAINER="conversations"
    local COSMOS_DB="$COSMOS_DATABASE"

    local ENV_FILE=".env"
    if [ ! -f "$ENV_FILE" ]; then touch "$ENV_FILE"; fi

    update_env_var() {
        local key=$1
        local value=$2
        if grep -q "^${key}=" "$ENV_FILE"; then
            sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
            echo "Updated $key in $ENV_FILE"
        else
            echo "${key}=${value}" >> "$ENV_FILE"
            echo "Added $key to $ENV_FILE"
        fi
    }

    echo -e "\nWriting to $ENV_FILE..."
    update_env_var "COSMOS_ENDPOINT" "$COSMOS_ENDPOINT"
    update_env_var "COSMOS_DATABASE" "$COSMOS_DATABASE"
    update_env_var "COSMOS_CONTAINER" "$COSMOS_CONTAINER"
    update_env_var "COSMOS_ACCOUNT" "$COSMOS_ACCOUNT"
    update_env_var "COSMOS_DB" "$COSMOS_DB"

    echo -e "\n--- Current Config Block ---"
    echo "COSMOS_ENDPOINT=$COSMOS_ENDPOINT"
    echo "COSMOS_DATABASE=$COSMOS_DATABASE"
    echo "COSMOS_CONTAINER=$COSMOS_CONTAINER"
    echo "COSMOS_ACCOUNT=$COSMOS_ACCOUNT"
    echo "COSMOS_DB=$COSMOS_DB"
}

cmd_list_users() {
    load_env
    print_header "Registered Users (Cosmos DB → Users container)"

    local COSMOS_EP="${COSMOS_ENDPOINT:-}"
    local COSMOS_DB="${COSMOS_DATABASE:-daibai-metadata}"

    if [[ -z "$COSMOS_EP" ]]; then
        print_error "COSMOS_ENDPOINT not set in .env. Run: ./scripts/cli.sh sync-env"
        return 1
    fi

    # Resolve the virtualenv Python (same SDK as the backend).
    # `az cosmosdb sql query` does not exist in the Azure CLI — the data plane
    # must be reached through the SDK with DefaultAzureCredential.
    local py
    if [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
        py="$PROJECT_DIR/.venv/bin/python"
    else
        py="$(command -v python3 python 2>/dev/null | head -1)"
    fi

    local RAW
    RAW=$(COSMOS_ENDPOINT="$COSMOS_EP" COSMOS_DATABASE="$COSMOS_DB" \
        "$py" - 2>/dev/null <<'PYEOF'
import os, json

endpoint = os.environ.get("COSMOS_ENDPOINT", "").rstrip("/")
database = os.environ.get("COSMOS_DATABASE", "daibai-metadata")

if not endpoint:
    print(json.dumps([])); raise SystemExit(0)

try:
    from azure.cosmos import CosmosClient
    from azure.identity import DefaultAzureCredential
    cred      = DefaultAzureCredential()
    client    = CosmosClient(endpoint, credential=cred)
    container = client.get_database_client(database).get_container_client("Users")
    users = list(container.query_items(
        query="SELECT * FROM c WHERE c.type = 'user'",
        enable_cross_partition_query=True,
    ))
    print(json.dumps(users))
except Exception as exc:
    # Output a sentinel object so the shell can detect the failure reliably.
    print(json.dumps({"_error": str(exc)}))
PYEOF
    )

    # Detect Python-reported errors (structured sentinel, not raw stderr).
    if echo "$RAW" | jq -e '._error' >/dev/null 2>&1; then
        local ERR_MSG
        ERR_MSG=$(echo "$RAW" | jq -r '._error')
        print_error "Cosmos DB query failed — ensure 'az login' is active and your account has the Cosmos DB Data Reader role."
        echo ""
        echo "  Detail: ${ERR_MSG}" | fold -s -w 100
        return 1
    fi

    if [[ -z "$RAW" || "$RAW" == "[]" || "$RAW" == "null" ]]; then
        echo "  No users found in the Users container."
        return 0
    fi

    local COUNT
    COUNT=$(echo "$RAW" | jq 'length' 2>/dev/null || echo "?")
    echo -e "  Total users: \033[1;32m${COUNT}\033[0m\n"

    printf "  %-36s  %-34s  %-26s  %s\n" "UID (Firebase)" "Email" "Registered" "Display Name"
    printf "  %-36s  %-34s  %-26s  %s\n" \
        "$(printf '%0.s-' {1..36})" \
        "$(printf '%0.s-' {1..34})" \
        "$(printf '%0.s-' {1..26})" \
        "$(printf '%0.s-' {1..20})"

    echo "$RAW" | jq -r '.[] | [
        (.uid // .id // "—"),
        (.email // .username // "—"),
        (.onboarded_at // .created_at // "—"),
        (.display_name // "—")
    ] | @tsv' 2>/dev/null | while IFS=$'\t' read -r uid email ts name; do
        printf "  %-36s  %-34s  %-26s  %s\n" "$uid" "$email" "$ts" "$name"
    done
}

cmd_test_redis() {
    load_env_for_redis
    if [[ -z "${REDIS_URL:-}${AZURE_REDIS_CONNECTION_STRING:-}" ]]; then
        print_error "REDIS_URL not set. Run redis-create first (writes to .env automatically)"
        echo ""
        echo '  ./scripts/cli.sh redis-create'
        echo ""
        exit 1
    fi
    print_header "Redis Integration Test (Add/Retrieve/Delete Keys)"
    run_pytest tests/test_redis.py -v -s
}

cmd_test_cache_connection() {
    load_env_for_redis
    print_header "CacheManager Ping (Mocked + Live when REDIS_URL set)"
    run_pytest tests/test_cache_connection.py -v -s
}

cmd_test_guardrails() {
    load_env
    print_header "SQL Guardrail Tests (30 mock + 8 live)"
    echo "  Mock: always run. Live: run when MySQL configured (MYSQL_* or daibai.yaml database)"
    echo ""
    run_pytest tests/test_sql_guardrails.py -v -s
}

cmd_cache_stats() {
    local conn
    conn=$(get_redis_connection)
    if [[ -z "${conn// }" ]]; then
        print_error "No Redis connection. Run: az login, then ./scripts/cli.sh redis-create"
        echo "  Or set REDIS_URL or AZURE_REDIS_CONNECTION_STRING in .env"
        exit 1
    fi
    check_redis_cli || exit 1
    print_header "Redis Stats"
    local tls=""
    [[ "$conn" == rediss://* ]] && tls="--tls"
    redis-cli -u "$conn" $tls info stats
    echo ""
    redis-cli -u "$conn" $tls info keyspace
    local semantic_count
    semantic_count=$(redis-cli --raw -u "$conn" $tls KEYS "semantic:*" 2>/dev/null | grep -c . || echo "0")
    echo ""
    echo "semantic: keys: $semantic_count"
}

cmd_cache_monitor() {
    local conn
    conn=$(get_redis_connection)
    if [[ -z "${conn// }" ]]; then
        print_error "No Redis connection. Run: az login, then ./scripts/cli.sh redis-create"
        echo "  Or set REDIS_URL or AZURE_REDIS_CONNECTION_STRING in .env"
        exit 1
    fi
    check_redis_cli || exit 1
    print_header "Redis Live Monitor (Ctrl+C to stop)"
    local tls=""
    [[ "$conn" == rediss://* ]] && tls="--tls"
    redis-cli -u "$conn" $tls monitor
}

cmd_cache_info() {
    local conn
    conn=$(get_redis_connection)

    if [[ -z "${conn// }" ]]; then
        echo "Error: No Redis connection string found." >&2
        echo "  Run: az login, then ./scripts/cli.sh redis-create" >&2
        echo "  Or set REDIS_URL or AZURE_REDIS_CONNECTION_STRING in .env" >&2
        exit 1
    fi

    # Extract Hostname and Password (primary key) from connection string
    local host pass
    if [[ "$conn" == *password=* ]]; then
        host="${conn%%,*}"
        host="${host%%:*}"
        pass="${conn#*password=}"
        pass="${pass%%,*}"
        pass="${pass%%,ssl=*}"
    elif [[ "$conn" == *"://"* && "$conn" == *@* ]]; then
        local rest="${conn#*://}"
        pass="${rest%%@*}"
        pass="${pass#:}"
        local hostport="${rest#*@}"
        host="${hostport%:*}"
    else
        print_error "Could not parse connection string."
        echo "  Expected: rediss://:password@host:port  OR  host:port,password=xxx,ssl=True"
        exit 1
    fi

    # Decode URL-encoded password for display (e.g. %3D -> =) so it's copy-pasteable into Redis Insight
    pass=$(python3 -c "import sys; from urllib.parse import unquote; print(unquote(sys.argv[1]))" "$pass" 2>/dev/null || echo "$pass")

    echo ""
    echo "========== REDIS INSIGHT SETUP CHEAT SHEET =========="
    echo ""
    echo "[ GENERAL ]"
    echo ""
    echo "  Database Alias:              DaiBai-Brain"
    echo "  Host:                       $host"
    echo "  Port:                       6380"
    echo "  Username:                   default"
    echo "  Password:                   $pass"
    echo "  Timeout (s):                5"
    echo "  Select Logical Database:    0 (checked)"
    echo "  Force Standalone Connection: Enabled (checked)"
    echo ""
    echo "----------------------------------------"
    echo ""
    echo "[ SECURITY ]"
    echo ""
    echo "  Use TLS:                            Enabled (checked)"
    echo "  Use SNI:                            Disabled (unchecked)"
    echo "  Verify TLS Certificate:            Disabled (unchecked)"
    echo "  CA Certificate:                    No CA Certificate"
    echo "    Options: No CA Certificate | Add CA Certificate"
    echo "  Require TLS Client Authentication: Disabled (unchecked)"
    echo "  Use SSH Tunnel:                    Disabled (unchecked)"
    echo ""
    echo "----------------------------------------"
    echo ""
    echo "[ DECOMPRESSION & FORMATTERS ]"
    echo ""
    echo "  Enable Automatic Data Decompression: Disabled (unchecked)"
    echo "  Decompression format:                No decompression"
    echo "    Options: No decompression | GZIP | LZ4 | SNAPPY | ZSTD | Brotli | PHP GZCompress"
    echo "  Key name format:                     Unicode (recommended) | HEX"
    echo ""
    echo "Run './scripts/cli.sh cache-test' to verify connectivity before opening the Desktop App."
    echo ""
}

cmd_cache_test() {
    local conn
    conn=$(get_redis_connection)

    if [[ -z "${conn// }" ]]; then
        echo "Error: No Redis connection string found." >&2
        echo "  Run: az login, then ./scripts/cli.sh redis-create" >&2
        echo "  Or set REDIS_URL or AZURE_REDIS_CONNECTION_STRING in .env" >&2
        exit 1
    fi

    # Extract host, port, password (same logic as cache-info)
    local host pass port=6380
    if [[ "$conn" == *password=* ]]; then
        host="${conn%%,*}"
        host="${host%%:*}"
        pass="${conn#*password=}"
        pass="${pass%%,*}"
        pass="${pass%%,ssl=*}"
    elif [[ "$conn" == *"://"* && "$conn" == *@* ]]; then
        local rest="${conn#*://}"
        pass="${rest%%@*}"
        pass="${pass#:}"
        local hostport="${rest#*@}"
        host="${hostport%:*}"
        [[ "$hostport" == *:* ]] && port="${hostport##*:}"
    else
        print_error "Could not parse connection string."
        exit 1
    fi

    check_redis_cli || exit 1

    print_header "Redis Connection Test"
    echo ""
    echo "  Host:     $host"
    echo "  Port:     $port"
    echo "  Username: default"
    echo ""
    echo -n "  Testing connection... "

    local tls=""
    [[ "$conn" == rediss://* || "$port" == "6380" ]] && tls="--tls"
    local result
    result=$(redis-cli -u "$conn" $tls PING 2>&1)

    if [[ "$result" == "PONG" ]]; then
        echo -e "${GREEN}OK${NC}"
        echo ""
        print_success "Redis is reachable. Connection verified."
    else
        echo -e "${RED}FAILED${NC}"
        echo ""
        print_error "Could not connect to Redis."
        echo "  Response: ${result:- (no response)}"
        echo ""
        echo "  Check: Host, Port, Password in .env"
        echo "  Run: ./scripts/cli.sh cache-info"
        exit 1
    fi
    echo ""
}

cmd_cosmos_role() {
    local principal_id=""
    while [[ -n "$1" ]]; do
        case "$1" in
            --principal-id)
                principal_id="$2"
                shift 2
                ;;
            *)
                shift
                ;;
        esac
    done

    if [[ -z "$principal_id" ]]; then
        print_info "Getting principal ID from signed-in user..."
        principal_id=$(az ad signed-in-user show --query id -o tsv 2>/dev/null)
        if [[ -z "$principal_id" ]]; then
            print_error "Could not get principal ID. Run: az login"
            exit 1
        fi
        print_info "Principal ID: $principal_id"
    fi

    local account="${COSMOS_ACCOUNT_NAME:-daibai-metadata}"
    local rg="${COSMOS_RESOURCE_GROUP:-daibai-rg}"
    local role_id="00000000-0000-0000-0000-000000000002"

    print_info "Creating Cosmos DB role assignment (account=$account, rg=$rg)..."
    if az cosmosdb sql role assignment create \
        --account-name "$account" \
        --resource-group "$rg" \
        --scope "/" \
        --principal-id "$principal_id" \
        --role-definition-id "$role_id"; then
        print_success "Cosmos DB role assignment created"
    else
        print_error "Role assignment failed"
        exit 1
    fi
}

cmd_cosmos_allow_ip() {
    load_env
    print_header "Cosmos DB Firewall — Add Current IP"

    # ── Resolve account + resource group ──────────────────────────────────────
    local account="${COSMOS_ACCOUNT:-${COSMOS_ACCOUNT_NAME:-}}"
    local rg="${COSMOS_RESOURCE_GROUP:-}"

    if [[ -z "$account" ]]; then
        print_info "Looking up Cosmos DB account from Azure..."
        account=$(az cosmosdb list --query "[0].name" -o tsv 2>/dev/null || true)
    fi
    if [[ -z "$rg" ]]; then
        rg=$(az cosmosdb list --query "[0].resourceGroup" -o tsv 2>/dev/null || true)
    fi
    if [[ -z "$account" || -z "$rg" ]]; then
        print_error "Could not determine Cosmos DB account or resource group."
        echo "  Ensure 'az login' is active and a Cosmos DB account exists in your subscription."
        return 1
    fi
    print_info "Account : $account"
    print_info "RG      : $rg"

    # ── Detect public IP ───────────────────────────────────────────────────────
    local my_ip
    my_ip=$(curl -s --max-time 5 https://api.ipify.org 2>/dev/null \
         || curl -s --max-time 5 https://checkip.amazonaws.com 2>/dev/null \
         || true)
    my_ip="${my_ip// /}"   # strip any whitespace
    if [[ -z "$my_ip" ]]; then
        print_error "Could not detect your public IP. Check internet connectivity."
        return 1
    fi
    print_info "Your IP : $my_ip"

    # ── Fetch existing IPs as a comma-separated list ──────────────────────────
    # az cosmosdb update uses --ip-range-filter "ip1,ip2,..." (not JSON).
    local existing_csv
    existing_csv=$(az cosmosdb show \
        --name "$account" \
        --resource-group "$rg" \
        --query "join(',', ipRules[].ipAddressOrRange)" \
        -o tsv 2>/dev/null || true)

    # Check if the IP is already present.
    if [[ ",$existing_csv," == *",$my_ip,"* ]]; then
        print_success "IP $my_ip is already in the Cosmos DB firewall allow-list."
        return 0
    fi

    # Append the new IP.
    local new_filter
    if [[ -z "$existing_csv" ]]; then
        new_filter="$my_ip"
    else
        new_filter="${existing_csv},${my_ip}"
    fi

    echo ""
    print_info "Adding $my_ip to the firewall rule set..."
    print_info "Filter  : $new_filter"
    echo ""

    if az cosmosdb update \
        --name "$account" \
        --resource-group "$rg" \
        --ip-range-filter "$new_filter" \
        --output none 2>&1; then
        echo ""
        print_success "Done. IP $my_ip is now allowed. Re-run 'list-users' in ~30 seconds."
    else
        echo ""
        print_error "Update failed. You may need the 'Contributor' or 'DocumentDB Account Contributor' role on the Cosmos DB account."
        return 1
    fi
}

cmd_env_preferences() {
    print_header "User Preferences"
    echo "Path: $HOME/.daibai/preferences.json"
    if [[ -f "$HOME/.daibai/preferences.json" ]]; then
        echo ""
        cat "$HOME/.daibai/preferences.json" 2>/dev/null | head -20
    fi
    echo ""
}

# ============================================================================
# TEST COMMANDS
# ============================================================================

run_pytest() {
    local py
    if [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
        py="$PROJECT_DIR/.venv/bin/python"
    else
        py="$(command -v python3 python 2>/dev/null | head -1)"
    fi
    [[ -z "$py" ]] && { print_error "Python not found"; return 1; }
    if ! "$py" -c "import pytest" 2>/dev/null; then
        print_error "pytest not installed. Run: pip install -e \".[dev]\""
        return 1
    fi
    # pytest-sugar (progress bar) and rich (success gauge) activate when installed
    "$py" -m pytest "$@"
}

cmd_test() {
    local sub="${1:-run}"
    shift || true
    case "$sub" in
        run)
            local path="tests/"
            [[ -n "$1" && "$1" != -* ]] && { path="$1"; shift; }
            [[ "$path" != tests/* && "$path" != tests ]] && path="tests/$path"
            # Exclude cloud tests by default for speed (~1s when offline)
            # -s shows colorized test descriptions
            if [[ "$path" == "tests/" || "$path" == "tests" ]]; then
                run_pytest tests/ -v -s -m "not cloud" "$@"
            else
                run_pytest "$path" -v -s "$@"
            fi
            ;;
        file)
            local f="$1"
            [[ -z "$f" ]] && { print_error "Usage: test file <path>"; return 1; }
            shift || true
            [[ "$f" != tests/* ]] && f="tests/$f"
            run_pytest "$f" -v -s "$@"
            ;;
        name)
            local pat="$1"
            [[ -z "$pat" ]] && { print_error "Usage: test name <pattern>"; return 1; }
            shift || true
            run_pytest tests/ -v -k "$pat" "$@"
            ;;
        gemini)
            local live=false
            [[ "$1" == "--live" ]] && { live=true; shift; }
            if $live; then
                run_pytest tests/test_gemini_get_models.py -v -s -k "live" "$@"
            else
                run_pytest tests/test_gemini_get_models.py -v -s "$@"
            fi
            ;;
        list)
            print_header "Test Files"
            ls -la "$PROJECT_DIR/tests"/test_*.py 2>/dev/null || echo "  (none)"
            ;;
        collect)
            run_pytest tests/ --collect-only -q 2>/dev/null || run_pytest tests/ --collect-only
            ;;
        coverage)
            run_pytest tests/ -v --cov=daibai --cov-report=term-missing 2>/dev/null || run_pytest tests/ -v
            ;;
        full)
            run_pytest tests/ -v -s "$@"
            ;;
        *)
            # Pass-through: test -x, test -k foo, test tests/file.py
            run_pytest tests/ -v "$sub" "$@"
            ;;
    esac
}

# ============================================================================
# MAIN DISPATCHER
# ============================================================================

main() {
    local command="${1:-help}"
    shift || true

    case "$command" in
        chat-start)
            cmd_chat_start "$@"
            ;;
        chat-stop)
            cmd_chat_stop
            ;;
        chat-restart)
            cmd_chat_restart "$@"
            ;;
        chat-bounce)
            cmd_chat_bounce
            ;;
        chat-status)
            cmd_chat_status "$@"
            ;;
        chat-toggle)
            cmd_chat_toggle
            ;;
        server)
            cmd_server "$@"
            ;;
        cli-launch)
            cmd_cli_launch
            ;;
        cli-query)
            cmd_cli_query "$@"
            ;;
        train)
            cmd_train "$@"
            ;;
        cosmos-role)
            cmd_cosmos_role "$@"
            ;;
        cosmos-allow-ip)
            cmd_cosmos_allow_ip
            ;;
        test-db)
            cmd_test_db
            ;;
        test-cosmos)
            cmd_test_cosmos
            ;;
        verify-azure-auth)
            cmd_verify_azure_auth
            ;;
        redis-create)
            cmd_redis_create
            ;;
        keyvault-create)
            cmd_keyvault_create
            ;;
        list-users)
            cmd_list_users
            ;;
        sync-env)
            sync_cosmos_env
            ;;
        test-redis)
            cmd_test_redis
            ;;
        test-cache-connection)
            cmd_test_cache_connection
            ;;
        test-guardrails)
            cmd_test_guardrails
            ;;
        cache-stats)
            cmd_cache_stats
            ;;
        cache-monitor)
            cmd_cache_monitor
            ;;
        cache-info)
            cmd_cache_info
            ;;
        cache-test)
            cmd_cache_test
            ;;
        setup|install)
            cmd_setup
            ;;
        is-ready)
            cmd_is_ready "$@"
            ;;
        config-path)
            cmd_config_path
            ;;
        config-edit)
            cmd_config_edit
            ;;
        docs)
            cmd_docs "$@"
            ;;
        docs-azure)
            cmd_docs_azure
            ;;
        env-check)
            cmd_env_check
            ;;
        env-edit)
            cmd_env_edit
            ;;
        env-clean)
            cmd_env_clean "$@"
            ;;
        env-preferences)
            cmd_env_preferences
            ;;
        test)
            cmd_test "$@"
            ;;
        status)
            cmd_chat_status "$@"
            ;;
        help|--help|-h|"")
            show_help
            ;;
        *)
            print_error "Unknown command: $command"
            echo ""
            show_help
            exit 1
            ;;
    esac
}

main "$@"
