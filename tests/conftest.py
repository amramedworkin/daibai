"""
Pytest configuration: colorized test descriptions and dashboard summary.

Run with: pytest tests/ -v -s
Colors: [DB]=cyan, [API]=green, [CLOUD-<component>]=yellow (e.g. CLOUD-REDIS, CLOUD-COSMOS, CLOUD-LIFESPAN)
"""

import sys
from pathlib import Path

# Load .env before any tests run so skipif/conditions see REDIS_URL, COSMOS_ENDPOINT, etc.
# Matches config.py: project .env and ~/.daibai/.env. Does not override existing env vars.
from dotenv import load_dotenv

_project_root = Path(__file__).resolve().parent.parent
for loc in [_project_root / ".env", Path.home() / ".daibai" / ".env"]:
    if loc.exists():
        load_dotenv(loc)
        break

# ANSI colors (work in most terminals)
_CYAN = "\033[96m"    # Database, config
_GREEN = "\033[92m"   # API, auth
_YELLOW = "\033[93m"  # Cloud tests
_MAGENTA = "\033[95m"  # LLM providers
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RESET = "\033[0m"
_PASS = "\033[92m✓\033[0m"
_FAIL = "\033[91m✗\033[0m"
_SKIP = "\033[93m⊘\033[0m"

# Map module to category and docstrings (populated during run)
_test_docs = {}
# Ordered list of test outcomes for heatmap (populated during run)
_test_results_ordered = []


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
    if "test_cache_connection" in nodeid:
        return "CLOUD-CONN", _YELLOW
    if "test_redis" in nodeid:
        return "CLOUD-REDIS", _YELLOW
    if "test_cache_logic" in nodeid:
        return "CLOUD-L1", _YELLOW
    if "test_semantic_cache" in nodeid:
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
    return None, None


def pytest_sessionstart(session):
    """Clear ordered results at session start."""
    global _test_results_ordered
    _test_results_ordered = []


def pytest_runtest_logreport(report):
    """Collect test outcomes in execution order for the heatmap."""
    if report.when == "call":
        if report.passed:
            _test_results_ordered.append("passed")
        elif report.failed:
            _test_results_ordered.append("failed")
        else:
            _test_results_ordered.append("skipped")


def pytest_runtest_setup(item):
    """Print the test's docstring in color before each test runs."""
    if item.function.__doc__:
        doc = item.function.__doc__.strip().split("\n")[0]
        _test_docs[item.nodeid] = doc
    if not item.function.__doc__:
        return
    if not (hasattr(sys.stdout, "isatty") and sys.stdout.isatty()):
        return
    doc = _test_docs[item.nodeid]
    mod = item.module.__name__
    _dashboard_mods = (
        "tests.test_database_logic", "tests.test_api", "tests.test_auth", "tests.test_config",
        "tests.test_cosmos_cloud", "tests.test_cosmos_store", "tests.test_cosmos_integration",
        "tests.test_server_lifespan", "tests.test_redis", "tests.test_cache_connection",
        "tests.test_cache_logic", "tests.test_semantic_cache", "tests.test_azure_config",
        "tests.test_llm_providers", "tests.test_new_providers", "tests.test_model_discovery",
        "tests.test_gemini_get_models",
    )
    if mod in _dashboard_mods:
        cat, color = _get_category(item.nodeid)
        if cat:
            prefix = f"{color}{_BOLD}[{cat}]{_RESET}"
            print(f"\n  {prefix} {doc}{_RESET}", flush=True)


