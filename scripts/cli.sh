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
    test-db                  Validate Cosmos DB Read/Write/Delete (Golden Ticket health check)
    test-cloud               Cosmos DB cloud integration test (requires COSMOS_ENDPOINT, run before deploy)

  SUPPORT & UTILITIES (menu 4)
    config-path             Show config file locations (● found ○ not found)
    config-edit             Edit daibai.yaml
    docs [file]             List docs/ or cat specific file
    docs-azure              Show Azure deployment guide (stdout, no pager)
    env-check               Check .env files (variable names only)
    env-edit                Edit .env file
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
    $(basename "$0") config-path
    $(basename "$0") cosmos-role
    $(basename "$0") cosmos-role --principal-id <object-id>
    $(basename "$0") test-db
    $(basename "$0") test-cloud
    $(basename "$0") test
    $(basename "$0") test -x
    $(basename "$0") test file test_config.py
    $(basename "$0") test name provider
    $(basename "$0") test gemini
    $(basename "$0") test gemini --live
    $(basename "$0") test coverage

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

cmd_test_cloud() {
    if [[ -z "${COSMOS_ENDPOINT:-}" ]]; then
        print_error "COSMOS_ENDPOINT not set. Add to environment:"
        echo ""
        echo '  export COSMOS_ENDPOINT="https://daibai-metadata.documents.azure.com:443/"'
        echo ""
        exit 1
    fi
    print_header "Cosmos DB Cloud Integration Test (Create/Read/Delete)"
    run_pytest tests/test_cosmos_cloud.py -v -s
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
        test-db)
            cmd_test_db
            ;;
        test-cloud)
            cmd_test_cloud
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
