"""
Daiby Agent - Core AI Database Assistant.

Orchestrates LLM providers and database connections for natural language SQL generation.
"""

import os
import re
import asyncio
from typing import Optional, Tuple, Dict, Any, List
from pathlib import Path

import pandas as pd

from .config import Config, load_config, DatabaseConfig, LLMProviderConfig
from ..llm import get_provider_class, create_provider
from ..llm.base import BaseLLMProvider, LLMResponse


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


class DaibyAgent:
    """
    Daiby AI Database Assistant.
    
    Provides natural language to SQL capabilities using configurable LLM providers
    and database connections.
    """
    
    def __init__(self, config: Optional[Config] = None, config_path: Optional[Path] = None):
        """
        Initialize the Daiby agent.
        
        Args:
            config: Pre-loaded Config object
            config_path: Path to daiby.yaml (used if config not provided)
        """
        self.config = config or load_config(config_path)
        
        # Database runners (lazy initialized)
        self._runners: Dict[str, DatabaseRunner] = {}
        self._current_db: Optional[str] = self.config.default_database
        
        # LLM providers (lazy initialized)
        self._providers: Dict[str, BaseLLMProvider] = {}
        self._current_llm: Optional[str] = self.config.default_llm
        
        # Schema cache
        self._schema_cache: Dict[str, str] = {}
    
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
    
    def run_sql(self, sql: str, db_name: Optional[str] = None) -> Optional[pd.DataFrame]:
        """Execute SQL and return results."""
        runner = self._get_runner(db_name)
        return runner.run_sql(sql)
    
    async def run_sql_async(self, sql: str, db_name: Optional[str] = None) -> Optional[pd.DataFrame]:
        """Execute SQL asynchronously."""
        runner = self._get_runner(db_name)
        return await runner.run_sql_async(sql)
    
    def get_schema(self, db_name: Optional[str] = None, refresh: bool = False) -> str:
        """Get database schema as text."""
        name = db_name or self._current_db
        
        if name in self._schema_cache and not refresh:
            return self._schema_cache[name]
        
        # Fetch schema from database
        schema_parts = []
        
        # Get tables
        tables_df = self.run_sql("SHOW TABLES", name)
        if tables_df is not None and not tables_df.empty:
            table_col = tables_df.columns[0]
            tables = tables_df[table_col].tolist()
            
            for table in tables[:50]:  # Limit to 50 tables
                schema_parts.append(f"\n-- Table: {table}")
                try:
                    create_df = self.run_sql(f"SHOW CREATE TABLE `{table}`", name)
                    if create_df is not None and not create_df.empty:
                        # Use iloc[row, col] for positional access
                        create_sql = create_df.iloc[0, 1]  # Second column has CREATE statement
                        schema_parts.append(create_sql)
                except Exception:
                    pass
        
        schema = "\n".join(schema_parts)
        self._schema_cache[name] = schema
        return schema
    
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
def create_agent(config_path: Optional[Path] = None) -> DaibyAgent:
    """Create a DaibyAgent with configuration from file."""
    return DaibyAgent(config_path=config_path)
