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

  Search:  $(basename "$0") search <term>   List commands matching <term>, pick one to run
            $(basename "$0") '*cache'        Same (quote the asterisk: '*cache')

Commands by Category:

  CHAT SERVICE & SERVER
    chat-start       Start web server          |  chat-stop        Stop web server
    chat-restart     Restart web server        |  chat-bounce      Restart & open UI
    chat-bounce-ff   Restart & open in Firefox |  chat-bounce-ff-wipe  Wipe users, restart & open Firefox
    chat-status      Show running status       |  chat-toggle      Toggle start/stop
    status           Alias for chat-status    |  server           Run foreground server
    chrome           Chrome with remote-debug port 9222 |  chrome-dev  Chrome dev + localhost:8080
  ------------------------------------------------------------------------------------------
  LOGGING
    log              Show logs (alias: logs-view) |  log-info       Where are logs (alias: logs-info)
    rotate-log       Zip current log and start new (alias: logs-rotate)
    logs-info        Log file location/size    |  logs-tail        Live tail log
    logs-view        Page through log          |  logs-errors      Errors and warnings
    logs-today       Today's entries           |  logs-search      Search log for pattern
    logs-clean       Remove rotated backups   |  logs-purge       Delete all logs
  ------------------------------------------------------------------------------------------
  CLI & AGENT
    cli-launch       Interactive REPL          |  cli-query        Single natural language query
    train            Train DB schema           |  index            Semantic schema index
  ------------------------------------------------------------------------------------------
  AZURE & INFRASTRUCTURE
    cosmos-role      Setup Cosmos RBAC         |  cosmos-allow-ip  Whitelist current IP
    redis-create     Create Azure Redis        |  keyvault-create  Create Key Vault
    sync-env         Sync Cosmos config        |  verify-azure-auth Verify secretless auth
    sp-create        Get/create DaiBaiApp SP, update AZURE_* in .env
    keyvault-dump    List all Key Vault secrets and values (uses KEY_VAULT_URL)
    keyvault-fix-rbac Grant Key Vault Secrets Officer (fixes Forbidden on migrate)
    keyvault-migrate       Copy .env API keys to Key Vault (--force to overwrite existing)
    keyvault-migrate-force Same as keyvault-migrate --force
  ------------------------------------------------------------------------------------------
  USERS & AUTH
    list-users       List Cosmos users         |  wait-for-users   Poll for users
    integrate-user   Sync Firebase user → Cosmos DB (uid or email)
    firebase-admin   Manage Firebase Auth
    firebase-disable-email-enum  Fix sign-in showing create-account form
  ------------------------------------------------------------------------------------------
  CACHE & REDIS
    cache-stats      Show Redis stats          |  cache-monitor    Live Redis monitor
    cache-info       Show connection info      |  cache-test       Test connection
  ------------------------------------------------------------------------------------------
  SYSTEM RESET (testing)
    system-reset     Clear Redis, indexes, Firebase, Cosmos, logs, ~/.daibai — fresh start
  ------------------------------------------------------------------------------------------
  CONFIG & ENVIRONMENT
    setup/install    Install dependencies      |  is-ready         Check env components
    env-check        Check .env variables      |  env-edit         Edit .env file
    env-clean        Clean .env duplicates     |  env-preferences  Show user preferences
    config-path      Show config locations     |  config-edit      Edit daibai.yaml
  ------------------------------------------------------------------------------------------
  DOCUMENTATION & TESTS
    docs             View documentation        |  docs-azure       View Azure guide
    test             Run unit test suite       |  test-db          Test DB validation
    test-cosmos      Test Cosmos DB E2E        |  test-redis       Test Redis integration
    test-guardrails  Test SQL guardrails       |  test-cache-connection Test CacheManager

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

cmd_chat_bounce_firefox() {
    if restart_chat_service; then
        open_chat_browser_firefox
    fi
}

cmd_chat_bounce_ff_wipe() {
    # Delete all Firebase + Cosmos users, then bounce chat and open Firefox
    print_header "Wipe users + bounce chat (Firefox)"
    load_env
    local py
    py="$(_resolve_python)"
    "$py" "$PROJECT_DIR/scripts/firebase_admin_mgr.py" delete-all --force
    echo ""
    if restart_chat_service; then
        open_chat_browser_firefox
    fi
}

