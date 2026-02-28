#!/bin/bash

# =================================================================
# DaiBai - AI Database Assistant | System Control Menu
# =================================================================
# Interactive menu for Chat, CLI, Command Line, and Utilities
# =================================================================

# --- Paths & Files ---
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_DIR="$PROJECT_DIR/scripts"

# Source common utilities (colors, menu helpers, status, actions)
source "$SCRIPT_DIR/lib/common.sh"
source "$SCRIPT_DIR/lib/status.sh"
source "$SCRIPT_DIR/lib/actions.sh"

# --- Read menu choice (empty Enter = 0/Back) ---
read_menu_choice() {
    read -r choice
    [[ -z "$choice" ]] && choice="0"
}

# --- Menu Header ---
show_header() {
    local title="$1"
    clear
    echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║${NC}  ${BOLD}DaiBai${NC} - ${title}"
    echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

# --- Quick Commit & Push (used from main menu and submenu 4) ---
handle_quick_commit() {
    clear
    echo -e "${CYAN}Quick Commit & Push${NC}"
    echo ""
    echo "This will: pull → add -A → commit → push"
    echo ""
    echo -e "${BOLD}Current changes:${NC}"
    git status -s 2>/dev/null || echo "  (no changes)"
    echo ""
    echo -n "Enter commit message (or 'c' to cancel): "
    read -r commit_msg
    if [[ "$commit_msg" == "c" ]] || [[ -z "$commit_msg" ]]; then
        echo -e "${YELLOW}Cancelled${NC}"
    elif [[ ! -x "$HOME/.local/bin/gitqik.sh" ]]; then
        echo -e "${YELLOW}gitqik.sh not found at ~/.local/bin/gitqik.sh${NC}"
    else
        echo ""
        echo -e "${CYAN}Running gitqik.sh...${NC}"
        echo ""
        "$HOME/.local/bin/gitqik.sh" "$commit_msg"
    fi
    echo ""
    echo "Press Enter to continue..."
    read -r
}

# --- Toggle start/stop chat service from main menu ---
handle_start_stop_chat_service() {
    clear
    if chat_service_is_running; then
        stop_chat_service
    else
        if start_chat_service_background; then
            open_chat_browser
        fi
    fi
    echo ""
    echo "Press Enter to continue..."
    read -r
}

# =============================================================================
# MAIN MENU
# =============================================================================

show_main_menu() {
    show_header "Control Center"
    show_chat_service_status_bar

    print_submenu_option "1" "Chat Service" \
        "web UI, daibai-server, open in browser"
    print_submenu_option "2" "Interactive CLI Service" \
        "REPL chat interface, daibai"
    print_submenu_option "3" "Command Line Service" \
        "single-query mode, daibai-train"
    print_submenu_option "4" "Support & Utilities" \
        "config, docs, environment"
    print_submenu_option "5" "Tests" \
        "pytest, run, list, coverage"
    print_submenu_option "6" "Redis Management" \
        "monitor, stats, Redis Insight setup"
    print_submenu_option "7" "Monitoring" \
        "view logs, tail, search, errors, cleanup"
    print_submenu_option "8" "Firebase User Management" \
        "create, list, update, delete, claims, links, revoke"
    echo ""
    print_action_option "q" "Quick Commit & Push ${YELLOW}${DIM}(gitqik.sh)${NC}"
    print_action_option "s" "Start/Stop Chat Service ${YELLOW}${DIM}(toggle, opens browser on start)${NC}"
    echo ""
    print_action_option "0" "Exit"
    echo ""
    echo -n "  Select > "
}

# =============================================================================
# CHAT SERVICE SUBMENU (1)
# =============================================================================

show_chat_service_menu() {
    show_header "Chat Service"
    show_chat_service_status_bar
    echo -e "  ${DIM}Web UI and daibai-server${NC}"
    echo ""
    print_action_option "1" "Start Web Server (background) ${YELLOW}${DIM}(kills existing first)${NC}"
    print_action_option "2" "Start Web Server & Open Browser ${YELLOW}${DIM}(kills existing first)${NC}"
    print_action_option "3" "Check if Server is Running"
    print_action_option "4" "Stop Web Server ${YELLOW}${DIM}(fully kill pid/sid)${NC}"
    print_action_option "5" "Restart Web Server ${YELLOW}${DIM}(stop then start)${NC}"
    print_action_option "6" "Run daibai-server (foreground) ${YELLOW}${DIM}(see logs, Ctrl+C to stop)${NC}"
    echo ""
    print_action_option "0" "Back to Main Menu"
    echo ""
    echo -n "  Select > "
}

handle_chat_service_menu() {
    while true; do
        show_chat_service_menu
        read_menu_choice
        case $choice in
            1)
                clear
                if start_chat_service_background; then
                    :
                fi
                echo ""
                echo "Press Enter to continue..."
                read -r
                ;;
            2)
                clear
                if start_chat_service_background; then
                    open_chat_browser
                fi
                echo ""
                echo "Press Enter to continue..."
                read -r
                ;;
            3)
                clear
                if chat_service_is_running; then
                    echo -e "${GREEN}Server is running at http://localhost:${DAIBAI_PORT:-8080}${NC}"
                else
                    echo -e "${YELLOW}Server does not appear to be running${NC}"
                fi
                echo ""
                echo "Press Enter to continue..."
                read -r
                ;;
            4)
                clear
                stop_chat_service
                echo ""
                echo "Press Enter to continue..."
                read -r
                ;;
            5)
                clear
                restart_chat_service
                echo ""
                echo "Press Enter to continue..."
                read -r
                ;;
            6)
                clear
                echo -e "${CYAN}Starting daibai-server in foreground (Ctrl+C to stop)...${NC}"
                run_daibai_server
                echo ""
                echo "Press Enter to continue..."
                read -r
                ;;
            0) return ;;
            *) ;;
        esac
    done
}

