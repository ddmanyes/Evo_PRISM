"""
BioAgent — Web UI (FastAPI)

路由：
    GET  /                              → 聊天介面（HTML）
    GET  /history                       → 歷史記錄瀏覽頁（HTML）
    GET  /results/{analysis_id}         → 報告 HTML 頁
    POST /api/chat                      → SSE 聊天（text/event-stream）
    GET  /api/history                   → 歷史查詢 JSON
    GET  /api/results/{id}/csv          → 下載 top_genes CSV
    POST /api/analysis/{id}/feedback    → 記錄 👍/👎 user_approval（AA2）
    GET  /health                        → 健檢 JSON
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import mistune
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv

load_dotenv()

from config.settings import BIO_DB_ROOT
from config.db_utils import db_health_check

logger = logging.getLogger(__name__)


# MCP HTTP transport — 必須在 FastAPI 建立前就建立 handler，
# 因為 FastAPI 不會把 lifespan 傳遞給 mount 的子 ASGI app，
# 故 mcp_lifespan 需在父 lifespan 中驅動 session_manager。
_mcp_handler = None
_mcp_lifespan_cm = None
try:
    from server.bio_memory_server import create_http_app as _create_mcp_app

    _mcp_handler, _mcp_lifespan_cm = _create_mcp_app()
except Exception as _e:
    logger.warning("MCP HTTP create_http_app failed (non-fatal): %s", _e)


@contextlib.asynccontextmanager
async def _lifespan(_app: FastAPI):
    # WAL pre-flight：read-only 試開 DB，若損壞自動 rename .wal → .wal.corrupt.<ts>
    # 避免後續 write 連線觸發 DuckDB C++ FatalException（無法 Python catch）
    try:
        from config.db_utils import wal_preflight_check

        _pf = wal_preflight_check()
        if _pf["ok"]:
            logger.info("WAL preflight ok (wal_existed=%s)", _pf["wal_existed"])
        else:
            logger.warning(
                "WAL preflight FAIL: %s | renamed_to=%s",
                _pf["error"],
                _pf["renamed_to"],
            )
    except Exception as _e:
        logger.exception("WAL preflight raised (non-fatal): %s", _e)

    _verify_static_files()

    # Embedding server warmup — fire-and-forget。
    # 冷啟動下第一次 embed 約 6–8 秒（GPU kernel 載入 + model warmup）。
    # 啟動就 dispatch 一次 dummy embed，讓真實使用者的第一個 query 走 warm path（< 30 ms）。
    # asyncio.create_task() = fire-and-forget；warmup 失敗不影響 server 啟動。
    asyncio.create_task(_warmup_embedding())

    # M4: API key 早期警告 — backend 非 local 但 key 缺，啟動就 log warning
    # 此處只 warn 不 raise，允許本機 local-only 部署
    try:
        from config.settings import validate_inference_backend, INFERENCE_BACKEND

        validate_inference_backend()
        logger.info("inference backend ok: %s", INFERENCE_BACKEND)
    except RuntimeError as _e:
        logger.warning("inference backend startup check FAIL: %s", _e)

    # 殭屍 running 記錄由背景 task 延遲清理（60 秒後）。
    # Read-only pre-check：絕大多數啟動沒有 zombie，可完全避開 write 連線（不觸發 WAL replay）。
    # 只在確實有 zombie 時才開短連線：不 LOAD vss、立即 CHECKPOINT 後 close，
    # 縮小 WAL 損壞視窗（ExFAT 無日誌）。同步 I/O 包在 to_thread 避免阻塞 event loop。
    def _do_deferred_cleanup() -> None:
        import duckdb as _duckdb
        from config.settings import DUCKDB_PATH as _DB

        try:
            with _duckdb.connect(str(_DB), read_only=True) as _ro:
                n = _ro.execute(
                    "SELECT COUNT(*) FROM analysis_history WHERE status='running'"
                ).fetchone()[0]
            if not n:
                logger.debug("deferred cleanup: no zombie running records, write open skipped")
                return
            _con = _duckdb.connect(str(_DB))
            try:
                _con.execute("UPDATE analysis_history SET status='stale' WHERE status='running'")
                _con.execute("CHECKPOINT")
            finally:
                _con.close()
            logger.info("deferred startup cleanup: cleared %d zombie running records", n)
        except Exception as _e:
            logger.warning("deferred startup cleanup failed (non-fatal): %s", _e)

    async def _deferred_cleanup():
        await asyncio.sleep(60)
        await asyncio.to_thread(_do_deferred_cleanup)

    async def _cleanup_loop():
        while True:
            await asyncio.sleep(3600)
            _cleanup_old_sessions()

    cleanup_task = asyncio.create_task(_deferred_cleanup())
    session_task = asyncio.create_task(_cleanup_loop())

    # 驅動 MCP session_manager.run() — 否則 /mcp 任何請求會 500
    async with _mcp_lifespan_cm() if _mcp_lifespan_cm else contextlib.nullcontext():
        yield

    cleanup_task.cancel()
    session_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await cleanup_task
    with contextlib.suppress(asyncio.CancelledError):
        await session_task


app = FastAPI(title="BioAgent", version="1.0.0", lifespan=_lifespan)

_cors_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()] or [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# MCP HTTP transport — 掛載於 /mcp，供外部客戶端直接呼叫 MCP 工具
# handler 與 lifespan 在檔案上方已建立，這裡只做 mount
if _mcp_handler is not None:
    app.mount("/mcp", _mcp_handler)
    logger.info("MCP HTTP transport mounted at /mcp")
else:
    logger.warning("MCP HTTP mount skipped (handler not available)")

STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


async def _warmup_embedding() -> None:
    """Pre-warm the embedding server so the first real query doesn't pay cold-start.

    Profile-measured: first embed call after server restart = ~6.8s
    (GPU kernel JIT + model load). Subsequent calls = 12-30 ms.

    Called fire-and-forget from `_lifespan` startup. Failures are logged but
    never raised — embedding is only needed for L1 cache features, and the rest
    of the app must remain reachable even if the local llama-server is down.
    """
    try:
        from analysis.embed import embed_text

        await asyncio.to_thread(embed_text, "warmup")
        logger.info("embedding warmup ok")
    except Exception as exc:
        logger.warning("embedding warmup failed (non-fatal): %s", exc)


def _verify_static_files() -> None:
    """Fail-fast 健檢：確認 static 資源可讀。

    保護雲端同步資料夾（Google Drive / iCloud / Dropbox）或外接磁碟卸載
    導致 ``Path(__file__).parent`` 解析失效的場景——當 uvicorn cwd 失效
    時 STATIC_DIR 雖然字串存在但 ``.exists()`` 回 False，過去要等使用者
    打 ``GET /`` 才會看到 ``index.html not found``。
    """
    required = ["index.html", "history.html", "engram.html"]
    if not STATIC_DIR.exists():
        logger.error(
            "STATIC_DIR not accessible: %s "
            "(cwd may be stale — restart uvicorn from the project root)",
            STATIC_DIR,
        )
        return
    missing = [f for f in required if not (STATIC_DIR / f).exists()]
    if missing:
        logger.error("Missing static files in %s: %s", STATIC_DIR, missing)
    else:
        logger.info("Static files OK (%s)", STATIC_DIR)


RESULTS_DIR = BIO_DB_ROOT / "results"

# ── Session 管理 ──────────────────────────────────────────────────────────────

_MAX_HISTORY = 24  # 12 輪 = 24 messages
_MAX_SESSIONS = 200  # 防止記憶體耗盡
_SESSION_TTL_HOURS = 24
_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")
_sessions: dict[str, collections.deque] = {}
_session_locks: dict[str, threading.Lock] = {}
_sessions_meta: dict[str, datetime] = {}
_sessions_dict_lock = threading.Lock()  # 保護以上三個字典的並發存取


def _get_session(session_id: str) -> tuple[collections.deque, threading.Lock]:
    if not _SESSION_ID_RE.match(session_id):
        raise ValueError(f"Invalid session_id format: {session_id!r}")
    with _sessions_dict_lock:
        if session_id not in _sessions:
            if len(_sessions) >= _MAX_SESSIONS:
                _cleanup_old_sessions_unsafe()
            if len(_sessions) >= _MAX_SESSIONS:
                raise RuntimeError("Session limit reached; retry later")
            _sessions[session_id] = collections.deque(maxlen=_MAX_HISTORY)
            _session_locks[session_id] = threading.Lock()
        _sessions_meta[session_id] = datetime.now(timezone.utc)
        return _sessions[session_id], _session_locks[session_id]


def _cleanup_old_sessions_unsafe() -> None:
    """Remove expired sessions. Caller must hold _sessions_dict_lock."""
    now = datetime.now(timezone.utc)
    expired = [
        sid
        for sid, last in _sessions_meta.items()
        if (now - last).total_seconds() > _SESSION_TTL_HOURS * 3600
    ]
    for sid in expired:
        _sessions.pop(sid, None)
        _session_locks.pop(sid, None)
        _sessions_meta.pop(sid, None)
    if expired:
        logger.info("Cleaned up %d expired sessions", len(expired))


def _cleanup_old_sessions() -> None:
    """Public wrapper — acquires lock before cleaning."""
    with _sessions_dict_lock:
        _cleanup_old_sessions_unsafe()


# ── Pydantic models ───────────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    session_id: str
    message: str = Field(..., max_length=50_000)
    backend: str = ""  # "local" | "claude" | ""（空字串讀 INFERENCE_BACKEND env）
    image_base64: str = Field("", max_length=20_000_000)  # data:image/png;base64,... 或純 base64


class FeedbackRequest(BaseModel):
    approval: int  # 1 = 👍 讚, -1 = 👎 倒讚


# ── HTML 工具 ─────────────────────────────────────────────────────────────────

_md = mistune.create_markdown(plugins=["table", "strikethrough"])


def _synthesize_dynamic_code_view(archive_dir: Path) -> str:
    """
    Dynamic code 歸檔專屬視圖：description 當標題 + status badge + 執行統計 +
    失敗橫幅 + 折疊 meta + code.py + output.txt + figures。

    預期目錄含 `meta.json` + `code.py`（由 bio_execute_code 寫入）。
    與通用目錄瀏覽（l2_convert 等）分流，由 `_synthesize_archive_view` 派發。
    """
    import base64
    import html as _html
    import json as _json

    # 讀 meta（失敗不致命）
    meta_raw = ""
    meta: dict = {}
    meta_path = archive_dir / "meta.json"
    if meta_path.is_file():
        meta_raw = meta_path.read_text(encoding="utf-8", errors="replace")
        try:
            meta = _json.loads(meta_raw)
        except Exception:
            meta = {}

    description = (meta.get("description") or "").strip() or f"Archive: {archive_dir.name}"
    status = (meta.get("status") or "").strip()
    duration = meta.get("duration_sec")
    code_lines = meta.get("code_lines")
    fig_count = meta.get("fig_count")
    error_summary = (meta.get("error_summary") or "").strip()

    # status badge
    if status == "completed":
        badge_color, badge_bg, badge_label = "#065f46", "#d1fae5", "✓ 完成"
    elif status == "failed":
        badge_color, badge_bg, badge_label = "#991b1b", "#fee2e2", "× 失敗"
    else:
        badge_color, badge_bg, badge_label = "#374151", "#e5e7eb", status or "—"

    badge_html = (
        f'<span style="display:inline-block;padding:3px 10px;border-radius:10px;'
        f'background:{badge_bg};color:{badge_color};font-size:12px;font-weight:600;">'
        f"{_html.escape(badge_label)}</span>"
    )

    stats_bits: list[str] = []
    if duration is not None:
        stats_bits.append(f"⏱ {duration:g} s")
    if code_lines is not None:
        stats_bits.append(f"{code_lines} 行程式碼")
    if fig_count:
        stats_bits.append(f"{fig_count} 張圖")
    stats_html = (
        (
            '<span style="color:#6c757d;font-size:13px;">'
            + " · ".join(_html.escape(s) for s in stats_bits)
            + "</span>"
        )
        if stats_bits
        else ""
    )

    # 頁面標頭（description 當 H1 + badge + stats + archive id）
    header_html = (
        f'<h1 style="margin-bottom:6px;">{_html.escape(description)}</h1>'
        f'<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;'
        f'margin-bottom:18px;">'
        f"{badge_html}{stats_html}"
        f'<code style="font-size:12px;color:#6c757d;background:#f1f3f5;'
        f'padding:2px 6px;border-radius:4px;">{_html.escape(archive_dir.name)}</code>'
        f"</div>"
    )

    parts: list[str] = [header_html]

    # 失敗橫幅（紅框 + error_summary 重點化 + traceback inline）
    if status == "failed":
        tb_path = archive_dir / "traceback.txt"
        tb_text = tb_path.read_text(encoding="utf-8", errors="replace") if tb_path.is_file() else ""
        parts.append(
            '<div style="background:#fee2e2;border-left:4px solid #ef4444;'
            'padding:14px 16px;border-radius:6px;margin-bottom:18px;">'
            '<div style="font-weight:700;color:#991b1b;margin-bottom:6px;">× 執行失敗</div>'
            + (
                f'<div style="color:#7f1d1d;font-size:14px;margin-bottom:8px;">'
                f"{_html.escape(error_summary)}</div>"
                if error_summary
                else ""
            )
            + (
                f'<pre style="background:rgba(0,0,0,0.04);padding:10px;border-radius:4px;'
                f'font-size:12px;overflow-x:auto;margin:0;color:#3f0a0a;">'
                f"{_html.escape(tb_text)}</pre>"
                if tb_text.strip()
                else ""
            )
            + "</div>"
        )

    # meta.json：折疊預設關
    if meta_raw:
        pretty = _json.dumps(meta, ensure_ascii=False, indent=2) if meta else meta_raw
        parts.append(
            '<details style="margin-bottom:18px;">'
            '<summary style="cursor:pointer;color:#6c757d;font-size:13px;">'
            "顯示 meta.json（原始）</summary>\n\n"
            "```json\n" + pretty + "\n```\n"
            "</details>"
        )

    # code.py
    code = archive_dir / "code.py"
    if code.is_file():
        parts.append("## 程式碼\n")
        parts.append("```python\n" + code.read_text(encoding="utf-8", errors="replace") + "\n```\n")

    # output.txt（成功時才顯示；失敗已在橫幅）
    out_path = archive_dir / "output.txt"
    if out_path.is_file():
        out_txt = out_path.read_text(encoding="utf-8", errors="replace")
        if out_txt.strip():
            parts.append("## 輸出\n")
            parts.append("```text\n" + out_txt + "\n```\n")

    # 圖片：inline base64
    figs = sorted(archive_dir.glob("fig_*.png"))
    if figs:
        parts.append("## 圖\n")
        for fp in figs:
            b64 = base64.b64encode(fp.read_bytes()).decode()
            parts.append(f"### {fp.name}\n\n![{fp.name}](data:image/png;base64,{b64})\n")

    return "\n".join(parts)


def _synthesize_directory_browser_view(directory: Path) -> str:
    """
    通用目錄瀏覽視圖：l2_convert 的 silver 資料夾、或任何非 dynamic_code 結構的目錄。

    呈現：
    - 目錄名當標題
    - 列出所有檔案（依副檔名分組），每筆顯示大小
    - 對 parquet 檔以 duckdb 讀 footer 取 schema（columns + types，不掃資料列）
    """
    import html as _html

    files = [f for f in sorted(directory.iterdir()) if f.is_file()]
    subdirs = [d for d in sorted(directory.iterdir()) if d.is_dir()]

    parts: list[str] = [
        f'<h1 style="margin-bottom:6px;">📁 {_html.escape(directory.name)}</h1>'
        f'<div style="color:#6c757d;font-size:13px;margin-bottom:18px;">'
        f"資料夾瀏覽 · {len(files)} 個檔案"
        + (f" · {len(subdirs)} 個子資料夾" if subdirs else "")
        + "</div>"
    ]

    # 按副檔名分組
    by_ext: dict[str, list[Path]] = {}
    for f in files:
        by_ext.setdefault(f.suffix.lower() or "(無副檔名)", []).append(f)

    for ext, group in sorted(by_ext.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        parts.append(f"## {ext}（{len(group)}）\n")
        rows = ["| 檔名 | 大小 |", "|---|---:|"]
        for f in group:
            kb = f.stat().st_size / 1024
            size_str = f"{kb / 1024:.1f} MB" if kb >= 1024 else f"{kb:.1f} KB"
            rows.append(f"| `{f.name}` | {size_str} |")
        parts.append("\n".join(rows) + "\n")

        # parquet：附 schema preview（最多前 3 個）
        if ext == ".parquet":
            for f in group[:3]:
                schema_md = _parquet_schema_markdown(f)
                if schema_md:
                    parts.append(
                        f'<details><summary style="cursor:pointer;color:#6c757d;font-size:13px;">'
                        f"<code>{_html.escape(f.name)}</code> schema</summary>\n\n"
                        f"{schema_md}\n</details>\n"
                    )

    if subdirs:
        parts.append("## 子資料夾\n")
        for d in subdirs:
            parts.append(f"- `{d.name}/`")
        parts.append("")

    if not files and not subdirs:
        parts.append('<div style="color:#6c757d;font-size:14px;">資料夾為空。</div>')

    return "\n".join(parts)


def _parquet_schema_markdown(path: Path) -> str:
    """讀 parquet footer 取欄位 + 型別（不掃資料列）。失敗回空字串。"""
    try:
        import duckdb

        rows = duckdb.execute("SELECT name, type FROM parquet_schema(?)", [str(path)]).fetchall()
    except Exception:
        return ""
    if not rows:
        return ""
    lines = ["| 欄位 | 型別 |", "|---|---|"]
    for name, dtype in rows:
        lines.append(f"| `{name}` | `{dtype}` |")
    return "\n".join(lines)


def _synthesize_archive_view(directory: Path) -> str:
    """目錄類 result_path 派發器：dynamic_code 結構 → 專屬視圖，其餘 → 通用瀏覽。"""
    if (directory / "meta.json").is_file() and (directory / "code.py").is_file():
        return _synthesize_dynamic_code_view(directory)
    return _synthesize_directory_browser_view(directory)


def _resolve_result_path(result_path: str) -> Path:
    """把 DB 的 result_path 解析成絕對 Path。相對路徑以 BIO_DB_ROOT 為基底（不依賴 CWD）。"""
    p = Path(result_path)
    if not p.is_absolute():
        p = BIO_DB_ROOT / p
    return p.resolve()


def _render_report_html(analysis_id: str, md_text: str, sample_id: str, timestamp: str) -> str:
    body = _md(md_text)
    csv_url = f"/api/results/{analysis_id}/csv"
    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>報告 — {sample_id}</title>
<style>
  :root {{
    --bg: #f8f9fa; --card: #ffffff; --text: #212529;
    --accent: #2563eb; --border: #dee2e6; --code-bg: #f1f3f5;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
          background: var(--bg); color: var(--text); line-height: 1.7; }}
  .toolbar {{ background: var(--card); border-bottom: 1px solid var(--border);
              padding: 12px 24px; display: flex; align-items: center; gap: 12px;
              position: sticky; top: 0; z-index: 10; }}
  .toolbar a {{ text-decoration: none; color: var(--accent); font-weight: 500; font-size: 14px; }}
  .toolbar .sep {{ color: var(--border); }}
  .btn {{ padding: 6px 14px; border-radius: 6px; font-size: 13px; cursor: pointer;
          border: 1px solid var(--border); background: var(--card); color: var(--text);
          text-decoration: none; display: inline-flex; align-items: center; gap: 6px; }}
  .btn:hover {{ background: var(--bg); }}
  .btn-primary {{ background: var(--accent); color: white; border-color: var(--accent); }}
  .btn-primary:hover {{ background: #1d4ed8; }}
  .spacer {{ flex: 1; }}
  .meta {{ font-size: 12px; color: #6c757d; }}
  main {{ max-width: 900px; margin: 32px auto; padding: 0 24px 64px; }}
  h1 {{ font-size: 1.6rem; margin-bottom: 8px; }}
  h2 {{ font-size: 1.2rem; margin: 32px 0 12px; padding-bottom: 6px;
        border-bottom: 2px solid var(--accent); color: var(--accent); }}
  h3 {{ font-size: 1rem; margin: 20px 0 8px; }}
  p {{ margin: 8px 0; }}
  table {{ border-collapse: collapse; width: 100%; margin: 16px 0; font-size: 14px; }}
  th {{ background: var(--accent); color: white; padding: 8px 12px; text-align: left; }}
  td {{ padding: 7px 12px; border-bottom: 1px solid var(--border); }}
  tr:hover td {{ background: #f0f4ff; }}
  code {{ background: var(--code-bg); padding: 2px 6px; border-radius: 4px; font-size: 13px; }}
  pre {{ background: var(--code-bg); padding: 16px; border-radius: 8px; overflow-x: auto; margin: 12px 0; }}
  hr {{ border: none; border-top: 1px solid var(--border); margin: 24px 0; }}
  em {{ color: #6c757d; font-size: 13px; }}
</style>
</head>
<body>
<div class="toolbar">
  <a href="/">← 聊天</a>
  <span class="sep">|</span>
  <a href="/history">歷史記錄</a>
  <span class="sep">|</span>
  <span class="meta">{sample_id} &nbsp;·&nbsp; {timestamp}</span>
  <div class="spacer"></div>
  <a href="{csv_url}" class="btn" download>⬇ CSV</a>
  <button class="btn btn-primary" onclick="window.print()">🖨 列印 / PDF</button>
</div>
<main>
{body}
</main>
</body>
</html>"""


