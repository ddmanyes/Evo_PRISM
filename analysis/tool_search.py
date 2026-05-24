"""工具語意搜尋（tool discovery）— 讓 Agent 在動態寫碼前先找既有可重用函數。

定位：補上「先搜既有工具 → 命中就重用 → 全 miss 才 bio_execute_code」這條閉環的
「發現」環節，避免 Agent 不知道 analysis/ 已有現成（且測過）的函數而從零重寫。

設計：
    - catalog 存於 bio_memory.duckdb 的 `tool_catalog` 表（與 memory_recent 同庫，
      共用 DuckDB VSS / HNSW 基礎建設）。
    - 來源：沙盒允許 import 的 analysis.* 模組（code_executor.ALLOWED_IMPORTS）之公開函數，
      以及 register_tool() 註冊／畢業的 HELIX 工具（自動進 catalog）。
    - 搜尋：本地 bge-m3 embedding + DuckDB HNSW 餘弦相似度——**0 LLM token**（同 l1_cache）。
      唯一 token 成本是回傳的 top-K 精簡結果（name + 簽名 + 一行說明）。
    - 索引冪等：以 source_hash 比對，內容沒變不重算 embedding（省本地算力）。

token 經濟學：搜尋引擎本身不耗 LLM token（embedding 在本地 server、HNSW 在 DuckDB）；
比「把整份工具目錄塞 system prompt」更省——後者每則訊息都付全目錄，這裡只在需要時付 top-K。
"""

from __future__ import annotations

import importlib
import inspect
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

import duckdb

from config.settings import (
    EMBEDDING_DIM,
    L1_CACHE_PATH,
    TOOL_SEARCH_COSINE_THRESHOLD,
    TOOL_SEARCH_TOP_N,
)

logger = logging.getLogger(__name__)

# 預設可重用函數來源：與沙盒 ALLOWED_IMPORTS 的 analysis.* 子集一致
# （「能 import 的」=「該被發現的」）。畢業後新增的模組請一併加入此清單與 ALLOWED_IMPORTS。
DEFAULT_SOURCE_MODULES = (
    "analysis.spatial_eda",
    "analysis.bulk_eda",
    "analysis.pathway_scoring",
    "analysis.multiomics_integration",
    "analysis.bulk_timeseries",
    "analysis.report_generator",
)


# ── 連線 / schema ─────────────────────────────────────────────────────────────


def _setup_vss(con: duckdb.DuckDBPyConnection, *, read_only: bool = False) -> None:
    try:
        con.execute("LOAD vss")
        if not read_only:
            con.execute("SET hnsw_enable_experimental_persistence = true")
    except Exception as e:
        logger.warning("VSS load warning: %s", e)


def ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    """建立 tool_catalog 表與 HNSW 索引（冪等）。"""
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS tool_catalog(
            name          VARCHAR PRIMARY KEY,   -- 唯一鍵：module.func 或 HELIX tool_name
            kind          VARCHAR,               -- 'function' | 'tool'
            signature     VARCHAR,
            summary       VARCHAR,               -- docstring 首行 / 工具描述
            module_path   VARCHAR,
            function_name VARCHAR,
            import_hint   VARCHAR,               -- 'from analysis.x import y'
            source_hash   VARCHAR,
            embedding     FLOAT[{EMBEDDING_DIM}],
            updated_at    TIMESTAMP
        )
        """
    )
    try:
        con.execute(
            "CREATE INDEX IF NOT EXISTS tool_catalog_hnsw "
            "ON tool_catalog USING HNSW (embedding) WITH (metric = 'cosine')"
        )
    except Exception as e:
        logger.warning("tool_catalog HNSW index skipped: %s", e)


# ── 內省工具 ──────────────────────────────────────────────────────────────────


def _signature_text(fn) -> str:
    try:
        return f"{fn.__name__}{inspect.signature(fn)}"
    except (TypeError, ValueError):
        return f"{fn.__name__}(...)"


def _doc_first_line(fn) -> str:
    doc = inspect.getdoc(fn) or ""
    for line in doc.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _source_hash(fn) -> str:
    """以函數原始碼算 hash（沿用 HELIX 的 compute_tool_hash，索引冪等與 HELIX 對齊）。"""
    try:
        from analysis.tool_registry import compute_tool_hash

        return compute_tool_hash(fn)
    except Exception:
        return "unavailable"


def _iter_public_functions(module_path: str) -> Iterator[tuple[str, object]]:
    """yield (function_name, fn)：模組內、定義於該模組、非底線開頭的可呼叫物件。"""
    mod = importlib.import_module(module_path)
    for fname, obj in inspect.getmembers(mod, inspect.isfunction):
        if fname.startswith("_"):
            continue
        if getattr(obj, "__module__", None) != module_path:
            continue  # 排除 import 進來的函數
        yield fname, obj


def _embed_text_for(signature: str, summary: str, module_path: str) -> str:
    """組裝送去 embedding 的文字——含模組、簽名、說明，讓語意搜尋按「意圖」命中。"""
    return f"{module_path} | {signature}\n{summary}".strip()


# ── 索引（寫入）───────────────────────────────────────────────────────────────


def _upsert(
    con: duckdb.DuckDBPyConnection,
    *,
    name: str,
    kind: str,
    signature: str,
    summary: str,
    module_path: str,
    function_name: str,
    import_hint: str,
    source_hash: str,
    embedding_provider: Optional[str],
) -> str:
    """寫入單筆；source_hash 未變則跳過（不重算 embedding）。回傳 'indexed'|'skipped'。"""
    existing = con.execute("SELECT source_hash FROM tool_catalog WHERE name = ?", [name]).fetchone()
    if existing and existing[0] == source_hash and source_hash != "unavailable":
        return "skipped"

    from analysis.embed import embed_text

    vec = embed_text(_embed_text_for(signature, summary, module_path), provider=embedding_provider)

    con.execute("DELETE FROM tool_catalog WHERE name = ?", [name])
    con.execute(
        """
        INSERT INTO tool_catalog
            (name, kind, signature, summary, module_path, function_name,
             import_hint, source_hash, embedding, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            name,
            kind,
            signature,
            summary,
            module_path,
            function_name,
            import_hint,
            source_hash,
            vec,
            datetime.now(timezone.utc),
        ],
    )
    return "indexed"


def index_modules(
    modules: Optional[tuple[str, ...]] = None,
    *,
    cache_path: Optional[Path] = None,
    embedding_provider: Optional[str] = None,
) -> dict:
    """回填：把指定 analysis.* 模組的公開函數索引進 catalog。

    Returns {"indexed": n, "skipped": n, "errors": [..]}。需要 embedding server 在線。
    """
    mods = DEFAULT_SOURCE_MODULES if modules is None else modules
    path = cache_path or L1_CACHE_PATH
    indexed = skipped = 0
    errors: list[str] = []

    with duckdb.connect(str(path)) as con:
        _setup_vss(con)
        ensure_schema(con)
        for module_path in mods:
            try:
                for fname, fn in _iter_public_functions(module_path):
                    name = f"{module_path}.{fname}"
                    status = _upsert(
                        con,
                        name=name,
                        kind="function",
                        signature=_signature_text(fn),
                        summary=_doc_first_line(fn),
                        module_path=module_path,
                        function_name=fname,
                        import_hint=f"from {module_path} import {fname}",
                        source_hash=_source_hash(fn),
                        embedding_provider=embedding_provider,
                    )
                    indexed += status == "indexed"
                    skipped += status == "skipped"
            except Exception as e:  # noqa: BLE001 — 單一模組失敗不應中斷整批
                logger.warning("index_modules: %s failed: %s", module_path, e)
                errors.append(f"{module_path}: {e}")
        con.execute("CHECKPOINT")

    logger.info("index_modules: indexed=%d skipped=%d errors=%d", indexed, skipped, len(errors))
    return {"indexed": indexed, "skipped": skipped, "errors": errors}


