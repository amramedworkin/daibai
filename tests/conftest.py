"""
Pytest configuration: colorized test descriptions and dashboard summary.

Run with: pytest tests/ -v -s
Colors: [DB]=cyan, [API]=green, [CLOUD-<component>]=yellow (e.g. CLOUD-REDIS, CLOUD-COSMOS, CLOUD-LIFESPAN)
Mock vs physical: [CAT] = mock (fakeredis, mocked embeddings, etc.); [CAT☁] = physical (real Redis, Cosmos, embeddings).
"""

import sys
from pathlib import Path

# Environment variables are provided to pytest via pytest-dotenv (configured in pyproject.toml).
# pytest-dotenv will load `.env` (and `.env.test` if present) before test collection, so tests can
# rely on AUTH_*, AZURE_*, and other environment variables at import/collection time.
#
# NOTE: We intentionally removed the common "jury-rigged" anti-patterns where tests/modules
# manually load .env at the top of files or via autouse fixtures in conftest. Examples that were
# removed (if present) are shown below — DO NOT reintroduce them:
#
# 1) Top-of-file load (DELETE THIS):
#    from dotenv import load_dotenv
#    load_dotenv()
#
# 2) Manual session fixture (DELETE THIS):
#    @pytest.fixture(scope="session", autouse=True)
#    def env_setup():
#        load_dotenv(".env")
#
# Rationale:
# - pytest-dotenv centralizes env loading and runs before collection, ensuring skip/skipif guards
#   and module-level env checks behave consistently.
# - env_override_existing_values = 1 (pyproject.toml) makes .env/.env.test take precedence
#   for deterministic test runs.
#
# If you need test-specific overrides, prefer creating a `.env.test` file (committed to CI secrets
# or kept locally) instead of in-code loading.

# ANSI colors (work in most terminals)
_CYAN = "\033[96m"    # Database, config
_GREEN = "\033[92m"   # API, auth
_YELLOW = "\033[93m"  # Cloud tests
_MAGENTA = "\033[95m"  # LLM providers
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RESET = "\033[0m"
_RED = "\033[91m"
_PASS = "\033[92m✓\033[0m"
_FAIL = "\033[91m✗\033[0m"
_SKIP = "\033[93m⊘\033[0m"

# Map module to category and docstrings (populated during run)
_test_docs = {}
# Nodeids of tests that hit real cloud services (from @pytest.mark.cloud)
_physical_test_nodeids = set()
# Ordered list of test outcomes for heatmap (populated during run)
_test_results_ordered = []
# Session stats: only count 'call' phase to avoid triple-counting (setup/call/teardown)
_session_stats = {"passed": 0, "failed": 0, "skipped": 0}
# Mock vs physical: passed/failed/skipped per category
_mock_physical_stats = {"mock": {"passed": 0, "failed": 0, "skipped": 0}, "physical": {"passed": 0, "failed": 0, "skipped": 0}}


def pytest_addoption(parser):
    """Add custom command line options for the dashboard."""
    parser.addoption(
        "--quiet-mode",
        action="store_true",
        help="Suppress per-test success messages; only show failures and dashboards (default via addopts).",
    )
    parser.addoption(
        "--no-quiet-mode",
        action="store_true",
        help="Disable quiet mode; show per-test output and full catalog (use with -v for verbose).",
    )


def _get_category(nodeid):
    """Return (category, color) for a test nodeid. Cloud tests get [CLOUD-<component>]."""
    if "test_database_logic" in nodeid:
        return "DB", _CYAN
    if "test_api" in nodeid:
        return "API", _GREEN
    if "test_auth" in nodeid:
        return "AUTH", _GREEN
    if "test_config" in nodeid:
        return "CONFIG", _CYAN
    if "test_schema_discovery" in nodeid or "test_schema_mapping" in nodeid or "test_schema_indexing" in nodeid:
        return "SCHEMA", _CYAN
    if "test_sql_guardrails" in nodeid:
        return "GUARDRAILS", _CYAN
    if "test_env_integrity" in nodeid:
        return "ENV-INTEGRITY", _CYAN
    if "test_env_ready" in nodeid:
        return "ENV-READY", _CYAN
    if "test_cache_connection" in nodeid:
        return "CLOUD-CONN", _YELLOW
    if "test_redis" in nodeid:
        return "CLOUD-REDIS", _YELLOW
    if "test_cache_logic" in nodeid:
        return "CLOUD-L1", _YELLOW
    if "test_semantic_cache" in nodeid or "test_semantic_precision" in nodeid:
        return "CLOUD-CACHE", _YELLOW
    if "test_server_lifespan" in nodeid:
        return "CLOUD-LIFESPAN", _YELLOW
    if "test_cosmos_cloud" in nodeid or "test_cosmos_integration" in nodeid or "test_cosmos_store" in nodeid:
        return "CLOUD-COSMOS", _YELLOW
    if "test_azure_config" in nodeid:
        return "CLOUD-AZURE", _YELLOW
    if "test_llm_providers" in nodeid:
        return "LLM-PROVIDERS", _MAGENTA
    if "test_new_providers" in nodeid:
        return "LLM-REGISTRY", _MAGENTA
    if "test_model_discovery" in nodeid:
        return "LLM-MODELS", _MAGENTA
    if "test_gemini_get_models" in nodeid:
        return "LLM-GEMINI", _MAGENTA
    if "test_auth_connectivity" in nodeid or "test_gui_login" in nodeid or "test_entra_user" in nodeid:
        return "AUTH", _GREEN
    return None, None