# ── 路由：頁面 ────────────────────────────────────────────────────────────────


def _static_missing_response(filename: str) -> HTMLResponse:
    body = (
        f"<h1>{filename} not found</h1>"
        f"<p>STATIC_DIR={STATIC_DIR} exists={STATIC_DIR.exists()}</p>"
        "<p>If the directory exists but files do not, the project tree may be incomplete. "
        "If <code>exists=False</code>, uvicorn's cwd is likely stale "
        "(e.g. Google Drive / iCloud re-mounted, or external disk ejected). "
        "Restart uvicorn from the project root.</p>"
    )
    return HTMLResponse(body, status_code=500)


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = STATIC_DIR / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return _static_missing_response("index.html")


@app.get("/history", response_class=HTMLResponse)
async def history_page():
    html_path = STATIC_DIR / "history.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return _static_missing_response("history.html")


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page():
    """控制面板：監控四大子系統（分析/HELIX/系統/快取）+ 手動操作（Phase 2 起）。"""
    html_path = STATIC_DIR / "dashboard.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return _static_missing_response("dashboard.html")


@app.get("/api/dashboard")
async def api_dashboard():
    """聚合所有 panel 一次回傳，供首屏載入。"""
    import duckdb
    from config.settings import DUCKDB_PATH
    from server.dashboard import full_snapshot

    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        return full_snapshot(con)