# =============================================================================
# INTERACTIVE CLI SERVICE SUBMENU (2)
# =============================================================================

show_interactive_cli_menu() {
    show_header "Interactive CLI Service"
    echo -e "  ${DIM}REPL chat interface with database and LLM${NC}"
    echo ""
    print_action_option "1" "Launch Interactive Chat ${YELLOW}${DIM}(daibai)${NC}"
    print_submenu_option "2" "Launch with Database" \
        "specify database before starting"
    print_submenu_option "3" "Launch with LLM" \
        "specify LLM provider before starting"
    echo ""
    print_action_option "0" "Back to Main Menu"
    echo ""
    echo -n "  Select > "
}

handle_interactive_cli_menu() {
    while true; do
        show_interactive_cli_menu
        read_menu_choice
        case $choice in
            1)
                clear
                run_daibai
                echo ""
                echo "Press Enter to continue..."
                read -r
                ;;
            2)
                clear
                echo -e "${CYAN}Launching interactive chat.${NC}"
                echo -e "${DIM}Use @db <name> to switch database, @llm <name> to switch LLM${NC}"
                echo ""
                run_daibai
                echo ""
                echo "Press Enter to continue..."
                read -r
                ;;
            3)
                clear
                echo -e "${CYAN}Launching interactive chat.${NC}"
                echo -e "${DIM}Use @llm <name> to switch LLM provider${NC}"
                echo ""
                run_daibai
                echo ""
                echo "Press Enter to continue..."
                read -r
                ;;
            0) return ;;
            *) ;;
        esac
    done
}

# =============================================================================
# COMMAND LINE SERVICE SUBMENU (3)
# =============================================================================

show_command_line_menu() {
    show_header "Command Line Service"
    echo -e "  ${DIM}Single-query mode and schema training${NC}"
    echo ""
    print_action_option "1" "Single Query ${YELLOW}${DIM}(daibai \"your question\")${NC}"
    print_action_option "2" "Train Schema ${YELLOW}${DIM}(daibai-train)${NC}"
    print_submenu_option "3" "Train Schema for Database" \
        "specify database to train"
    echo ""
    print_action_option "0" "Back to Main Menu"
    echo ""
    echo -n "  Select > "
}

handle_command_line_menu() {
    while true; do
        show_command_line_menu
        read_menu_choice
        case $choice in
            1)
                clear
                echo -e "${CYAN}Enter your natural language query:${NC}"
                echo ""
                echo -n "  > "
                read -r query
                if [[ -n "$query" ]]; then
                    run_daibai "$query"
                else
                    echo -e "${YELLOW}Empty query. Cancelled.${NC}"
                fi
                echo ""
                echo "Press Enter to continue..."
                read -r
                ;;
            2)
                clear
                run_daibai_train
                echo ""
                echo "Press Enter to continue..."
                read -r
                ;;
            3)
                clear
                echo -n "Enter database name to train: "
                read -r db
                if [[ -n "$db" ]]; then
                    run_daibai_train --database "$db"
                else
                    echo -e "${YELLOW}Database name required. Cancelled.${NC}"
                fi
                echo ""
                echo "Press Enter to continue..."
                read -r
                ;;
            0) return ;;
            *) ;;
        esac
    done
}

