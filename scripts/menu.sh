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

# --- Menu Header ---
show_header() {
    local title="$1"
    clear
    echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║${NC}  ${BOLD}DaiBai${NC} - ${title}"
    echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

# --- Toggle start/stop chat service from main menu ---
handle_start_stop_chat_service() {
    clear
    if chat_service_is_running; then
        stop_chat_service
    else
        start_chat_service_background
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
    echo ""
    print_action_option "s" "Start/Stop Chat Service ${YELLOW}${DIM}(toggle)${NC}"
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
    print_action_option "1" "Start Web Server (background) ${YELLOW}${DIM}(daibai-server)${NC}"
    print_action_option "2" "Start Web Server & Open Browser ${YELLOW}${DIM}(background, port 8080)${NC}"
    print_action_option "3" "Check if Server is Running"
    print_action_option "4" "Stop Web Server ${YELLOW}${DIM}(if started by menu)${NC}"
    echo ""
    print_action_option "0" "Back to Main Menu"
    echo ""
    echo -n "  Select > "
}

handle_chat_service_menu() {
    while true; do
        show_chat_service_menu
        read -r choice
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
                    (sleep 1 && (xdg-open "http://localhost:${DAIBAI_PORT:-8080}" 2>/dev/null || open "http://localhost:${DAIBAI_PORT:-8080}" 2>/dev/null)) &
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
        read -r choice
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
        read -r choice
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
        read -r choice
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
        read -r choice
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
# MAIN LOOP
# =============================================================================

while true; do
    show_main_menu
    read -r choice
    case $choice in
        1) handle_chat_service_menu ;;
        2) handle_interactive_cli_menu ;;
        3) handle_command_line_menu ;;
        4) handle_support_menu ;;
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