# ── 控制面板手動操作（Phase 2）────────────────────────────────────────────────

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _dashboard_actions_guard(request: Request) -> None:
    """三層防護：env-gate → loopback-only → 選用 X-Dashboard-Token。

    每次都從 config.settings 讀「現值」（非 import 時綁定），以便測試 monkeypatch
    與 runtime 改 env 生效。任一層不過直接 raise HTTPException。
    """
    from config import settings

    if not settings.DASHBOARD_ACTIONS_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="控制面板手動操作未啟用（設環境變數 DASHBOARD_ACTIONS_ENABLED=true 後重啟）",
        )

    if not settings.DASHBOARD_ACTIONS_ALLOW_REMOTE:
        host = request.client.host if request.client else None
        if host not in _LOOPBACK_HOSTS:
            raise HTTPException(
                status_code=403,
                detail="僅允許本機 (loopback) 觸發手動操作",
            )

    token = settings.DASHBOARD_ACTION_TOKEN
    if token and request.headers.get("X-Dashboard-Token") != token:
        raise HTTPException(status_code=401, detail="X-Dashboard-Token 缺失或不符")


@app.get("/api/dashboard/actions")
async def api_dashboard_actions_status():
    """回報手動操作是否啟用 + 可用操作清單（供前端決定是否渲染操作面板）。"""
    from config import settings
    from server.dashboard_actions import list_actions

    return {
        "enabled": settings.DASHBOARD_ACTIONS_ENABLED,
        "allow_remote": settings.DASHBOARD_ACTIONS_ALLOW_REMOTE,
        "token_required": bool(settings.DASHBOARD_ACTION_TOKEN),
        "actions": list_actions(),
    }