# =============================================================================
# SUPPORT & UTILITIES SUBMENU (4)
# =============================================================================

show_support_menu() {
    show_header "Support & Utilities"
    echo -e "  ${DIM}Configuration, documentation, and environment${NC}"
    echo ""
    print_action_option "1" "Show Configuration Path ${YELLOW}${DIM}(daibai.yaml locations)${NC}"
    print_action_option "2" "Edit Configuration ${YELLOW}${DIM}(daibai.yaml)${NC}"
    print_action_option "3" "Open Documentation ${YELLOW}${DIM}(docs/)${NC}"
    print_action_option "4" "Open Azure Deployment Guide ${YELLOW}${DIM}(docs/AZURE_GUIDE.md)${NC}"
    print_submenu_option "5" "Environment" \
        "check .env, preferences"
    print_action_option "6" "CLI Usage ${YELLOW}${DIM}(scripts/cli.sh)${NC}"
    print_action_option "7" "Quick Commit & Push ${YELLOW}${DIM}(gitqik.sh)${NC}"
    echo ""
    print_action_option "0" "Back to Main Menu"
    echo ""
    echo -n "  Select > "
}

handle_support_menu() {
    while true; do
        show_support_menu
        read_menu_choice
        case $choice in
            1)
                clear
                echo -e "${CYAN}Configuration file search order:${NC}"
                echo ""
                for loc in "./daibai.yaml" "./.daibai.yaml" "$HOME/.daibai/daibai.yaml" "$HOME/.config/daibai/daibai.yaml"; do
                    if [[ -f "$loc" ]]; then
                        echo -e "  ${GREEN}●${NC} $loc"
                    else
                        echo -e "  ${DIM}○${NC} $loc"
                    fi
                done
                echo ""
                echo "Press Enter to continue..."
                read -r
                ;;
            2)
                clear
                local config=""
                for loc in "$PROJECT_DIR/daibai.yaml" "$PROJECT_DIR/.daibai.yaml" "$HOME/.daibai/daibai.yaml" "$HOME/.config/daibai/daibai.yaml"; do
                    if [[ -f "$loc" ]]; then
                        config="$loc"
                        break
                    fi
                done
                if [[ -z "$config" ]]; then
                    config="$PROJECT_DIR/daibai.yaml"
                    echo -e "${YELLOW}No config found. Creating from example...${NC}"
                    if [[ -f "$PROJECT_DIR/daibai.yaml.example" ]]; then
                        cp "$PROJECT_DIR/daibai.yaml.example" "$config"
                    fi
                fi
                "${EDITOR:-nano}" "$config"
                echo ""
                echo "Press Enter to continue..."
                read -r
                ;;
            3)
                clear
                if [[ -d "$PROJECT_DIR/docs" ]]; then
                    echo -e "${CYAN}Documentation:${NC}"
                    ls -la "$PROJECT_DIR/docs/"
                    echo ""
                    echo -n "Open a file? (filename or Enter to skip): "
                    read -r doc
                    if [[ -n "$doc" && -f "$PROJECT_DIR/docs/$doc" ]]; then
                        "${PAGER:-less}" "$PROJECT_DIR/docs/$doc"
                    fi
                else
                    echo -e "${YELLOW}docs/ directory not found${NC}"
                fi
                echo ""
                echo "Press Enter to continue..."
                read -r
                ;;
            4)
                clear
                if [[ -f "$PROJECT_DIR/docs/AZURE_GUIDE.md" ]]; then
                    "${PAGER:-less}" "$PROJECT_DIR/docs/AZURE_GUIDE.md"
                else
                    echo -e "${YELLOW}docs/AZURE_GUIDE.md not found${NC}"
                fi
                echo ""
                echo "Press Enter to continue..."
                read -r
                ;;
            5)
                handle_support_env_menu
                ;;
            6)
                clear
                "$SCRIPT_DIR/cli.sh" help
                echo ""
                echo "Press Enter to continue..."
                read -r
                ;;
            7)
                handle_quick_commit
                ;;
            0) return ;;
            *) ;;
        esac
    done
}

