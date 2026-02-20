"""
DaiBai Agent - Core AI Database Assistant.

Orchestrates LLM providers and database connections for natural language SQL generation.
"""

import os
import re
import json
import hashlib
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Tuple, Dict, Any, List
from pathlib import Path

import pandas as pd

from .config import Config, load_config, DatabaseConfig, LLMProviderConfig
from ..llm import get_provider_class, create_provider
from ..llm.base import BaseLLMProvider, LLMResponse


class SchemaCache:
    """Persistent schema cache with staleness detection."""
    
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
    
    def _cache_path(self, db_name: str) -> Path:
        return self.cache_dir / f"{db_name}_schema.json"
    
    def get(self, db_name: str) -> Optional[Dict[str, Any]]:
        """Get cached schema data if exists."""
        path = self._cache_path(db_name)
        if path.exists():
            try:
                with open(path, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return None
    
    def save(self, db_name: str, schema: str, table_count: int) -> None:
        """Save schema to cache with metadata."""
        schema_hash = hashlib.md5(schema.encode()).hexdigest()
        data = {
            "schema": schema,
            "table_count": table_count,
            "schema_hash": schema_hash,
            "cached_at": datetime.now().isoformat(),
            "version": 1,
        }
        path = self._cache_path(db_name)
        with open(path, "w") as f:
            json.dump(data, f)
    
    def is_stale(self, db_name: str, current_table_count: int = 0, max_age_hours: int = 24) -> bool:
        """Check if cache is stale based on age (table count is informational only)."""
        cached = self.get(db_name)
        if not cached:
            return True
        
        # Check age
        cached_at = cached.get("cached_at")
        if cached_at:
            try:
                cache_time = datetime.fromisoformat(cached_at)
                if datetime.now() - cache_time > timedelta(hours=max_age_hours):
                    return True
            except ValueError:
                return True
        
        return False
    
    def clear(self, db_name: str) -> None:
        """Clear cache for a database."""
        path = self._cache_path(db_name)
        if path.exists():
            path.unlink()


class DatabaseRunner:
    """Executes SQL against a database connection."""
    
    def __init__(self, config: DatabaseConfig):
        self.config = config
        self._connection = None
    
    def _ensure_connection(self):
        """Ensure database connection is established."""
        if self._connection is None:
            try:
                import mysql.connector
                self._connection = mysql.connector.connect(
                    host=self.config.host,
                    port=self.config.port,
                    database=self.config.database,
                    user=self.config.user,
                    password=self.config.password,
                )
            except ImportError:
                raise ImportError("MySQL support requires mysql-connector-python")
    
    def run_sql(self, sql: str) -> Optional[pd.DataFrame]:
        """Execute SQL and return results as DataFrame."""
        self._ensure_connection()
        
        try:
            cursor = self._connection.cursor(dictionary=True)
            cursor.execute(sql)
            
            # Check if this is a SELECT-like query
            if cursor.description:
                rows = cursor.fetchall()
                return pd.DataFrame(rows)
            else:
                # For INSERT/UPDATE/DELETE, commit and return affected rows
                self._connection.commit()
                return pd.DataFrame([{"affected_rows": cursor.rowcount}])
        except Exception as e:
            self._connection.rollback()
            raise e
    
    async def run_sql_async(self, sql: str) -> Optional[pd.DataFrame]:
        """Async SQL execution (runs sync in executor)."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.run_sql, sql)
    
    def close(self):
        """Close database connection."""
        if self._connection:
            self._connection.close()
            self._connection = None


class DaiBaiAgent:
    """
    DaiBai AI Database Assistant.
    
    Provides natural language to SQL capabilities using configurable LLM providers
    and database connections.
    """
    
    def __init__(self, config: Optional[Config] = None, config_path: Optional[Path] = None, 
                 auto_train: bool = True, verbose: bool = False):
        """
        Initialize the DaiBai agent.
        
        Args:
            config: Pre-loaded Config object
            config_path: Path to daibai.yaml (used if config not provided)
            auto_train: Auto-train schema if not cached or stale
            verbose: Print training messages
        """
        self.config = config or load_config(config_path)
        self.verbose = verbose
        
        # Database runners (lazy initialized)
        self._runners: Dict[str, DatabaseRunner] = {}
        self._current_db: Optional[str] = self.config.default_database
        
        # LLM providers (lazy initialized)
        self._providers: Dict[str, BaseLLMProvider] = {}
        self._current_llm: Optional[str] = self.config.default_llm
        
        # Schema cache (in-memory)
        self._schema_memory: Dict[str, str] = {}
        
        # Persistent schema cache
        self._schema_cache = SchemaCache(self.config.memory_dir / "schemas")
        
        # Auto-train on init if needed
        self._trained_dbs: set = set()
        if auto_train and self._current_db:
            self._ensure_trained(self._current_db)
    
    @property
    def current_database(self) -> Optional[str]:
        """Get current database name."""
        return self._current_db
    
    @property
    def current_llm(self) -> Optional[str]:
        """Get current LLM provider name."""
        return self._current_llm
    
    def _get_runner(self, db_name: Optional[str] = None) -> DatabaseRunner:
        """Get or create database runner."""
        name = db_name or self._current_db
        if not name:
            raise ValueError("No database selected. Use switch_database() first.")
        
        if name not in self._runners:
            db_config = self.config.get_database(name)
            self._runners[name] = DatabaseRunner(db_config)
        
        return self._runners[name]
    
    def _get_provider(self, llm_name: Optional[str] = None) -> BaseLLMProvider:
        """Get or create LLM provider."""
        name = llm_name or self._current_llm
        if not name:
            raise ValueError("No LLM provider selected. Use switch_llm() first.")
        
        if name not in self._providers:
            llm_config = self.config.get_llm(name)
            provider_class = get_provider_class(llm_config.provider_type)
            
            # Build provider kwargs from config
            kwargs = {
                "model": llm_config.model,
                "temperature": llm_config.temperature,
                "max_tokens": llm_config.max_tokens,
            }
            if llm_config.api_key:
                kwargs["api_key"] = llm_config.api_key
            if llm_config.endpoint:
                kwargs["endpoint"] = llm_config.endpoint
            kwargs.update(llm_config.extra)
            
            self._providers[name] = provider_class(**kwargs)
        
        return self._providers[name]
    
    def switch_database(self, db_name: str) -> None:
        """Switch to a different database."""
        if db_name not in self.config.databases:
            raise ValueError(f"Database '{db_name}' not found. Available: {self.config.list_databases()}")
        self._current_db = db_name
    
    def switch_llm(self, llm_name: str) -> None:
        """Switch to a different LLM provider."""
        if llm_name not in self.config.llm_providers:
            raise ValueError(f"LLM '{llm_name}' not found. Available: {self.config.list_llm_providers()}")
        self._current_llm = llm_name
    
    def _get_table_count(self, db_name: Optional[str] = None) -> int:
        """Get current table count from database."""
        try:
            df = self.run_sql("SHOW TABLES", db_name)
            return len(df) if df is not None else 0
        except Exception:
            return 0
    
    def _ensure_trained(self, db_name: str) -> None:
        """Ensure schema is trained/cached for a database."""
        if db_name in self._trained_dbs:
            return
        
        # Check if we have cached schema
        cached = self._schema_cache.get(db_name)
        
        if cached:
            # Check if stale
            current_count = self._get_table_count(db_name)
            if not self._schema_cache.is_stale(db_name, current_count):
                # Use cached schema
                self._schema_memory[db_name] = cached["schema"]
                self._trained_dbs.add(db_name)
                if self.verbose:
                    print(f"Loaded cached schema for {db_name} ({cached['table_count']} tables)")
                return
            else:
                if self.verbose:
                    print(f"Schema cache stale for {db_name} (tables: {cached['table_count']} -> {current_count})")
        
        # Need to train/refresh
        self.train_schema(db_name)
    
    def train_schema(self, db_name: Optional[str] = None, verbose: Optional[bool] = None) -> Dict[str, Any]:
        """
        Train/index the schema for a database.
        
        Args:
            db_name: Database to train (uses current if not specified)
            verbose: Print progress (uses self.verbose if not specified)
        
        Returns:
            Training statistics
        """
        name = db_name or self._current_db
        if not name:
            raise ValueError("No database specified")
        
        show_progress = verbose if verbose is not None else self.verbose
        
        if show_progress:
            print(f"Training schema for {name}...")
        
        # Fetch fresh schema from database
        schema = self._fetch_schema_from_db(name)
        table_count = schema.count("-- Table:")
        
        # Save to persistent cache
        self._schema_cache.save(name, schema, table_count)
        
        # Update in-memory cache
        self._schema_memory[name] = schema
        self._trained_dbs.add(name)
        
        if show_progress:
            print(f"Trained: {table_count} tables, {len(schema)} chars")
        
        return {
            "database": name,
            "tables": table_count,
            "schema_size": len(schema),
        }
    
    def refresh_schema(self, db_name: Optional[str] = None) -> Dict[str, Any]:
        """Force refresh schema for a database."""
        name = db_name or self._current_db
        if name:
            self._trained_dbs.discard(name)
            self._schema_cache.clear(name)
        return self.train_schema(name, verbose=True)
    
    def is_trained(self, db_name: Optional[str] = None) -> bool:
        """Check if database schema is trained."""
        name = db_name or self._current_db
        if not name:
            return False
        return name in self._trained_dbs or self._schema_cache.get(name) is not None
    
    def get_training_status(self) -> Dict[str, Any]:
        """Get training status for all databases."""
        status = {}
        for db_name in self.config.list_databases():
            cached = self._schema_cache.get(db_name)
            if cached:
                status[db_name] = {
                    "trained": True,
                    "tables": cached.get("table_count", 0),
                    "cached_at": cached.get("cached_at"),
                    "in_memory": db_name in self._trained_dbs,
                }
            else:
                status[db_name] = {"trained": False}
        return status
    
    def run_sql(self, sql: str, db_name: Optional[str] = None) -> Optional[pd.DataFrame]:
        """Execute SQL and return results."""
        runner = self._get_runner(db_name)
        return runner.run_sql(sql)
    
    async def run_sql_async(self, sql: str, db_name: Optional[str] = None) -> Optional[pd.DataFrame]:
        """Execute SQL asynchronously."""
        runner = self._get_runner(db_name)
        return await runner.run_sql_async(sql)
    
    def _fetch_schema_from_db(self, db_name: str) -> str:
        """Fetch fresh schema from database."""
        schema_parts = []
        
        # Get tables
        tables_df = self.run_sql("SHOW TABLES", db_name)
        if tables_df is not None and not tables_df.empty:
            table_col = tables_df.columns[0]
            tables = tables_df[table_col].tolist()
            
            for table in tables[:50]:  # Limit to 50 tables
                schema_parts.append(f"\n-- Table: {table}")
                try:
                    create_df = self.run_sql(f"SHOW CREATE TABLE `{table}`", db_name)
                    if create_df is not None and not create_df.empty:
                        # Use iloc[row, col] for positional access
                        create_sql = create_df.iloc[0, 1]  # Second column has CREATE statement
                        schema_parts.append(create_sql)
                except Exception:
                    pass
        
        return "\n".join(schema_parts)
    
    def get_schema(self, db_name: Optional[str] = None, refresh: bool = False) -> str:
        """Get database schema as text (uses cache if available)."""
        name = db_name or self._current_db
        if not name:
            return ""
        
        # Force refresh if requested
        if refresh:
            self.train_schema(name)
        
        # Ensure trained (will use cache if available)
        self._ensure_trained(name)
        
        # Return from in-memory cache
        return self._schema_memory.get(name, "")
    
    def generate(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> LLMResponse:
        """Generate LLM response for a prompt."""
        provider = self._get_provider()
        
        # Build context with schema if not provided
        if context is None:
            context = {}
        if "schema" not in context and self._current_db:
            try:
                context["schema"] = self.get_schema()
            except Exception:
                pass
        
        return provider.generate(prompt, context)
    
    async def generate_async(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> LLMResponse:
        """Generate LLM response asynchronously."""
        provider = self._get_provider()
        
        if context is None:
            context = {}
        if "schema" not in context and self._current_db:
            try:
                context["schema"] = self.get_schema()
            except Exception:
                pass
        
        return await provider.generate_async(prompt, context)
    
    def generate_sql(self, prompt: str, mode: str = "sql") -> str:
        """Generate SQL from natural language.
        
        Args:
            prompt: Natural language description
            mode: 'sql' for SELECT, 'ddl' for CREATE/ALTER, 'crud' for INSERT/UPDATE/DELETE
        
        Returns:
            Generated SQL string
        """
        mode_prompts = {
            "sql": "Generate ONLY a SELECT query for this request.",
            "ddl": "Generate ONLY DDL (CREATE VIEW, CREATE TABLE, ALTER, DROP) for this request. Use CREATE OR REPLACE VIEW when creating views.",
            "crud": "Generate ONLY an INSERT, UPDATE, or DELETE statement for this request. CRITICAL: Always include appropriate WHERE clauses.",
        }
        
        db_name = self._current_db or "unknown"
        
        enhanced_prompt = f"""{mode_prompts.get(mode, mode_prompts['sql'])}
Database: {db_name}

Request: {prompt}

Return the SQL in a ```sql code block. Do not execute it."""
        
        context = {
            "system_prompt": "You are an expert SQL developer. Generate clean, efficient SQL."
        }
        
        response = self.generate(enhanced_prompt, context)
        return response.sql or self._extract_sql(response.text)
    
    async def generate_sql_async(self, prompt: str, mode: str = "sql") -> str:
        """Generate SQL asynchronously."""
        mode_prompts = {
            "sql": "Generate ONLY a SELECT query for this request.",
            "ddl": "Generate ONLY DDL (CREATE VIEW, CREATE TABLE, ALTER, DROP) for this request.",
            "crud": "Generate ONLY an INSERT, UPDATE, or DELETE statement for this request.",
        }
        
        db_name = self._current_db or "unknown"
        
        enhanced_prompt = f"""{mode_prompts.get(mode, mode_prompts['sql'])}
Database: {db_name}

Request: {prompt}

Return the SQL in a ```sql code block. Do not execute it."""
        
        context = {
            "system_prompt": "You are an expert SQL developer. Generate clean, efficient SQL."
        }
        
        response = await self.generate_async(enhanced_prompt, context)
        return response.sql or self._extract_sql(response.text)
    
    def _extract_sql(self, text: str) -> str:
        """Extract SQL from response text."""
        if not text:
            return ""
        
        # Look for SQL in code blocks
        patterns = [
            r'```sql\s*([\s\S]*?)\s*```',
            r'```\s*(SELECT[\s\S]*?)\s*```',
            r'```\s*(INSERT[\s\S]*?)\s*```',
            r'```\s*(UPDATE[\s\S]*?)\s*```',
            r'```\s*(DELETE[\s\S]*?)\s*```',
            r'```\s*(CREATE[\s\S]*?)\s*```',
            r'```\s*(ALTER[\s\S]*?)\s*```',
            r'```\s*(DROP[\s\S]*?)\s*```',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        
        # Look for SQL without code blocks
        sql_keywords = ['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'ALTER', 'DROP']
        for keyword in sql_keywords:
            pattern = rf'({keyword}\s+[\s\S]*?)(;|$)'
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                sql = match.group(1).strip()
                if not sql.endswith(';'):
                    sql += ';'
                return sql
        
        return text
    
    def is_destructive(self, sql: str) -> bool:
        """Check if SQL would modify data or schema."""
        destructive_keywords = ['INSERT', 'UPDATE', 'DELETE', 'DROP', 'TRUNCATE', 'ALTER', 'CREATE']
        sql_upper = sql.upper().strip()
        return any(sql_upper.startswith(kw) for kw in destructive_keywords)
    
    def close(self):
        """Close all database connections."""
        for runner in self._runners.values():
            runner.close()
        self._runners.clear()


# Convenience function for quick usage
def create_agent(config_path: Optional[Path] = None) -> DaiBaiAgent:
    """Create a DaiBaiAgent with configuration from file."""
    return DaiBaiAgent(config_path=config_path)