@app.post("/api/dashboard/action")
async def api_dashboard_action(request: Request):
    """執行單一手動操作。先過 guard，再委派 dashboard_actions.dispatch（threadpool）。"""
    _dashboard_actions_guard(request)

    try:
        body = await request.json()
    except Exception:
        body = {}
    action = body.get("action") if isinstance(body, dict) else None
    args = body.get("args") if isinstance(body, dict) else None

    from server.dashboard_actions import dispatch

    result = await asyncio.to_thread(dispatch, action, args or {})
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


# ── 動態程式碼畢業（Phase 3，唯讀）─────────────────────────────────────────────


@app.get("/api/dashboard/graduation")
async def api_graduation_candidates():
    """畢業候選清單（同 description 多次 completed + 非 1 行噪音）。唯讀，無需 guard。"""
    import duckdb

    from config import settings
    from config.settings import DUCKDB_PATH
    from server.graduation import list_candidates

    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        candidates = list_candidates(con)
    return {
        "candidates": candidates,
        "min_code_lines": settings.GRADUATION_MIN_CODE_LINES,
        "min_completed": settings.GRADUATION_MIN_COMPLETED_RUNS,
    }


@app.get("/api/dashboard/graduation/{analysis_id}")
async def api_graduation_plan(analysis_id: str):
    """單筆畢業計畫：archive（code/meta/output）+ 生成的 analysis/ 骨架。唯讀。"""
    _require_analysis_id(analysis_id)
    import duckdb

    from config.settings import DUCKDB_PATH
    from server.graduation import graduation_plan

    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        try:
            return graduation_plan(con, analysis_id)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))