# --- Support: Environment submenu ---
show_support_env_menu() {
    show_header "Support - Environment"
    echo -e "  ${DIM}Environment and preferences${NC}"
    echo ""
    print_action_option "1" "Check .env File"
    print_action_option "2" "Edit .env File"
    print_action_option "3" "Show User Preferences Path ${YELLOW}${DIM}(~/.daibai/preferences.json)${NC}"
    echo ""
    print_action_option "0" "Back"
    echo ""
    echo -n "  Select > "
}

handle_support_env_menu() {
    while true; do
        show_support_env_menu
        read_menu_choice
        case $choice in
            1)
                clear
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
                echo "Press Enter to continue..."
                read -r
                ;;
            2)
                clear
                local env_file="$PROJECT_DIR/.env"
                [[ ! -f "$env_file" && -f "$PROJECT_DIR/.env.example" ]] && cp "$PROJECT_DIR/.env.example" "$env_file"
                "${EDITOR:-nano}" "$env_file"
                echo ""
                echo "Press Enter to continue..."
                read -r
                ;;
            3)
                clear
                echo -e "${CYAN}User preferences:${NC} $HOME/.daibai/preferences.json"
                if [[ -f "$HOME/.daibai/preferences.json" ]]; then
                    echo ""
                    cat "$HOME/.daibai/preferences.json" 2>/dev/null | head -20
                fi
                echo ""
                echo "Press Enter to continue..."
                read -r
                ;;
            0) return ;;
            *) ;;
        esac
    done
}

# =============================================================================
# TESTS SUBMENU (5)
# =============================================================================

run_pytest() {
    local py
    if [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
        py="$PROJECT_DIR/.venv/bin/python"
    else
        py="$(command -v python3 python 2>/dev/null | head -1)"
    fi
    [[ -z "$py" ]] && { echo -e "${RED}Python not found${NC}"; return 1; }
    if ! "$py" -c "import pytest" 2>/dev/null; then
        echo -e "${YELLOW}pytest not installed. Run: pip install -e \".[dev]\"${NC}"
        return 1
    fi
    "$py" -m pytest "$@"
}

show_test_menu() {
    show_header "Tests"
    echo -e "  ${DIM}pytest control for tests/${NC}"
    echo ""
    local count
    count=$(find "$PROJECT_DIR/tests" -name "test_*.py" 2>/dev/null | wc -l)
    echo -e "  ${DIM}Test files: $count${NC}"
    echo ""
    print_action_option "1" "Run All Tests (Quiet Mode) ${YELLOW}${DIM}(-q --quiet-mode)${NC}"
    print_action_option "2" "Run All Tests (stop on first fail) ${YELLOW}${DIM}(-x)${NC}"
    print_action_option "3" "Run Specific Test File"
    print_action_option "4" "Run Specific Test by Name"
    print_action_option "5" "Run Tests Matching Pattern ${YELLOW}${DIM}(-k)${NC}"
    print_action_option "6" "List Test Files"
    print_action_option "7" "List All Test Names"
    print_action_option "8" "Run with Coverage ${YELLOW}${DIM}(--cov)${NC}"
    print_action_option "9" "Dashboard Only ${YELLOW}${DIM}(-q --quiet-mode)${NC}"
    print_action_option "10" "Run Verbose ${YELLOW}${DIM}(-v, per-test docstrings)${NC}"
    echo ""
    print_action_option "0" "Back to Main Menu"
    echo ""
    echo -n "  Select > "
}

handle_test_menu() {
    while true; do
        show_test_menu
        read_menu_choice
        case $choice in
            1)
                clear
                echo -e "${CYAN}Running all tests (Quiet Mode)...${NC}"
                echo ""
                run_pytest tests/ -q --quiet-mode
                echo ""
                echo "Press Enter to continue..."
                read -r
                ;;
            2)
                clear
                echo -e "${CYAN}Running all tests (stop on first failure)...${NC}"
                echo ""
                run_pytest tests/ -v -x
                echo ""
                echo "Press Enter to continue..."
                read -r
                ;;
            3)
                clear
                echo -e "${CYAN}Available test files:${NC}"
                echo ""
                ls -1 "$PROJECT_DIR/tests"/test_*.py 2>/dev/null | xargs -I{} basename {} | nl
                echo ""
                echo -n "Enter filename (e.g. test_config.py) or Enter to cancel: "
                read -r f
                if [[ -n "$f" && -f "$PROJECT_DIR/tests/$f" ]]; then
                    echo ""
                    run_pytest "tests/$f" -v
                else
                    echo -e "${YELLOW}Cancelled${NC}"
                fi
                echo ""
                echo "Press Enter to continue..."
                read -r
                ;;
            4)
                clear
                echo -n "Enter test name (e.g. test_list_available_providers) or pattern: "
                read -r name
                if [[ -n "$name" ]]; then
                    echo ""
                    run_pytest tests/ -v -k "$name"
                else
                    echo -e "${YELLOW}Cancelled${NC}"
                fi
                echo ""
                echo "Press Enter to continue..."
                read -r
                ;;
            5)
                clear
                echo -n "Enter -k pattern (e.g. 'llm' or 'provider'): "
                read -r pat
                if [[ -n "$pat" ]]; then
                    echo ""
                    run_pytest tests/ -v -k "$pat"
                else
                    echo -e "${YELLOW}Cancelled${NC}"
                fi
                echo ""
                echo "Press Enter to continue..."
                read -r
                ;;
            6)
                clear
                echo -e "${CYAN}Test files in tests/:${NC}"
                echo ""
                ls -la "$PROJECT_DIR/tests"/test_*.py 2>/dev/null || echo "  (none)"
                echo ""
                echo "Press Enter to continue..."
                read -r
                ;;
            7)
                clear
                echo -e "${CYAN}All test names:${NC}"
                echo ""
                run_pytest tests/ --collect-only -q 2>/dev/null | grep -E "test_|>" || run_pytest tests/ --collect-only 2>/dev/null | head -80
                echo ""
                echo "Press Enter to continue..."
                read -r
                ;;
            8)
                clear
                echo -e "${CYAN}Running tests with coverage...${NC}"
                echo ""
                run_pytest tests/ -v --cov=daibai --cov-report=term-missing 2>/dev/null || run_pytest tests/ -v
                echo ""
                echo "Press Enter to continue..."
                read -r
                ;;
            9)
                clear
                echo -e "${CYAN}Running Dashboard-Only Summary...${NC}"
                echo ""
                run_pytest tests/ -q --quiet-mode
                echo ""
                echo "Press Enter to continue..."
                read -r
                ;;
            10)
                clear
                echo -e "${CYAN}Running all tests (verbose)...${NC}"
                echo ""
                run_pytest tests/ -v --no-quiet-mode
                echo ""
                echo "Press Enter to continue..."
                read -r
                ;;
            0) return ;;
            *) ;;
        esac
    done
}