def _get_skip_reason(report):
    """Extract skip reason from a skipped TestReport for display."""
    def _clean(s):
        if not s:
            return "No reason given"
        s = str(s).replace("Skipped: ", "").replace("Skipped", "").strip()
        return s or "No reason given"

    try:
        if hasattr(report, "longrepr") and report.longrepr is not None:
            lr = report.longrepr
            if isinstance(lr, tuple) and len(lr) >= 3:
                return _clean(lr[2])
            if hasattr(lr, "reprcrash") and lr.reprcrash is not None:
                return _clean(getattr(lr.reprcrash, "message", ""))
            if isinstance(lr, str):
                return _clean(lr)
        if hasattr(report, "longreprtext") and report.longreprtext:
            return _clean(report.longreprtext.strip())
    except Exception:
        pass
    return "No reason given"


def _is_physical_test(nodeid):
    """True if test hits real Redis, Cosmos, embeddings, etc. (not mocked)."""
    if nodeid in _physical_test_nodeids:
        return True
    if "_live" in nodeid:
        return True
    cloud_only_modules = (
        "test_redis",
        "test_cosmos_store",
        "test_cosmos_cloud",
        "test_cosmos_integration",
        "test_server_lifespan",
    )
    if any(m in nodeid for m in cloud_only_modules):
        return True
    return False


def _format_category(cat, nodeid):
    """Return category string with mock/physical indicator: [CAT] or [CAT☁]."""
    if not cat:
        return cat
    return f"{cat}☁" if _is_physical_test(nodeid) else cat


def pytest_sessionstart(session):
    """Clear ordered results and session stats at session start."""
    global _test_results_ordered, _session_stats, _physical_test_nodeids, _mock_physical_stats
    _test_results_ordered = []
    _session_stats = {"passed": 0, "failed": 0, "skipped": 0}
    _physical_test_nodeids = set()
    _mock_physical_stats = {"mock": {"passed": 0, "failed": 0, "skipped": 0}, "physical": {"passed": 0, "failed": 0, "skipped": 0}}


def pytest_runtest_logreport(report):
    """Collect test outcomes. ONLY count 'call' phase to avoid triple-counting setup/teardown.
    Exception: count skipped in 'setup' too (tests skipped before call never reach call).
    XFAIL (expected failure): count as passed so dashboard shows success."""
    outcome = None
    if report.when == "call":
        wasxfail = getattr(report, "wasxfail", None)
        if report.passed:
            outcome = "passed"
            _session_stats["passed"] += 1
            _test_results_ordered.append("passed")
        elif report.failed and wasxfail:
            outcome = "passed"
            _session_stats["passed"] += 1
            _test_results_ordered.append("passed")
        elif report.failed:
            outcome = "failed"
            _session_stats["failed"] += 1
            _test_results_ordered.append("failed")
        elif report.skipped and wasxfail:
            outcome = "passed"
            _session_stats["passed"] += 1
            _test_results_ordered.append("passed")
        elif report.skipped:
            outcome = "skipped"
            _session_stats["skipped"] += 1
            _test_results_ordered.append("skipped")
    elif report.when == "setup" and report.skipped:
        outcome = "skipped"
        _session_stats["skipped"] += 1
        _test_results_ordered.append("skipped")

    if outcome:
        bucket = "physical" if _is_physical_test(report.nodeid) else "mock"
        _mock_physical_stats[bucket][outcome] += 1