@app.get("/results/{analysis_id}", response_class=HTMLResponse)
async def report_page(analysis_id: str):
    _require_analysis_id(analysis_id)
    import duckdb
    from config.settings import DUCKDB_PATH

    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        row = con.execute(
            "SELECT sample_id, result_path, completed_at, summary FROM analysis_history WHERE analysis_id=?",
            [analysis_id],
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="分析記錄不存在")

    sample_id, result_path, completed_at, summary = row
    if not result_path:
        raise HTTPException(status_code=404, detail="此分析無 result_path 記錄")

    resolved = _resolve_result_path(result_path)
    if not resolved.is_relative_to(BIO_DB_ROOT.resolve()):
        raise HTTPException(status_code=403, detail="Access denied")
    if not resolved.exists():
        raise HTTPException(
            status_code=404,
            detail=f"報告檔案不存在：{result_path}（可能為舊絕對路徑，專案已搬遷）",
        )

    ts = completed_at.strftime("%Y-%m-%d %H:%M UTC") if completed_at else ""

    # 目錄類 result_path → 派發到對應視圖
    # （dynamic_code 結構 → 專屬視圖；其餘 → 通用瀏覽，見 _synthesize_archive_view）
    if resolved.is_dir():
        md_text = _synthesize_archive_view(resolved)
        return HTMLResponse(_render_report_html(analysis_id, md_text, sample_id, ts))

    # 檔案類（.md / .txt 報告）→ 原本流程
    if resolved.is_file():
        md_text = resolved.read_text(encoding="utf-8")
        return HTMLResponse(_render_report_html(analysis_id, md_text, sample_id, ts))

    raise HTTPException(status_code=404, detail="result_path 既非檔案也非目錄")


# ── 路由：API ─────────────────────────────────────────────────────────────────


