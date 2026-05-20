"""動態程式碼畢業層 — 把反覆執行的 dynamic_code 引導固化成正式 analysis/ 函數（Phase 3）。

定位：純邏輯，不含 FastAPI，可單元測試。三件事：
    list_candidates()  — 找出值得畢業的 dynamic_code（同 description 多次 completed + 非 1 行噪音）
    read_archive()     — 安全讀取某次執行的 archive（code.py / meta.json / output）
    graduation_plan()  — read_archive + 生成 analysis/ 函數骨架 + register_tool() 片段

設計原則：
    - **只讀 + 生成片段，不自動寫檔**。把 Python 自動寫進 analysis/ 風險高（還要補
      register_tool、去硬編碼、改圖片輸出），故畢業助手只產出「可複製的骨架」交人工審。
    - archive 讀取沙盒限定在 DYNAMIC_CODE_DIR 內，杜絕路徑穿越。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from config.settings import (
    BIO_DB_ROOT,
    DYNAMIC_CODE_DIR,
    GRADUATION_MIN_CODE_LINES,
    GRADUATION_MIN_COMPLETED_RUNS,
)


# ── 候選查詢 ─────────────────────────────────────────────────────────────────

def list_candidates(
    con,
    *,
    min_code_lines: int | None = None,
    min_completed: int | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """找出畢業候選：同 description 的 completed 次數 ≥ 門檻、最大 code_lines ≥ 門檻。

    比 dashboard.dynamic_code_panel 的「重複 ≥ 2 次」更嚴格——額外要求 code_lines
    達標，過濾掉 print(1) 這類 smoke/test 噪音。每筆附一個代表性（最新 completed）
    執行的 analysis_id + result_path，供畢業助手讀 code。
    """
    mcl = GRADUATION_MIN_CODE_LINES if min_code_lines is None else min_code_lines
    mcr = GRADUATION_MIN_COMPLETED_RUNS if min_completed is None else min_completed

    return _rows_to_dicts(
        con.execute(
            """
            SELECT parameters->>'description' AS description,
                   COUNT(*) AS runs,
                   COUNT(*) FILTER (WHERE status='completed') AS completed_runs,
                   MAX(TRY_CAST(parameters->>'code_lines' AS INTEGER)) AS max_code_lines,
                   MAX(completed_at) AS last_run,
                   ARG_MAX(analysis_id::VARCHAR, completed_at)
                       FILTER (WHERE status='completed') AS rep_analysis_id,
                   ARG_MAX(result_path, completed_at)
                       FILTER (WHERE status='completed') AS rep_result_path
            FROM analysis_history
            WHERE analysis_type='dynamic_code'
            GROUP BY 1
            HAVING COUNT(*) FILTER (WHERE status='completed') >= ?
               AND MAX(TRY_CAST(parameters->>'code_lines' AS INTEGER)) >= ?
            ORDER BY completed_runs DESC, max_code_lines DESC
            LIMIT ?
            """,
            [mcr, mcl, int(limit)],
        )
    )


def _rows_to_dicts(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# ── archive 讀取（沙盒）──────────────────────────────────────────────────────

def read_archive(con, analysis_id: str) -> dict[str, Any]:
    """讀取某次 dynamic_code 執行的 archive 目錄內容。

    Raises:
        ValueError: 找不到記錄 / result_path 為空 / 路徑逸出沙盒 / 目錄不存在。
    """
    row = con.execute(
        "SELECT result_path, status, parameters->>'description' "
        "FROM analysis_history WHERE analysis_id = ? AND analysis_type='dynamic_code'",
        [analysis_id],
    ).fetchone()
    if row is None:
        raise ValueError(f"找不到 dynamic_code 記錄：{analysis_id}")

    result_path, status, description = row
    if not result_path:
        raise ValueError("此記錄無 result_path")

    archive = Path(result_path)
    if not archive.is_absolute():
        archive = BIO_DB_ROOT / archive
    archive = archive.resolve()

    # 沙盒：必須落在 DYNAMIC_CODE_DIR 內
    sandbox = DYNAMIC_CODE_DIR.resolve()
    if not archive.is_relative_to(sandbox):
        raise ValueError("result_path 逸出 dynamic_code 沙盒")
    if not archive.is_dir():
        raise ValueError(f"archive 目錄不存在：{result_path}（專案可能已搬遷）")

    code = _read_text(archive / "code.py")
    meta = _read_json(archive / "meta.json")
    # 成功有 output.txt、失敗有 traceback.txt
    output = _read_text(archive / "output.txt") or _read_text(archive / "traceback.txt")

    return {
        "analysis_id": analysis_id,
        "description": description or meta.get("description") or "",
        "status": status,
        "archive_dir": str(archive.relative_to(BIO_DB_ROOT.resolve())),
        "code": code,
        "meta": meta,
        "output": output,
    }


def _read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8") if p.is_file() else ""
    except OSError:
        return ""


def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    except (OSError, json.JSONDecodeError):
        return {}


# ── 骨架生成 ─────────────────────────────────────────────────────────────────

def slugify(description: str) -> str:
    """description → Python 識別字安全的 snake_case slug。"""
    s = re.sub(r"[^0-9a-zA-Z]+", "_", (description or "").lower()).strip("_")
    if not s:
        s = "graduated_analysis"
    if s[0].isdigit():
        s = f"g_{s}"
    return s


def _indent(code: str, spaces: int = 4) -> str:
    pad = " " * spaces
    return "\n".join((pad + line) if line.strip() else line for line in code.splitlines())


def generate_scaffold(description: str, code: str, *, analysis_id: str) -> dict[str, str]:
    """從原始 code 生成 analysis/ 函數骨架 + register_tool() 片段。

    Returns dict: {fn_name, module_name, tool_name, scaffold}
    """
    slug = slugify(description)
    fn_name = f"run_{slug}"
    module_name = slug
    tool_name = f"bio_{slug}"
    id8 = analysis_id[:8]

    body = _indent(code.rstrip() or "pass")

    scaffold = f'''"""{description} — graduated from dynamic_code {id8}.

