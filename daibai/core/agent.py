"""
DaiBai Agent - Core AI Database Assistant.

Orchestrates LLM providers and database connections for natural language SQL generation.
"""

import logging
import os
import re
import json
import time
import hashlib
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Set, Tuple, Dict, Any, List, Callable, Awaitable
from pathlib import Path

import pandas as pd

from .config import Config, load_config, DatabaseConfig, LLMProviderConfig, get_redis_connection_string
from .guardrails import GuardrailPipeline, SQLValidator, SecurityViolation, extract_tables_from_query
from .cache import CacheManager
from .metrics import SchemaPruningMetrics
from .schema import SchemaManager
from ..llm import get_provider_class, create_provider
from ..llm.base import BaseLLMProvider, LLMResponse, SemanticCache, CachedLLMProvider


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
                with open(path, "r", encoding="utf-8") as f:
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
        with open(path, "w", encoding="utf-8") as f:
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
    """Executes SQL against a database connection. All queries pass through SQLValidator."""

    _validator = SQLValidator()

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
    
    def run_sql(
        self,
        sql: str,
        allowed_tables: Optional[Set[str]] = None,
        strict_scope: bool = False,
        execution_mode: str = "read_only",
    ) -> Optional[pd.DataFrame]:
        """Execute SQL and return results as DataFrame. Validates through SQLValidator first."""
        self._validator.validate(
            sql,
            allowed_tables=allowed_tables,
            current_db=self.config.database,
            strict_scope=strict_scope,
            execution_mode=execution_mode,
        )
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
    
    async def run_sql_async(
        self,
        sql: str,
        allowed_tables: Optional[Set[str]] = None,
        strict_scope: bool = False,
        execution_mode: str = "read_only",
    ) -> Optional[pd.DataFrame]:
        """Async SQL execution (runs sync in executor)."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.run_sql(
                sql,
                allowed_tables=allowed_tables,
                strict_scope=strict_scope,
                execution_mode=execution_mode,
            ),
        )
    
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

        # SchemaManager for semantic pruning (lazy per-db)
        self._schema_managers: Dict[str, SchemaManager] = {}
        self._cache_manager: Optional[CacheManager] = None

        # Last allowed_tables from pruned context (for run_sql scope enforcement)
        self._last_allowed_tables: Optional[Set[str]] = None

        # Last sanitized query (for logging/debug; set during generate_sql/generate_sql_async)
        self._last_sanitized_query: Optional[str] = None

        # Usage metrics for schema pruning tuning
        self._pruning_metrics = SchemaPruningMetrics(self.config.memory_dir / "metrics")

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
    
    def _get_cache_manager(self) -> Optional[CacheManager]:
        """Lazy-init CacheManager for schema pruning (Redis + embeddings)."""
        if self._cache_manager is not None:
            return self._cache_manager
        cache_disabled = os.environ.get("DAIBAI_DISABLE_SEMANTIC_CACHE", "").strip().lower() in ("1", "true", "yes")
        redis_url = get_redis_connection_string()
        if cache_disabled or not redis_url:
            return None
        self._cache_manager = CacheManager(connection_string=redis_url)
        return self._cache_manager

    def _get_schema_manager(self, db_name: Optional[str] = None) -> Optional[SchemaManager]:
        """Get or create SchemaManager for a database (for semantic pruning)."""
        name = db_name or self._current_db
        if not name:
            return None
        if name not in self._schema_managers:
            db_config = self.config.get_database(name)
            cache = self._get_cache_manager()
            self._schema_managers[name] = SchemaManager(
                config=db_config,
                cache_manager=cache,
                redis_client=cache._get_client() if cache else None,
            )
        return self._schema_managers[name]

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
            
            provider = provider_class(**kwargs)

            # Always use semantic cache unless explicitly disabled (testing/debug/stand-down)
            cache_disabled = os.environ.get("DAIBAI_DISABLE_SEMANTIC_CACHE", "").strip().lower() in ("1", "true", "yes")
            redis_url = get_redis_connection_string()
            if not cache_disabled and redis_url:
                cache = SemanticCache(connection_string=redis_url)
                provider = CachedLLMProvider(provider, cache)

            self._providers[name] = provider
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

        # Index for semantic pruning (Redis + embeddings)
        sm = self._get_schema_manager(name)
        if sm:
            try:
                indexed = sm.index_schema(schema_name=name, force=True)
                if show_progress and indexed:
                    print(f"Indexed {indexed} tables for semantic search")
            except Exception:
                pass

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
    
    def get_schema_pruning_stats(self) -> Dict[str, Any]:
        """
        Get usage metrics for schema pruning (depth/scope over time).
        Use suggested_limit to tune SCHEMA_VECTOR_LIMIT in .env.
        """
        return self._pruning_metrics.get_stats()

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
    
    def run_sql(
        self,
        sql: str,
        db_name: Optional[str] = None,
        allowed_tables: Optional[Set[str]] = None,
        strict_scope: bool = False,
        execution_mode: str = "read_only",
    ) -> Optional[pd.DataFrame]:
        """Execute SQL and return results. Validates through SQLValidator first.
        
        When allowed_tables is not provided, uses _last_allowed_tables from the most
        recent generate_sql/generate_sql_async (pruned context scope enforcement).
        Records usage metrics for SCHEMA_VECTOR_LIMIT tuning.
        """
        scope = allowed_tables if allowed_tables is not None else self._last_allowed_tables
        runner = self._get_runner(db_name)
        try:
            result = runner.run_sql(
                sql,
                allowed_tables=scope,
                strict_scope=strict_scope,
                execution_mode=execution_mode,
            )
            self._record_pruning_metrics(sql, scope)
            return result
        except SecurityViolation as e:
            if e.layer == "scope":
                self._pruning_metrics.record_scope_violation()
            raise

    async def run_sql_async(
        self,
        sql: str,
        db_name: Optional[str] = None,
        allowed_tables: Optional[Set[str]] = None,
        strict_scope: bool = False,
        execution_mode: str = "read_only",
    ) -> Optional[pd.DataFrame]:
        """Execute SQL asynchronously. Validates through SQLValidator first.
        
        Uses _last_allowed_tables from pruned context when allowed_tables not provided.
        Records usage metrics for SCHEMA_VECTOR_LIMIT tuning.
        """
        scope = allowed_tables if allowed_tables is not None else self._last_allowed_tables
        runner = self._get_runner(db_name)
        try:
            result = await runner.run_sql_async(
                sql,
                allowed_tables=scope,
                strict_scope=strict_scope,
                execution_mode=execution_mode,
            )
            self._record_pruning_metrics(sql, scope)
            return result
        except SecurityViolation as e:
            if e.layer == "scope":
                self._pruning_metrics.record_scope_violation()
            raise

    def _record_pruning_metrics(self, sql: str, allowed_tables: Optional[Set[str]]) -> None:
        """Record schema pruning metrics after successful execution."""
        tables_in_context = len(allowed_tables) if allowed_tables else 0
        tables_in_query = len(extract_tables_from_query(sql))
        self._pruning_metrics.record_success(
            tables_in_context=tables_in_context,
            tables_in_query=tables_in_query,
        )
    
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
    
    def _extract_table_names_from_ddl(self, ddl_strings: List[str]) -> Set[str]:
        """Extract table names from DDL strings (format: '-- Table: tablename')."""
        tables: Set[str] = set()
        for ddl in ddl_strings:
            for m in re.finditer(r"-- Table:\s*(\w+)", ddl, re.IGNORECASE):
                tables.add(m.group(1))
        return tables

    def extract_missing_tables(self, sql: str) -> Set[str]:
        """
        Extract tables from SQL that were not in the last allowed scope.
        Used for recovery when SecurityViolation (scope) occurs from overzealous pruning.
        """
        tables_in_sql = extract_tables_from_query(sql)
        allowed = self._last_allowed_tables or set()
        # Normalize for case-insensitive comparison
        allowed_norm = {t.lower() for t in allowed}
        return {t for t in tables_in_sql if t.lower() not in allowed_norm}

    def _get_pruned_schema(
        self,
        prompt: str,
        db_name: Optional[str] = None,
        force_tables: Optional[Set[str]] = None,
    ) -> Tuple[str, Optional[Set[str]]]:
        """
        Get schema pruned by semantic relevance to the prompt.
        Returns (schema_text, allowed_tables). If pruning unavailable, returns (full_schema, None).
        When force_tables is provided, appends DDL for missing tables via discover_schema.
        """
        name = db_name or self._current_db
        if not name:
            return "", None

        self._ensure_trained(name)
        sm = self._get_schema_manager(name)
        if sm:
            try:
                ddl_list = sm.search_schema_v1(query=prompt, schema_name=name)
                if ddl_list:
                    pruned = "\n".join(ddl_list)
                    allowed = self._extract_table_names_from_ddl(ddl_list)
                    # Inject DDL for force_tables (overzealous prune correction)
                    if force_tables:
                        allowed_lower = {t.lower() for t in allowed}
                        missing = {t for t in force_tables if t.lower() not in allowed_lower}
                        if missing:
                            all_ddls = sm.discover_schema(name)
                            if all_ddls:
                                all_ddls_lower_keys = {k.lower(): v for k, v in all_ddls.items()}
                                for m_table in missing:
                                    if m_table.lower() in all_ddls_lower_keys:
                                        pruned += f"\n{all_ddls_lower_keys[m_table.lower()]}"
                                        # Preserve original casing from all_ddls
                                        orig_key = next(k for k in all_ddls if k.lower() == m_table.lower())
                                        allowed.add(orig_key)
                    return pruned, allowed
            except Exception:
                pass

        # Fallback: full schema, no scope restriction
        full = self.get_schema(name)
        return full, None

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
    
    def generate_sql(
        self,
        prompt: str,
        mode: str = "sql",
        force_tables: Optional[Set[str]] = None,
        execution_mode: str = "read_only",
    ) -> str:
        """Generate SQL from natural language.
        
        Uses semantic schema pruning: only the most relevant tables (Top-K by
        SCHEMA_VECTOR_LIMIT) are injected into the LLM context. When Redis +
        embeddings are available, token usage drops significantly on large databases.
        
        Args:
            prompt: Natural language description
            mode: 'sql' for SELECT, 'ddl' for CREATE/ALTER, 'crud' for INSERT/UPDATE/DELETE
        
        Returns:
            Generated SQL string
        """
        GuardrailPipeline.validate_prompt(prompt, execution_mode=execution_mode)
        sanitized = GuardrailPipeline.sanitize_query_sync(prompt, self.generate)
        self._last_sanitized_query = sanitized
        mode_prompts = {
            "sql": "Generate ONLY a SELECT query for this request.",
            "ddl": "Generate ONLY DDL (CREATE VIEW, CREATE TABLE, ALTER, DROP) for this request. Use CREATE OR REPLACE VIEW when creating views.",
            "crud": "Generate ONLY an INSERT, UPDATE, or DELETE statement for this request. CRITICAL: Always include appropriate WHERE clauses.",
        }

        db_name = self._current_db or "unknown"
        pruned_schema, allowed_tables = self._get_pruned_schema(
            sanitized, db_name=db_name, force_tables=force_tables
        )
        if allowed_tables is None:
            allowed_tables = set()

        self._last_allowed_tables = allowed_tables

        # Fetch table list from index for LLM grounding (no DB hit)
        table_list_str = ""
        sm = self._get_schema_manager(db_name)
        if sm:
            try:
                table_names = sm.get_table_names_from_index(schema_name=db_name)
                if table_names:
                    table_list_str = ", ".join(table_names)
            except Exception:
                pass

        enhanced_prompt = f"""{mode_prompts.get(mode, mode_prompts['sql'])}
