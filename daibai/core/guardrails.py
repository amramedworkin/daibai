"""
Phase 3 Step 3: Deterministic SQL Guardrail (Safety-First Architecture).

Literature-backed (Peng et al. 2023 IEEE): Two-stage pipeline:
- Stage 1 (Pre-LLM): Prompt sanitizer blocks in-band SQL injection in natural language.
- Stage 2 (Post-LLM): SQL AST validator blocks DML/DDL, DoS (benchmark), info disclosure
  (user/version), tautologies (OR 1=1), system schema probing, out-of-scope tables.
"""

import re
from typing import Callable, List, Optional, Set

import sqlparse
from sqlparse.sql import Identifier, IdentifierList
from sqlparse.tokens import Keyword, DML, String


# Blocked keywords (DML/DDL that modify data or schema)
_BLOCKED_KEYWORDS = frozenset({
    "DELETE", "DROP", "UPDATE", "INSERT", "ALTER", "TRUNCATE",
    "GRANT", "REVOKE", "EXEC", "EXECUTE", "CALL", "MERGE",
})

# DoS and info disclosure functions (Peng et al. 2023)
_BLOCKED_FUNCTIONS = frozenset({
    "pg_sleep", "sleep", "waitfor", "benchmark",  # DoS
    "user", "version", "database", "system_user", "session_user",
    "current_database", "current_user", "current_schema",  # Info disclosure
    "load_file", "into_outfile", "into_dumpfile",  # File exfil
})

