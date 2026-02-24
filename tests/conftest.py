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
_CYAN = "\033[96m"    # Database tests
_GREEN = "\033[92m"   # API tests
_YELLOW = "\033[93m"  # Cloud tests
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RESET = "\033[0m"
_PASS = "\033[92m✓\033[0m"
_FAIL = "\033[91m✗\033[0m"
_SKIP = "\033[93m⊘\033[0m"

# Map module to category and docstrings (populated during run)
_test_docs = {}


def _get_category(nodeid):
    """Return (category, color) for a test nodeid. Cloud tests get [CLOUD-<component>]."""
    if "test_database_logic" in nodeid:
        return "DB", _CYAN
    if "test_api" in nodeid:
        return "API", _GREEN
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
    return None, None


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
    if mod in ("tests.test_database_logic", "tests.test_api", "tests.test_cosmos_cloud", "tests.test_cosmos_store", "tests.test_server_lifespan", "tests.test_redis", "tests.test_cache_connection", "tests.test_cache_logic", "tests.test_semantic_cache"):
        cat, color = _get_category(item.nodeid)
        if cat:
            prefix = f"{color}{_BOLD}[{cat}]{_RESET}"
            print(f"\n  {prefix} {doc}{_RESET}", flush=True)


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Print a dashboard-style summary of test results at the end."""
    use_color = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    b, r = (_BOLD, _RESET) if use_color else ("", "")
    key_mods = ("test_database_logic", "test_api", "test_cosmos_cloud", "test_cosmos_integration", "test_cosmos_store", "test_server_lifespan", "test_redis", "test_cache_connection", "test_cache_logic", "test_semantic_cache")

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
    if not dashboard:
        return
    b, r = (_BOLD, _RESET) if use_color else ("", "")
    pass_sym = _PASS if use_color else "PASS"
    fail_sym = _FAIL if use_color else "FAIL"
    skip_sym = _SKIP if use_color else "SKIP"
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
