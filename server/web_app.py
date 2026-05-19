"""
BioAgent — Web UI (FastAPI)

路由：
    GET  /                              → 聊天介面（HTML）
    GET  /history                       → 歷史記錄瀏覽頁（HTML）
    GET  /results/{analysis_id}         → 報告 HTML 頁
    POST /api/chat                      → SSE 聊天（text/event-stream）
    GET  /api/history                   → 歷史查詢 JSON
    GET  /api/results/{id}/csv          → 下載 top_genes CSV
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
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from config.settings import BIO_DB_ROOT
from config.db_utils import db_health_check, open_db

logger = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def _lifespan(_app: FastAPI):
    # 啟動時清理殭屍 running 記錄（server 重啟 = 舊進程已死）
    # 用 BaseException 而非 Exception — DuckDB FatalException 是 C++ exception，
    # 不繼承 Python Exception，except Exception 抓不到會直接 abort process。
    try:
        from config.db_utils import cleanup_stale_runs
        with open_db() as _con:
            n = cleanup_stale_runs(_con, hours=0)
            if n:
                logger.info("startup: cleared %d zombie running records", n)
    except BaseException as _e:
        logger.warning("startup cleanup failed (non-fatal): %s", _e)

    async def _cleanup_loop():
        while True:
            await asyncio.sleep(3600)
            _cleanup_old_sessions()
    task = asyncio.create_task(_cleanup_loop())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="BioAgent", version="1.0.0", lifespan=_lifespan)

_cors_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()] or ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# MCP HTTP transport — 掛載於 /mcp，供外部客戶端直接呼叫 MCP 工具
try:
    from server.bio_memory_server import create_http_app as _create_mcp_app
    app.mount("/mcp", _create_mcp_app())
    logger.info("MCP HTTP transport mounted at /mcp")
except Exception as _e:
    logger.warning("MCP HTTP mount failed (non-fatal): %s", _e)

STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

RESULTS_DIR = BIO_DB_ROOT / "results"

# ── Session 管理 ──────────────────────────────────────────────────────────────

_MAX_HISTORY = 24  # 12 輪 = 24 messages
_MAX_SESSIONS = 200  # 防止記憶體耗盡
_SESSION_TTL_HOURS = 24
_SESSION_ID_RE = re.compile(r'^[a-zA-Z0-9_\-]{1,64}$')
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
        sid for sid, last in _sessions_meta.items()
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
    message: str
    backend: str = ""  # "local" | "claude" | ""（空字串讀 INFERENCE_BACKEND env）
    image_base64: str = ""  # data:image/png;base64,... 或純 base64


# ── HTML 工具 ─────────────────────────────────────────────────────────────────

_md = mistune.create_markdown(plugins=["table", "strikethrough"])


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

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = STATIC_DIR / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>index.html not found</h1>", status_code=500)


@app.get("/history", response_class=HTMLResponse)
async def history_page():
    html_path = STATIC_DIR / "history.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>history.html not found</h1>", status_code=500)


@app.get("/results/{analysis_id}", response_class=HTMLResponse)
async def report_page(analysis_id: str):
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
    if not result_path or not Path(result_path).exists():
        raise HTTPException(status_code=404, detail="報告檔案不存在")

    resolved = Path(result_path).resolve()
    if not str(resolved).startswith(str(BIO_DB_ROOT.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")

    md_text = resolved.read_text(encoding="utf-8")
    ts = completed_at.strftime("%Y-%m-%d %H:%M UTC") if completed_at else ""
    return HTMLResponse(_render_report_html(analysis_id, md_text, sample_id, ts))


# ── 路由：API ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    try:
        stats = db_health_check()
        return {"ok": True, "db": stats}
    except Exception as e:
        return {"ok": False, "error": str(e)}


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
    import duckdb
    from config.settings import DUCKDB_PATH, L2_ROOT

    with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
        row = con.execute(
            "SELECT sample_id FROM analysis_history WHERE analysis_id=?",
            [analysis_id],
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="分析記錄不存在")

    sample_id = row[0]
    if not re.match(r'^[a-z0-9_-]+$', sample_id):
        raise HTTPException(status_code=422, detail="資料庫中 sample_id 格式不合法")
    expr_glob = str((L2_ROOT / sample_id / "expression").resolve() / "*.parquet")
    if not expr_glob.startswith(str(L2_ROOT.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")

    try:
        with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
            df = con.execute(
                f"""SELECT gene_name,
                           SUM(count)::BIGINT AS total_umi,
                           COUNT(*)::BIGINT   AS n_bins
                    FROM read_parquet('{expr_glob}')
                    GROUP BY gene_name
                    ORDER BY total_umi DESC
                    LIMIT 100"""
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
    if not str(md_path).startswith(str(BIO_DB_ROOT.resolve())):
        return []
    if not md_path.exists() or md_path.suffix != ".md":
        return []
    try:
        md_text = md_path.read_text(encoding="utf-8")
    except Exception:
        return []
    images: list[dict] = []
    seen: set[str] = set()
    for i, m in enumerate(re.finditer(
        r'!\[([^\]]*)\]\((data:image/([^;]+);base64,([A-Za-z0-9+/=\r\n]+))\)', md_text
    )):
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
                req.message, history_snapshot,
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
                yield _sse("tool_calls", {"calls": [
                    {"name": c["name"], "result_preview": str(c["result"])[:120]}
                    for c in result.tool_calls
                ]})

            yield _sse("tokens", {
                "input": result.input_tokens,
                "output": result.output_tokens,
                "tools": len(result.tool_calls),
            })

            report_link = _extract_report_link(result.tool_calls)
            images = await loop.run_in_executor(
                None, _extract_images_from_tool_calls, result.tool_calls
            )
            yield _sse("message", {"text": result.text, "report_link": report_link, "images": images})

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
    IMG_RE = re.compile(
        r'!\[([^\]]*)\]\((data:image/([^;]+);base64,([A-Za-z0-9+/=\r\n]+))\)'
    )

    def _extract_from_text(text: str, label_prefix: str) -> None:
        for i, m in enumerate(IMG_RE.finditer(text)):
            alt, data_uri, img_type, b64_raw = m.group(1), m.group(2), m.group(3), m.group(4)
            b64_clean = b64_raw.replace("\n", "").replace("\r", "")
            b64_hash = b64_clean[:32]
            if b64_hash in seen:
                continue
            seen.add(b64_hash)
            filename = (alt.strip().replace(" ", "_") or f"{label_prefix}_{i}") + "." + img_type
            images.append({"filename": filename, "data_uri": f"data:image/{img_type};base64,{b64_clean}"})

    for call_idx, call in enumerate(tool_calls):
        result = call.get("result", "")
        if not isinstance(result, str):
            continue

        # 來源 1：tool result 文字直接內嵌圖片（bio_execute_code 的 fig_md）
        _extract_from_text(result, f"fig_{call_idx:02d}")

        # 來源 2：result_path 指向的 .md 報告檔案
        match = re.search(r'(?:result_path|report_path):\s*(.+?)(?:\n|$)', result)
        if match:
            md_path = Path(match.group(1).strip()).resolve()
            if (str(md_path).startswith(str(BIO_DB_ROOT.resolve()))
                    and md_path.exists() and md_path.suffix == ".md"):
                try:
                    _extract_from_text(md_path.read_text(encoding="utf-8"), f"report_{call_idx:02d}")
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

_SAMPLE_ID_RE   = re.compile(r"^[a-zA-Z0-9_\-]+$")
_ANALYSIS_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def _require_sample_id(sample_id: str) -> None:
    if not _SAMPLE_ID_RE.match(sample_id):
        raise HTTPException(status_code=422, detail="無效的 sample_id 格式")


def _require_analysis_id(analysis_id: str) -> None:
    if not _ANALYSIS_ID_RE.match(analysis_id.lower()):
        raise HTTPException(status_code=422, detail="無效的 analysis_id 格式（需為 UUID）")


@app.get("/engram", response_class=HTMLResponse)
async def engram_page():
    html_path = STATIC_DIR / "engram.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>engram.html not found</h1>", status_code=500)


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
            "sample_id":      r[0],
            "run_count":      r[1],
            "artifact_count": r[2],
            "last_run_at":    r[3].isoformat() if r[3] else None,
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
            "analysis_id":    r[0],
            "analysis_type":  r[1],
            "status":         r[2],
            "completed_at":   r[3].isoformat() if r[3] else None,
            "summary":        r[4],
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
            con, analysis_id,
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
        "label":       row[1],
        "mime_type":   row[2],
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
        return compare_analyses(con, analysis_ids,
                                artifact_subtype=artifact_subtype,
                                include_inline=False)


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
            con, q,
            n=n,
            artifact_subtype=artifact_subtype,
            sample_id=sample_id,
        )


# ── 啟動 ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server.web_app:app", host="0.0.0.0", port=8000, reload=False, log_level="info")
