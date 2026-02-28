"""
Chinook Playground Manager
============================
Manages a disposable SQLite playground database that is a safe copy of the
read-only Chinook master database.  Provides:

    reset_playground()              Restore playground.db from chinook_master.db
    execute_playground_query(sql)   Run SQL with a hard query-time limit
    get_chinook_schema()            Return the full DDL as a formatted string
                                    (suitable for use as an LLM system prompt)

God-mode
--------
When god_mode=True, execute_playground_query opens the playground file for
read-write so INSERT/UPDATE/DELETE/DDL are allowed.  The default (False) opens
it read-only via the SQLite URI ?mode=ro flag so destructive statements are
rejected at the driver level — not just filtered by a regex.

Query timeout
-------------
sqlite3.connect(timeout=N) only covers *lock-wait* time, not query execution.
Real cancellation is achieved by calling connection.interrupt() from a
threading.Timer after QUERY_TIMEOUT_SECONDS, which causes SQLite to raise
OperationalError("interrupted") at the next opcode boundary.
"""

import shutil
import sqlite3
import threading
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths — resolved relative to this file so the module works regardless of
# the working directory the process was launched from.
# ---------------------------------------------------------------------------

_ROOT      = Path(__file__).resolve().parent.parent.parent   # repo root
_DATA_DIR  = _ROOT / "data"
_MASTER_DB = _DATA_DIR / "chinook_master.db"
_PLAY_DB   = _DATA_DIR / "playground.db"

# Hard limit on query wall-clock time (seconds).
QUERY_TIMEOUT_SECONDS: float = 5.0

# Maximum rows returned per query (prevents accidental full-table dumps).
MAX_ROWS: int = 500


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class PlaygroundError(Exception):
    """Base class for all playground errors."""


class QueryTimeoutError(PlaygroundError):
    """Raised when a query exceeds QUERY_TIMEOUT_SECONDS."""


class ReadOnlyViolationError(PlaygroundError):
    """Raised when a write statement is attempted in read-only mode."""


# ---------------------------------------------------------------------------
# Playground lifecycle
# ---------------------------------------------------------------------------

def reset_playground() -> Path:
    """
    Copy chinook_master.db → playground.db, discarding any previous changes.

    Returns the path to the freshly-reset playground database.
    Raises FileNotFoundError if the master database is missing.
    """
    if not _MASTER_DB.exists():
        raise FileNotFoundError(
            f"Master database not found: {_MASTER_DB}\n"
            "Place chinook_master.db in the data/ directory."
        )
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src=_MASTER_DB, dst=_PLAY_DB)
    return _PLAY_DB


# ---------------------------------------------------------------------------
# Query execution
# ---------------------------------------------------------------------------

def execute_playground_query(
    sql: str,
    *,
    god_mode: bool = False,
    timeout: float = QUERY_TIMEOUT_SECONDS,
    max_rows: int = MAX_ROWS,
) -> dict[str, Any]:
    """
    Execute *sql* against the playground database and return results.

    Parameters
    ----------
    sql       : SQL statement to execute.
    god_mode  : If True, open for read-write (INSERT/UPDATE/DELETE/DDL allowed).
                If False (default), open in read-only mode via SQLite URI flag.
    timeout   : Hard wall-clock limit in seconds.  The query is interrupted via
                connection.interrupt() when the limit is reached.
    max_rows  : Maximum number of rows to include in the result set.

    Returns
    -------
    {
        "columns":      list[str],
        "rows":         list[list],
        "row_count":    int,
        "truncated":    bool,   # True when more rows exist beyond max_rows
        "timed_out":    bool,
    }

    Raises
    ------
    FileNotFoundError      If playground.db does not yet exist.
    QueryTimeoutError      If the query does not complete within *timeout*.
    sqlite3.OperationalError / sqlite3.DatabaseError  For SQL errors.
    """
    if not _PLAY_DB.exists():
        raise FileNotFoundError(
            f"Playground database not found: {_PLAY_DB}\n"
            "Call reset_playground() first."
        )

    # Build the connection URI.  SQLite URI mode requires an absolute path
    # with forward-slashes even on Windows.
    abs_path = _PLAY_DB.resolve().as_posix()
    if god_mode:
        # Read-write: plain path, no URI needed.
        conn_str = str(_PLAY_DB.resolve())
        use_uri  = False
    else:
        # Read-only: SQLite URI with mode=ro rejects any write at the VFS level.
        conn_str = f"file:{abs_path}?mode=ro"
        use_uri  = True

    conn = sqlite3.connect(conn_str, uri=use_uri, check_same_thread=False)
    conn.row_factory = sqlite3.Row   # column-name access

    timed_out = False

    def _interrupt():
        nonlocal timed_out
        timed_out = True
        conn.interrupt()

    timer = threading.Timer(timeout, _interrupt)
    timer.daemon = True

    try:
        timer.start()
        cur = conn.cursor()
        cur.execute(sql)

        columns: list[str] = []
        rows:    list[list] = []
        truncated = False

        if cur.description:
            columns = [d[0] for d in cur.description]
            fetched = cur.fetchmany(max_rows + 1)
            if len(fetched) > max_rows:
                fetched = fetched[:max_rows]
                truncated = True
            rows = [list(r) for r in fetched]

        # In god_mode, persist DML/DDL changes before closing the connection.
        if god_mode:
            conn.commit()

        if timed_out:
            raise QueryTimeoutError(
                f"Query exceeded the {timeout:.1f}s time limit and was interrupted."
            )

        return {
            "columns":   columns,
            "rows":      rows,
            "row_count": len(rows),
            "truncated": truncated,
            "timed_out": False,
        }

    except sqlite3.OperationalError as exc:
        if timed_out:
            raise QueryTimeoutError(
                f"Query exceeded the {timeout:.1f}s time limit and was interrupted."
            ) from exc
        raise
    finally:
        timer.cancel()
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Schema helper — static string baked from the real DDL
# ---------------------------------------------------------------------------

