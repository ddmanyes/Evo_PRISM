"""
Security and behavioral tests for server/code_executor.py (AA4).

Tests the sandbox boundary: ALLOWED_IMPORTS whitelist, BLOCKED_PATTERNS blacklist,
timeout enforcement, CWD isolation, and AST-level import detection.
All tests use is_safe() / _check_imports() directly where possible to avoid
spawning real subprocesses (faster, CI-friendly). subprocess tests are marked
with @pytest.mark.slow and only exercise the full sandbox_exec() path.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from server.code_executor import (
    ALLOWED_IMPORTS,
    BLOCKED_PATTERNS,
    SANDBOX_CWD,
    SecurityError,
    _check_imports,
    is_safe,
    sandbox_exec,
)


# ── ALLOWED_IMPORTS whitelist ────────────────────────────────────────────────


class TestAllowedImports:
    """Whitelist: listed modules must pass AST import check."""

    def test_pandas_allowed(self):
        ok, reason = is_safe("import pandas as pd")
        assert ok, reason

    def test_numpy_allowed(self):
        ok, reason = is_safe("import numpy as np")
        assert ok, reason

    def test_json_allowed(self):
        ok, reason = is_safe("import json")
        assert ok, reason

    def test_analysis_spatial_eda_allowed(self):
        ok, reason = is_safe("from analysis.spatial_eda import run_spatial_qc")
        assert ok, reason

    def test_scipy_stats_allowed(self):
        ok, reason = is_safe("from scipy.stats import ttest_ind")
        assert ok, reason


# ── BLOCKED_PATTERNS blacklist ────────────────────────────────────────────────


class TestBlockedPatterns:
    """Blacklist: each blocked pattern must trigger is_safe() → False."""

    @pytest.mark.parametrize("pattern", [
        "os.system('ls')",
        "os.popen('id')",
        "subprocess.run(['ls'])",
        "eval('1+1')",
        "exec('print(1)')",
        "compile('x=1', '', 'exec')",
        "open('/etc/passwd')",
        "__import__('os')",
        "importlib.import_module('os')",
        "getattr(os, 'system')('ls')",
        "__builtins__['eval']",
        "__class__.__bases__",
        "__subclasses__()",
        "socket.connect(('1.2.3.4', 80))",
        "urllib.request.urlopen('http://x')",
        "requests.get('http://x')",
        "shutil.rmtree('/tmp')",
        "write_to_l1_cache('x', 'y')",
        "safe_write(con, 'INSERT INTO x VALUES (1)')",
        "register_tool(con, 'bad_tool', fn=lambda: None)",
        "df.to_csv('/tmp/leak.csv')",
        "np.save('/tmp/x', arr)",
        ".write_h5ad('/tmp/x.h5ad')",
        "COPY t TO '/tmp/out.csv'",
    ])
    def test_blocked(self, pattern):
        ok, reason = is_safe(pattern)
        assert not ok, f"Expected {pattern!r} to be blocked, but is_safe returned True"

    def test_duckdb_not_allowed(self):
        """duckdb is NOT in ALLOWED_IMPORTS (sandbox must not write DB directly)."""
        violations = _check_imports("import duckdb")
        assert "duckdb" in violations

    def test_config_not_allowed(self):
        """config.settings is not in ALLOWED_IMPORTS (no key leakage)."""
        violations = _check_imports("from config.settings import ANTHROPIC_API_KEY")
        assert "config.settings" in violations or "config" in violations

    def test_l1_cache_not_allowed(self):
        """analysis.l1_cache not in ALLOWED_IMPORTS (no cache writes from sandbox)."""
        violations = _check_imports("from analysis.l1_cache import write_to_l1_cache")
        assert violations  # any violation means it's blocked

    def test_tool_registry_not_allowed(self):
        """analysis.tool_registry not allowed (no HELIX mutations from sandbox)."""
        violations = _check_imports("from analysis.tool_registry import register_tool")
        assert violations


# ── AST import detection ─────────────────────────────────────────────────────


class TestASTImportDetection:
    """_check_imports must catch both 'import X' and 'from X import Y' forms."""

    def test_catches_import_os(self):
        assert "os" in _check_imports("import os")

    def test_catches_from_os_path(self):
        violations = _check_imports("from os.path import join")
        assert violations

    def test_catches_import_sys(self):
        assert "sys" in _check_imports("import sys")

    def test_catches_nested_import(self):
        code = "import os\nimport pandas\nimport subprocess"
        violations = _check_imports(code)
        assert "os" in violations
        assert "subprocess" in violations
        assert "pandas" not in violations  # pandas IS allowed

    def test_syntax_error_raises_security_error(self):
        with pytest.raises(SecurityError, match="語法錯誤"):
            _check_imports("def broken(:\n    pass")

    def test_clean_code_returns_empty(self):
        assert _check_imports("import pandas as pd\nimport numpy as np") == []


# ── CWD isolation ────────────────────────────────────────────────────────────


class TestCWDIsolation:
    """SANDBOX_CWD must NOT point inside the L3 raw data directory."""

    def test_sandbox_cwd_is_bio_db_root(self):
        """SANDBOX_CWD resolves to BIO_DB_ROOT, not a subdirectory with L3 data."""
        from config.settings import BIO_DB_ROOT, L3_ROOT

        cwd = Path(SANDBOX_CWD).resolve()
        l3 = Path(str(L3_ROOT)).resolve()
        # CWD must not be inside L3
        assert not str(cwd).startswith(str(l3)), (
            f"SANDBOX_CWD={cwd} is inside L3_ROOT={l3} — L3 raw data would be accessible"
        )

    def test_sandbox_cwd_equals_bio_db_root(self):
        from config.settings import BIO_DB_ROOT

        assert Path(SANDBOX_CWD).resolve() == Path(str(BIO_DB_ROOT)).resolve()


# ── sandbox_exec() behavioral tests ─────────────────────────────────────────


class TestSandboxExec:
    """Full sandbox_exec() integration tests (spawn real subprocess)."""

    def test_safe_code_succeeds(self):
        result = sandbox_exec("print('hello sandbox')")
        assert result.success
        assert "hello sandbox" in result.output

    def test_blocked_code_raises_security_error(self):
        with pytest.raises(SecurityError):
            sandbox_exec("import os; os.system('ls')")

    def test_blocked_eval_raises_security_error(self):
        with pytest.raises(SecurityError):
            sandbox_exec("eval('1+1')")

    def test_disallowed_import_raises_security_error(self):
        with pytest.raises(SecurityError):
            sandbox_exec("import duckdb")

    def test_timeout_returns_failure(self):
        result = sandbox_exec("while True: pass", timeout=2)
        assert not result.success
        assert "Timeout" in result.traceback or "timeout" in result.traceback.lower()
        assert result.duration_sec >= 2.0

    def test_duration_reported(self):
        result = sandbox_exec("import time; time.sleep(0.1)")
        assert result.duration_sec >= 0.1

    def test_runtime_error_captured(self):
        result = sandbox_exec("raise ValueError('boom')")
        assert not result.success
        assert "boom" in result.traceback

    def test_preamble_not_security_checked(self):
        """Preamble is trusted (injected by the server, not LLM)."""
        result = sandbox_exec(
            code="print(injected_var)",
            preamble="injected_var = 'from_preamble'",
        )
        assert result.success
        assert "from_preamble" in result.output

    def test_json_import_works(self):
        result = sandbox_exec("import json; print(json.dumps({'ok': True}))")
        assert result.success
        assert '"ok": true' in result.output.lower() or "'ok': True" in result.output