def pytest_runtest_setup(item):
    """Print the test's docstring in color before each test runs."""
    if item.get_closest_marker("cloud"):
        _physical_test_nodeids.add(item.nodeid)
    if item.function.__doc__:
        doc = item.function.__doc__.strip().split("\n")[0]
        _test_docs[item.nodeid] = doc
    # Suppress per-test progress in quiet mode (--no-quiet-mode disables)
    quiet = (
        item.config.getoption("--quiet-mode", default=False)
        and not item.config.getoption("--no-quiet-mode", default=False)
    )
    if quiet:
        return
    if not item.function.__doc__:
        return
    if not (hasattr(sys.stdout, "isatty") and sys.stdout.isatty()):
        return
    doc = _test_docs[item.nodeid]
    mod = item.module.__name__
    _dashboard_mods = (
        "tests.test_database_logic", "tests.test_api", "tests.test_auth", "tests.test_config",
        "tests.test_schema_discovery", "tests.test_schema_mapping", "tests.test_schema_indexing", "tests.test_sql_guardrails", "tests.test_env_integrity",
        "tests.test_cosmos_cloud", "tests.test_cosmos_store", "tests.test_cosmos_integration",
        "tests.test_server_lifespan", "tests.test_redis", "tests.test_cache_connection",
        "tests.test_cache_logic", "tests.test_semantic_cache", "tests.test_semantic_precision",
        "tests.test_azure_config",
        "tests.test_llm_providers", "tests.test_new_providers", "tests.test_model_discovery",
        "tests.test_gemini_get_models",
    )
    if mod in _dashboard_mods:
        cat, color = _get_category(item.nodeid)
        if cat:
            tag = _format_category(cat, item.nodeid)
            prefix = f"{color}{_BOLD}[{tag}]{_RESET}"
            print(f"\n  {prefix} {doc}{_RESET}", flush=True)


def _print_status_gauge(terminalreporter, use_color, n_passed, n_failed, n_skipped):
    """Print temperature-gauge style success indicator with legend.
    Uses session_stats counts (call-phase only) to avoid triple-counting from plugins."""
    total = n_passed + n_failed + n_skipped
    if total == 0:
        return
    pct = round(100 * n_passed / total) if total else 0

    terminalreporter.write_line("")
    terminalreporter.write_line(f"{'─' * 70}")
    terminalreporter.write_line("  SESSION STATUS (Temperature Gauge)")
    terminalreporter.write_line(f"{'─' * 70}")

    # Temperature bar: 20 chars wide, filled proportionally
    bar_width = 20
    filled = int(bar_width * n_passed / total) if total else 0
    bar = "█" * filled + "░" * (bar_width - filled)

    # Temperature gauge + legend
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.text import Text

        console = Console(force_terminal=use_color)
        if pct == 100:
            style = "bold green"
            label = f"[{bar}] 100% SUCCESS"
        elif pct >= 80:
            style = "bold yellow"
            label = f"[{bar}] {pct}% SUCCESSFUL"
        else:
            style = "bold red"
            label = f"[{bar}] {pct}% SUCCESSFUL"
        text = Text(label, style=style)
        console.print(Panel(text, title="SUCCESS GAUGE", border_style="dim"))
        # Legend: what the gauge represents
        console.print(f"  >> Legend: Pass={n_passed}  Fail={n_failed}  Skip={n_skipped}  (total {total})")
        # Project phase (Phase 2 = Embeddings + Similarity Search complete)
        phase = 2 if n_passed == total and total > 0 else 1
        phase_text = f"Phase {phase} (COMPLETE)" if pct == 100 else f"Phase {phase} (IN PROGRESS)"
        console.print(f"  >> PROJECT PHASE: {phase_text}")
    except ImportError:
        # Fallback: plain text or ANSI
        b, g, r = (_BOLD, _GREEN, _RESET) if use_color else ("", "", "")
        terminalreporter.write_line(f"  [{bar}] {pct}%")
        terminalreporter.write_line(f"  >> Legend: Pass={n_passed}  Fail={n_failed}  Skip={n_skipped}  (total {total})")
        if pct == 100:
            terminalreporter.write_line(f"  >> SESSION STATUS: {b}{g}100% SUCCESS{r}")
        else:
            terminalreporter.write_line(f"  >> SESSION STATUS: {pct}% SUCCESSFUL")
        terminalreporter.write_line("  >> PROJECT PHASE: 1 (COMPLETE)" if pct == 100 else "  >> PROJECT PHASE: 1 (IN PROGRESS)")