#: Pre-formatted DDL for every Chinook table.  Hardcoded so callers can embed
#: it in an LLM system prompt without opening a database connection.
_CHINOOK_SCHEMA: str = """\
-- Chinook Database Schema (SQLite)
-- 11 tables covering artists, albums, tracks, customers, invoices, employees,
-- genres, media types, playlists, and playlist-track associations.

CREATE TABLE Artist (
    ArtistId  INTEGER       NOT NULL PRIMARY KEY,
    Name      NVARCHAR(120)
);

CREATE TABLE Album (
    AlbumId   INTEGER        NOT NULL PRIMARY KEY,
    Title     NVARCHAR(160)  NOT NULL,
    ArtistId  INTEGER        NOT NULL REFERENCES Artist(ArtistId)
);

CREATE TABLE MediaType (
    MediaTypeId  INTEGER       NOT NULL PRIMARY KEY,
    Name         NVARCHAR(120)
);

CREATE TABLE Genre (
    GenreId  INTEGER       NOT NULL PRIMARY KEY,
    Name     NVARCHAR(120)
);

CREATE TABLE Track (
    TrackId      INTEGER        NOT NULL PRIMARY KEY,
    Name         NVARCHAR(200)  NOT NULL,
    AlbumId      INTEGER                 REFERENCES Album(AlbumId),
    MediaTypeId  INTEGER        NOT NULL REFERENCES MediaType(MediaTypeId),
    GenreId      INTEGER                 REFERENCES Genre(GenreId),
    Composer     NVARCHAR(220),
    Milliseconds INTEGER        NOT NULL,
    Bytes        INTEGER,
    UnitPrice    NUMERIC(10,2)  NOT NULL
);

CREATE TABLE Employee (
    EmployeeId  INTEGER       NOT NULL PRIMARY KEY,
    LastName    NVARCHAR(20)  NOT NULL,
    FirstName   NVARCHAR(20)  NOT NULL,
    Title       NVARCHAR(30),
    ReportsTo   INTEGER                REFERENCES Employee(EmployeeId),
    BirthDate   DATETIME,
    HireDate    DATETIME,
    Address     NVARCHAR(70),
    City        NVARCHAR(40),
    State       NVARCHAR(40),
    Country     NVARCHAR(40),
    PostalCode  NVARCHAR(10),
    Phone       NVARCHAR(24),
    Fax         NVARCHAR(24),
    Email       NVARCHAR(60)
);

CREATE TABLE Customer (
    CustomerId    INTEGER       NOT NULL PRIMARY KEY,
    FirstName     NVARCHAR(40)  NOT NULL,
    LastName      NVARCHAR(20)  NOT NULL,
    Company       NVARCHAR(80),
    Address       NVARCHAR(70),
    City          NVARCHAR(40),
    State         NVARCHAR(40),
    Country       NVARCHAR(40),
    PostalCode    NVARCHAR(10),
    Phone         NVARCHAR(24),
    Fax           NVARCHAR(24),
    Email         NVARCHAR(60)  NOT NULL,
    SupportRepId  INTEGER                REFERENCES Employee(EmployeeId)
);

CREATE TABLE Invoice (
    InvoiceId         INTEGER        NOT NULL PRIMARY KEY,
    CustomerId        INTEGER        NOT NULL REFERENCES Customer(CustomerId),
    InvoiceDate       DATETIME       NOT NULL,
    BillingAddress    NVARCHAR(70),
    BillingCity       NVARCHAR(40),
    BillingState      NVARCHAR(40),
    BillingCountry    NVARCHAR(40),
    BillingPostalCode NVARCHAR(10),
    Total             NUMERIC(10,2)  NOT NULL
);

CREATE TABLE InvoiceLine (
    InvoiceLineId  INTEGER        NOT NULL PRIMARY KEY,
    InvoiceId      INTEGER        NOT NULL REFERENCES Invoice(InvoiceId),
    TrackId        INTEGER        NOT NULL REFERENCES Track(TrackId),
    UnitPrice      NUMERIC(10,2)  NOT NULL,
    Quantity       INTEGER        NOT NULL
);

CREATE TABLE Playlist (
    PlaylistId  INTEGER       NOT NULL PRIMARY KEY,
    Name        NVARCHAR(120)
);

CREATE TABLE PlaylistTrack (
    PlaylistId  INTEGER  NOT NULL REFERENCES Playlist(PlaylistId),
    TrackId     INTEGER  NOT NULL REFERENCES Track(TrackId),
    PRIMARY KEY (PlaylistId, TrackId)
);
"""


def get_chinook_schema() -> str:
    """
    Return a pre-formatted DDL string for the entire Chinook database.

    The string is suitable for inclusion in an LLM system prompt, e.g.:

        system_prompt = (
            "You are a SQL assistant. The database schema is:\\n\\n"
            + get_chinook_schema()
        )

    No database connection is required — the schema is embedded in this module.
    """
    return _CHINOOK_SCHEMA