def _check_llama_server(port: int, timeout: float = 1.5) -> bool:
    """探活 llama-server，回 True 表示 /health 200。"""
    try:
        import httpx

        r = httpx.get(f"http://127.0.0.1:{port}/health", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def _read_backup_status() -> dict:
    """讀 logs/backup_status.json；若不存在/壞檔回 {}。"""
    p = Path(__file__).parent.parent / "logs" / "backup_status.json"
    if not p.exists():
        return {}
    try:
        import json as _json

        data = _json.loads(p.read_text())
        if data.get("last_success_at"):
            try:
                last = datetime.fromisoformat(data["last_success_at"])
                age_hours = (datetime.now() - last).total_seconds() / 3600
                data["last_success_age_hours"] = round(age_hours, 2)
            except (ValueError, TypeError):
                data["last_success_age_hours"] = None
        return data
    except Exception:
        return {}


def _disk_free_gb(path: Path) -> float | None:
    try:
        import shutil as _sh

        return round(_sh.disk_usage(path).free / 1024**3, 2)
    except Exception:
        return None


@app.get("/health")
async def health():
    backup = _read_backup_status()
    embedding_ok = _check_llama_server(8081)
    multimodal_ok = _check_llama_server(8080)
    disk_free = _disk_free_gb(BIO_DB_ROOT)
    age_h = backup.get("last_success_age_hours")
    # 健康準則：DB OK + embedding OK + 最近 36 小時內有成功備份
    try:
        stats = db_health_check()
        db_ok = True
    except Exception as e:
        stats = {"error": str(e)}
        db_ok = False
    backup_fresh = age_h is not None and age_h < 36
    overall = db_ok and embedding_ok and backup_fresh
    # WAL pre-flight status (written at lifespan startup)
    wal_status: dict = {}
    try:
        import json as _json

        _wp = Path(__file__).parent.parent / "logs" / "wal_preflight_status.json"
        if _wp.exists():
            wal_status = _json.loads(_wp.read_text())
    except Exception:
        wal_status = {}

    return {
        "ok": overall,
        "db": stats,
        "embedding_server_ok": embedding_ok,
        "multimodal_server_ok": multimodal_ok,
        "backup": {
            "last_success_at": backup.get("last_success_at"),
            "last_success_age_hours": age_h,
            "last_size_bytes": backup.get("last_size_bytes"),
            "last_error": backup.get("last_error"),
            "fresh": backup_fresh,
        },
        "wal_preflight": {
            "checked_at": wal_status.get("checked_at"),
            "ok": wal_status.get("ok"),
            "wal_existed": wal_status.get("wal_existed"),
            "renamed_to": wal_status.get("renamed_to"),
            "error": wal_status.get("error"),
        },
        "disk_free_gb": disk_free,
    }


@app.get("/api/backend")
async def get_backend():
    from config.settings import INFERENCE_BACKEND, CLAUDE_MODEL, GOOGLE_MODEL
    import os

    local_ok = False
    try:
        import httpx

        r = httpx.get("http://localhost:8080/health", timeout=2.0)
        local_ok = r.json().get("status") == "ok"
    except Exception:
        pass
    google_ok = bool(os.getenv("GOOGLE_API_KEY", ""))
    return {
        "default": INFERENCE_BACKEND,
        "local_available": local_ok,
        "claude_model": CLAUDE_MODEL,
        "google_model": GOOGLE_MODEL,
        "google_available": google_ok,
    }


@app.get("/api/history")
async def api_history(sample_id: Optional[str] = None, limit: int = 50):
    import duckdb
    from config.settings import DUCKDB_PATH

    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        if sample_id:
            rows = con.execute(
                """SELECT analysis_id, sample_id, analysis_type, status,
                          completed_at, summary, result_path
                   FROM analysis_history
                   WHERE sample_id = ?
                   ORDER BY completed_at DESC NULLS LAST LIMIT ?""",
                [sample_id, limit],
            ).fetchall()
        else:
            rows = con.execute(
                """SELECT analysis_id, sample_id, analysis_type, status,
                          completed_at, summary, result_path
                   FROM analysis_history
                   ORDER BY completed_at DESC NULLS LAST LIMIT ?""",
                [limit],
            ).fetchall()

    return [
        {
            "analysis_id": r[0],
            "sample_id": r[1],
            "analysis_type": r[2],
            "status": r[3],
            "completed_at": r[4].isoformat() if r[4] else None,
            "summary": r[5],
            "has_report": bool(r[6] and Path(r[6]).exists()),
        }
        for r in rows
    ]


@app.get("/api/results/{analysis_id}/csv")
async def download_csv(analysis_id: str):
    import glob as _glob

    import duckdb
    from config.settings import DUCKDB_PATH, L2_ROOT

    _require_analysis_id(analysis_id)

    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        row = con.execute(
            "SELECT sample_id FROM analysis_history WHERE analysis_id=?",
            [analysis_id],
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="分析記錄不存在")

    sample_id = row[0]
    if not re.match(r"^[a-z0-9_-]+$", sample_id):
        raise HTTPException(status_code=422, detail="資料庫中 sample_id 格式不合法")
    l2_resolved = L2_ROOT.resolve()
    expr_dir = (L2_ROOT / sample_id / "expression").resolve()
    if not expr_dir.is_relative_to(l2_resolved):
        raise HTTPException(status_code=403, detail="Access denied")

    parquet_files = sorted(
        f for f in _glob.glob(str(expr_dir / "*.parquet"))
        if Path(f).resolve().is_relative_to(expr_dir)
    )
    if not parquet_files:
        raise HTTPException(status_code=404, detail="找不到 expression parquet 檔案")

    try:
        with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
            df = con.execute(
                "SELECT gene_name,"
                " SUM(count)::BIGINT AS total_umi,"
                " COUNT(*)::BIGINT AS n_bins"
                " FROM read_parquet(?)"
                " GROUP BY gene_name"
                " ORDER BY total_umi DESC"
                " LIMIT 100",
                [parquet_files],
            ).fetchdf()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"CSV 生成失敗：{e}")

    csv_content = df.to_csv(index=False)
    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=top_genes_{sample_id}.csv"},
    )


@app.get("/api/results/{analysis_id}/images")
async def result_images(analysis_id: str):
    """回傳分析報告中的圖片清單（filename + data_uri）。"""
    _require_analysis_id(analysis_id)
    import re

    import duckdb

    from config.settings import DUCKDB_PATH

    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        row = con.execute(
            "SELECT result_path FROM analysis_history WHERE analysis_id=?",
            [analysis_id],
        ).fetchone()
    if not row or not row[0]:
        return []
    md_path = Path(row[0]).resolve()
    if not md_path.is_relative_to(BIO_DB_ROOT.resolve()):
        return []
    if not md_path.exists() or md_path.suffix != ".md":
        return []
    try:
        md_text = md_path.read_text(encoding="utf-8")
    except Exception:
        return []
    images: list[dict] = []
    seen: set[str] = set()
    for i, m in enumerate(
        re.finditer(r"!\[([^\]]*)\]\((data:image/([^;]+);base64,([A-Za-z0-9+/=\r\n]+))\)", md_text)
    ):
        alt, img_type, b64_raw = m.group(1), m.group(3), m.group(4)
        b64_hash = b64_raw[:32]
        if b64_hash in seen:
            continue
        seen.add(b64_hash)
        data_uri = f"data:image/{img_type};base64," + b64_raw.replace("\n", "").replace("\r", "")
        filename = (alt.strip().replace(" ", "_") or f"image_{i}") + "." + img_type
        images.append({"filename": filename, "data_uri": data_uri})
    return images