# =============================================================================
# REDIS MANAGEMENT SUBMENU (6)
# =============================================================================

show_redis_menu() {
    show_header "Redis Management"
    echo -e "  ${DIM}Monitor, stats, and Redis Insight setup for Azure Cache for Redis${NC}"
    echo ""
    print_action_option "1" "Live Monitor ${YELLOW}${DIM}(redis-cli monitor)${NC}"
    print_action_option "2" "Stats Overview ${YELLOW}${DIM}(info stats, info keyspace)${NC}"
    print_action_option "3" "Setup Visual Tools ${YELLOW}${DIM}(Redis Insight how-to)${NC}"
    print_action_option "4" "Test Connection ${YELLOW}${DIM}(verify host, port, credentials)${NC}"
    echo ""
    print_action_option "0" "Back to Main Menu"
    echo ""
    echo -n "  Select > "
}

handle_redis_menu() {
    while true; do
        show_redis_menu
        read_menu_choice
        case $choice in
            1)
                clear
                "$SCRIPT_DIR/cli.sh" cache-monitor
                echo ""
                echo "Press Enter to continue..."
                read -r
                ;;
            2)
                clear
                "$SCRIPT_DIR/cli.sh" cache-stats
                echo ""
                echo "Press Enter to continue..."
                read -r
                ;;
            3)
                clear
                echo -e "${CYAN}Redis Insight - How to connect to Azure Cache for Redis${NC}"
                echo ""
                if [[ -f "$PROJECT_DIR/docs/MONITORING.md" ]]; then
                    "${PAGER:-less}" "$PROJECT_DIR/docs/MONITORING.md"
                else
                    echo -e "${YELLOW}docs/MONITORING.md not found.${NC}"
                    echo ""
                    echo "Quick setup:"
                    echo "  1. Download Redis Insight from https://redis.io/insight/"
                    echo "  2. Add database: use AZURE_REDIS_CONNECTION_STRING or REDIS_URL from .env"
                    echo "  3. Enable 'Use TLS' (required for Azure)"
                    echo "  4. Port is typically 6380 for Azure Cache for Redis"
                    echo ""
                fi
                echo "Press Enter to continue..."
                read -r
                ;;
            4)
                clear
                "$SCRIPT_DIR/cli.sh" cache-test
                echo ""
                echo "Press Enter to continue..."
                read -r
                ;;
            0) return ;;
            *) ;;
        esac
    done
}

