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
    # 科學運算（duckdb/config 移除；glob 移除：可列舉任意目錄）
    "pandas",
    "numpy",
    "scipy",
    "scipy.stats",
    "matplotlib",
    "matplotlib.pyplot",
    "seaborn",
    "sklearn",
    "sklearn.preprocessing",
    "sklearn.decomposition",
    # 生物資訊（唯計算用，不含 IO；scanpy/anndata 仍需但 IO 由 BLOCKED_PATTERNS 封鎖）
    "anndata",
    "scanpy",
    "squidpy",
    # 專案分析模組：只允許 spatial_eda / bulk_eda 等唯讀分析函數；
    # l1_cache / history_query / tool_registry 不開放（可寫 DB）
    "analysis.spatial_eda",
    "analysis.bulk_eda",
    "analysis.pathway_scoring",
    "analysis.multiomics_integration",
    "analysis.bulk_timeseries",
    "analysis.report_generator",
    # 標準庫安全子集（pathlib/glob/os 不開放）
    "json",
    "re",
    "math",
    "datetime",
    "time",
    "collections",
    "itertools",
    "functools",
    "typing",
    "dataclasses",
}

BLOCKED_PATTERNS = [
    # ── 系統呼叫 / RCE ───────────────────────────────────────────────────────
    "os.system",
    "os.popen",
    "os.execv",
    "os.execve",
    "os.fork",       # fork bomb
    "os.chdir",      # CWD escape
    "os.environ",    # env-var path injection
    "subprocess",
    "multiprocessing",
    "__import__",
    "eval(",
    "exec(",
    "compile(",
    "pty.",          # terminal spawning
    "ctypes.",       # C library / libc.system()
    # ── 檔案 I/O — 直接寫法 ─────────────────────────────────────────────────
    "open(",         # ADV-02 修復：任何 open() 均需 AST path check（見下方）
    "io.open(",      # io 模組繞過
    # ── pathlib 繞過（直接讀寫路徑而非透過 open）────────────────────────────
    ".read_text(",
    ".read_bytes(",
    ".write_text(",
    ".write_bytes(",
    # ── pandas/numpy/scanpy/anndata 的隱性檔案 I/O ──────────────────────────
    "read_csv(",
    "read_parquet(",
    "read_excel(",
    "read_table(",
    "read_json(",
    "read_hdf(",
    "read_feather(",
    "read_pickle(",
    "np.load(",
    "np.fromfile(",
    "np.genfromtxt(",
    "np.loadtxt(",
    "sc.read",
    "sc.read_h5ad(",
    "anndata.read",
    # ── 寫入 ──────────────────────────────────────────────────────────────────
    "savefig(",
    "to_csv(",
    "to_parquet(",
    "to_excel(",
    "to_hdf(",
    "to_feather(",
    "to_pickle(",
    "to_json(",
    "np.save(",
    "np.savetxt(",
    "np.savez(",
    ".write_h5ad(",
    ".write_zarr(",
    ".write(",
    "COPY ",
    "EXPORT ",
    # ── 網路 ──────────────────────────────────────────────────────────────────
    "socket",
    "urllib",
    "requests",
    "httpx",
    # ── 危險的序列化 / 反序列化 ──────────────────────────────────────────────
    "pickle.",
    "marshal.",
    # ── 路徑操作 ──────────────────────────────────────────────────────────────
    "shutil.rmtree",
    "shutil.move",
    "pathlib.Path.unlink",
    "pathlib.Path.rmdir",
    "glob.glob(",
    "glob.iglob(",
    # ── L1/DB 寫入（防止從 analysis.* 呼叫寫入函數）──────────────────────────
    "write_to_l1_cache(",
    "safe_write(",
    "register_tool(",
    # ── builtin 繞過 ──────────────────────────────────────────────────────────
    "importlib",
    "getattr(",
    "__builtins__",
    "__class__",
    "__subclasses__",
    "__globals__",
    "__locals__",
    "f_globals",
    "f_locals",
    "gi_frame",
    "dill",
    "cloudpickle",
    "joblib.dump",
    "vars(",
    # ── 資源耗盡 / timeout 繞過 ──────────────────────────────────────────────
    "signal.",       # signal handler 可中斷 timeout 機制
    "resource.",     # setrlimit 繞過
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
    except SyntaxError as e:
        raise SecurityError(f"語法錯誤，拒絕執行：{e}") from e

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


def _check_open_paths(code: str) -> list[str]:
    """
    AST-level check: detect open() / io.open() calls with absolute or
    path-traversal string literals.

    Catches patterns that bypass the plain "open(" text scan:
      - Path('/etc/passwd').open(...)  →  caught by BLOCKED_PATTERNS ".write_text(" etc.
      - open('/etc/passwd', 'r')       →  caught here (absolute) AND by "open(" text scan
      - open('../../../secret', 'r')   →  caught here (traversal) AND by "open(" text scan

    Returns list of violation descriptions (empty ⇒ clean).
    """
    violations: list[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []  # SyntaxError already caught by _check_imports

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Match: open(...) or io.open(...) or anything.open(...)
        is_open_call = (
            (isinstance(func, ast.Name) and func.id == "open")
            or (isinstance(func, ast.Attribute) and func.attr == "open")
        )
        if not is_open_call:
            continue
        if not node.args:
            continue
        first_arg = node.args[0]
        if not (isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str)):
            continue
        path_val: str = first_arg.value
        if os.path.isabs(path_val):
            violations.append(
                f"Absolute path in open(): {path_val!r}"
            )
        elif ".." in path_val.replace("\\", "/").split("/"):
            violations.append(
                f"Path traversal in open(): {path_val!r}"
            )
    return violations


def is_safe(code: str) -> tuple[bool, str]:
    """
    Returns (True, "") if safe, (False, reason) if blocked.

    Layers:
      1. BLOCKED_PATTERNS text scan  (fast, catches most RCE / IO patterns)
      2. _check_imports() AST scan   (catches import-level violations)
      3. _check_open_paths() AST scan (catches absolute / traversal paths in open())
    """
    for pattern in BLOCKED_PATTERNS:
        if pattern in code:
            return False, f"Blocked pattern: {pattern!r}"

    bad_imports = _check_imports(code)
    if bad_imports:
        return False, f"Disallowed imports: {bad_imports}"

    path_violations = _check_open_paths(code)
    if path_violations:
        return False, f"Path whitelist violation: {path_violations[0]}"

    return True, ""


# ── 沙盒執行 ─────────────────────────────────────────────────────────────────


def sandbox_exec(code: str, timeout: int = 60, *, preamble: str = "") -> ExecResult:
    """
    在受限環境執行 Python 程式碼。

    Args:
        code:     LLM 生成的程式碼（安全性檢查對象）
        timeout:  執行超時秒數（預設 60）
        preamble: 系統注入的前置程式碼（不經安全性檢查，由呼叫端負責）

    Returns:
        ExecResult(success, output, traceback, duration_sec)

    Raises:
        SecurityError: 程式碼含禁止 pattern 或不在白名單的 import
    """
    import time

    ok, reason = is_safe(code)
    if not ok:
        raise SecurityError(reason)

    if preamble:
        ok_pre, reason_pre = is_safe(preamble)
        if not ok_pre:
            raise SecurityError(f"preamble blocked: {reason_pre}")
        code = preamble + "\n" + code

    # 最小化環境：不繼承 os.environ，避免洩漏 API 金鑰。
    # 安全性說明：主要防線是 BLOCKED_PATTERNS + import whitelist，而非 env 隔離。
    # env 隔離的目的僅是避免 subprocess 看到 ANTHROPIC_API_KEY 等敏感金鑰。
    _SENSITIVE_KEYS = {
        "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY",
        "AWS_SECRET_ACCESS_KEY", "AWS_ACCESS_KEY_ID",
        "GITHUB_TOKEN", "HUGGING_FACE_HUB_TOKEN",
    }
    if os.name == "nt":
        # Windows：繼承完整 PATH 以確保 DLL / venv 可用，只剔除金鑰變數
        _safe_env = {k: v for k, v in os.environ.items() if k not in _SENSITIVE_KEYS}
        _safe_env["PYTHONPATH"] = SANDBOX_CWD
    else:
        # Unix：最小化 env（DLL 由 LD_LIBRARY_PATH 決定，通常不需）
        _safe_env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(Path.home()),
            "LANG": os.environ.get("LANG", "en_US.UTF-8"),
            "PYTHONPATH": SANDBOX_CWD,
        }

    tmp_path: str | None = None
    t0 = time.time()
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            tmp_path = f.name

        r = subprocess.run(
            [sys.executable, tmp_path],
            cwd=SANDBOX_CWD,
            timeout=timeout,
            capture_output=True,
            text=True,
            env=_safe_env,
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
        if tmp_path and os.path.exists(tmp_path):
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
