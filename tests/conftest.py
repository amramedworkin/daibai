"""
Pytest configuration: colorized test descriptions and dashboard summary.

Run with: pytest tests/ -v -s
Colors: [DB]=cyan, [API]=green, [CLOUD]=yellow
"""

import sys

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
    """Return (category, color) for a test nodeid."""
    if "test_database_logic" in nodeid:
        return "DB", _CYAN
    if "test_api" in nodeid:
        return "API", _GREEN
    if "test_cosmos_cloud" in nodeid or "test_cosmos_integration" in nodeid or "test_cosmos_store" in nodeid or "test_server_lifespan" in nodeid:
        return "CLOUD", _YELLOW
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
    if mod in ("tests.test_database_logic", "tests.test_api", "tests.test_cosmos_cloud", "tests.test_cosmos_store", "tests.test_server_lifespan"):
        if "test_database_logic" in mod:
            prefix = f"{_CYAN}{_BOLD}[DB]"
        elif "test_api" in mod:
            prefix = f"{_GREEN}{_BOLD}[API]"
        else:
            prefix = f"{_YELLOW}{_BOLD}[CLOUD]"
        print(f"\n  {prefix} {doc}{_RESET}", flush=True)


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Print a dashboard-style summary of test results at the end."""
    use_color = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    # Group reports by category
    stats = terminalreporter.stats
    passed = stats.get("passed", [])
    failed = stats.get("failed", [])
    skipped = stats.get("skipped", [])
    all_reports = [(r, "passed") for r in passed] + [(r, "failed") for r in failed] + [(r, "skipped") for r in skipped]
    # Only show dashboard for our key test modules
    key_mods = ("test_database_logic", "test_api", "test_cosmos_cloud", "test_cosmos_integration", "test_cosmos_store", "test_server_lifespan")
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
            if len(doc) > 58:
                doc = doc[:55] + "..."
            terminalreporter.write_line(f"  {sym} {c}[{cat}]{r} {doc}")
    terminalreporter.write_line(f"{b}{'─' * 70}{r}")