def index_registered_tool(
    tool_name: str,
    module_path: str,
    function_name: str,
    description: str,
    *,
    cache_path: Optional[Path] = None,
    embedding_provider: Optional[str] = None,
) -> str:
    """register_tool() 掛勾用：把剛註冊／畢業的 HELIX 工具加進 catalog（best-effort）。

    回傳 'indexed'|'skipped'|'error'。失敗只記 warning，絕不向外拋（不可拖累註冊）。
    """
    path = cache_path or L1_CACHE_PATH
    try:
        signature = f"{function_name}(...)"
        source_hash = description  # 工具描述變動即重嵌入（簡化：以描述當指紋）
        try:
            mod = importlib.import_module(module_path)
            fn = getattr(mod, function_name.split(".")[0], None)
            if fn is not None:
                signature = _signature_text(fn)
                source_hash = _source_hash(fn)
        except Exception:
            pass

        with duckdb.connect(str(path)) as con:
            _setup_vss(con)
            ensure_schema(con)
            status = _upsert(
                con,
                name=tool_name,
                kind="tool",
                signature=signature,
                summary=description,
                module_path=module_path,
                function_name=function_name,
                import_hint=f"（MCP 工具）{tool_name}",
                source_hash=source_hash,
                embedding_provider=embedding_provider,
            )
            con.execute("CHECKPOINT")
        return status
    except Exception as e:  # noqa: BLE001
        logger.warning("index_registered_tool: %r skipped: %s", tool_name, e)
        return "error"


# ── 搜尋（讀取）───────────────────────────────────────────────────────────────


def search_tools(
    query: str,
    *,
    n: int = TOOL_SEARCH_TOP_N,
    threshold: float = TOOL_SEARCH_COSINE_THRESHOLD,
    cache_path: Optional[Path] = None,
    embedding_provider: Optional[str] = None,
) -> list[dict]:
    """語意搜尋既有工具（HNSW cosine）。回傳 top-K 精簡候選，依 score 降冪。

    每筆：name, kind, signature, summary, import_hint, module_path, function_name, score。
    空列表 = 無夠相似的既有工具（agent 應改用 bio_execute_code 從零寫）。
    """
    path = cache_path or L1_CACHE_PATH
    if not path.exists():
        logger.warning("tool catalog db not found: %s", path)
        return []

    from analysis.embed import embed_text

    qvec = embed_text(query, provider=embedding_provider)

    with duckdb.connect(str(path)) as con:
        _setup_vss(con)
        ensure_schema(con)
        if con.execute("SELECT COUNT(*) FROM tool_catalog").fetchone()[0] == 0:
            return []
        rows = con.execute(
            f"""
            SELECT name, kind, signature, summary, import_hint,
                   module_path, function_name,
                   array_cosine_similarity(embedding, ?::FLOAT[{EMBEDDING_DIM}]) AS score
            FROM   tool_catalog
            ORDER BY score DESC
            LIMIT ?
            """,
            [qvec, int(n)],
        ).fetchall()

    cols = [
        "name",
        "kind",
        "signature",
        "summary",
        "import_hint",
        "module_path",
        "function_name",
        "score",
    ]
    return [dict(zip(cols, r)) for r in rows if r[-1] >= threshold]


if __name__ == "__main__":
    import sys

    from analysis.embed import server_health

    if not server_health()["ok"]:
        print("[tool_search] embedding server 離線，先啟動 port 8081")
        raise SystemExit(1)

    print("[tool_search] 回填既有 analysis.* 函數 …")
    print(" ", index_modules())
    q = sys.argv[1] if len(sys.argv) > 1 else "時間序列 log2 fold change"
    print(f"\n[tool_search] 搜尋：{q!r}")
    for r in search_tools(q, threshold=0.0):
        print(f"  {r['score']:.3f}  {r['name']}{r['signature']}  — {r['summary'][:40]}")
