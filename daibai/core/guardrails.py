"""
Phase 3 Step 3: Deterministic SQL Guardrail (Safety-First Architecture).

Multi-layered validator that prevents non-SELECT or out-of-scope queries from
reaching the database. Treats the LLM as an untrusted client.
"""

from typing import Callable, List, Optional, Set

import sqlparse
from sqlparse.sql import Identifier, IdentifierList
from sqlparse.tokens import Keyword, DML, String


# Blocked keywords (DML/DDL that modify data or schema)
_BLOCKED_KEYWORDS = frozenset({
    "DELETE", "DROP", "UPDATE", "INSERT", "ALTER", "TRUNCATE",
    "GRANT", "REVOKE", "EXEC", "EXECUTE", "CALL",
})


class SecurityViolation(Exception):
    """Raised when SQL validation fails (DML blocked, out-of-scope, or injection)."""

    def __init__(self, message: str, layer: str):
        self.layer = layer
        super().__init__(message)


def _normalize_identifier(name: str) -> str:
    """Normalize table/identifier name (strip quotes, lowercase for comparison)."""
    if not name:
        return ""
    s = str(name).strip().strip("`\"[]").strip()
    return s.lower()


def _extract_tables_from_parsed(parsed) -> Set[str]:
    """Extract table names from a parsed sqlparse statement (FROM/JOIN)."""
    tables: Set[str] = set()

    def _walk(tokens):
        if not hasattr(tokens, "__iter__"):
            return
        tok_list = list(tokens) if not isinstance(tokens, list) else tokens
        i = 0
        after_from_or_join = False
        while i < len(tok_list):
            t = tok_list[i]
            if hasattr(t, "ttype") and t.ttype is Keyword:
                v = getattr(t, "value", "").upper()
                if v == "FROM":
                    after_from_or_join = True
                elif v in ("JOIN", "LEFT", "RIGHT", "INNER", "OUTER", "CROSS"):
                    if v in ("LEFT", "RIGHT", "INNER", "OUTER", "CROSS"):
                        i += 1
                        if i < len(tok_list) and getattr(tok_list[i], "ttype", None) is Keyword:
                            if getattr(tok_list[i], "value", "").upper() == "JOIN":
                                after_from_or_join = True
                    else:
                        after_from_or_join = True
                elif v in ("WHERE", "GROUP", "ORDER", "LIMIT", "HAVING", "UNION") or v.startswith(
                    ("GROUP", "ORDER", "LIMIT", "HAVING")
                ):
                    after_from_or_join = False
            elif after_from_or_join:
                if isinstance(t, IdentifierList):
                    for ident in t.get_identifiers():
                        name = ident.get_real_name() or ident.get_name()
                        if name:
                            tables.add(_normalize_identifier(name))
                elif isinstance(t, Identifier):
                    name = t.get_real_name() or t.get_name()
                    if name:
                        tables.add(_normalize_identifier(name))
            if hasattr(t, "tokens") and t.tokens and not isinstance(t, (Identifier, IdentifierList)):
                _walk(t.tokens)
            i += 1

    if hasattr(parsed, "tokens"):
        _walk(parsed.tokens)
    return tables


def _get_statement_keywords_outside_strings(parsed) -> List[str]:
    """
    Extract SQL keywords that are NOT inside string literals.
    Uses sqlparse to distinguish keywords from string content.
    Includes DML, DDL, and Keyword token types.
    """
    keywords: List[str] = []

    def _is_string_type(ttype):
        if ttype is None:
            return False
        name = getattr(ttype, "name", "") or str(ttype)
        return "String" in name

    def _is_keyword_type(ttype):
        if ttype is None:
            return False
        if ttype in (Keyword, DML):
            return True
        name = str(ttype)
        return "Keyword" in name or "DML" in name or "DDL" in name

    def _walk(tokens):
        for t in (tokens.tokens if hasattr(tokens, "tokens") else tokens):
            if hasattr(t, "ttype"):
                if _is_string_type(t.ttype):
                    pass  # Skip string content
                elif _is_keyword_type(t.ttype):
                    keywords.append(getattr(t, "value", "").upper())
                elif hasattr(t, "tokens") and t.tokens:
                    _walk(t)

    if hasattr(parsed, "tokens"):
        _walk(parsed)
    return keywords


class SQLValidator:
    """
    Multi-layered SQL validator for read-only, in-scope execution.

    Layer 1: Lexical block (forbidden keywords)
    Layer 2: Scope check (only allowed tables)
    Layer 3: Injection shield (single statement only)
    """

    def __init__(self, blocked_keywords: Optional[Set[str]] = None):
        self._blocked = blocked_keywords or _BLOCKED_KEYWORDS

    def validate(
        self,
        query: str,
        allowed_tables: Optional[Set[str]] = None,
    ) -> None:
        """
        Validate SQL. Raises SecurityViolation if invalid.

        Args:
            query: SQL string to validate.
            allowed_tables: If provided, only these tables may be referenced.
                           If None, scope check is skipped (permissive).
        """
        if not query or not query.strip():
            raise SecurityViolation("Empty query", "lexical")

        # Layer 3: Injection shield - block multi-statement
        # Use sqlparse.split to avoid splitting on ; inside string literals
        statements = [s.strip() for s in sqlparse.split(query) if s.strip()]
        if len(statements) > 1:
            raise SecurityViolation(
                f"Multi-statement queries are blocked (found {len(statements)} statements)",
                "injection",
            )

        sql = statements[0]
        parsed_list = sqlparse.parse(sql)
        if not parsed_list:
            raise SecurityViolation("Invalid or empty SQL", "lexical")

        parsed = parsed_list[0]

        # Layer 1: Lexical block - forbidden keywords (excluding string literals)
        keywords = _get_statement_keywords_outside_strings(parsed)
        for kw in keywords:
            if kw in self._blocked:
                raise SecurityViolation(
                    f"Forbidden keyword: {kw}",
                    "lexical",
                )

        # Layer 2: Scope check
        if allowed_tables is not None:
            allowed_normalized = {_normalize_identifier(t) for t in allowed_tables}
            refs = _extract_tables_from_parsed(parsed)
            out_of_scope = refs - allowed_normalized
            if out_of_scope:
                raise SecurityViolation(
                    f"Table(s) not in allowed scope: {sorted(out_of_scope)}",
                    "scope",
                )

    def is_in_scope(self, query: str, allowed_tables: Set[str]) -> bool:
        """
        Check if query only references allowed tables.
        Does not perform lexical or injection checks.
        """
        if not query or not query.strip():
            return False
        statements = [s.strip() for s in query.split(";") if s.strip()]
        if not statements:
            return False
        parsed_list = sqlparse.parse(statements[0])
        if not parsed_list:
            return False
        allowed_normalized = {_normalize_identifier(t) for t in allowed_tables}
        refs = _extract_tables_from_parsed(parsed_list[0])
        return refs <= allowed_normalized

    def validate_and_execute(
        self,
        execute_fn: Callable[[str], any],
        query: str,
        allowed_tables: Optional[Set[str]] = None,
    ):
        """
        Validate query, then execute. Returns execute_fn(query) if valid.
        Raises SecurityViolation if validation fails.
        """
        self.validate(query, allowed_tables=allowed_tables)
        return execute_fn(query)
