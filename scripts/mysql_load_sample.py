#!/usr/bin/env python3
"""
Load sample SQL dumps (Chinook, Northwind) into MySQL server(s).

Uses mysql CLI (mysql < file.sql) when available — fast bulk load.
Falls back to Python mysql.connector if mysql is not installed.

Datasets:
  chinook  - data/Chinook_MySql.sql
  northwind - data/Northwind_MySql.sql
  both    - Load Chinook then Northwind

Targets (where to load):
  local   - DB_SOURCE_HOST, DB_SOURCE_USER, DB_SOURCE_PASSWORD, DB_SOURCE_PORT from .env
  runtime - DB_RUNTIME_HOST, DB_RUNTIME_USER, DB_RUNTIME_PASSWORD from .env (port 3306)
  both    - Load into local, then runtime

Usage: mysql_load_sample.py <dataset> [target]
  Default target is "both".
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Project root
_ROOT = Path(__file__).parent.parent
_DATASETS = {
    "chinook": _ROOT / "data" / "Chinook_MySql.sql",
    "northwind": _ROOT / "data" / "Northwind_MySql.sql",
}


def load_dotenv():
    """Load .env from standard locations."""
    from dotenv import load_dotenv as _load
    for loc in [_ROOT / ".env", Path.home() / ".daibai" / ".env"]:
        if loc.exists():
            _load(loc)
            break


def get_local_config():
    host = os.environ.get("DB_SOURCE_HOST", "localhost")
    user = os.environ.get("DB_SOURCE_USER") or os.environ.get("MYSQL_USER", "root")
    password = os.environ.get("DB_SOURCE_PASSWORD") or os.environ.get("MYSQL_PASSWORD", "")
    port = int(os.environ.get("DB_SOURCE_PORT", "3306"))
    return {"host": host, "user": user, "password": password, "port": port}


def get_runtime_config():
    host = os.environ.get("DB_RUNTIME_HOST")
    user = os.environ.get("DB_RUNTIME_USER")
    password = os.environ.get("DB_RUNTIME_PASSWORD", "")
    port = int(os.environ.get("DB_RUNTIME_PORT", "3306"))
    return {"host": host, "user": user, "password": password, "port": port}


def _split_statements(sql: str) -> list:
    """Split SQL by semicolon, respecting single/double/backtick-quoted strings."""
    stmts = []
    current = []
    in_string = False
    escape_char = None
    i = 0
    while i < len(sql):
        c = sql[i]
        if in_string:
            if c == escape_char:
                if i + 1 < len(sql) and sql[i + 1] == escape_char:
                    current.append(c)
                    i += 1
                else:
                    in_string = False
            current.append(c)
            i += 1
            continue
        if c in ("'", '"', "`"):
            in_string = True
            escape_char = c
            current.append(c)
            i += 1
            continue
        if c == ";":
            stmts.append("".join(current))
            current = []
            i += 1
            continue
        current.append(c)
        i += 1
    if current:
        stmts.append("".join(current))
    return stmts


def _load_via_mysql_cli(dataset: str, label: str, config: dict, sql_file: Path) -> bool:
    """Fast path: mysql < file.sql via command line."""
    mysql_bin = shutil.which("mysql")
    if not mysql_bin:
        return False

    print(f"[{label}] Using mysql CLI (bulk load)")

    env = os.environ.copy()
    if config.get("password"):
        env["MYSQL_PWD"] = config["password"]

    cmd = [
        mysql_bin,
        "--batch",           # Non-interactive, no buffering to tty
        "--quick",           # Don't cache results (stream rows) — avoids memory bloat
        "-h", config["host"],
        "-P", str(config["port"]),
        "-u", config["user"],
    ]
    if "azure.com" in config["host"].lower() or "database.azure" in config["host"].lower():
        cmd.extend(["--ssl-mode=REQUIRED", "--connect-timeout=30"])
    # Write to temp file and pass as stdin — avoids pipe deadlock with large SQL
    with tempfile.NamedTemporaryFile(
        mode="wb", suffix=".sql", delete=False
    ) as tmp:
        tmp.write(b"SET FOREIGN_KEY_CHECKS=0;\n")
        tmp.write(sql_file.read_bytes())
        tmp_path = tmp.name
    try:
        with open(tmp_path, "rb") as f:
            proc = subprocess.run(
                cmd,
                stdin=f,
                capture_output=True,
                env=env,
            )
    finally:
        os.unlink(tmp_path)

    if proc.returncode != 0 and proc.stderr:
        print(f"[ERROR] {label}: {proc.stderr.decode('utf-8', errors='replace').strip()}", file=sys.stderr)
        return False
    if proc.returncode != 0:
        return False
    print(f"[SUCCESS] {label}: {dataset} loaded into {config['host']}")
    return True


def _load_via_python(dataset: str, label: str, config: dict, sql_file: Path) -> bool:
    """Fallback: execute via mysql.connector statement-by-statement."""
    print(f"[{label}] Using Python mysql.connector (statement-by-statement)")

    import mysql.connector

    sql_content = sql_file.read_text(encoding="utf-8", errors="replace")
    sql_content = "SET FOREIGN_KEY_CHECKS=0;\n" + sql_content

    try:
        conn = mysql.connector.connect(
            host=config["host"],
            port=config["port"],
            user=config["user"],
            password=config["password"] or "",
        )
        conn.autocommit = False
        cursor = conn.cursor()
        try:
            for stmt in _split_statements(sql_content):
                stmt = stmt.strip()
                if stmt and not stmt.startswith("--"):
                    try:
                        cursor.execute(stmt)
                        if cursor.description:
                            cursor.fetchall()
                    except mysql.connector.Error as e:
                        err_str = str(e).lower()
                        if (
                            "unknown database" in err_str
                            or "doesn't exist" in err_str
                            or "failed to add the foreign key" in err_str
                            or "missing unique key" in err_str
                        ):
                            conn.rollback()
                        else:
                            raise
            conn.commit()
            print(f"[SUCCESS] {label}: {dataset} loaded into {config['host']}")
            return True
        finally:
            cursor.close()
            conn.close()
    except mysql.connector.Error as e:
        print(f"[ERROR] {label}: {e}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[ERROR] {label}: {e}", file=sys.stderr)
        return False


def load_sql_into_mysql(dataset: str, label: str, config: dict, sql_file: Path) -> bool:
    """Execute SQL file against MySQL. Prefers mysql CLI for speed."""
    if not config.get("host") or not config.get("user"):
        print(f"[ERROR] {label}: Missing host or user. Set DB_* vars in .env.", file=sys.stderr)
        return False

    if not sql_file.exists():
        print(f"[ERROR] SQL file not found: {sql_file}", file=sys.stderr)
        return False

    if not sql_file.read_text(encoding="utf-8", errors="replace").strip():
        print(f"[ERROR] SQL file is empty: {sql_file}", file=sys.stderr)
        return False

    if _load_via_mysql_cli(dataset, label, config, sql_file):
        return True

    try:
        import mysql.connector
    except ImportError:
        print("[ERROR] mysql CLI not found and mysql-connector-python not installed. Install: apt install mysql-client && pip install mysql-connector-python", file=sys.stderr)
        return False

    return _load_via_python(dataset, label, config, sql_file)


def main():
    load_dotenv()
    dataset = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
    target = (sys.argv[2] if len(sys.argv) > 2 else "both").lower()

    if dataset not in ("chinook", "northwind", "both"):
        print(f"Usage: {sys.argv[0]} <chinook|northwind|both> [local|runtime|both]", file=sys.stderr)
        print("  chinook   - Load data/Chinook_MySql.sql", file=sys.stderr)
        print("  northwind - Load data/Northwind_MySql.sql", file=sys.stderr)
        print("  both      - Load Chinook then Northwind", file=sys.stderr)
        print("  Target defaults to 'both' (local + runtime)", file=sys.stderr)
        sys.exit(1)

    if target not in ("local", "runtime", "both"):
        print(f"Usage: {sys.argv[0]} <chinook|northwind|both> [local|runtime|both]", file=sys.stderr)
        sys.exit(1)

    # Which datasets to load
    datasets_to_load = []
    if dataset == "both":
        datasets_to_load = [("chinook", _DATASETS["chinook"]), ("northwind", _DATASETS["northwind"])]
    else:
        datasets_to_load = [(dataset, _DATASETS[dataset])]

    ok = True
    for name, sql_file in datasets_to_load:
        if target in ("local", "both"):
            cfg = get_local_config()
            if not load_sql_into_mysql(name, "LOCAL", cfg, sql_file):
                ok = False
        if target in ("runtime", "both"):
            cfg = get_runtime_config()
            if not cfg.get("host"):
                print(f"[ERROR] RUNTIME: DB_RUNTIME_HOST not set in .env (for {name})", file=sys.stderr)
                ok = False
            elif not load_sql_into_mysql(name, "RUNTIME", cfg, sql_file):
                ok = False

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