⚠️ 自動生成骨架，畢業前必須人工審查：
  - 去除硬編碼路徑 → 改從 config.settings 取（CLAUDE.md：禁止硬編碼路徑）
  - 參數化寫死的值（sample_id 等）並補型別註記
  - 若有 matplotlib 圖 → 改回 inline base64 data URI（CLAUDE.md 圖片輸出規則）
  - 回傳 Markdown 字串；呼叫端寫入 analysis_history（status/result_path/tool_id）
"""
from __future__ import annotations


def {fn_name}(sample_id: str | None = None) -> str:
    """{description}"""
    # ── 原 dynamic_code（描述：{description}）─────────────────────
{body}
    # ─────────────────────────────────────────────────────────────
    return "TODO: 回傳 Markdown 摘要"


# ── 骨架修改完成後，註冊到 HELIX（執行一次；見 CLAUDE.md 7.1）────────────────
# import duckdb
# from config.settings import DUCKDB_PATH
# from analysis.tool_registry import register_tool
#
# with duckdb.connect(str(DUCKDB_PATH)) as con:
#     register_tool(
#         con,
#         tool_name="{tool_name}",
#         fn={fn_name},
#         version="0.1.0",
#         module_path="analysis.{module_name}",
#         function_name="{fn_name}",
#         change_reason="graduated from dynamic_code {id8}",
#     )
'''
    return {
        "fn_name": fn_name,
        "module_name": module_name,
        "tool_name": tool_name,
        "suggested_path": f"analysis/{module_name}.py",
        "scaffold": scaffold,
    }


def graduation_plan(con, analysis_id: str) -> dict[str, Any]:
    """組合 archive 讀取 + 骨架生成，供畢業助手一次回傳。"""
    arc = read_archive(con, analysis_id)
    gen = generate_scaffold(arc["description"], arc["code"], analysis_id=analysis_id)
    return {**arc, **gen}
