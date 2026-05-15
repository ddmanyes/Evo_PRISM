"""
Phase 5 — 動態程式碼沙盒執行器。

現階段（macOS 測試）：subprocess + timeout + ALLOWED_IMPORTS 過濾。
Linux 部署後：只需替換 sandbox_exec() 內部（外部介面不變）。

安全設計：
    - ALLOWED_IMPORTS：白名單，只允許科學運算 + 專案套件
    - BLOCKED_PATTERNS：黑名單，禁止系統呼叫、eval/exec、開檔
    - SANDBOX_CWD：沙盒工作目錄設定為 bio_DB/（不含 L3 原始數據）
    - timeout=60：防止無限迴圈

使用範例：
    result = sandbox_exec("import pandas as pd; print(pd.__version__)")
    if result.success:
        print(result.output)
    else:
        print(result.traceback)
"""

from __future__ import annotations

import os
import sys
import ast
import tempfile
import subprocess
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import BIO_DB_ROOT

# ── 安全策略 ──────────────────────────────────────────────────────────────────

ALLOWED_IMPORTS = {
    # 科學運算
    "duckdb", "pandas", "numpy", "scipy", "scipy.stats",
    "matplotlib", "matplotlib.pyplot", "seaborn",
    "sklearn", "sklearn.preprocessing", "sklearn.decomposition",
    # 生物資訊
    "anndata", "scanpy", "squidpy",
    # 專案模組
    "analysis", "config",
    # 標準庫（安全子集）
    "pathlib", "json", "re", "math", "datetime", "collections",
    "itertools", "functools", "typing", "dataclasses",
}

BLOCKED_PATTERNS = [
    "os.system", "os.popen", "os.execv", "os.execve",
    "subprocess", "multiprocessing",
    "__import__",
    "eval(", "exec(",
    "compile(",
    "open(",           # 禁止任意檔案開啟（應透過分析函數）
    "socket",
    "urllib", "requests", "httpx",  # 禁止外部網路
    "shutil.rmtree", "shutil.move",
    "pathlib.Path.unlink", "pathlib.Path.rmdir",
    "importlib",
]

SANDBOX_CWD = str(BIO_DB_ROOT)


# ── 結果型別 ──────────────────────────────────────────────────────────────────


@dataclass
class ExecResult:
    success: bool
    output: str
    traceback: str
    duration_sec: float = 0.0


class SecurityError(Exception):
    """程式碼含禁止 pattern，拒絕執行。"""


# ── 安全檢查 ──────────────────────────────────────────────────────────────────


def _check_imports(code: str) -> list[str]:
    """解析 AST，回傳不在白名單的 import 模組名稱。"""
    violations: list[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []  # 語法錯誤留給執行期處理

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if alias.name not in ALLOWED_IMPORTS and root not in ALLOWED_IMPORTS:
                    violations.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            root = mod.split(".")[0]
            if mod not in ALLOWED_IMPORTS and root not in ALLOWED_IMPORTS:
                violations.append(mod)
    return violations


def is_safe(code: str) -> tuple[bool, str]:
    """
    Returns (True, "") if safe, (False, reason) if blocked.
    """
    for pattern in BLOCKED_PATTERNS:
        if pattern in code:
            return False, f"Blocked pattern: {pattern!r}"

    bad_imports = _check_imports(code)
    if bad_imports:
        return False, f"Disallowed imports: {bad_imports}"

    return True, ""


# ── 沙盒執行 ─────────────────────────────────────────────────────────────────


def sandbox_exec(code: str, timeout: int = 60) -> ExecResult:
    """
    在受限環境執行 Python 程式碼。

    Args:
        code:    要執行的 Python 程式碼字串
        timeout: 執行超時秒數（預設 60）

    Returns:
        ExecResult(success, output, traceback, duration_sec)

    Raises:
        SecurityError: 程式碼含禁止 pattern 或不在白名單的 import
    """
    import time

    ok, reason = is_safe(code)
    if not ok:
        raise SecurityError(reason)

    # 以暫存檔方式執行（避免 exec() 的作用域問題）
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(code)
        tmp_path = f.name

    t0 = time.time()
    try:
        r = subprocess.run(
            [sys.executable, tmp_path],
            cwd=SANDBOX_CWD,
            timeout=timeout,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PYTHONPATH": SANDBOX_CWD,  # 讓 import analysis.* 可用
            },
        )
        elapsed = time.time() - t0
        return ExecResult(
            success=r.returncode == 0,
            output=r.stdout.strip(),
            traceback=r.stderr.strip(),
            duration_sec=round(elapsed, 2),
        )
    except subprocess.TimeoutExpired:
        return ExecResult(
            success=False,
            output="",
            traceback=f"TimeoutExpired: 程式碼執行超過 {timeout} 秒。",
            duration_sec=float(timeout),
        )
    finally:
        os.unlink(tmp_path)


if __name__ == "__main__":
    # 快速 smoke test
    tests = [
        ("safe code", "import pandas as pd\nprint('pandas:', pd.__version__)"),
        ("blocked pattern", "import os; os.system('ls')"),
        ("disallowed import", "import requests\nrequests.get('http://example.com')"),
        ("syntax error", "def foo(:\n    pass"),
    ]
    for name, code in tests:
        try:
            result = sandbox_exec(code, timeout=5)
            status = "✅ OK" if result.success else f"❌ {result.traceback[:60]}"
            print(f"[{name}] {status}")
        except SecurityError as e:
            print(f"[{name}] 🔒 SecurityError: {e}")