def _print_status_heatmap(terminalreporter, use_color, passed, failed, skipped):
    """Print heatmap grid and success percentage. Uses rich when available."""
    total = len(passed) + len(failed) + len(skipped)
    if total == 0:
        return
    n_passed = len(passed)
    pct = round(100 * n_passed / total) if total else 0

    # Heatmap from ordered results
    blocks = []
    for outcome in _test_results_ordered:
        if outcome == "passed":
            blocks.append("🟩")
        elif outcome == "failed":
            blocks.append("🟥")
        else:
            blocks.append("🟨")

    terminalreporter.write_line("")
    terminalreporter.write_line(f"{'─' * 70}")
    terminalreporter.write_line("  DAIBAI TEST HEATMAP")
    terminalreporter.write_line(f"{'─' * 70}")

    # Grid: 10 per row
    row_size = 10
    for i in range(0, len(blocks), row_size):
        row = " ".join(blocks[i : i + row_size])
        terminalreporter.write_line(f"  {row}")
    terminalreporter.write_line(f"{'─' * 70}")

    # Success gauge - use rich for bold/color when available
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.text import Text

        console = Console(force_terminal=use_color)
        if pct == 100:
            style = "bold green"
            label = "100% SUCCESS"
        elif pct >= 80:
            style = "bold yellow"
            label = f"{pct}% SUCCESSFUL"
        else:
            style = "bold red"
            label = f"{pct}% SUCCESSFUL"
        text = Text(label, style=style)
        console.print(Panel(text, title="SESSION STATUS", border_style="dim"))
        # Project phase (Phase 2 = Embeddings complete when CLOUD-L1 passes)
        phase = 2 if n_passed == total and total > 0 else 1
        phase_text = f"Phase {phase} (COMPLETE)" if pct == 100 else f"Phase {phase} (IN PROGRESS)"
        console.print(f"  >> PROJECT PHASE: {phase_text}")
    except ImportError:
        # Fallback: plain text or ANSI
        b, g, r = (_BOLD, _GREEN, _RESET) if use_color else ("", "", "")
        if pct == 100:
            terminalreporter.write_line(f"  >> SESSION STATUS: {b}{g}100% SUCCESS{r}")
        else:
            terminalreporter.write_line(f"  >> SESSION STATUS: {pct}% SUCCESSFUL")
        terminalreporter.write_line("  >> PROJECT PHASE: 1 (COMPLETE)" if pct == 100 else "  >> PROJECT PHASE: 1 (IN PROGRESS)")


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Print a dashboard-style summary of test results at the end."""
    use_color = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    b, r = (_BOLD, _RESET) if use_color else ("", "")
    key_mods = (
        "test_database_logic", "test_api", "test_auth", "test_config",
        "test_cosmos_cloud", "test_cosmos_integration", "test_cosmos_store",
        "test_server_lifespan", "test_redis", "test_cache_connection", "test_cache_logic",
        "test_semantic_cache", "test_azure_config", "test_llm_providers", "test_new_providers",
        "test_model_discovery", "test_gemini_get_models",
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
            doc = _test_docs.get(nodeid, nodeid.split("::")[-1])
            msg = msg_full[:72] + "..." if len(msg_full) > 75 else msg_full
            c = color if use_color else ""
            count = f" ({len(nodeids)} occurrence{'s' if len(nodeids) > 1 else ''})" if len(nodeids) > 1 else ""
            terminalreporter.write_line(f"  {warn_sym} {c}[{cat}]{r} {doc}{count}")
            terminalreporter.write_line(f"      {msg}")
        terminalreporter.write_line(f"{b}{'─' * 70}{r}")

    # Test Dashboard
    passed = terminalreporter.stats.get("passed", [])
    failed = terminalreporter.stats.get("failed", [])
    skipped = terminalreporter.stats.get("skipped", [])
    all_reports = [(r, "passed") for r in passed] + [(r, "failed") for r in failed] + [(r, "skipped") for r in skipped]
    dashboard = [(r, status) for r, status in all_reports if any(m in r.nodeid for m in key_mods)]
    b, r = (_BOLD, _RESET) if use_color else ("", "")
    pass_sym = _PASS if use_color else "PASS"
    fail_sym = _FAIL if use_color else "FAIL"
    skip_sym = _SKIP if use_color else "SKIP"
    if dashboard:
        terminalreporter.write_line("")
        terminalreporter.write_line(f"{b}{'─' * 70}{r}")
        terminalreporter.write_line(f"{b}  Test Dashboard{r}")
        terminalreporter.write_line(f"{b}{'─' * 70}{r}")
        for report, status in dashboard:
            cat, color = _get_category(report.nodeid)
            if cat:
                sym = pass_sym if status == "passed" else (fail_sym if status == "failed" else skip_sym)
                c = color if use_color else ""
                doc = _test_docs.get(report.nodeid, report.nodeid.split("::")[-1])
                if len(doc) > 78:
                    doc = doc[:75] + "..."
                terminalreporter.write_line(f"  {sym} {c}[{cat}]{r} {doc}")
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
            "Config loading (env vars, .env injection).",
            "Secrets and settings load from env/YAML as expected.",
            "AI providers and database connections are configured.",
            "Config may be wrong or missing; LLM keys may not resolve.",
            "The app may not connect to AI or your database.",
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
            "Semantic cache (similarity search, retrieval by intent).",
            "Similar prompts return cached responses; LLM cost savings active.",
            "Ask the same thing different ways—you get cached answers, not new AI calls.",
            "Similarity retrieval broken; cache may miss or never hit.",
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
            "DB": _CYAN, "API": _GREEN, "AUTH": _GREEN, "CONFIG": _CYAN,
            "CLOUD-CONN": _YELLOW, "CLOUD-REDIS": _YELLOW, "CLOUD-L1": _YELLOW,
            "CLOUD-CACHE": _YELLOW, "CLOUD-LIFESPAN": _YELLOW, "CLOUD-COSMOS": _YELLOW,
            "CLOUD-AZURE": _YELLOW,
            "LLM-PROVIDERS": _MAGENTA, "LLM-REGISTRY": _MAGENTA,
            "LLM-MODELS": _MAGENTA, "LLM-GEMINI": _MAGENTA,
        }
        terminalreporter.write_line("")
        terminalreporter.write_line(f"{b}{'─' * 70}{r}")
        terminalreporter.write_line(f"{b}  Catalog Summary{r}")
        terminalreporter.write_line(f"{b}{'─' * 70}{r}")
        for cat in sorted(_CATALOG.keys(), key=lambda c: (c.startswith("CLOUD-"), c)):
            if cat not in cats_in_run:
                continue
            what, pass_tech, pass_big, fail_tech, fail_big = _CATALOG[cat]
            cc = _CAT_COLORS.get(cat, "") if use_color else ""
            dim = _DIM if use_color else ""
            terminalreporter.write_line(f"  {cc}[{cat}]{r} {what}")
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
            tag = f"[{cat}]" if cat else ""
            terminalreporter.write_line(f"  {err_sym} {c}{tag}{r} {doc}")
            if msg:
                terminalreporter.write_line(f"      {msg}")
        terminalreporter.write_line(f"{b}{'─' * 70}{r}")

    # Status Heatmap and Success Gauge
    _print_status_heatmap(terminalreporter, use_color, passed, failed, skipped)