# =============================================================================
# MONITORING SUBMENU (7)
# =============================================================================

show_monitoring_menu() {
    local log_dir="${XDG_STATE_HOME:-$HOME/.local/state}/daibai/logs"
    show_header "Monitoring — Logs & Aspire Dashboard"
    echo -e "  ${DIM}$log_dir/daibai.log${NC}"
    echo -e "  ${DIM}10 MB max per file  |  midnight rollover  |  7 days retained${NC}"
    echo ""
    print_action_option "1" "Log File Info ${YELLOW}${DIM}(location, size, modified, rotated files)${NC}"
    print_action_option "2" "Live Tail ${YELLOW}${DIM}(follow current log — Ctrl+C to stop)${NC}"
    print_action_option "3" "View Log ${YELLOW}${DIM}(page with less, starts at end — q to quit)${NC}"
    print_action_option "4" "Errors & Warnings ${YELLOW}${DIM}(filter [ERROR] and [WARNING] lines)${NC}"
    print_action_option "5" "Today's Entries ${YELLOW}${DIM}(filter by today's date)${NC}"
    print_action_option "6" "Search Log ${YELLOW}${DIM}(grep for a pattern)${NC}"
    print_action_option "7" "Clean Rotated Files ${YELLOW}${DIM}(remove daibai.log.* backups)${NC}"
    print_action_option "8" "${RED}Purge All Logs${NC} ${DIM}(delete every log file — irreversible)${NC}"
    print_action_option "9" "Start Aspire Dashboard ${YELLOW}${DIM}(OTel, background — http://localhost:18888)${NC}"
    print_action_option "10" "Stop Aspire Dashboard ${YELLOW}${DIM}(kill Docker container)${NC}"
    print_action_option "11" "Rotate Log ${YELLOW}${DIM}(gzip current, start fresh)${NC}"
    echo ""
    print_action_option "0" "Back to Main Menu"
    echo ""
    echo -n "  Select > "
}

handle_monitoring_menu() {
    while true; do
        show_monitoring_menu
        read_menu_choice
        case $choice in
            1)
                clear
                "$SCRIPT_DIR/cli.sh" logs-info
                echo "Press Enter to continue..."
                read -r
                ;;
            2)
                clear
                "$SCRIPT_DIR/cli.sh" logs-tail
                echo ""
                echo "Press Enter to continue..."
                read -r
                ;;
            3)
                # logs-view opens $PAGER directly; no pause needed
                "$SCRIPT_DIR/cli.sh" logs-view
                ;;
            4)
                clear
                "$SCRIPT_DIR/cli.sh" logs-errors
                echo "Press Enter to continue..."
                read -r
                ;;
            5)
                # logs-today opens $PAGER; no extra pause
                "$SCRIPT_DIR/cli.sh" logs-today
                ;;
            6)
                clear
                echo -n "  Search pattern: "
                read -r pattern
                if [[ -n "$pattern" ]]; then
                    "$SCRIPT_DIR/cli.sh" logs-search "$pattern"
                fi
                ;;
            7)
                clear
                "$SCRIPT_DIR/cli.sh" logs-clean
                echo "Press Enter to continue..."
                read -r
                ;;
            8)
                clear
                "$SCRIPT_DIR/cli.sh" logs-purge
                echo "Press Enter to continue..."
                read -r
                ;;
            9)
                clear
                "$SCRIPT_DIR/cli.sh" dashboard
                echo "Press Enter to continue..."
                read -r
                ;;
            10)
                clear
                "$SCRIPT_DIR/cli.sh" dashboard-stop
                echo "Press Enter to continue..."
                read -r
                ;;
            11)
                clear
                "$SCRIPT_DIR/cli.sh" logs-rotate
                echo "Press Enter to continue..."
                read -r
                ;;
            0) return ;;
            *) ;;
        esac
    done
}

# =============================================================================
# FIREBASE USER MANAGEMENT SUBMENU (8)
# =============================================================================