cmd_chrome() {
    local data_dir="${XDG_CONFIG_HOME:-$HOME/.config}/chrome-dev-profile"
    mkdir -p "$data_dir"
    local chrome_args=(--remote-debugging-port=9222 --user-data-dir="$data_dir" "$@")
    if command -v google-chrome &>/dev/null; then
        print_info "Opening Chrome with remote-debugging-port=9222 (background)"
        google-chrome "${chrome_args[@]}" &>/dev/null &
        disown 2>/dev/null || true
    elif command -v google-chrome-stable &>/dev/null; then
        print_info "Opening Chrome with remote-debugging-port=9222 (background)"
        google-chrome-stable "${chrome_args[@]}" &>/dev/null &
        disown 2>/dev/null || true
    elif command -v chromium &>/dev/null; then
        print_info "Opening Chromium with remote-debugging-port=9222 (background)"
        chromium "${chrome_args[@]}" &>/dev/null &
        disown 2>/dev/null || true
    elif [[ "$(uname -s)" == "Darwin" ]] && [[ -d "/Applications/Google Chrome.app" ]]; then
        print_info "Opening Chrome with remote-debugging-port=9222"
        open -a "Google Chrome" --args --remote-debugging-port=9222 --user-data-dir="$data_dir" "$@"
    else
        print_error "Chrome not found. Install google-chrome or chromium."
        exit 1
    fi
}