def _print_mock_physical_section(terminalreporter, use_color):
    """Print Mock vs Physical test counts and a small bar chart."""
    mock = _mock_physical_stats["mock"]
    phys = _mock_physical_stats["physical"]
    mock_total = mock["passed"] + mock["failed"] + mock["skipped"]
    phys_total = phys["passed"] + phys["failed"] + phys["skipped"]
    total = mock_total + phys_total
    if total == 0:
        return

    b, r = (_BOLD, _RESET) if use_color else ("", "")
    g, red, y = (_GREEN, _RED, _YELLOW) if use_color else ("", "", "")
    dim = _DIM if use_color else ""

    terminalreporter.write_line("")
    terminalreporter.write_line(f"{b}{'─' * 70}{r}")
    terminalreporter.write_line(f"{b}  Mock vs Physical{r}")
    terminalreporter.write_line(f"{b}{'─' * 70}{r}")

    # Counts
    terminalreporter.write_line(
        f"  Mock:     {mock_total} run  →  {g}{mock['passed']} pass{r}  {red}{mock['failed']} fail{r}  {y}{mock['skipped']} skip{r}"
    )
    terminalreporter.write_line(
        f"  Physical: {phys_total} run  →  {g}{phys['passed']} pass{r}  {red}{phys['failed']} fail{r}  {y}{phys['skipped']} skip{r}"
    )

    # Bar chart: 40 chars wide, scaled to max of the two
    bar_width = 40
    max_val = max(mock_total, phys_total, 1)
    mock_fill = int(bar_width * mock_total / max_val) if max_val else 0
    phys_fill = int(bar_width * phys_total / max_val) if max_val else 0
    mock_bar = "█" * mock_fill + "░" * (bar_width - mock_fill)
    phys_bar = "█" * phys_fill + "░" * (bar_width - phys_fill)
    terminalreporter.write_line("")
    terminalreporter.write_line(f"  {dim}Mock     [{mock_bar}] {mock_total}{r}")
    terminalreporter.write_line(f"  {dim}Physical [{phys_bar}] {phys_total}{r}")
    terminalreporter.write_line(f"{b}{'─' * 70}{r}")


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Print a dashboard-style summary of test results at the end."""
    use_color = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    b, r = (_BOLD, _RESET) if use_color else ("", "")
    key_mods = (
        "test_database_logic", "test_api", "test_auth", "test_config", "test_schema_discovery", "test_schema_mapping", "test_schema_indexing", "test_sql_guardrails",
        "test_env_integrity",
        "test_cosmos_cloud", "test_cosmos_integration", "test_cosmos_store",
        "test_server_lifespan", "test_redis", "test_cache_connection", "test_cache_logic",
        "test_semantic_cache", "test_semantic_precision", "test_azure_config", "test_llm_providers", "test_new_providers",
        "test_model_discovery", "test_gemini_get_models",
        "live/",  # tests/live/* (auth, gui login, entra user flow)
    )

    # Warning Dashboard (before Test Dashboard): group by message, show one test per unique warning
    all_warnings = terminalreporter.stats.get("warnings", [])
    warn_dashboard = [(w, w.nodeid) for w in all_warnings if w.nodeid and any(m in w.nodeid for m in key_mods)]
    if warn_dashboard:
        warn_sym = "\033[93m⚠\033[0m" if use_color else "WARN"
        by_msg = {}
        for wr, nodeid in warn_dashboard:
            raw = (wr.message or "").strip().split("\n")[0]
            parts = raw.split(":", 3)
            m = parts[-1].strip() if len(parts) >= 4 else raw
            if m not in by_msg:
                by_msg[m] = []
            by_msg[m].append(nodeid)
        terminalreporter.write_line("")
        terminalreporter.write_line(f"{b}{'─' * 70}{r}")
        terminalreporter.write_line(f"{b}  Warning Dashboard{r}")
        terminalreporter.write_line(f"{b}{'─' * 70}{r}")
        for msg_full, nodeids in by_msg.items():
            nodeid = nodeids[0]
            cat, color = _get_category(nodeid)
            if not cat:
                continue
            tag = _format_category(cat, nodeid)
            doc = _test_docs.get(nodeid, nodeid.split("::")[-1])
            msg = msg_full[:72] + "..." if len(msg_full) > 75 else msg_full
            c = color if use_color else ""
            count = f" ({len(nodeids)} occurrence{'s' if len(nodeids) > 1 else ''})" if len(nodeids) > 1 else ""
            terminalreporter.write_line(f"  {warn_sym} {c}[{tag}]{r} {doc}{count}")
            terminalreporter.write_line(f"      {msg}")
        terminalreporter.write_line(f"{b}{'─' * 70}{r}")

    # Test Dashboard
    passed = terminalreporter.stats.get("passed", [])
    failed = terminalreporter.stats.get("failed", [])
    skipped = terminalreporter.stats.get("skipped", [])
    xfailed = terminalreporter.stats.get("xfailed", [])
    xfailed_reports = [(r[0] if isinstance(r, tuple) else r, "xfailed") for r in xfailed]
    all_reports = (
        [(r, "passed") for r in passed]
        + [(r, "failed") for r in failed]
        + [(r, "skipped") for r in skipped]
        + xfailed_reports
    )
    dashboard_raw = [(r, status) for r, status in all_reports if any(m in r.nodeid for m in key_mods)]
    # Deduplicate by nodeid (some envs report same test multiple times); keep worst status
    _status_rank = {"failed": 3, "skipped": 2, "passed": 1, "xfailed": 1}
    by_nodeid = {}
    for r, status in dashboard_raw:
        if r.nodeid not in by_nodeid or _status_rank.get(status, 0) > _status_rank.get(by_nodeid[r.nodeid][1], 0):
            by_nodeid[r.nodeid] = (r, status)
    dashboard = list(by_nodeid.values())
    b, r = (_BOLD, _RESET) if use_color else ("", "")
    pass_sym = _PASS if use_color else "PASS"
    fail_sym = _FAIL if use_color else "FAIL"
    skip_sym = _SKIP if use_color else "SKIP"
    xfail_sym = f"{_GREEN}XFAIL{_RESET}" if use_color else "XFAIL"
    if dashboard:
        # For parametrized tests, extract [param] from nodeid so each variant has a unique line
        def _display_doc(report):
            doc = _test_docs.get(report.nodeid, report.nodeid.split("::")[-1])
            tail = report.nodeid.split("::")[-1] if "::" in report.nodeid else report.nodeid
            param_suffix = ""
            if "[" in tail and "]" in tail:
                param = tail[tail.index("[") + 1 : tail.rindex("]")]
                param_suffix = f" [{param}]"
            # Truncate base doc so total fits in 78 chars (param_suffix stays visible)
            max_base = 78 - len(param_suffix)
            if len(doc) > max_base:
                doc = doc[: max_base - 3] + "..."
            return doc + param_suffix

        _cat_colors = {
            "DB": _CYAN, "API": _GREEN, "AUTH": _GREEN, "CONFIG": _CYAN, "SCHEMA": _CYAN, "GUARDRAILS": _CYAN,
            "ENV-INTEGRITY": _CYAN, "ENV-READY": _CYAN,
            "CLOUD-CONN": _YELLOW, "CLOUD-REDIS": _YELLOW, "CLOUD-L1": _YELLOW,
            "CLOUD-CACHE": _YELLOW, "CLOUD-LIFESPAN": _YELLOW, "CLOUD-COSMOS": _YELLOW,
            "CLOUD-AZURE": _YELLOW,
            "LLM-PROVIDERS": _MAGENTA, "LLM-REGISTRY": _MAGENTA,
            "LLM-MODELS": _MAGENTA, "LLM-GEMINI": _MAGENTA,
        }
        terminalreporter.write_line("")
        terminalreporter.write_line(f"{b}{'─' * 70}{r}")
        terminalreporter.write_line(f"{b}  Test Dashboard{r}")
        terminalreporter.write_line(f"{b}{'─' * 70}{r}")
        for report, status in dashboard:
            quiet = (
                config.getoption("--quiet-mode", default=False)
                and not config.getoption("--no-quiet-mode", default=False)
            )
            if quiet and status == "passed":
                continue
            cat, _ = _get_category(report.nodeid)
            if cat:
                tag = _format_category(cat, report.nodeid)
                sym = (
                    pass_sym
                    if status == "passed"
                    else (xfail_sym if status == "xfailed" else (fail_sym if status == "failed" else skip_sym))
                )
                cc = _cat_colors.get(cat, _CYAN) if use_color else ""
                doc = _display_doc(report)
                terminalreporter.write_line(f"  {sym} {cc}[{tag}]{r} {doc}")
        terminalreporter.write_line(f"{b}{'─' * 70}{r}")

    # Catalog Summary: what each category tests and what pass/fail means (only when dashboard tests ran)
    if dashboard:
        # Each entry: (what_it_tests, pass_technical, pass_big_picture, fail_technical, fail_big_picture)
        _CATALOG = {
            "DB": (
                "Database logic (CosmosStore fetch/extend/upsert, mocked).",
                "Conversation persistence and history extension work correctly.",
                "Chat history is saved and restored across sessions.",
                "Data may not save or load; chat history could be lost.",
                "Your conversations may disappear or fail to load.",
            ),
            "API": (
            "API flow (FastAPI routes, chat POST → store).",
            "Server correctly receives requests and persists chat records.",
            "The app can send messages and receive AI replies.",
            "API may reject valid requests or fail to save data.",
            "Chat may not work; messages may not send or save.",
        ),
        "AUTH": (
            "Authentication (JWT validation, protected endpoints).",
            "Auth gate blocks unauthenticated requests; valid tokens work.",
            "Login protects your data; only you can access your chats.",
            "Unauthorized access possible or valid users may be blocked.",
            "Anyone might access your data, or you may be locked out.",
        ),
        "CONFIG": (
            "Config loading (env vars, .env injection, CACHE_THRESHOLD).",
            "Secrets and settings load from env/YAML; CACHE_THRESHOLD (0.0–1.0) for semantic cache precision.",
            "AI providers and database connections are configured; cache matching strictness is tunable.",
            "Config may be wrong or missing; LLM keys or CACHE_THRESHOLD may not resolve.",
            "The app may not connect to AI or your database; cache may be too strict or too loose.",
        ),
        "SCHEMA": (
            "Schema discovery and semantic mapping (information_schema, vectorize_schema, get_relevant_tables).",
            "SchemaManager extracts metadata; vectorize_schema stores DDL embeddings; get_relevant_tables prunes by query.",
            "The agent sends only relevant tables to the LLM; table pruning reduces cost and confusion.",
            "Schema extraction or vector store may fail; fallback to full schema or empty context.",
            "SQL may reference wrong tables; SCHEMA_VECTOR_LIMIT and threshold affect pruning quality.",
        ),
        "GUARDRAILS": (
            "SQL guardrails (lexical block, scope check, injection shield).",
            "SQLValidator blocks DML, out-of-scope tables, and multi-statement injection.",
            "LLM-generated SQL cannot damage data; only allowed read-only queries execute.",
            "Validator may block valid queries or miss novel attack patterns.",
            "Malicious or buggy SQL could reach the database.",
        ),
        "ENV-INTEGRITY": (
            ".env file integrity (no duplicate keys, no malformed KEY=value lines).",
            "Each key appears once; all entries match KEY=value format; clean_env works.",
            "Your .env is clean and predictable; Smart Append and clean_env keep it that way.",
            "Duplicate keys or malformed lines detected; run ./scripts/cli.sh env-clean to fix.",
            "Config may load wrong values; run env-clean to deduplicate and fix.",
        ),
        "ENV-READY": (
            "Env component detection (Redis, Cosmos, DB, LLM, Key Vault).",
            "Each component correctly detected from REDIS_URL, COSMOS_ENDPOINT, daibai.yaml, etc.",
            "Run ./scripts/cli.sh is-ready to see what is configured.",
            "Component detection logic may be wrong.",
            "is-ready report may be inaccurate.",
        ),
        "CLOUD-CONN": (
            "Redis connection (CacheManager.ping, config wiring).",
            "Cache can connect to Redis; config and error handling work.",
            "Smart caching is ready to save LLM costs.",
            "Redis unreachable or config wrong; cache will not work.",
            "Cost-saving cache is offline; every query hits the AI.",
        ),
        "CLOUD-REDIS": (
            "Redis key-value ops (add/retrieve/delete, live).",
            "Redis read/write works; cache storage is functional.",
            "Cached answers are stored and retrieved correctly.",
            "Redis I/O failing; cache may be unusable.",
            "Cache cannot store or recall answers.",
        ),
        "CLOUD-L1": (
            "L1 cache (get/set, set_semantic, get_embedding, singleton).",
            "Key-value and semantic storage work; embeddings load once.",
            "Intent matching works: similar questions reuse answers.",
            "Cache storage or embedding logic broken; semantic cache may fail.",
            "Repeated questions may not get cached; higher AI bills.",
        ),
        "CLOUD-CACHE": (
            "Semantic cache (similarity search, CACHE_THRESHOLD, retrieval by intent).",
            "Similar prompts return cached responses; CACHE_THRESHOLD (0.0–1.0) controls match strictness.",
            "Ask the same thing different ways—you get cached answers, not new AI calls.",
            "Similarity retrieval broken; CACHE_THRESHOLD may be misconfigured; cache may miss or never hit.",
            "You pay for every query even when you already asked something similar.",
        ),
        "CLOUD-LIFESPAN": (
            "FastAPI lifespan (CosmosStore init/shutdown).",
            "App starts and stops cleanly; connections closed properly.",
            "The server starts reliably and shuts down without leaks.",
            "Startup/shutdown may leak connections or fail.",
            "Server may crash on start/stop or leave connections open.",
        ),
        "CLOUD-COSMOS": (
            "Cosmos DB (CosmosStore lifecycle, real Azure).",
            "Cosmos read/write works; chat history persisted to Azure.",
            "Your chats are safely stored in the cloud.",
            "Cosmos unreachable or permissions wrong; data may not persist.",
            "Chat history may not sync to Azure; data could be lost.",
        ),
        "CLOUD-AZURE": (
            "Azure Key Vault and config (DefaultAzureCredential).",
            "Secrets load from Key Vault; cloud config works.",
            "API keys and secrets are securely stored in Azure.",
            "Key Vault unreachable or wrong; secrets may not load.",
            "The app may not find your API keys; AI calls may fail.",
        ),
        "LLM-PROVIDERS": (
            "LLM provider classes (registration, inheritance).",
            "Provider registry and model wiring correct.",
            "You can switch between Gemini, OpenAI, Anthropic, etc.",
            "Provider setup broken; LLM calls may fail.",
            "AI providers may not work; chat may be unavailable.",
        ),
        "LLM-REGISTRY": (
            "New provider registry (add custom providers).",
            "Custom providers can be registered and used.",
            "You can add your own AI providers.",
            "Registry or extension logic broken.",
            "Custom providers may not load or work.",
        ),
        "LLM-MODELS": (
            "Model discovery (fetch, sanitization).",
            "Models loaded and sanitized correctly.",
            "You see the right model list for each provider.",
            "Model list or config may be wrong.",
            "Wrong or missing models; you may not see your preferred model.",
        ),
        "LLM-GEMINI": (
            "Gemini API (get-models, auth).",
            "Gemini connectivity and model listing work.",
            "Gemini is connected and ready for chat.",
            "Gemini API unreachable or key invalid.",
            "Gemini may not respond; check your API key.",
        ),
        }
        cats_in_run = {_get_category(r.nodeid)[0] for r, _ in dashboard if _get_category(r.nodeid)[0]}
        cats_with_failures = {
            _get_category(r.nodeid)[0] for r, status in dashboard
            if status == "failed" and _get_category(r.nodeid)[0]
        }
        _CAT_COLORS = {
            "DB": _CYAN, "API": _GREEN, "AUTH": _GREEN, "CONFIG": _CYAN, "SCHEMA": _CYAN, "GUARDRAILS": _CYAN,
            "ENV-INTEGRITY": _CYAN, "ENV-READY": _CYAN,
            "CLOUD-CONN": _YELLOW, "CLOUD-REDIS": _YELLOW, "CLOUD-L1": _YELLOW,
            "CLOUD-CACHE": _YELLOW, "CLOUD-LIFESPAN": _YELLOW, "CLOUD-COSMOS": _YELLOW,
            "CLOUD-AZURE": _YELLOW,
            "LLM-PROVIDERS": _MAGENTA, "LLM-REGISTRY": _MAGENTA,
            "LLM-MODELS": _MAGENTA, "LLM-GEMINI": _MAGENTA,
        }
        quiet_mode = (
            config.getoption("--quiet-mode", default=False)
            and not config.getoption("--no-quiet-mode", default=False)
        )
        terminalreporter.write_line("")
        terminalreporter.write_line(f"{b}{'─' * 70}{r}")
        terminalreporter.write_line(f"{b}  Catalog Summary{r}")
        terminalreporter.write_line(f"{b}{'─' * 70}{r}")
        terminalreporter.write_line(f"  {_DIM if use_color else ''}[CAT]=mock (fakeredis, mocked embeddings)  [CAT☁]=physical (real Redis, Cosmos, embeddings){r}")
        for cat in sorted(_CATALOG.keys(), key=lambda c: (c.startswith("CLOUD-"), c)):
            if cat not in cats_in_run:
                continue
            what, pass_tech, pass_big, fail_tech, fail_big = _CATALOG[cat]
            cc = _CAT_COLORS.get(cat, "") if use_color else ""
            dim = _DIM if use_color else ""
            passed = cat not in cats_with_failures
            pass_label = f"{_GREEN}PASS{r}" if use_color else "PASS"
            fail_label = f"{_RED}FAIL{r}" if use_color else "FAIL"
            label = pass_label if passed else fail_label
            if quiet_mode:
                big = fail_big if cat in cats_with_failures else pass_big
                terminalreporter.write_line(f"  {cc}[{cat}]{r} {label} {big}")
            else:
                terminalreporter.write_line(f"  {cc}[{cat}]{r} {label} {what}")
                if cat in cats_with_failures:
                    terminalreporter.write_line(f"      {dim}Fail-Technical:{r} {fail_tech}")
                    terminalreporter.write_line(f"      {dim}Fail-Big Picture:{r} {fail_big}")
                else:
                    terminalreporter.write_line(f"      {dim}Pass-Technical:{r} {pass_tech}")
                    terminalreporter.write_line(f"      {dim}Pass-Big Picture:{r} {pass_big}")
        terminalreporter.write_line(f"{b}{'─' * 70}{r}")

    # Error Dashboard (last): failed tests and setup/teardown/collection errors
    failed_reports = terminalreporter.stats.get("failed", [])
    error_reports = terminalreporter.stats.get("error", [])
    err_items = [(r, "failed") for r in failed_reports] + [(r, "error") for r in error_reports]
    err_dashboard = [(r, kind) for r, kind in err_items if r.nodeid and any(m in r.nodeid for m in key_mods)]
    if err_dashboard:
        err_sym = _FAIL if use_color else "FAIL"
        terminalreporter.write_line("")
        terminalreporter.write_line(f"{b}{'─' * 70}{r}")
        terminalreporter.write_line(f"{b}  Error Dashboard{r}")
        terminalreporter.write_line(f"{b}{'─' * 70}{r}")
        for report, kind in err_dashboard:
            cat, color = _get_category(report.nodeid)
            if not cat:
                continue
            tag = _format_category(cat, report.nodeid)
            doc = _test_docs.get(report.nodeid, report.nodeid.split("::")[-1])
            if len(doc) > 78:
                doc = doc[:75] + "..."
            msg = ""
            try:
                if hasattr(report, "longrepr") and report.longrepr is not None:
                    lr = report.longrepr
                    if hasattr(lr, "reprcrash") and lr.reprcrash is not None:
                        msg = getattr(lr.reprcrash, "message", "") or ""
                    elif isinstance(lr, str):
                        msg = lr.split("\n")[0]
                if not msg and hasattr(report, "longreprtext"):
                    msg = (report.longreprtext or "").split("\n")[0]
            except Exception:
                pass
            if len(msg) > 58:
                msg = msg[:55] + "..."
            c = color if use_color else ""
            tag_str = f"[{tag}]" if tag else ""
            terminalreporter.write_line(f"  {err_sym} {c}{tag_str}{r} {doc}")
            if msg:
                terminalreporter.write_line(f"      {msg}")
        terminalreporter.write_line(f"{b}{'─' * 70}{r}")

    # Skipped Tests (with reasons): every skipped test must have a logged reason
    skipped_reports = terminalreporter.stats.get("skipped", [])
    if skipped_reports:
        terminalreporter.write_line("")
        terminalreporter.write_line(f"{b}{'─' * 70}{r}")
        terminalreporter.write_line(f"{b}  Skipped Tests (reasons){r}")
        terminalreporter.write_line(f"{b}{'─' * 70}{r}")
        for report in skipped_reports:
            if not report.nodeid:
                continue
            reason = _get_skip_reason(report)
            cat, color = _get_category(report.nodeid)
            tag = _format_category(cat, report.nodeid) if cat else ""
            doc = _test_docs.get(report.nodeid, report.nodeid.split("::")[-1])
            if len(doc) > 50:
                doc = doc[:47] + "..."
            skip_sym = _SKIP if use_color else "SKIP"
            cc = color if use_color and cat else ""
            tag_str = f"{cc}[{tag}]{r} " if tag else ""
            terminalreporter.write_line(f"  {skip_sym} {tag_str}{doc}")
            terminalreporter.write_line(f"      {_DIM if use_color else ''}Reason: {reason}{r}")
        terminalreporter.write_line(f"{b}{'─' * 70}{r}")

    # Mock vs Physical: counts and bar chart
    _print_mock_physical_section(terminalreporter, use_color)

    # Status Gauge — xfailed counted as passed in pytest_runtest_logreport
    _print_status_gauge(
        terminalreporter,
        use_color,
        _session_stats["passed"],
        _session_stats["failed"],
        _session_stats["skipped"],
    )