# Helper: run firebase_admin_mgr.py via cli.sh so env loading is consistent.
_fb() { "$SCRIPT_DIR/cli.sh" firebase-admin "$@"; }

# Prompt for a UID and store in $REPLY_UID. Returns 1 if cancelled.
_prompt_uid() {
    echo ""
    echo -n "  Enter Firebase UID (or 'c' to cancel): "
    read -r REPLY_UID
    if [[ -z "$REPLY_UID" || "$REPLY_UID" == "c" ]]; then
        echo -e "${YELLOW}Cancelled.${NC}"
        return 1
    fi
}

show_firebase_admin_menu() {
    show_header "Firebase User Management"
    echo -e "  ${DIM}Manage Firebase Authentication users via Admin SDK${NC}"
    echo ""
    print_action_option "1" "List Users"
    print_action_option "2" "Create User ${YELLOW}${DIM}(email_verified=True, no email sent)${NC}"
    print_submenu_option "3" "Modify User" \
        "change email / name / password / phone for a UID"
    print_submenu_option "4" "Set Permissions (Claims)" \
        "toggle admin or premium_user flags"
    print_submenu_option "5" "Security Tools" \
        "generate reset/verify link, revoke sessions"
    print_action_option "6" "Delete User ${YELLOW}${DIM}(Firebase + Cosmos)${NC}"
    print_action_option "7" "${RED}WIPE ALL DATA${NC} ${DIM}(Auth + Database — irreversible)${NC}"
    print_action_option "8" "Run Sync Repair ${YELLOW}${DIM}(fix Firebase ↔ Cosmos mismatches)${NC}"
    echo ""
    print_action_option "0" "Back to Main Menu"
    echo ""
    echo -n "  Select > "
}

# 1 — List
handle_fb_list() {
    clear
    _fb list
    echo "Press Enter to continue..."
    read -r
}

# 2 — Create
handle_fb_create() {
    clear
    show_header "Create Firebase User"
    echo -e "  ${DIM}Admin-created accounts have email_verified=True automatically.${NC}"
    echo ""
    echo -n "  Email address : "; read -r fb_email
    [[ -z "$fb_email" ]] && { echo -e "${YELLOW}Cancelled.${NC}"; return; }
    echo -n "  Password      : "; read -rs fb_pass; echo
    [[ -z "$fb_pass" ]]  && { echo -e "${YELLOW}Cancelled.${NC}"; return; }
    echo -n "  Display name  : "; read -r fb_name
    echo -n "  Phone (E.164, optional): "; read -r fb_phone
    echo ""
    local args=("--email" "$fb_email" "--password" "$fb_pass")
    [[ -n "$fb_name"  ]] && args+=("--name"  "$fb_name")
    [[ -n "$fb_phone" ]] && args+=("--phone" "$fb_phone")
    _fb create "${args[@]}"
    echo ""
    echo "Press Enter to continue..."
    read -r
}

# 3 — Modify
handle_fb_modify_menu() {
    while true; do
        clear
        show_header "Modify User"
        echo ""
        print_action_option "1" "List users first (to find a UID)"
        print_action_option "2" "Change Email"
        print_action_option "3" "Change Display Name"
        print_action_option "4" "Change Password"
        print_action_option "5" "Change Phone Number"
        echo ""
        print_action_option "0" "Back"
        echo ""
        echo -n "  Select > "
        read_menu_choice
        case $choice in
            1)  clear; _fb list; echo "Press Enter..."; read -r ;;
            2|3|4|5)
                clear
                _fb list 2>/dev/null
                _prompt_uid || continue
                local uid="$REPLY_UID"
                case $choice in
                    2) echo -n "  New email    : "; read -r val; [[ -n "$val" ]] && _fb update "$uid" --email "$val" ;;
                    3) echo -n "  New name     : "; read -r val; _fb update "$uid" --name "$val" ;;
                    4) echo -n "  New password : "; read -rs val; echo; [[ -n "$val" ]] && _fb update "$uid" --password "$val" ;;
                    5) echo -n "  New phone (E.164, blank to remove): "; read -r val; _fb update "$uid" --phone "$val" ;;
                esac
                echo ""; echo "Press Enter to continue..."; read -r
                ;;
            0) return ;;
            *) ;;
        esac
    done
}