# System schema probing
_BLOCKED_SCHEMAS = frozenset({
    "information_schema", "pg_catalog", "pg_toast", "sys",
    "mysql", "sqlite_master", "performance_schema",
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


def _extract_cte_names(parsed) -> Set[str]:
    """
    Extract CTE names from WITH clause(s). These are derived tables defined in the query,
    not base tables, so they should not be scope-checked against allowed_tables.
    """
    ctes: Set[str] = set()
    if not hasattr(parsed, "tokens"):
        return ctes
    tok_list = list(parsed.tokens)
    i = 0
    in_with = False
    while i < len(tok_list):
        t = tok_list[i]
        v_upper = getattr(t, "value", "").upper() if hasattr(t, "value") else ""
        if v_upper == "WITH":
            in_with = True
            i += 1
            continue
        if in_with:
            if v_upper in ("SELECT", "INSERT", "UPDATE", "DELETE", "RECURSIVE"):
                if v_upper != "RECURSIVE":
                    break
            elif isinstance(t, Identifier):
                name = t.get_real_name() or t.get_name()
                if name and v_upper != "AS":
                    ctes.add(_normalize_identifier(name))
            i += 1
        else:
            i += 1
    return ctes


def _extract_tables_from_parsed(parsed) -> Set[str]:
    """Extract table names from a parsed sqlparse statement (FROM/JOIN). Handles UNION."""
    tables: Set[str] = set()
    _KEYWORDS = frozenset({
        "SELECT", "FROM", "JOIN", "WHERE", "ON", "AND", "OR",
        "LEFT", "RIGHT", "INNER", "OUTER", "CROSS", "UNION",
        "GROUP", "ORDER", "LIMIT", "HAVING", "AS", "ALL", "BY",
    })
    # Structural keywords that reset/change "after FROM" state (never table names)
    _STRUCTURAL = frozenset({
        "FROM", "JOIN", "ON", "WHERE", "GROUP", "ORDER", "LIMIT", "HAVING",
        "UNION", "UNION ALL", "LEFT", "RIGHT", "INNER", "OUTER", "CROSS",
    })

    def _walk(tokens, after_from=False):
        if not hasattr(tokens, "__iter__"):
            return
        tok_list = list(tokens) if not isinstance(tokens, list) else tokens
        i = 0
        aft = after_from
        while i < len(tok_list):
            t = tok_list[i]
            v = (getattr(t, "value", "") or str(t)).strip().upper()
            # Update aft based on structural keywords
            if v == "FROM":
                aft = True
            elif v in ("JOIN", "LEFT", "RIGHT", "INNER", "OUTER", "CROSS"):
                if v in ("LEFT", "RIGHT", "INNER", "OUTER", "CROSS"):
                    i += 1
                    if i < len(tok_list):
                        nv = (getattr(tok_list[i], "value", "") or str(tok_list[i])).strip().upper()
                        if nv == "JOIN":
                            aft = True
                else:
                    aft = True
            else:
                first = v.split()[0] if v else ""
                if first in ("ON", "WHERE", "GROUP", "ORDER", "LIMIT", "HAVING", "UNION"):
                    aft = False
            # Collect table names when aft (after FROM/JOIN); include tokens sqlparse marks as Keyword (e.g. "admin")
            if aft and v and v not in _STRUCTURAL:
                if isinstance(t, IdentifierList):
                    for ident in t.get_identifiers():
                        name = ident.get_real_name() or ident.get_name()
                        if name:
                            tables.add(_normalize_identifier(name))
                elif isinstance(t, Identifier):
                    name = t.get_real_name() or t.get_name()
                    if name:
                        tables.add(_normalize_identifier(name))
                else:
                    val = str(t).strip()
                    if val and " " not in val and (val[0].isalpha() or (len(val) > 1 and val[0] == "_")):
                        if val.upper() not in _KEYWORDS:
                            tables.add(_normalize_identifier(val))
            if hasattr(t, "tokens") and t.tokens and not isinstance(t, (Identifier, IdentifierList)):
                _walk(t.tokens, aft)
            i += 1

    if hasattr(parsed, "tokens"):
        _walk(parsed.tokens)
    return tables


def extract_tables_from_query(sql: str) -> Set[str]:
    """Extract table names from a SQL query string (for metrics/analytics)."""
    if not sql or not sql.strip():
        return set()
    parsed_list = sqlparse.parse(sqlparse.format(sql, strip_comments=True))
    if not parsed_list:
        return set()
    return _extract_tables_from_parsed(parsed_list[0])


def _extract_qualified_refs_from_from_join(parsed) -> Set[str]:
    """Extract schema.table refs from FROM/JOIN for system schema check."""
    refs: Set[str] = set()

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
            elif after_from_or_join and isinstance(t, Identifier):
                parent = getattr(t, "get_parent_name", lambda: None)()
                name = t.get_real_name() or t.get_name()
                if parent and name:
                    refs.add(f"{_normalize_identifier(parent)}.{_normalize_identifier(name)}")
            if hasattr(t, "tokens") and t.tokens and not isinstance(t, Identifier):
                _walk(t.tokens)
            i += 1

    if hasattr(parsed, "tokens"):
        _walk(parsed.tokens)
    return refs


def _extract_functions_from_parsed(parsed) -> Set[str]:
    """Extract function names from parsed SQL (e.g. benchmark(...), user())."""
    funcs: Set[str] = set()

    def _walk(tokens):
        if not hasattr(tokens, "__iter__"):
            return
        for t in (tokens.tokens if hasattr(tokens, "tokens") else tokens):
            if hasattr(t, "tokens") and t.tokens:
                _walk(t)
            val = getattr(t, "value", str(t))
            if "(" in val:
                name = val.split("(")[0].strip().lower()
                if name and name.replace("_", "").isalnum():
                    funcs.add(name)

    if hasattr(parsed, "tokens"):
        _walk(parsed.tokens)
    return funcs


def _extract_functions_from_query(query: str) -> Set[str]:
    """Regex fallback: find func_name( patterns in query (outside strings)."""
    funcs: Set[str] = set()
    # Match identifier followed by (
    for m in re.finditer(r"\b([a-z_][a-z0-9_]*)\s*\(", query, re.I):
        funcs.add(m.group(1).lower())
    return funcs


def _detect_tautology(query: str) -> bool:
    """Detect OR 1=1, OR 'x'='x' and similar tautology injection patterns."""
    # OR 1=1, OR 2=2, OR 0=0
    if re.search(r"(?i)\bOR\s+['\"]?\d+['\"]?\s*=\s*['\"]?\d+['\"]?", query):
        return True
    # OR 'x'='x', OR "a"="a" (same on both sides)
    if re.search(r"(?i)\bOR\s+['\"]([^'\"]*)['\"]\s*=\s*['\"]\1['\"]", query):
        return True
    # OR 1 (truthy)
    if re.search(r"(?i)\bOR\s+1\b", query):
        return True
    return False


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

        # Layer 1b: Blocked functions (DoS, info disclosure)
        funcs = _extract_functions_from_parsed(parsed) | _extract_functions_from_query(sql)
        blocked = funcs & _BLOCKED_FUNCTIONS
        if blocked:
            raise SecurityViolation(
                f"Forbidden function(s): {sorted(blocked)}",
                "lexical",
            )

        # Layer 1c: Tautology detection (OR 1=1 injection)
        if _detect_tautology(sql):
            raise SecurityViolation(
                "Tautology (OR 1=1 pattern) detected. Query blocked.",
                "lexical",
            )

        # Layer 2: Scope check and system schema probing
        refs = _extract_tables_from_parsed(parsed)
        qualified = _extract_qualified_refs_from_from_join(parsed)
        cte_names = _extract_cte_names(parsed)
        refs_to_check = refs - cte_names
        # Always block system schema probing (even when allowed_tables is None)
        for tbl in refs_to_check:
            tbl_lower = tbl.lower()
            if tbl_lower in _BLOCKED_SCHEMAS:
                raise SecurityViolation(
                    f"System schema probing blocked: {tbl}",
                    "scope",
                )
        for q in qualified:
            ql = q.lower()
            if ql in _BLOCKED_SCHEMAS:
                raise SecurityViolation(
                    f"System schema probing blocked: {q}",
                    "scope",
                )
            if any(ql.startswith(f"{s}.") for s in _BLOCKED_SCHEMAS):
                raise SecurityViolation(
                    f"System schema probing blocked: {q}",
                    "scope",
                )
        if allowed_tables is not None:
            allowed_normalized = {_normalize_identifier(t) for t in allowed_tables}
            out_of_scope = refs_to_check - allowed_normalized
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


# ---------------------------------------------------------------------------
# GuardrailPipeline: Two-stage (Pre-LLM + Post-LLM)
# ---------------------------------------------------------------------------

SUSPICIOUS_PROMPT_PATTERNS = [
    r"(?i)\bUNION\s+SELECT\b",
    r"(?i)\bDROP\s+DATABASE\b",
    r"(?i)\bDROP\s+TABLE\b",
    r"(?i)\bDELETE\s+FROM\b",
    r"(?i)\bINSERT\s+INTO\b",
    r"(?i)\bUPDATE\s+\w+\s+SET\b",
    r"\\g\s*",
    r"(?i)\bOR\s+\d+\s*=\s*\d+\b",
    r"(?i)\b;\s*DROP\b",
    r"(?i)\b;\s*DELETE\b",
    r"(?i)\b;\s*TRUNCATE\b",
    r"(?i)\bexec\s*(\s|$)",
    r"(?i)\bexecute\s+immediate\b",
    r"(?i)\bbenchmark\s*\(",
    r"(?i)\bsleep\s*\(",
    r"(?i)\bpg_sleep\s*\(",
]


class GuardrailPipeline:
    """
    Two-stage guardrail (Peng et al. 2023):
    Stage 1: Pre-LLM prompt sanitizer blocks in-band SQL injection.
    Stage 2: Post-LLM SQL validator (delegates to SQLValidator).
    """

    def __init__(self, validator: Optional[SQLValidator] = None):
        self._validator = validator or SQLValidator()

    @classmethod
    def validate_prompt(cls, user_prompt: str) -> bool:
        """
        Stage 1: Pre-LLM input sanitization.
        Blocks suspicious SQL syntax in natural language (in-band injection).
        """
        if not user_prompt or not user_prompt.strip():
            return True
        for pattern in SUSPICIOUS_PROMPT_PATTERNS:
            if re.search(pattern, user_prompt):
                raise SecurityViolation(
                    "Suspicious SQL syntax detected in natural language prompt. Request blocked.",
                    "prompt",
                )
        return True

    def validate_sql(
        self,
        query: str,
        allowed_tables: Optional[Set[str]] = None,
    ) -> None:
        """
        Stage 2: Post-LLM SQL validation.
        Delegates to SQLValidator (lexical, function, tautology, scope).
        """
        self._validator.validate(query, allowed_tables=allowed_tables)
