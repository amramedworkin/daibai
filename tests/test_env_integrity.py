"""Tests for .env integrity: no duplicate keys, no malformed entries, EnvValidator."""

import re
from pathlib import Path

import pytest

from daibai.core.env_ready import EnvValidator

# Project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Only check project .env to avoid duplicate runs when both project and ~/.daibai/.env exist.
# (Both would have .name=".env", causing duplicate parametrize ids and confusing dashboard.)
_PROJECT_ENV = _PROJECT_ROOT / ".env"

# Valid KEY=value line (key: identifier, value: anything)
_KEY_VALUE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def _get_env_files():
    """Return project .env if it exists (single source to avoid triple-reporting)."""
    return [_PROJECT_ENV] if _PROJECT_ENV.exists() else []


def _parse_env_keys(path: Path) -> tuple[list[str], list[tuple[int, str]]]:
    """Return (list of keys in order, list of (line_num, malformed_line))."""
    keys = []
    malformed = []
    for i, line in enumerate(path.read_text().splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _KEY_VALUE_RE.match(stripped)
        if m:
            keys.append(m.group(1))
        else:
            malformed.append((i, stripped[:60]))
    return keys, malformed


@pytest.mark.parametrize("env_path", _get_env_files(), ids=lambda p: "project_env")
def test_env_no_duplicate_keys(env_path):
    """No duplicate keys in .env; each setting appears exactly once."""
    keys, _ = _parse_env_keys(env_path)
    seen = {}
    duplicates = []
    for i, k in enumerate(keys):
        if k in seen:
            duplicates.append(f"{k} (lines {seen[k] + 1} and {i + 1})")
        else:
            seen[k] = i
    assert not duplicates, f"Duplicate keys in {env_path}: {duplicates}"


@pytest.mark.parametrize("env_path", _get_env_files(), ids=lambda p: "project_env")
def test_env_no_malformed_entries(env_path):
    """All non-comment lines in .env match KEY=value format."""
    _, malformed = _parse_env_keys(env_path)
    assert not malformed, (
        f"Malformed entries in {env_path}: "
        + ", ".join(f"line {n}: {repr(m[:40])}" for n, m in malformed)
    )


def test_clean_env_removes_duplicates(tmp_path):
    """clean_env.py removes duplicate keys, keeps last value."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "clean_env", _PROJECT_ROOT / "scripts" / "clean_env.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)


    env_file = tmp_path / ".env"
    env_file.write_text(
        "REDIS_URL=first\n"
        "REDIS_URL=second\n"
        "REDIS_URL=third\n"
        "GEMINI_API_KEY=abc\n"
    )
    changed, _ = mod.clean_env(env_file)
    assert changed
    content = env_file.read_text()
    assert content.count("REDIS_URL=") == 1
    assert "REDIS_URL=third" in content
    assert "GEMINI_API_KEY=abc" in content


def test_clean_env_removes_malformed(tmp_path):
    """clean_env.py removes malformed lines, preserves valid ones."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "clean_env", _PROJECT_ROOT / "scripts" / "clean_env.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "VALID_KEY=ok\n"
        "no-equals-here\n"
        "=no_key\n"
        "ANOTHER_VALID=yes\n"
    )
    changed, _ = mod.clean_env(env_file)
    assert changed
    content = env_file.read_text()
    assert "VALID_KEY=ok" in content
    assert "ANOTHER_VALID=yes" in content
    assert "no-equals-here" not in content
    assert "=no_key" not in content


def test_env_validator_fails_when_db_host_missing(monkeypatch):
    """EnvValidator returns (False, issues) when DB_HOST is missing (env-based DB)."""
    for k in ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME", "MYSQL_HOST", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr("daibai.core.env_ready._database_configured_via_yaml", lambda: False)
    monkeypatch.setenv("GEMINI_API_KEY", "valid-key-abc123")

    is_valid, issues = EnvValidator.validate()
    assert is_valid is False
    assert "DB_HOST" in issues or "DB_USER" in issues


def test_env_validator_fails_on_placeholder_llm_key(monkeypatch):
    """EnvValidator returns (False, issues) when GEMINI_API_KEY is placeholder."""
    monkeypatch.setattr("daibai.core.env_ready._database_configured_via_yaml", lambda: True)
    monkeypatch.setenv("GEMINI_API_KEY", "your-api-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("KEY_VAULT_URL", raising=False)

    is_valid, issues = EnvValidator.validate()
    assert is_valid is False
    assert any("AT_LEAST_ONE_OF" in issue for issue in issues)


def test_env_validator_passes_with_valid_mock_data(monkeypatch):
    """EnvValidator returns (True, []) with valid mock env."""
    monkeypatch.setattr("daibai.core.env_ready._database_configured_via_yaml", lambda: True)
    monkeypatch.setenv("GEMINI_API_KEY", "sk-valid-key-abc123xyz")

    is_valid, issues = EnvValidator.validate()
    assert is_valid is True
    assert len(issues) == 0


def test_clean_env_idempotent_on_clean_file(tmp_path):
    """clean_env.py leaves an already-clean .env unchanged."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "clean_env", _PROJECT_ROOT / "scripts" / "clean_env.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    env_file = tmp_path / ".env"
    env_file.write_text("REDIS_URL=rediss://x@host:6380\n# comment\n\nGEMINI_API_KEY=key\n")
    changed, _ = mod.clean_env(env_file)
    assert not changed