Database: {db_name}

Request: {sanitized}

Return the SQL in a ```sql code block. Do not execute it."""

        system_prompt = (
            "You are an expert SQL developer. Generate clean, efficient SQL. "
        )
        if table_list_str:
            system_prompt += (
                f"The active database contains the following tables: {table_list_str}. "
                "Use this for counting tables or identifying schema scope. "
            )
        system_prompt += (
            "You may query information_schema or pg_catalog if you need deeper metadata than the provided DDLs."
        )
        if allowed_tables:
            system_prompt += f" You may ONLY query these tables: {', '.join(sorted(allowed_tables))}."

        context = {
            "system_prompt": system_prompt,
            "schema": pruned_schema,
            "allowed_tables": allowed_tables,
        }

        response = self.generate(enhanced_prompt, context)
        return response.sql or self._extract_sql(response.text)
    
    async def generate_sql_async(
        self,
        prompt: str,
        mode: str = "sql",
        history: Optional[List[Dict[str, Any]]] = None,
        force_tables: Optional[Set[str]] = None,
        execution_mode: str = "read_only",
        trace_callback: Optional[Callable[..., Awaitable[None]]] = None,
    ) -> str:
        """Generate SQL asynchronously. Optionally pass conversation history for context.
        
        Uses semantic schema pruning when Redis + embeddings are available.
        """
        GuardrailPipeline.validate_prompt(prompt, execution_mode=execution_mode)

        if trace_callback:
            await trace_callback(step_name="Query Sanitization", status="running", step_id="query-sanitization")
        sanitize_start = time.perf_counter()
        sanitized = await GuardrailPipeline.sanitize_query(prompt, self.generate_async)
        self._last_sanitized_query = sanitized
        if trace_callback:
            sanitize_ms = (time.perf_counter() - sanitize_start) * 1000
            await trace_callback(
                step_name="Query Sanitization",
                status="success",
                duration_ms=sanitize_ms,
                input_data=prompt,
                output_data=sanitized,
                step_id="query-sanitization",
            )
        mode_prompts = {
            "sql": "Generate ONLY a SELECT query for this request.",
            "ddl": "Generate ONLY DDL (CREATE VIEW, CREATE TABLE, ALTER, DROP) for this request.",
            "crud": "Generate ONLY an INSERT, UPDATE, or DELETE statement for this request.",
        }

        db_name = self._current_db or "unknown"
        if trace_callback:
            await trace_callback(
                step_name="Semantic Pruning",
                status="running",
                tech="MiniLM-L6",
                step_id="semantic-pruning",
            )
        prune_start = time.perf_counter()
        pruned_schema, allowed_tables = self._get_pruned_schema(
            sanitized, db_name=db_name, force_tables=force_tables
        )
        if trace_callback:
            prune_ms = (time.perf_counter() - prune_start) * 1000
            await trace_callback(
                step_name="Semantic Pruning",
                status="success",
                tech="MiniLM-L6",
                duration_ms=prune_ms,
                input_data=sanitized,
                output_data={"allowed_tables": sorted(allowed_tables) if allowed_tables else []},
                step_id="semantic-pruning",
            )
        if allowed_tables is None:
            allowed_tables = set()

        self._last_allowed_tables = allowed_tables

        # Fetch table list from index for LLM grounding (no DB hit)
        table_list_str = ""
        sm = self._get_schema_manager(db_name)
        if sm:
            try:
                table_names = sm.get_table_names_from_index(schema_name=db_name)
                if table_names:
                    table_list_str = ", ".join(table_names)
            except Exception:
                pass

        enhanced_prompt = f"""{mode_prompts.get(mode, mode_prompts['sql'])}
Database: {db_name}

Request: {sanitized}

Return the SQL in a ```sql code block. Do not execute it."""

        system_prompt = (
            "You are an expert SQL developer. Generate clean, efficient SQL. "
        )
        if table_list_str:
            system_prompt += (
                f"The active database contains the following tables: {table_list_str}. "
                "Use this for counting tables or identifying schema scope. "
            )
        system_prompt += (
            "You may query information_schema or pg_catalog if you need deeper metadata than the provided DDLs."
        )
        if allowed_tables:
            system_prompt += f" You may ONLY query these tables: {', '.join(sorted(allowed_tables))}."

        context: Dict[str, Any] = {
            "system_prompt": system_prompt,
            "schema": pruned_schema,
            "allowed_tables": allowed_tables,
        }
        if history:
            context["messages"] = [{"role": m.get("role"), "content": m.get("content", "")} for m in history]
        if trace_callback:
            context["_trace_callback"] = trace_callback

        llm_tech = self._current_llm or "LLM"
        if trace_callback:
            await trace_callback(
                step_name="SQL Generation",
                status="running",
                tech=llm_tech,
                step_id="sql-generation",
            )
        gen_start = time.perf_counter()
        response = await self.generate_async(enhanced_prompt, context)
        sql_result = response.sql or self._extract_sql(response.text)
        if trace_callback:
            gen_ms = (time.perf_counter() - gen_start) * 1000
            await trace_callback(
                step_name="SQL Generation",
                status="success",
                tech=llm_tech,
                duration_ms=gen_ms,
                input_data=sanitized,
                output_data=sql_result,
                step_id="sql-generation",
            )
        return sql_result

    async def rewrite_sql_async(
        self,
        sql: str,
        db_qualify: bool,
        table_qualify: bool,
        use_alias: bool,
        db_name: str,
    ) -> str:
        """Rewrites an existing SQL query to apply database prefixes, table qualifications, or aliases."""
        instructions = []

        if db_qualify and db_name:
            instructions.append(
                f"Prefix all table names in FROM and JOIN clauses with the database name '{db_name}'. (e.g., {db_name}.table_name)"
            )

        if use_alias:
            instructions.append(
                "Assign a short, logical alias to every table in the FROM/JOIN clauses (e.g., 'v_advertiser_contacts va')."
            )
            instructions.append(
                "Prefix EVERY column name in the SELECT, WHERE, GROUP BY, and ORDER BY clauses with its corresponding table alias."
            )
        elif table_qualify:
            instructions.append("Prefix EVERY column name in the query with its full, exact table name.")

        if not instructions:
            return sql

        prompt = f"""Rewrite the following SQL query according to these strict rules:
{chr(10).join(f'- {i}' for i in instructions)}

Return ONLY the raw, formatted SQL code. Do not include markdown formatting blocks (like ```sql), do not include explanations, and do not change the core logic of the query.

Original SQL:
{sql}
"""

        try:
            response = await asyncio.wait_for(
                self.generate_async(prompt, {"system_prompt": "You are a strict SQL formatting utility."}),
                timeout=15.0,
            )
            rewritten = response.text.strip() if response and response.text else sql

            # Clean up markdown formatting if the LLM disobeys
            if rewritten.startswith("```sql"):
                rewritten = rewritten[6:]
            if rewritten.endswith("```"):
                rewritten = rewritten[:-3]

            return rewritten.strip()
        except Exception as e:
            logging.getLogger(__name__).error("SQL rewrite failed: %s", e)
            return sql

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
