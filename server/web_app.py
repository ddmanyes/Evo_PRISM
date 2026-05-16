"""
Hermes Bio-Memory — Web UI (FastAPI)

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
import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import mistune
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from config.settings import BIO_DB_ROOT
from config.db_utils import db_health_check

logger = logging.getLogger(__name__)

app = FastAPI(title="Hermes Bio-Memory", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

RESULTS_DIR = BIO_DB_ROOT / "results"

# ── Session 管理 ──────────────────────────────────────────────────────────────

_MAX_HISTORY = 24  # 12 輪 = 24 messages
_sessions: dict[str, collections.deque] = {}
_session_locks: dict[str, threading.Lock] = {}
_sessions_meta: dict[str, datetime] = {}


def _get_session(session_id: str) -> tuple[collections.deque, threading.Lock]:
    if session_id not in _sessions:
        _sessions[session_id] = collections.deque(maxlen=_MAX_HISTORY)
        _session_locks[session_id] = threading.Lock()
    _sessions_meta[session_id] = datetime.now(timezone.utc)
    return _sessions[session_id], _session_locks[session_id]


# ── Pydantic models ───────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    session_id: str
    message: str


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

    md_text = Path(result_path).read_text(encoding="utf-8")
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
    expr_glob = str(L2_ROOT / sample_id / "expression" / "*.parquet")

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


@app.post("/api/chat")
async def chat(req: ChatRequest):
    from server.agent import handle_message

    history_deque, lock = _get_session(req.session_id)

    async def event_stream():
        yield _sse("status", {"phase": "thinking"})

        loop = asyncio.get_running_loop()
        history_snapshot = list(history_deque)
        future = loop.run_in_executor(
            None, handle_message, req.message, history_snapshot
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
            })

            report_link = _extract_report_link(result.tool_calls)
            yield _sse("message", {"text": result.text, "report_link": report_link})

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


# ── 啟動 ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server.web_app:app", host="0.0.0.0", port=8000, reload=False, log_level="info")