cmd_chrome_dev() {
    cmd_chrome "http://localhost:8080"
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

cmd_index() {
    load_env
    local target="${1:-playground}"
    local extra_args=()
    shift || true
    # Pass remaining flags (e.g. --force) straight through to the Python script.
    while [[ $# -gt 0 ]]; do
        extra_args+=("$1")
        shift
    done

    local py
    py="$(_resolve_python)"
    [[ -z "$py" ]] && { print_error "Python not found"; exit 1; }

    print_header "Semantic Schema Indexer — target: $target"
    "$py" "$PROJECT_DIR/scripts/index_db.py" "$target" "${extra_args[@]}"
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

cmd_keyvault_fix_rbac() {
    load_env
    if [[ -z "${KEY_VAULT_URL:-}" ]]; then
        print_error "KEY_VAULT_URL not set in .env. Run: ./scripts/cli.sh keyvault-create"
        exit 1
    fi
    local vault_name="${KEY_VAULT_NAME:-daibai-kv}"
    local rg="${KEY_VAULT_RESOURCE_GROUP:-daibai-rg}"
    print_header "Key Vault RBAC — Grant Secrets Officer (write secrets)"
    echo ""
    echo "  Vault: $vault_name  Resource Group: $rg"
    echo ""
    local principal_id
    principal_id=$(az ad signed-in-user show --query id -o tsv 2>/dev/null || true)
    if [[ -z "$principal_id" ]]; then
        print_error "Not logged in. Run: az login"
        exit 1
    fi
    local vault_id
    vault_id=$(az keyvault show --name "$vault_name" --resource-group "$rg" --query id -o tsv 2>/dev/null || true)
    if [[ -z "$vault_id" ]]; then
        print_error "Key Vault not found. Run: ./scripts/cli.sh keyvault-create"
        exit 1
    fi
    echo "  Assigning Key Vault Secrets Officer..."
    if az role assignment create \
        --role "Key Vault Secrets Officer" \
        --assignee "$principal_id" \
        --scope "$vault_id" \
        -o none 2>/dev/null; then
        print_success "Done. Run keyvault-migrate-force again. (RBAC can take ~1 min to propagate.)"
    else
        print_error "Assignment failed. You may need Contributor/Owner on the vault or subscription."
        exit 1
    fi
    echo ""
}

cmd_keyvault_dump() {
    load_env
    local py
    py="$(_resolve_python)"
    [[ -z "$py" ]] && { print_error "Python not found"; exit 1; }
    if [[ -z "${KEY_VAULT_URL:-}" ]]; then
        print_error "KEY_VAULT_URL not set in .env"
        echo ""
        echo "  Run: ./scripts/cli.sh keyvault-create"
        echo "  Or add to .env: KEY_VAULT_URL=https://your-vault.vault.azure.net/"
        exit 1
    fi
    "$py" "$SCRIPT_DIR/keyvault_dump.py" "$@"
}

cmd_keyvault_migrate() {
    load_env
    local py
    py="$(_resolve_python)"
    [[ -z "$py" ]] && { print_error "Python not found"; exit 1; }
    if [[ -z "${KEY_VAULT_URL:-}" ]]; then
        print_error "KEY_VAULT_URL not set in .env"
        echo ""
        echo "  Run: ./scripts/cli.sh keyvault-create"
        exit 1
    fi
    "$py" "$SCRIPT_DIR/keyvault_migrate.py" "$@"
}

# -----------------------------------------------------------------------------
# Service Principal (DaiBaiApp) - Create or show client ID
# -----------------------------------------------------------------------------
cmd_sp_create() {
    load_env
    local sp_name="${AZURE_CLIENT_NAME:-DaiBaiApp}"
    print_header "DaiBai Service Principal ($sp_name)"
    if ! command -v az &>/dev/null; then
        print_error "Azure CLI required. Install: https://docs.microsoft.com/en-us/cli/azure/install-azure-cli"
        exit 1
    fi
    if ! az account show &>/dev/null; then
        print_error "Not logged in. Run: az login"
        exit 1
    fi

    local ENV_FILE="$PROJECT_DIR/.env"
    [[ ! -f "$ENV_FILE" ]] && touch "$ENV_FILE"

    local existing
    existing=$(az ad sp list --display-name "$sp_name" --query "[0]" -o json 2>/dev/null || true)
    if [[ -n "$existing" && "$existing" != "null" && "$existing" != "[]" ]]; then
        local app_id tenant_id tenant_name
        app_id=$(echo "$existing" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('appId',''))" 2>/dev/null)
        tenant_id=$(az account show --query tenantId -o tsv 2>/dev/null)
        tenant_name=$(az rest --method get --url "https://graph.microsoft.com/v1.0/organization" --query "value[0].displayName" -o tsv 2>/dev/null || true)
        echo ""
        echo "  Service principal '$sp_name' already exists."
        echo ""
        echo "  AZURE_CLIENT_NAME=$sp_name"
        echo "  AZURE_TENANT_NAME=${tenant_name:-}"
        echo "  AZURE_TENANT_ID=$tenant_id"
        echo "  AZURE_CLIENT_ID=$app_id"
        echo "  AZURE_CLIENT_SECRET=(cannot be retrieved — add manually; see steps below)"
        echo ""
        echo "  To add AZURE_CLIENT_SECRET:"
        echo "    1. Azure Portal → Azure Active Directory → App registrations"
        echo "    2. Find '$sp_name' (or search by Client ID above)"
        echo "    3. Certificates & secrets → New client secret"
        echo "    4. Copy the Value (shown once only) and add to .env:"
        echo "       AZURE_CLIENT_SECRET=<paste-value>"
        echo ""
        local py
        py="$(_resolve_python)"
        if [[ -n "$py" ]]; then
            "$py" "$SCRIPT_DIR/update_env.py" "$ENV_FILE" \
                "AZURE_CLIENT_NAME=$sp_name" \
                "AZURE_TENANT_NAME=$tenant_name" \
                "AZURE_TENANT_ID=$tenant_id" \
                "AZURE_CLIENT_ID=$app_id" 2>/dev/null || true
        fi
        print_success "Updated AZURE_CLIENT_NAME, AZURE_TENANT_NAME, AZURE_TENANT_ID, AZURE_CLIENT_ID in .env"
        echo ""
        return 0
    fi

    echo ""
    echo "  Creating service principal '$sp_name'..."
    local out
    out=$(az ad sp create-for-rbac --name "$sp_name" -o json 2>/dev/null) || {
        print_error "Failed to create service principal"
        az ad sp create-for-rbac --name "$sp_name" -o json 2>&1 | head -5
        exit 1
    }
    local app_id password tenant_id tenant_name
    app_id=$(echo "$out" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('appId',''))" 2>/dev/null)
    password=$(echo "$out" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('password',''))" 2>/dev/null)
    tenant_id=$(echo "$out" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('tenant',''))" 2>/dev/null)
    tenant_name=$(az rest --method get --url "https://graph.microsoft.com/v1.0/organization" --query "value[0].displayName" -o tsv 2>/dev/null || true)
    if [[ -z "$app_id" || -z "$password" || -z "$tenant_id" ]]; then
        print_error "Failed to parse create output"
        echo "$out" | head -20
        exit 1
    fi
    echo ""
    print_success "Service principal created."
    echo ""
    echo "  AZURE_CLIENT_NAME=$sp_name"
    echo "  AZURE_TENANT_NAME=${tenant_name:-}"
    echo "  AZURE_TENANT_ID=$tenant_id"
    echo "  AZURE_CLIENT_ID=$app_id"
    echo "  AZURE_CLIENT_SECRET=(saved to .env)"
    echo ""
    local py
    py="$(_resolve_python)"
    if [[ -n "$py" ]] && "$py" "$SCRIPT_DIR/update_env.py" "$ENV_FILE" \
        "AZURE_CLIENT_NAME=$sp_name" \
        "AZURE_TENANT_NAME=$tenant_name" \
        "AZURE_TENANT_ID=$tenant_id" \
        "AZURE_CLIENT_ID=$app_id" \
        "AZURE_CLIENT_SECRET=$password" 2>/dev/null; then
        :
    else
        [[ -n "$(tail -c1 "$ENV_FILE" 2>/dev/null)" ]] && echo "" >> "$ENV_FILE"
        if grep -q "^AZURE_CLIENT_NAME=" "$ENV_FILE"; then
            sed -i "s|^AZURE_CLIENT_NAME=.*|AZURE_CLIENT_NAME=$sp_name|" "$ENV_FILE"
        else
            echo "AZURE_CLIENT_NAME=$sp_name" >> "$ENV_FILE"
        fi
        if [[ -n "$tenant_name" ]]; then
            if grep -q "^AZURE_TENANT_NAME=" "$ENV_FILE"; then
                sed -i "s|^AZURE_TENANT_NAME=.*|AZURE_TENANT_NAME=$tenant_name|" "$ENV_FILE"
            else
                echo "AZURE_TENANT_NAME=$tenant_name" >> "$ENV_FILE"
            fi
        fi
        if grep -q "^AZURE_TENANT_ID=" "$ENV_FILE"; then
            sed -i "s|^AZURE_TENANT_ID=.*|AZURE_TENANT_ID=$tenant_id|" "$ENV_FILE"
        else
            echo "AZURE_TENANT_ID=$tenant_id" >> "$ENV_FILE"
        fi
        if grep -q "^AZURE_CLIENT_ID=" "$ENV_FILE"; then
            sed -i "s|^AZURE_CLIENT_ID=.*|AZURE_CLIENT_ID=$app_id|" "$ENV_FILE"
        else
            echo "AZURE_CLIENT_ID=$app_id" >> "$ENV_FILE"
        fi
        if grep -q "^AZURE_CLIENT_SECRET=" "$ENV_FILE"; then
            sed -i "s|^AZURE_CLIENT_SECRET=.*|AZURE_CLIENT_SECRET=$password|" "$ENV_FILE"
        else
            echo "AZURE_CLIENT_SECRET=$password" >> "$ENV_FILE"
        fi
    fi
    print_success "Updated AZURE_CLIENT_NAME, AZURE_TENANT_NAME, AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET in .env"
    echo ""
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

_resolve_python() {
    # Return the path to the virtualenv Python (same SDK as the backend).
    if [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
        echo "$PROJECT_DIR/.venv/bin/python"
    else
        command -v python3 python 2>/dev/null | head -1
    fi
}

cmd_firebase_disable_email_enum() {
    load_env
    local project_id="${FIREBASE_PROJECT_ID:-daibai-affb0}"
    print_header "Disable Firebase email enumeration protection"
    echo "  This fixes: Sign In showing 'Create account' for existing users."
    echo "  Project: $project_id"
    echo ""
    if ! command -v gcloud &>/dev/null; then
        print_error "gcloud CLI required. Run: https://cloud.google.com/sdk/docs/install"
        echo ""
        echo "  Or use Firebase Console: Authentication → Settings → User actions"
        echo "  Uncheck 'Email enumeration protection' and Save."
        exit 1
    fi
    local token
    token=$(gcloud auth print-access-token --project="$project_id" 2>/dev/null) || {
        print_error "Run: gcloud auth login"
        exit 1
    }
    local resp
    resp=$(curl -s -w "\n%{http_code}" -X PATCH \
        -d '{"emailPrivacyConfig":{"enableImprovedEmailPrivacy":false}}' \
        -H "Authorization: Bearer $token" \
        -H "Content-Type: application/json" \
        -H "X-Goog-User-Project: $project_id" \
        "https://identitytoolkit.googleapis.com/admin/v2/projects/$project_id/config?updateMask=emailPrivacyConfig" 2>/dev/null)
    local code="${resp##*$'\n'}"
    if [[ "$code" == "200" ]]; then
        print_success "Email enumeration protection disabled."
        echo "  Existing users will now see the Sign In form (password) instead of Create account."
    else
        print_error "Request failed (HTTP $code)"
        echo "${resp%$'\n'*}" | head -5
    fi
    echo ""
}

cmd_list_users() {
    load_env
    print_header "Registered Users (Cosmos DB → Users container)"

    if [[ -z "${COSMOS_ENDPOINT:-}" ]]; then
        print_error "COSMOS_ENDPOINT not set in .env. Run: ./scripts/cli.sh sync-env"
        return 1
    fi

    local py
    py="$(_resolve_python)"

    local RAW
    RAW=$("$py" "$PROJECT_DIR/scripts/check_users.py" 2>/dev/null)

    # Detect Python-reported errors (structured sentinel {"_error": "..."}).
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

cmd_wait_for_users() {
    load_env
    print_header "Waiting for Users in Cosmos DB"

    if [[ -z "${COSMOS_ENDPOINT:-}" ]]; then
        print_error "COSMOS_ENDPOINT not set in .env. Run: ./scripts/cli.sh sync-env"
        return 1
    fi

    local py
    py="$(_resolve_python)"
    local attempt=0

    echo "  Polling every 5 s — press Ctrl+C to abort."
    echo ""

    while true; do
        attempt=$((attempt + 1))
        local RAW
        RAW=$("$py" "$PROJECT_DIR/scripts/check_users.py" 2>/dev/null)

        if echo "$RAW" | jq -e '._error' >/dev/null 2>&1; then
            local ERR
            ERR=$(echo "$RAW" | jq -r '._error')
            echo "  [attempt $attempt] DB error — retrying in 10 s: $ERR"
            sleep 10
            continue
        fi

        local COUNT
        COUNT=$(echo "$RAW" | jq 'length' 2>/dev/null || echo "0")

        if [[ "$COUNT" -gt 0 ]]; then
            print_success "Found $COUNT user(s)! Running list-users..."
            echo ""
            cmd_list_users
            return 0
        fi

        echo "  [attempt $attempt] No users yet — checking again in 5 s..."
        sleep 5
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

cmd_system_reset() {
    load_env
    local py
    py="$(_resolve_python)"
    exec "$py" "$PROJECT_DIR/scripts/system_reset.py" "$@"
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
# LOG COMMANDS
# ============================================================================
# Log location: <project_root>/logs/ (same as server.py)

_log_dir() {
    echo "$PROJECT_DIR/logs"
}

_log_file() {
    echo "$(_log_dir)/daibai.log"
}

cmd_logs_info() {
    local log_dir log_file
    log_dir="$(_log_dir)"
    log_file="$(_log_file)"

    print_header "DaiBai Log Files"
    echo ""
    echo "  Directory : $log_dir"
    echo "  Rotation  : 10 MB max per file | midnight rollover | 7 days retained"
    echo ""

    if [[ ! -d "$log_dir" ]]; then
        echo "  (log directory does not exist yet — server has not run)"
        echo ""
        return 0
    fi

    # Active log file
    if [[ -f "$log_file" ]]; then
        local size lines modified
        size=$(du -sh "$log_file" 2>/dev/null | cut -f1)
        lines=$(wc -l < "$log_file" 2>/dev/null || echo "?")
        modified=$(stat -c "%y" "$log_file" 2>/dev/null | cut -d'.' -f1 \
                || stat -f "%Sm" "$log_file" 2>/dev/null)
        echo -e "  ${GREEN}●${NC} daibai.log"
        printf "      %-12s %s\n" "Size:"     "$size"
        printf "      %-12s %s\n" "Lines:"    "$lines"
        printf "      %-12s %s\n" "Modified:" "$modified"
        printf "      %-12s %s\n" "Path:"     "$log_file"
    else
        echo -e "  ${DIM}○ daibai.log (not yet created)${NC}"
    fi

    # Rotated backups
    echo ""
    echo "  Rotated backups:"
    local found_any=false
    while IFS= read -r f; do
        [[ -z "$f" ]] && continue
        found_any=true
        local sz mod
        sz=$(du -sh "$f" 2>/dev/null | cut -f1)
        mod=$(stat -c "%y" "$f" 2>/dev/null | cut -d'.' -f1 \
           || stat -f "%Sm" "$f" 2>/dev/null)
        printf "    %-30s  [%s]  %s\n" "$(basename "$f")" "$sz" "$mod"
    done < <(ls -t "$log_dir"/daibai.log.* 2>/dev/null)
    $found_any || echo "    (none)"

    echo ""
    echo "  Total directory size: $(du -sh "$log_dir" 2>/dev/null | cut -f1)"
    echo ""
}

cmd_logs_tail() {
    local log_file
    log_file="$(_log_file)"
    if [[ ! -f "$log_file" ]]; then
        print_error "Log file not found: $log_file"
        echo "  Start the server first: ./scripts/cli.sh chat-start"
        exit 1
    fi
    print_header "Live Log Tail — Ctrl+C to stop"
    echo "  $log_file"
    echo ""
    tail -f "$log_file"
}

cmd_logs_view() {
    local log_file
    log_file="$(_log_file)"
    if [[ ! -f "$log_file" ]]; then
        print_error "Log file not found: $log_file"
        echo "  Start the server first: ./scripts/cli.sh chat-start"
        exit 1
    fi
    # +G = start at end; press g to go to top inside less
    "${PAGER:-less}" +G "$log_file"
}

cmd_logs_errors() {
    local log_file
    log_file="$(_log_file)"
    print_header "Errors and Warnings"
    if [[ ! -f "$log_file" ]]; then
        echo "  (log file not found — server has not run yet)"
        return 0
    fi
    local count
    count=$(grep -cE '\[(ERROR|WARNING)\]' "$log_file" 2>/dev/null || echo "0")
    echo "  File  : $log_file"
    echo "  Found : $count error/warning line(s)"
    echo ""
    grep -nE '\[(ERROR|WARNING)\]' "$log_file" 2>/dev/null | tail -200 \
        || echo "  (none)"
    echo ""
}

cmd_logs_today() {
    local log_file
    log_file="$(_log_file)"
    print_header "Today's Log Entries"
    if [[ ! -f "$log_file" ]]; then
        echo "  (log file not found)"
        return 0
    fi
    local today count
    today=$(date '+%Y-%m-%d')
    count=$(grep -c "^$today" "$log_file" 2>/dev/null || echo "0")
    echo "  Date  : $today"
    echo "  Lines : $count"
    echo ""
    grep "^$today" "$log_file" 2>/dev/null | "${PAGER:-less}" || true
}

cmd_logs_search() {
    local pattern="${1:-}"
    local log_file
    log_file="$(_log_file)"
    if [[ -z "$pattern" ]]; then
        print_error "Usage: $(basename "$0") logs-search <pattern>"
        exit 1
    fi
    if [[ ! -f "$log_file" ]]; then
        print_error "Log file not found: $log_file"
        exit 1
    fi
    print_header "Log Search: $pattern"
    echo "  File: $log_file"
    echo ""
    local count
    count=$(grep -c "$pattern" "$log_file" 2>/dev/null || echo "0")
    echo "  Matches: $count"
    echo ""
    grep -n --color=auto "$pattern" "$log_file" 2>/dev/null | "${PAGER:-less}" -R \
        || echo "  (no matches)"
    echo ""
}

cmd_logs_clean() {
    local log_dir
    log_dir="$(_log_dir)"
    print_header "Clean Rotated Log Files"

    if [[ ! -d "$log_dir" ]]; then
        echo "  Log directory does not exist: $log_dir"
        return 0
    fi

    local files=()
    while IFS= read -r f; do
        [[ -n "$f" ]] && files+=("$f")
    done < <(ls "$log_dir"/daibai.log.* 2>/dev/null)

    if [[ ${#files[@]} -eq 0 ]]; then
        echo "  No rotated backup files to clean."
        return 0
    fi

    local total_removed_size=0
    echo "  Files to remove:"
    for f in "${files[@]}"; do
        local sz
        sz=$(du -sh "$f" 2>/dev/null | cut -f1)
        printf "    %-30s  [%s]\n" "$(basename "$f")" "$sz"
    done
    echo ""
    echo -n "  Remove ${#files[@]} file(s)? [y/N] "
    read -r confirm
    if [[ "$confirm" =~ ^[Yy]$ ]]; then
        rm -f "$log_dir"/daibai.log.*
        print_success "Rotated log files removed."
    else
        echo "  Cancelled."
    fi
    echo ""
}

cmd_logs_purge() {
    local log_dir
    log_dir="$(_log_dir)"
    print_header "Purge ALL Log Files"

    if [[ ! -d "$log_dir" ]]; then
        echo "  Log directory does not exist: $log_dir"
        return 0
    fi

    local total_size
    total_size=$(du -sh "$log_dir" 2>/dev/null | cut -f1)
    echo -e "  ${RED}This will delete ALL log files, including the active log.${NC}"
    echo "  Directory : $log_dir"
    echo "  Total size: $total_size"
    echo ""
    echo -n "  Type 'purge' to confirm, or Enter to cancel: "
    read -r confirm
    if [[ "$confirm" == "purge" ]]; then
        rm -f "$log_dir"/daibai.log "$log_dir"/daibai.log.* "$log_dir"/daibai-archive-*.*
        print_success "All log files removed."
    else
        echo "  Cancelled."
    fi
    echo ""
}

cmd_logs_rotate() {
    local log_dir log_file archive_ts archive_path
    log_dir="$(_log_dir)"
    log_file="$(_log_file)"

    print_header "Rotate Log — Archive Current, Start Fresh"

    if [[ ! -f "$log_file" ]]; then
        echo "  Log file does not exist yet: $log_file"
        echo "  Start the server to create it."
        return 0
    fi

    archive_ts=$(date +%Y%m%d-%H%M%S)
    archive_path="$log_dir/daibai-archive-$archive_ts.log"

    echo "  Current log : $log_file"
    echo "  Archive     : $archive_path.gz"
    echo ""

    cp "$log_file" "$archive_path" || { print_error "Failed to copy log"; exit 1; }
    gzip -f "$archive_path" || { print_error "Failed to gzip archive"; exit 1; }
    if truncate -s 0 "$log_file" 2>/dev/null; then
        :
    elif printf '' > "$log_file" 2>/dev/null; then
        :
    else
        print_error "Could not clear active log — check permissions. Archive was created."
    fi

    local archived_sz
    archived_sz=$(du -sh "${archive_path}.gz" 2>/dev/null | cut -f1)
    print_success "Log archived to ${archive_path}.gz [${archived_sz}]. Active log cleared."
    echo ""
}

# ============================================================================
# WILDCARD SEARCH — cli.sh *<term> lists matching commands, prompt to run one
# ============================================================================
# Format: "command:Short description"

_CLI_WILDCARD_CMDS=(
    "chat-start:Start web server"
    "chat-stop:Stop web server"
    "chat-restart:Restart web server"
    "chat-bounce:Restart and open UI"
    "chat-bounce-ff:Restart and open in Firefox"
    "chat-bounce-ff-wipe:Wipe all users, restart and open in Firefox"
    "chat-status:Show running status"
    "chat-toggle:Toggle start/stop"
    "server:Run foreground server"
    "chrome:Chrome with remote-debug port 9222"
    "chrome-dev:Chrome dev mode with localhost:8080"
    "cli-launch:Interactive REPL"
    "cli-query:Single natural language query"
    "train:Train DB schema"
    "index:Semantic schema index"
    "cosmos-role:Setup Cosmos RBAC"
    "cosmos-allow-ip:Whitelist current IP"
    "redis-create:Create Azure Redis"
    "keyvault-create:Create Key Vault"
    "keyvault-dump:Dump Key Vault secrets (values)"
    "keyvault-fix-rbac:Grant Key Vault Secrets Officer role"
    "keyvault-migrate:Copy .env API keys to Key Vault"
    "keyvault-migrate-force:Migrate and overwrite existing secrets"
    "sync-env:Sync Cosmos config"
    "verify-azure-auth:Verify secretless auth"
    "sp-create:Get or create DaiBaiApp service principal, update .env"
    "list-users:List Cosmos users"
    "integrate-user:Sync Firebase user → Cosmos DB"
    "firebase-disable-email-enum:Fix sign-in showing create-account"
    "wait-for-users:Poll for users"
    "firebase-admin:Manage Firebase Auth"
    "cache-stats:Show Redis stats"
    "cache-info:Show connection info"
    "cache-monitor:Live Redis monitor"
    "cache-test:Test connection"
    "system-reset:Clear all state for fresh testing"
    "setup:Install dependencies"
    "install:Install dependencies"
    "is-ready:Check env components"
    "config-path:Show config locations"
    "config-edit:Edit daibai.yaml"
    "docs:View documentation"
    "docs-azure:View Azure guide"
    "env-check:Check .env variables"
    "env-edit:Edit .env file"
    "env-clean:Clean .env duplicates"
    "env-preferences:Show user preferences"
    "test:Run unit test suite"
    "test-db:Test DB validation"
    "test-cosmos:Test Cosmos DB E2E"
    "test-redis:Test Redis integration"
    "test-guardrails:Test SQL guardrails"
    "test-cache-connection:Test CacheManager"
    "logs-info:Log file location and size"
    "logs-tail:Live tail log"
    "logs-view:Page through log"
    "logs-errors:Errors and warnings"
    "logs-today:Today's entries"
    "logs-search:Search log for pattern"
    "logs-clean:Remove rotated backups"
    "logs-purge:Delete all logs"
    "logs-rotate:Archive and start fresh"
    "log:Show logs"
    "log-info:Where are logs"
    "rotate-log:Zip current log and start new"
)

cmd_wildcard_search() {
    local term="$1"
    shift || true
    term=$(echo "$term" | tr '[:upper:]' '[:lower:]')
    local matches=()
    local descs=()
    local i
    for entry in "${_CLI_WILDCARD_CMDS[@]}"; do
        local cmd="${entry%%:*}"
        local desc="${entry#*:}"
        local cmd_lower
        cmd_lower=$(echo "$cmd" | tr '[:upper:]' '[:lower:]')
        if [[ -z "$term" || "$cmd_lower" == *"$term"* ]]; then
            matches+=("$cmd")
            descs+=("$desc")
        fi
    done
    if [[ ${#matches[@]} -eq 0 ]]; then
        print_error "No commands matching '*$term'"
        return 1
    fi
    echo ""
    echo "Commands matching '*$term':"
    echo ""
    for i in "${!matches[@]}"; do
        printf "  %d. %-24s - %s\n" $((i + 1)) "${matches[$i]}" "${descs[$i]}"
    done
    echo ""
    echo -n "Select option (1-${#matches[@]}): "
    read -r choice
    if [[ "$choice" =~ ^[0-9]+$ ]] && [[ "$choice" -ge 1 ]] && [[ "$choice" -le ${#matches[@]} ]]; then
        local idx=$((choice - 1))
        main "${matches[$idx]}" "$@"
    fi
}

# ============================================================================
# MAIN DISPATCHER
# ============================================================================

main() {
    local command="${1:-help}"
    shift || true

    # Wildcard search: cli.sh *<term> (quote it) or cli.sh search <term>
    if [[ "${command:0:1}" == '*' ]]; then
        local term="${command#\*}"
        cmd_wildcard_search "$term" "$@"
        return
    fi
    if [[ "$command" == "search" ]]; then
        local term="${1:-}"
        shift || true
        cmd_wildcard_search "$term" "$@"
        return
    fi

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
        chat-bounce-ff)
            cmd_chat_bounce_firefox
            ;;
        chat-bounce-ff-wipe)
            cmd_chat_bounce_ff_wipe
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
        chrome)
            cmd_chrome "$@"
            ;;
        chrome-dev)
            cmd_chrome_dev
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
        index)
            cmd_index "$@"
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
        keyvault-fix-rbac)
            cmd_keyvault_fix_rbac
            ;;
        keyvault-dump)
            cmd_keyvault_dump "$@"
            ;;
        keyvault-migrate)
            cmd_keyvault_migrate "$@"
            ;;
        keyvault-migrate-force)
            cmd_keyvault_migrate --force
            ;;
        sp-create)
            cmd_sp_create
            ;;
        list-users)
            cmd_list_users
            ;;
        firebase-disable-email-enum)
            cmd_firebase_disable_email_enum
            ;;
        integrate-user)
            load_env
            local id="${1:-}"
            if [[ -z "$id" ]]; then
                echo -n "Enter uid or email: "
                read -r id
                [[ -z "$id" ]] && { print_error "No uid or email supplied."; exit 1; }
            fi
            exec "$(_resolve_python)" "$PROJECT_DIR/scripts/firebase_admin_mgr.py" integrate "$id"
            ;;
        wait-for-users)
            cmd_wait_for_users
            ;;
        firebase-admin)
            load_env
            local py
            py="$(_resolve_python)"
            exec "$py" "$PROJECT_DIR/scripts/firebase_admin_mgr.py" "$@"
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
        system-reset)
            cmd_system_reset "$@"
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
        logs-info)
            cmd_logs_info
            ;;
        logs-tail)
            cmd_logs_tail
            ;;
        logs-view)
            cmd_logs_view
            ;;
        logs-errors)
            cmd_logs_errors
            ;;
        logs-today)
            cmd_logs_today
            ;;
        logs-search)
            cmd_logs_search "$@"
            ;;
        logs-clean)
            cmd_logs_clean
            ;;
        logs-purge)
            cmd_logs_purge
            ;;
        logs-rotate)
            cmd_logs_rotate
            ;;
        log)
            cmd_logs_view
            ;;
        log-info)
            cmd_logs_info
            ;;
        rotate-log)
            cmd_logs_rotate
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