@app.post("/api/chat")
async def chat(req: ChatRequest):
    from server.agent import handle_message

    try:
        history_deque, lock = _get_session(req.session_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    async def event_stream():
        yield _sse("status", {"phase": "thinking"})

        loop = asyncio.get_running_loop()
        history_snapshot = list(history_deque)
        future = loop.run_in_executor(
            None,
            lambda: handle_message(
                req.message,
                history_snapshot,
                backend=req.backend,
                image_base64=req.image_base64,
            ),
        )

        try:
            while True:
                done, _ = await asyncio.wait({future}, timeout=5.0)
                if done:
                    break
                yield _sse("ping", {})

            result = await future

            if result.tool_calls:
                yield _sse(
                    "tool_calls",
                    {
                        "calls": [
                            {"name": c["name"], "result_preview": str(c["result"])[:120]}
                            for c in result.tool_calls
                        ]
                    },
                )

            yield _sse(
                "tokens",
                {
                    "input": result.input_tokens,
                    "output": result.output_tokens,
                    "tools": len(result.tool_calls),
                },
            )

            report_link = _extract_report_link(result.tool_calls)
            images = await loop.run_in_executor(
                None, _extract_images_from_tool_calls, result.tool_calls
            )
            yield _sse(
                "message", {"text": result.text, "report_link": report_link, "images": images}
            )

            with lock:
                history_deque.append({"role": "user", "content": req.message})
                history_deque.append({"role": "assistant", "content": result.text})

        except Exception as e:
            logger.exception("Agent error session=%s", req.session_id)
            yield _sse("error", {"message": str(e)})
        finally:
            yield _sse("done", {})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _extract_images_from_tool_calls(tool_calls: list) -> list[dict]:
    """從工具呼叫結果抽出所有 base64 圖片，來源有兩種：
    1. tool result 文字中直接內嵌的 ![alt](data:image/...;base64,...) 區塊
    2. result_path 指向的 .md 報告檔案中的同格式區塊
    回傳 [{"filename": "fig_00.png", "data_uri": "data:image/png;base64,..."}, ...]
    """
    import re

    images: list[dict] = []
    seen: set[str] = set()
    IMG_RE = re.compile(r"!\[([^\]]*)\]\((data:image/([^;]+);base64,([A-Za-z0-9+/=\r\n]+))\)")

    def _extract_from_text(text: str, label_prefix: str) -> None:
        for i, m in enumerate(IMG_RE.finditer(text)):
            alt, img_type, b64_raw = m.group(1), m.group(3), m.group(4)
            b64_clean = b64_raw.replace("\n", "").replace("\r", "")
            b64_hash = b64_clean[:32]
            if b64_hash in seen:
                continue
            seen.add(b64_hash)
            filename = (alt.strip().replace(" ", "_") or f"{label_prefix}_{i}") + "." + img_type
            images.append(
                {"filename": filename, "data_uri": f"data:image/{img_type};base64,{b64_clean}"}
            )

    for call_idx, call in enumerate(tool_calls):
        result = call.get("result", "")
        if not isinstance(result, str):
            continue

        # 來源 1：tool result 文字直接內嵌圖片（bio_execute_code 的 fig_md）
        _extract_from_text(result, f"fig_{call_idx:02d}")

        # 來源 2：result_path 指向的 .md 報告檔案
        match = re.search(r"(?:result_path|report_path):\s*(.+?)(?:\n|$)", result)
        if match:
            md_path = Path(match.group(1).strip()).resolve()
            if (
                md_path.is_relative_to(BIO_DB_ROOT.resolve())
                and md_path.exists()
                and md_path.suffix == ".md"
            ):
                try:
                    _extract_from_text(
                        md_path.read_text(encoding="utf-8"), f"report_{call_idx:02d}"
                    )
                except Exception:
                    pass

    return images


def _extract_report_link(tool_calls: list) -> Optional[str]:
    """從工具呼叫結果中提取 analysis_id，生成報告連結。"""
    for call in tool_calls:
        result = call.get("result", "")
        if isinstance(result, str) and "analysis_id" in result:
            try:
                data = json.loads(result)
                aid = data.get("analysis_id")
                if aid:
                    return f"/results/{aid}"
            except Exception:
                pass
    return None


# ── ENGRAM 路由 ───────────────────────────────────────────────────────────────

_SAMPLE_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")
_ANALYSIS_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _require_sample_id(sample_id: str) -> None:
    if not _SAMPLE_ID_RE.match(sample_id):
        raise HTTPException(status_code=422, detail="無效的 sample_id 格式")


def _require_analysis_id(analysis_id: str) -> None:
    if not _ANALYSIS_ID_RE.match(analysis_id.lower()):
        raise HTTPException(status_code=422, detail="無效的 analysis_id 格式（需為 UUID）")


@app.post("/api/analysis/{analysis_id}/feedback")
async def analysis_feedback(analysis_id: str, body: FeedbackRequest):
    """記錄使用者對分析結果的 👍/👎 回饋（AA2 user_approval）。

    approval=1 → 讚；approval=-1 → 倒讚。
    寫入 analysis_history.user_approval，供 HELIX Eq.(1) f_promote 計算使用。
    """
    import duckdb
    from config.settings import DUCKDB_PATH
    from config.db_utils import safe_write

    _require_analysis_id(analysis_id)
    if body.approval not in (1, -1):
        raise HTTPException(status_code=422, detail="approval 必須為 1 或 -1")

    with duckdb.connect(str(DUCKDB_PATH)) as con:
        row = con.execute(
            "SELECT analysis_id FROM analysis_history WHERE analysis_id=?",
            [analysis_id],
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="分析記錄不存在")

        safe_write(
            con,
            "UPDATE analysis_history SET user_approval=? WHERE analysis_id=?",
            [body.approval, analysis_id],
        )

    label = "👍" if body.approval == 1 else "👎"
    return JSONResponse({"status": "ok", "analysis_id": analysis_id, "approval": body.approval, "label": label})


@app.get("/engram", response_class=HTMLResponse)
async def engram_page():
    html_path = STATIC_DIR / "engram.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return _static_missing_response("engram.html")


@app.get("/api/engram/samples")
async def engram_samples():
    """列出所有有 artifact 記錄的樣本及統計。"""
    import duckdb
    from config.settings import DUCKDB_PATH

    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        rows = con.execute(
            """
            SELECT ah.sample_id,
                   COUNT(DISTINCT ah.analysis_id) AS run_count,
                   COUNT(aa.artifact_id)          AS artifact_count,
                   MAX(ah.completed_at)           AS last_run_at
            FROM   analysis_history   ah
            JOIN   analysis_artifacts aa ON ah.analysis_id = aa.analysis_id
            WHERE  ah.status = 'completed'
            GROUP  BY ah.sample_id
            ORDER  BY last_run_at DESC NULLS LAST
            """
        ).fetchall()
    return [
        {
            "sample_id": r[0],
            "run_count": r[1],
            "artifact_count": r[2],
            "last_run_at": r[3].isoformat() if r[3] else None,
        }
        for r in rows
    ]


@app.get("/api/engram/summary/{sample_id}")
async def engram_summary(sample_id: str):
    """一個樣本的 artifact 統計概覽（0-token 設計）。"""
    _require_sample_id(sample_id)
    import duckdb
    from config.settings import DUCKDB_PATH
    from analysis.artifact_registry import artifact_summary

    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        return artifact_summary(con, sample_id)


@app.get("/api/engram/analyses/{sample_id}")
async def engram_analyses(sample_id: str):
    """列出某樣本下所有已完成分析，附帶 artifact 數量。"""
    _require_sample_id(sample_id)
    import duckdb
    from config.settings import DUCKDB_PATH

    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        rows = con.execute(
            """
            SELECT ah.analysis_id::VARCHAR, ah.analysis_type, ah.status,
                   ah.completed_at, ah.summary,
                   COUNT(aa.artifact_id) AS artifact_count
            FROM   analysis_history   ah
            LEFT   JOIN analysis_artifacts aa ON ah.analysis_id = aa.analysis_id
            WHERE  ah.sample_id = ?
              AND  ah.status    = 'completed'
            GROUP  BY ah.analysis_id, ah.analysis_type, ah.status, ah.completed_at, ah.summary
            ORDER  BY ah.completed_at DESC NULLS LAST
            """,
            [sample_id],
        ).fetchall()
    return [
        {
            "analysis_id": r[0],
            "analysis_type": r[1],
            "status": r[2],
            "completed_at": r[3].isoformat() if r[3] else None,
            "summary": r[4],
            "artifact_count": r[5],
        }
        for r in rows
    ]


@app.get("/api/engram/artifacts/{analysis_id}")
async def engram_artifacts(
    analysis_id: str,
    artifact_type: Optional[str] = None,
    artifact_subtype: Optional[str] = None,
    include_inline: bool = False,
):
    """列出某分析的所有 artifact（預設不含 inline_data）。"""
    _require_analysis_id(analysis_id)
    import duckdb
    from config.settings import DUCKDB_PATH
    from analysis.artifact_registry import get_artifacts

    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        return get_artifacts(
            con,
            analysis_id,
            artifact_type=artifact_type,
            artifact_subtype=artifact_subtype,
            include_inline=include_inline,
        )


@app.get("/api/engram/artifact/{artifact_id}/inline")
async def engram_artifact_inline(artifact_id: str):
    """取得單一 artifact 的 inline_data（base64）。"""
    _require_analysis_id(artifact_id)
    import duckdb
    from config.settings import DUCKDB_PATH

    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        row = con.execute(
            """SELECT artifact_id::VARCHAR, label, mime_type, inline_data
               FROM analysis_artifacts WHERE artifact_id = ?""",
            [artifact_id],
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Artifact 不存在")
    return {
        "artifact_id": row[0],
        "label": row[1],
        "mime_type": row[2],
        "inline_data": row[3],
    }


@app.get("/api/engram/compare")
async def engram_compare(
    ids: str,
    artifact_subtype: Optional[str] = None,
):
    """並排比較多個分析的 artifact。ids 以逗號分隔。"""
    import duckdb
    from config.settings import DUCKDB_PATH
    from analysis.artifact_registry import compare_analyses

    analysis_ids = [i.strip() for i in ids.split(",") if i.strip()]
    if not analysis_ids:
        return {}
    for aid in analysis_ids:
        _require_analysis_id(aid)
    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        return compare_analyses(
            con, analysis_ids, artifact_subtype=artifact_subtype, include_inline=False
        )


@app.get("/api/engram/search")
async def engram_search(
    q: str,
    artifact_subtype: Optional[str] = None,
    sample_id: Optional[str] = None,
    n: int = 10,
):
    """語意搜尋 artifact（Layer 1: 精確 subtype；Layer 2: HNSW）。"""
    import duckdb
    from config.settings import DUCKDB_PATH
    from analysis.artifact_registry import search_artifacts

    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        return search_artifacts(
            con,
            q,
            n=n,
            artifact_subtype=artifact_subtype,
            sample_id=sample_id,
        )


# ── 啟動 ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    _host = os.getenv("WEB_APP_HOST", "127.0.0.1")
    uvicorn.run("server.web_app:app", host=_host, port=8000, reload=False, log_level="info")