# 4 — Set claims
handle_fb_claims_menu() {
    while true; do
        clear
        show_header "Set Permissions (Custom Claims)"
        echo -e "  ${DIM}Claims take effect after the user refreshes their ID token.${NC}"
        echo ""
        print_action_option "1" "Grant admin"
        print_action_option "2" "Revoke admin"
        print_action_option "3" "Grant premium_user"
        print_action_option "4" "Revoke premium_user"
        print_action_option "5" "Enter custom JSON"
        print_action_option "6" "List users (to find UID)"
        echo ""
        print_action_option "0" "Back"
        echo ""
        echo -n "  Select > "
        read_menu_choice
        [[ "$choice" == "0" ]] && return
        [[ "$choice" == "6" ]] && { clear; _fb list; echo "Press Enter..."; read -r; continue; }
        clear
        _prompt_uid || continue
        local uid="$REPLY_UID"
        local json_claims
        case $choice in
            1) json_claims='{"admin":true}' ;;
            2) json_claims='{"admin":false}' ;;
            3) json_claims='{"premium_user":true}' ;;
            4) json_claims='{"premium_user":false}' ;;
            5) echo -n "  JSON claims (e.g. {\"role\":\"editor\"}): "; read -r json_claims ;;
            *) continue ;;
        esac
        _fb set-claims "$uid" "$json_claims"
        echo ""; echo "Press Enter to continue..."; read -r
    done
}

# 5 — Security tools
handle_fb_security_menu() {
    while true; do
        clear
        show_header "Security Tools"
        echo ""
        print_action_option "1" "Generate Password-Reset Link"
        print_action_option "2" "Generate Email-Verify Link"
        print_action_option "3" "Revoke All Sessions (refresh tokens)"
        echo ""
        print_action_option "0" "Back"
        echo ""
        echo -n "  Select > "
        read_menu_choice
        [[ "$choice" == "0" ]] && return
        [[ "$choice" != "1" && "$choice" != "2" && "$choice" != "3" ]] && continue

        # Always list users first so the admin can confirm the UID and email
        # before performing any action.
        clear
        show_header "Security Tools — Select a User"
        _fb list
        echo ""

        _prompt_uid || continue
        local uid="$REPLY_UID"
        echo ""

        case $choice in
            1) _fb links "$uid" reset  ;;
            2) _fb links "$uid" verify ;;
            3) _fb revoke "$uid"       ;;
        esac
        echo ""; echo "Press Enter to continue..."; read -r
    done
}

# 6 — Delete one
handle_fb_delete() {
    clear
    _fb list 2>/dev/null
    _prompt_uid || return
    _fb delete "$REPLY_UID"
    echo ""; echo "Press Enter to continue..."; read -r
}

# 7 — Delete all
handle_fb_delete_all() {
    clear
    show_header "NUCLEAR: Delete ALL Firebase Users"
    echo -e "  ${RED}This will permanently remove every user from Firebase Authentication.${NC}"
    echo ""
    _fb delete-all
    echo ""; echo "Press Enter to continue..."; read -r
}

handle_fb_sync_repair() {
    clear
    show_header "Sync Repair — Firebase ↔ Cosmos DB"
    echo -e "  ${DIM}Checks both systems for mismatches and offers to fix them.${NC}"
    echo ""
    _fb sync-check
    echo ""
    echo "Press Enter to continue..."
    read -r
}

handle_firebase_admin_menu() {
    while true; do
        show_firebase_admin_menu
        read_menu_choice
        case $choice in
            1) handle_fb_list ;;
            2) handle_fb_create ;;
            3) handle_fb_modify_menu ;;
            4) handle_fb_claims_menu ;;
            5) handle_fb_security_menu ;;
            6) handle_fb_delete ;;
            7) handle_fb_delete_all ;;
            8) handle_fb_sync_repair ;;
            0) return ;;
            *) ;;
        esac
    done
}

# =============================================================================
# MAIN LOOP
# =============================================================================

while true; do
    show_main_menu
    read_menu_choice
    case $choice in
        1) handle_chat_service_menu ;;
        2) handle_interactive_cli_menu ;;
        3) handle_command_line_menu ;;
        4) handle_support_menu ;;
        5) handle_test_menu ;;
        6) handle_redis_menu ;;
        7) handle_monitoring_menu ;;
        8) handle_firebase_admin_menu ;;
        q|Q) handle_quick_commit ;;
        s|S) handle_start_stop_chat_service ;;
        0)
            echo ""
            echo -e "${BLUE}Exiting.${NC}"
            exit 0
            ;;
        *)
            # Invalid input, just refresh
            ;;
    esac
done
