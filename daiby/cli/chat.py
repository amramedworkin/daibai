#!/usr/bin/env python3
"""
Daiby Interactive Chat Agent.

An interactive REPL for querying databases using natural language.
Supports multiple LLM providers and database connections.

Usage:
    daiby                    # Interactive mode
    daiby "query"           # Single query mode
    daiby -v "query"        # Verbose mode
"""

import sys
import os
import asyncio
import re
import subprocess
import warnings
from typing import Optional, Dict, Any
from pathlib import Path
from tabulate import tabulate

from ..core.config import load_config, load_user_preferences, save_user_preferences, Config
from ..core.agent import DaibyAgent


class Colors:
    """ANSI color codes for terminal output."""
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    UNDERLINE = '\033[4m'
    END = '\033[0m'


class ChatAgent:
    """Interactive AI-powered database assistant."""
    
    def __init__(self, config: Optional[Config] = None, interactive: bool = True, verbose: bool = False):
        self.verbose = verbose
        
        if not verbose:
            warnings.filterwarnings('ignore')
        
        # Load configuration
        self.config = config or load_config()
        self.agent = DaibyAgent(self.config)
        
        # Load user preferences
        prefs = load_user_preferences()
        self.current_db = prefs.get("database") or self.config.default_database
        self.current_llm = prefs.get("llm") or self.config.default_llm
        self.mode = prefs.get("mode", "sql")
        self.clipboard = prefs.get("clipboard", True)
        
        # Apply preferences to agent
        if self.current_db:
            try:
                self.agent.switch_database(self.current_db)
            except ValueError:
                self.current_db = self.config.default_database
        
        if self.current_llm:
            try:
                self.agent.switch_llm(self.current_llm)
            except ValueError:
                self.current_llm = self.config.default_llm
        
        # Session state
        self.auto_execute = False
        self.dry_run = False
        self.query_count = 0
        self.max_queries_per_session = 100
        self.interactive = interactive
        
        # Exports directory
        self.exports_dir = self.config.exports_dir
        self.exports_dir.mkdir(parents=True, exist_ok=True)
    
    def _save_state(self):
        """Save current preferences."""
        save_user_preferences({
            "database": self.current_db,
            "llm": self.current_llm,
            "mode": self.mode,
            "clipboard": self.clipboard,
        })
    
    def _copy_to_clipboard(self, text: str) -> bool:
        """Copy text to system clipboard."""
        if not self.clipboard:
            return False
        try:
            process = subprocess.Popen(['xclip', '-selection', 'clipboard'], 
                                       stdin=subprocess.PIPE)
            process.communicate(text.encode('utf-8'))
            return process.returncode == 0
        except Exception:
            return False
    
    def _test_connectivity(self):
        """Test database and LLM connectivity."""
        print(f"\n{Colors.CYAN}Testing Connectivity...{Colors.END}\n")
        
        # Test databases
        print(f"{Colors.YELLOW}Databases:{Colors.END}")
        for db_name in self.config.list_databases():
            marker = " (current)" if db_name == self.current_db else ""
            try:
                self.agent.switch_database(db_name)
                df = self.agent.run_sql("SELECT 1 as test")
                if df is not None and not df.empty:
                    print(f"  {Colors.GREEN}✓{Colors.END} {db_name}{marker} - Connected")
                else:
                    print(f"  {Colors.RED}✗{Colors.END} {db_name}{marker} - No response")
            except Exception as e:
                error_msg = str(e)[:50]
                print(f"  {Colors.RED}✗{Colors.END} {db_name}{marker} - {error_msg}")
        
        # Restore current database
        if self.current_db:
            try:
                self.agent.switch_database(self.current_db)
            except Exception:
                pass
        
        # Test LLM providers
        print(f"\n{Colors.YELLOW}LLM Providers:{Colors.END}")
        test_prompt = "Say 'OK' if you can hear me."
        for llm_name in self.config.list_llm_providers():
            marker = " (current)" if llm_name == self.current_llm else ""
            print(f"  {Colors.DIM}Prompt: \"{test_prompt}\"{Colors.END}")
            try:
                self.agent.switch_llm(llm_name)
                response = self.agent.generate(test_prompt, {})
                if response and response.text:
                    reply = response.text.strip().replace('\n', ' ')[:40]
                    print(f"  {Colors.GREEN}✓{Colors.END} {llm_name}{marker} - \"{reply}\"")
                else:
                    print(f"  {Colors.RED}✗{Colors.END} {llm_name}{marker} - No response")
            except Exception as e:
                error_msg = str(e)[:50]
                print(f"  {Colors.RED}✗{Colors.END} {llm_name}{marker} - {error_msg}")
        
        # Restore current LLM
        if self.current_llm:
            try:
                self.agent.switch_llm(self.current_llm)
            except Exception:
                pass
        
        print()
    
    async def _run_smoke_test(self):
        """Run comprehensive smoke test of all capabilities."""
        print(f"\n{Colors.CYAN}{'='*60}{Colors.END}")
        print(f"{Colors.BOLD}           DAIBY SMOKE TEST{Colors.END}")
        print(f"{Colors.CYAN}{'='*60}{Colors.END}\n")
        
        results = {"passed": 0, "failed": 0, "skipped": 0}
        original_db = self.current_db
        original_llm = self.current_llm
        
        def report(name: str, passed: bool, message: str = ""):
            if passed:
                results["passed"] += 1
                status = f"{Colors.GREEN}PASS{Colors.END}"
            else:
                results["failed"] += 1
                status = f"{Colors.RED}FAIL{Colors.END}"
            msg = f" - {message}" if message else ""
            print(f"  [{status}] {name}{msg}")
        
        # 1. Configuration
        print(f"{Colors.YELLOW}1. Configuration{Colors.END}")
        try:
            dbs = self.config.list_databases()
            report("Load config", True, f"{len(dbs)} database(s)")
        except Exception as e:
            report("Load config", False, str(e)[:40])
        
        llms = self.config.list_llm_providers()
        report("LLM providers", len(llms) > 0, f"{len(llms)} provider(s)")
        
        # 2. Database Connectivity
        print(f"\n{Colors.YELLOW}2. Database Connectivity{Colors.END}")
        for db_name in self.config.list_databases():
            try:
                self.agent.switch_database(db_name)
                df = self.agent.run_sql("SELECT 1 as test")
                report(f"Connect to {db_name}", df is not None and not df.empty)
            except Exception as e:
                report(f"Connect to {db_name}", False, str(e)[:40])
        
        # Restore original DB
        if original_db:
            self.agent.switch_database(original_db)
        
        # 3. Schema Training
        print(f"\n{Colors.YELLOW}3. Schema Training{Colors.END}")
        for db_name in self.config.list_databases():
            try:
                is_trained = self.agent.is_trained(db_name)
                if is_trained:
                    cached = self.agent._schema_cache.get(db_name)
                    tables = cached.get("table_count", 0) if cached else 0
                    report(f"Schema for {db_name}", True, f"{tables} tables cached")
                else:
                    # Try to train
                    stats = self.agent.train_schema(db_name)
                    report(f"Train {db_name}", True, f"{stats['tables']} tables")
            except Exception as e:
                report(f"Schema for {db_name}", False, str(e)[:40])
        
        # 4. LLM Connectivity
        print(f"\n{Colors.YELLOW}4. LLM Connectivity{Colors.END}")
        test_prompt = "Reply with exactly: OK"
        for llm_name in self.config.list_llm_providers():
            try:
                self.agent.switch_llm(llm_name)
                response = self.agent.generate(test_prompt, {"schema": ""})
                if response and response.text:
                    reply = response.text.strip()[:20]
                    report(f"LLM {llm_name}", True, f'"{reply}"')
                else:
                    report(f"LLM {llm_name}", False, "No response")
            except Exception as e:
                report(f"LLM {llm_name}", False, str(e)[:40])
        
        # Restore original LLM
        if original_llm:
            self.agent.switch_llm(original_llm)
        
        # 5. SQL Generation
        print(f"\n{Colors.YELLOW}5. SQL Generation{Colors.END}")
        try:
            sql = await self.agent.generate_sql_async("count all records in the first table", "sql")
            has_select = sql and "SELECT" in sql.upper()
            report("Generate SELECT", has_select, sql[:40] if sql else "No SQL")
        except Exception as e:
            report("Generate SELECT", False, str(e)[:40])
        
        try:
            sql = await self.agent.generate_sql_async("create a view for active records", "ddl")
            has_create = sql and "CREATE" in sql.upper()
            report("Generate DDL", has_create, sql[:40] if sql else "No SQL")
        except Exception as e:
            report("Generate DDL", False, str(e)[:40])
        
        # 6. SQL Execution
        print(f"\n{Colors.YELLOW}6. SQL Execution{Colors.END}")
        try:
            df = self.agent.run_sql("SELECT 1 as smoke_test, NOW() as timestamp")
            report("Execute SQL", df is not None and not df.empty, f"{len(df)} row(s)")
        except Exception as e:
            report("Execute SQL", False, str(e)[:40])
        
        try:
            df = self.agent.run_sql("SHOW TABLES")
            count = len(df) if df is not None else 0
            report("SHOW TABLES", count > 0, f"{count} table(s)")
        except Exception as e:
            report("SHOW TABLES", False, str(e)[:40])
        
        # 7. Mode Switching
        print(f"\n{Colors.YELLOW}7. Mode Switching{Colors.END}")
        for mode in ["sql", "ddl", "crud"]:
            try:
                old_mode = self.mode
                self.mode = mode
                report(f"Switch to {mode} mode", self.mode == mode)
                self.mode = old_mode
            except Exception as e:
                report(f"Switch to {mode} mode", False, str(e)[:40])
        
        # 8. Database Switching
        print(f"\n{Colors.YELLOW}8. Database Switching{Colors.END}")
        for db_name in self.config.list_databases():
            try:
                self.agent.switch_database(db_name)
                report(f"Switch to {db_name}", self.agent.current_database == db_name)
            except Exception as e:
                report(f"Switch to {db_name}", False, str(e)[:40])
        
        # Restore
        if original_db:
            self.agent.switch_database(original_db)
        
        # 9. Clipboard (optional)
        print(f"\n{Colors.YELLOW}9. Utilities{Colors.END}")
        try:
            copied = self._copy_to_clipboard("smoke test")
            report("Clipboard", copied or not self.clipboard, "disabled" if not self.clipboard else "")
        except Exception as e:
            report("Clipboard", False, str(e)[:40])
        
        report("Exports dir", self.exports_dir.exists(), str(self.exports_dir))
        
        # Summary
        print(f"\n{Colors.CYAN}{'='*60}{Colors.END}")
        total = results["passed"] + results["failed"]
        pct = (results["passed"] / total * 100) if total > 0 else 0
        
        if results["failed"] == 0:
            print(f"{Colors.GREEN}All tests passed!{Colors.END} ({results['passed']}/{total})")
        else:
            print(f"{Colors.YELLOW}Results:{Colors.END} {results['passed']} passed, {results['failed']} failed ({pct:.0f}%)")
        
        print(f"{Colors.CYAN}{'='*60}{Colors.END}\n")
    
    def print_banner(self):
        """Print welcome banner."""
        print(f"""
{Colors.CYAN}╔══════════════════════════════════════════════════════════════╗
║              {Colors.BOLD}Daiby - AI Database Assistant{Colors.END}{Colors.CYAN}                 ║
║                Multi-LLM Text-to-SQL Tool                      ║
╚══════════════════════════════════════════════════════════════╝{Colors.END}
""")
        self.print_help()
    
    def print_help(self):
        """Print available commands."""
        dry_run_status = f"{Colors.YELLOW}ON{Colors.END}" if self.dry_run else "OFF"
        auto_exec_status = f"{Colors.YELLOW}ON{Colors.END}" if self.auto_execute else "OFF"
        clip_status = f"{Colors.GREEN}ON{Colors.END}" if self.clipboard else "OFF"
        
        print(f"""
{Colors.YELLOW}Database & LLM Selection:{Colors.END}
  {Colors.GREEN}@use <db>{Colors.END}     - Switch to named database
  {Colors.GREEN}@llm <name>{Colors.END}   - Switch LLM provider (gemini, openai, azure, anthropic)
  {Colors.GREEN}@databases{Colors.END}    - List available databases
  {Colors.GREEN}@providers{Colors.END}    - List available LLM providers

{Colors.YELLOW}Operation Modes:{Colors.END}
  {Colors.GREEN}@sql{Colors.END}          - SQL mode: Generate SELECT queries (default)
  {Colors.GREEN}@ddl{Colors.END}          - DDL mode: Generate CREATE/ALTER/DROP statements
  {Colors.GREEN}@crud{Colors.END}         - CRUD mode: Generate INSERT/UPDATE/DELETE

{Colors.YELLOW}Safety Features:{Colors.END}
  {Colors.GREEN}@dry-run{Colors.END}      - Toggle dry-run mode (currently: {dry_run_status})
  {Colors.GREEN}@execute{Colors.END}      - Toggle auto-execute (currently: {auto_exec_status})
  {Colors.GREEN}@clipboard{Colors.END}    - Toggle clipboard copy (currently: {clip_status})
  {Colors.GREEN}@verbose{Colors.END}      - Toggle verbose mode

{Colors.YELLOW}Schema Training:{Colors.END}
  {Colors.GREEN}@train [db]{Colors.END}   - Train/index schema (auto-runs on first use)
  {Colors.GREEN}@refresh [db]{Colors.END} - Force refresh schema from database
  {Colors.GREEN}@status{Colors.END}       - Show training status for all databases

{Colors.YELLOW}Exploration:{Colors.END}
  {Colors.GREEN}@schema{Colors.END}       - Show current database schema
  {Colors.GREEN}@tables{Colors.END}       - List tables in current database
  {Colors.GREEN}@test{Colors.END}         - Test database and LLM connectivity
  {Colors.GREEN}@smoke{Colors.END}        - Run comprehensive smoke test
  {Colors.GREEN}@help{Colors.END}         - Show this help
  {Colors.GREEN}@examples{Colors.END}     - Show usage examples
  {Colors.GREEN}@quit{Colors.END}         - Exit

Type {Colors.CYAN}@examples{Colors.END} for usage examples.
""")
    
    def print_examples(self):
        """Print usage examples."""
        print(f"""
{Colors.BOLD}═══════════════════════════════════════════════════════════════════{Colors.END}
{Colors.BOLD}                    USAGE EXAMPLES{Colors.END}
{Colors.BOLD}═══════════════════════════════════════════════════════════════════{Colors.END}

{Colors.YELLOW}━━━ GENERATING SQL (default - no execution) ━━━{Colors.END}

  {Colors.CYAN}join accounts and contacts{Colors.END}
  {Colors.CYAN}select users with their roles{Colors.END}
  {Colors.CYAN}count of records grouped by type{Colors.END}

  SQL is generated but NOT executed unless you ask for results.

{Colors.YELLOW}━━━ GETTING RESULTS (triggers execution) ━━━{Colors.END}

  {Colors.CYAN}show me all active accounts{Colors.END}
  {Colors.CYAN}list the top 10 records{Colors.END}
  {Colors.CYAN}how many users are there{Colors.END}

  Keywords like "show me", "list", "how many" trigger execution.

{Colors.YELLOW}━━━ CSV EXPORT ━━━{Colors.END}

  {Colors.CYAN}export csv all accounts{Colors.END}
  {Colors.CYAN}save csv users by role{Colors.END}

  Creates a meaningfully-named file in exports directory.

{Colors.YELLOW}━━━ MARKDOWN TABLE OUTPUT ━━━{Colors.END}

  {Colors.CYAN}as markdown table accounts by type{Colors.END}
  {Colors.CYAN}show as markdown all users{Colors.END}

{Colors.YELLOW}━━━ DDL MODE ━━━{Colors.END}

  {Colors.CYAN}@ddl create a view for active accounts{Colors.END}
  {Colors.CYAN}@ddl add an index on name column{Colors.END}

{Colors.YELLOW}━━━ SWITCHING DATABASES ━━━{Colors.END}

  {Colors.CYAN}@use production{Colors.END}   - Switch to production database
  {Colors.CYAN}@use staging{Colors.END}      - Switch to staging database
  {Colors.CYAN}@databases{Colors.END}        - List available databases

{Colors.YELLOW}━━━ SWITCHING LLM PROVIDERS ━━━{Colors.END}

  {Colors.CYAN}@llm gemini{Colors.END}       - Use Google Gemini
  {Colors.CYAN}@llm openai{Colors.END}       - Use OpenAI GPT
  {Colors.CYAN}@llm anthropic{Colors.END}    - Use Anthropic Claude
  {Colors.CYAN}@providers{Colors.END}        - List available providers

{Colors.YELLOW}━━━ CLIPBOARD (auto-copy SQL/DDL) ━━━{Colors.END}

  SQL and DDL are automatically copied to clipboard by default.
  
  {Colors.CYAN}@clipboard{Colors.END}  - Toggle clipboard auto-copy on/off
  {Colors.CYAN}@clip{Colors.END}       - Same as @clipboard (shortcut)
""")
    
    def get_prompt(self) -> str:
        """Build the colorized prompt."""
        db_indicator = f"{Colors.CYAN}{self.current_db or 'none'}{Colors.END}"
        
        mode_colors = {"sql": Colors.GREEN, "ddl": Colors.BLUE, "crud": Colors.RED}
        mode_color = mode_colors.get(self.mode, Colors.GREEN)
        mode_indicator = f"{mode_color}{self.mode}{Colors.END}"
        
        llm_indicator = f"{Colors.YELLOW}{self.current_llm or 'none'}{Colors.END}"
        
        return f"{Colors.BOLD}[{db_indicator}:{mode_indicator}:{llm_indicator}{Colors.BOLD}]{Colors.END} > "
    
    def handle_command(self, user_input: str) -> bool:
        """Handle @ commands. Returns True if command was handled."""
        cmd = user_input.strip().lower()
        parts = cmd.split(maxsplit=1)
        base_cmd = parts[0]
        arg = parts[1] if len(parts) > 1 else None
        
        if base_cmd == "@use" and arg:
            try:
                self.agent.switch_database(arg)
                self.current_db = arg
                self._save_state()
                print(f"{Colors.GREEN}✓ Switched to database: {arg}{Colors.END}")
            except ValueError as e:
                print(f"{Colors.RED}Error: {e}{Colors.END}")
            return True
        
        elif base_cmd == "@llm" and arg:
            try:
                self.agent.switch_llm(arg)
                self.current_llm = arg
                self._save_state()
                print(f"{Colors.GREEN}✓ Switched to LLM: {arg}{Colors.END}")
            except ValueError as e:
                print(f"{Colors.RED}Error: {e}{Colors.END}")
            return True
        
        elif base_cmd == "@databases":
            dbs = self.config.list_databases()
            print(f"\n{Colors.YELLOW}Available databases:{Colors.END}")
            for db in dbs:
                marker = " (current)" if db == self.current_db else ""
                print(f"  {Colors.GREEN}{db}{Colors.END}{marker}")
            return True
        
        elif base_cmd == "@providers":
            providers = self.config.list_llm_providers()
            print(f"\n{Colors.YELLOW}Available LLM providers:{Colors.END}")
            for p in providers:
                marker = " (current)" if p == self.current_llm else ""
                print(f"  {Colors.GREEN}{p}{Colors.END}{marker}")
            return True
        
        elif base_cmd == "@sql":
            self.mode = "sql"
            self._save_state()
            print(f"{Colors.GREEN}✓ Mode: SQL (SELECT queries){Colors.END}")
            return True
        
        elif base_cmd == "@ddl":
            self.mode = "ddl"
            self._save_state()
            print(f"{Colors.BLUE}✓ Mode: DDL (CREATE/ALTER/DROP){Colors.END}")
            return True
        
        elif base_cmd == "@crud":
            self.mode = "crud"
            self._save_state()
            print(f"{Colors.RED}✓ Mode: CRUD (INSERT/UPDATE/DELETE){Colors.END}")
            return True
        
        elif base_cmd in ("@dry-run", "@dryrun"):
            self.dry_run = not self.dry_run
            status = "ON" if self.dry_run else "OFF"
            print(f"{Colors.YELLOW}✓ Dry-run mode: {status}{Colors.END}")
            return True
        
        elif base_cmd == "@execute":
            self.auto_execute = not self.auto_execute
            status = "ON" if self.auto_execute else "OFF"
            print(f"{Colors.YELLOW}✓ Auto-execute: {status}{Colors.END}")
            return True
        
        elif base_cmd == "@verbose":
            self.verbose = not self.verbose
            if self.verbose:
                warnings.filterwarnings('default')
                print(f"{Colors.YELLOW}✓ Verbose mode: ON{Colors.END}")
            else:
                warnings.filterwarnings('ignore')
                print(f"{Colors.GREEN}✓ Verbose mode: OFF{Colors.END}")
            return True
        
        elif base_cmd in ("@clipboard", "@clip", "@cb"):
            self.clipboard = not self.clipboard
            self._save_state()
            status = "ON" if self.clipboard else "OFF"
            print(f"{Colors.GREEN}✓ Clipboard: {status}{Colors.END}")
            return True
        
        elif base_cmd == "@schema":
            try:
                schema = self.agent.get_schema()
                print(f"\n{Colors.CYAN}Schema for {self.current_db}:{Colors.END}\n")
                print(schema[:5000] + "..." if len(schema) > 5000 else schema)
            except Exception as e:
                print(f"{Colors.RED}Error getting schema: {e}{Colors.END}")
            return True
        
        elif base_cmd == "@tables":
            try:
                df = self.agent.run_sql("SHOW TABLES")
                if df is not None and not df.empty:
                    print(f"\n{Colors.GREEN}Tables in {self.current_db}:{Colors.END}")
                    print(tabulate(df, headers='keys', tablefmt='simple', showindex=False))
            except Exception as e:
                print(f"{Colors.RED}Error: {e}{Colors.END}")
            return True
        
        elif base_cmd == "@test":
            self._test_connectivity()
            return True
        
        elif base_cmd == "@train":
            db = arg if arg else self.current_db
            if db:
                print(f"{Colors.CYAN}Training schema for {db}...{Colors.END}")
                try:
                    stats = self.agent.train_schema(db, verbose=True)
                    print(f"{Colors.GREEN}✓ Trained: {stats['tables']} tables, {stats['schema_size']} chars{Colors.END}")
                except Exception as e:
                    print(f"{Colors.RED}Error: {e}{Colors.END}")
            else:
                print(f"{Colors.YELLOW}No database selected{Colors.END}")
            return True
        
        elif base_cmd == "@refresh":
            db = arg if arg else self.current_db
            if db:
                print(f"{Colors.CYAN}Refreshing schema for {db}...{Colors.END}")
                try:
                    stats = self.agent.refresh_schema(db)
                    print(f"{Colors.GREEN}✓ Refreshed: {stats['tables']} tables, {stats['schema_size']} chars{Colors.END}")
                except Exception as e:
                    print(f"{Colors.RED}Error: {e}{Colors.END}")
            else:
                print(f"{Colors.YELLOW}No database selected{Colors.END}")
            return True
        
        elif base_cmd == "@status":
            status = self.agent.get_training_status()
            print(f"\n{Colors.YELLOW}Training Status:{Colors.END}")
            for db_name, info in status.items():
                marker = " (current)" if db_name == self.current_db else ""
                if info.get("trained"):
                    mem = "in-memory" if info.get("in_memory") else "cached"
                    print(f"  {Colors.GREEN}✓{Colors.END} {db_name}{marker}: {info['tables']} tables ({mem})")
                else:
                    print(f"  {Colors.RED}✗{Colors.END} {db_name}{marker}: Not trained")
            print()
            return True
        
        elif base_cmd == "@smoke":
            return "@smoke"
        
        elif base_cmd == "@help":
            self.print_help()
            return True
        
        elif base_cmd == "@examples":
            self.print_examples()
            return True
        
        elif base_cmd in ("@quit", "@exit", "@q"):
            print(f"{Colors.CYAN}Goodbye!{Colors.END}")
            sys.exit(0)
        
        return False
    
    def _wants_results(self, query: str) -> tuple:
        """Detect if user wants results executed."""
        q = query.lower()
        
        if any(phrase in q for phrase in ['markdown table', 'md table', 'as markdown']):
            return True, 'markdown'
        
        if any(phrase in q for phrase in ['to csv', 'as csv', 'csv file', 'export csv', 'save csv']):
            return True, 'csv'
        
        result_keywords = [
            'run ', 'execute', 'show me', 'give me', 'get me', 
            'fetch', 'return results', 'show results', 'list all', 
            'list the', 'what are', 'how many', 'count of', 'display'
        ]
        if any(kw in q for kw in result_keywords):
            return True, 'table'
        
        return False, 'none'
    
    def _generate_filename(self, query: str, sql: str) -> str:
        """Generate a meaningful filename for exports."""
        tables = re.findall(r'\bFROM\s+(\w+)', sql, re.IGNORECASE)
        tables += re.findall(r'\bJOIN\s+(\w+)', sql, re.IGNORECASE)
        
        q = query.lower()
        concepts = []
        if 'count' in q:
            concepts.append('count')
        if 'list' in q:
            concepts.append('list')
        if len(tables) > 1:
            concepts.append('joined')
        
        parts = []
        if tables:
            parts.append('_'.join(tables[:2]))
        if concepts:
            parts.append('_'.join(concepts[:2]))
        
        if parts:
            filename = '_'.join(parts)
        else:
            words = re.findall(r'\w+', query)[:4]
            filename = '_'.join(words) if words else 'query_results'
        
        filename = re.sub(r'[^\w_]', '', filename)[:50]
        return f"{filename}.csv"
    
    async def execute_sql(self, sql: str, output_format: str = 'table', query: str = '') -> None:
        """Execute SQL and display/save results."""
        self.query_count += 1
        if self.query_count > self.max_queries_per_session:
            print(f"{Colors.RED}Rate limit reached. Please restart.{Colors.END}")
            return
        
        if self.dry_run:
            print(f"\n{Colors.YELLOW}[DRY RUN] Would execute:{Colors.END}")
            print(f"  {sql}")
            return
        
        try:
            df = await self.agent.run_sql_async(sql)
            if df is not None and not df.empty:
                row_count = len(df)
                
                if output_format == 'csv':
                    filename = self._generate_filename(query or sql, sql)
                    filepath = self.exports_dir / filename
                    df.to_csv(filepath, index=False)
                    print(f"\n{Colors.GREEN}✓ Saved {row_count} rows to: {filepath}{Colors.END}")
                
                elif output_format == 'markdown':
                    print(f"\n{Colors.GREEN}Results ({row_count} rows):{Colors.END}\n")
                    print(tabulate(df.head(100), headers='keys', tablefmt='github', showindex=False))
                    if row_count > 100:
                        print(f"\n{Colors.YELLOW}... showing first 100 of {row_count} rows{Colors.END}")
                
                else:
                    print(f"\n{Colors.GREEN}Results ({row_count} rows):{Colors.END}")
                    print(tabulate(df.head(50), headers='keys', tablefmt='rounded_grid', showindex=False))
                    if row_count > 50:
                        print(f"{Colors.YELLOW}... showing first 50 of {row_count} rows{Colors.END}")
            else:
                print(f"{Colors.GREEN}✓ Executed (no results){Colors.END}")
        except Exception as e:
            print(f"{Colors.RED}Error executing SQL: {e}{Colors.END}")
    
    async def handle_query(self, user_input: str) -> None:
        """Handle a natural language query."""
        # Check for inline mode prefixes
        if user_input.startswith("@ddl "):
            self.mode = "ddl"
            user_input = user_input[5:]
        elif user_input.startswith("@crud "):
            self.mode = "crud"
            user_input = user_input[6:]
        elif user_input.startswith("@sql "):
            self.mode = "sql"
            user_input = user_input[5:]
        
        wants_results, output_format = self._wants_results(user_input)
        
        if self.verbose:
            print(f"\n{Colors.CYAN}Thinking...{Colors.END}")
        
        try:
            sql = await self.agent.generate_sql_async(user_input, self.mode)
            
            if sql:
                print(f"\n{Colors.CYAN}Generated SQL:{Colors.END}")
                print(sql)
                
                if self._copy_to_clipboard(sql):
                    print(f"{Colors.GREEN}📋 Copied to clipboard{Colors.END}")
                
                is_destructive = self.agent.is_destructive(sql)
                
                if self.mode == "ddl":
                    if self.interactive:
                        choice = input(f"\n{Colors.CYAN}Execute DDL? (y/n): {Colors.END}").strip().lower()
                        if choice == 'y':
                            await self.execute_sql(sql)
                
                elif self.mode == "crud" or is_destructive:
                    if self.interactive and wants_results:
                        choice = input(f"\n{Colors.RED}Execute destructive SQL? (y/n): {Colors.END}").strip().lower()
                        if choice == 'y':
                            await self.execute_sql(sql)
                    elif wants_results:
                        print(f"{Colors.YELLOW}⚠ Destructive SQL requires interactive mode{Colors.END}")
                
                else:
                    if wants_results:
                        await self.execute_sql(sql, output_format=output_format, query=user_input)
                    elif self.interactive:
                        choice = input(f"\n{Colors.CYAN}Execute? (y/n/csv/md): {Colors.END}").strip().lower()
                        if choice == 'y':
                            await self.execute_sql(sql)
                        elif choice == 'csv':
                            await self.execute_sql(sql, output_format='csv', query=user_input)
                        elif choice == 'md':
                            await self.execute_sql(sql, output_format='markdown', query=user_input)
            else:
                print(f"{Colors.YELLOW}No SQL generated{Colors.END}")
                
        except Exception as e:
            print(f"{Colors.RED}Error: {e}{Colors.END}")
            if self.verbose:
                import traceback
                traceback.print_exc()
    
    async def run_interactive(self):
        """Run interactive REPL."""
        self.print_banner()
        
        while True:
            try:
                user_input = input(self.get_prompt()).strip()
                
                if not user_input:
                    continue
                
                if user_input.startswith("@"):
                    result = self.handle_command(user_input)
                    if result == "@smoke":
                        await self._run_smoke_test()
                        continue
                    elif result:
                        continue
                
                await self.handle_query(user_input)
                
            except KeyboardInterrupt:
                print(f"\n{Colors.CYAN}Use @quit to exit{Colors.END}")
            except EOFError:
                print(f"\n{Colors.CYAN}Goodbye!{Colors.END}")
                break
    
    async def run_single_query(self, query: str):
        """Run a single query and exit."""
        await self.handle_query(query)


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Daiby - AI Database Assistant")
    parser.add_argument("query", nargs="?", help="Single query to run")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    parser.add_argument("-c", "--config", help="Path to daiby.yaml config file")
    
    args = parser.parse_args()
    
    config = None
    if args.config:
        config = load_config(Path(args.config))
    
    if args.query:
        agent = ChatAgent(config=config, interactive=False, verbose=args.verbose)
        asyncio.run(agent.run_single_query(args.query))
    else:
        agent = ChatAgent(config=config, interactive=True, verbose=args.verbose)
        asyncio.run(agent.run_interactive())


if __name__ == "__main__":
    main()
