"""
MCP Resources — 讓使用者透過 MCP 原生 resources 通道取得「分析後的數據檔案」。

定位：
    Tools 是「做事」，Resources 是「取檔」。分析函數產出的 artifact（csv/parquet/png/md）
    經 analysis.artifact_registry 登記於 analysis_artifacts（含 file_path / mime_type）。
    本模組把這些 artifact 暴露為 MCP resource：
        - list_artifact_resources()  → resources/list（給客戶端列出可取的檔）
        - read_artifact_resource()   → resources/read（依 artifact:// URI 取回內容）

設計要點：
    - URI 格式：artifact://<artifact_id>（UUID）
    - 文字類（csv/tsv/json/md/txt）回傳 str；二進位（parquet/png/...）回傳 bytes（SDK 轉 base64 blob）
    - 沙盒：file_path 解析後必須落在 BIO_DB_ROOT 內且存在，否則拒絕（defense in depth）
    - 大小上限：超過 settings.ARTIFACT_RESOURCE_MAX_MB 的檔拒絕 inline，引導改用 web_app 下載端點
      （大型 parquet base64 化會灌爆傳輸與 LLM context）

注意：read 一律從 file_path 讀磁碟（權威來源），不依賴 DB 內可能過期的 inline 快取。
"""
from __future__ import annotations

import re

from config.settings import (
    ARTIFACT_RESOURCE_MAX_MB,
    BIO_DB_ROOT,
    WEB_APP_BASE_URL,
    resolve_artifact_path,
)

ARTIFACT_URI_SCHEME = "artifact://"

# 視為「文字」的 mime（read 回傳 str）；其餘一律 bytes
_TEXT_MIME_PREFIXES = ("text/",)
_TEXT_MIME_EXACT = {
    "application/json",
    "text/csv",
    "text/tab-separated-values",
    "text/markdown",
}

# artifact_id 必為 UUID 格式（防注入；查詢雖已參數化，仍提早擋掉雜訊）
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class ArtifactResourceError(Exception):
    """resource 取用失敗（不存在 / 路徑越界 / 過大）。"""


def parse_artifact_uri(uri: str) -> str:
    """從 artifact://<id> 解出 artifact_id；格式錯誤時 raise。"""
    s = str(uri).strip()
    if not s.startswith(ARTIFACT_URI_SCHEME):
        raise ArtifactResourceError(f"非 artifact URI：{uri!r}")
    artifact_id = s[len(ARTIFACT_URI_SCHEME):].strip("/")
    if not _UUID_RE.match(artifact_id):
        raise ArtifactResourceError(f"artifact_id 格式錯誤：{artifact_id!r}")
    return artifact_id


def _is_text_mime(mime: str | None) -> bool:
    if not mime:
        return False
    if mime in _TEXT_MIME_EXACT:
        return True
    return any(mime.startswith(p) for p in _TEXT_MIME_PREFIXES)


def list_artifact_resources(con, limit: int = 200) -> list[dict]:
    """
    列出可取的 artifact，回傳供 types.Resource 建構的 dict list。

    每筆：{uri, name, mime_type, size_kb, description}
    依 created_at 由新到舊。
    """
    rows = con.execute(
        """
        SELECT artifact_id::VARCHAR, label, mime_type, file_size_kb,
               artifact_subtype, artifact_type
        FROM analysis_artifacts
        ORDER BY created_at DESC
        LIMIT ?
        """,
        [int(limit)],
    ).fetchall()

    out: list[dict] = []
    for artifact_id, label, mime, size_kb, subtype, atype in rows:
        desc = f"{atype or 'artifact'}"
        if subtype:
            desc += f" / {subtype}"
        if size_kb is not None:
            desc += f" · {size_kb} KB"
        out.append(
            {
                "uri": f"{ARTIFACT_URI_SCHEME}{artifact_id}",
                "name": label or f"artifact {artifact_id[:8]}",
                "mime_type": mime or "application/octet-stream",
                "size_kb": size_kb,
                "description": desc,
            }
        )
    return out


def read_artifact_resource(con, uri: str) -> tuple[str | bytes, str]:
    """
    依 artifact:// URI 取回內容，回傳 (content, mime_type)。

    文字 mime → str；二進位 → bytes。
    Raises ArtifactResourceError：不存在 / 路徑越界 / 檔案遺失 / 超過大小上限。
    """
    artifact_id = parse_artifact_uri(uri)

    row = con.execute(
        "SELECT file_path, mime_type FROM analysis_artifacts WHERE artifact_id = ?",
        [artifact_id],
    ).fetchone()
    if not row:
        raise ArtifactResourceError(f"artifact_id={artifact_id!r} 不存在於 analysis_artifacts")

    file_path, mime = row
    if not file_path:
        raise ArtifactResourceError(f"artifact_id={artifact_id!r} 無 file_path 記錄")

    abs_path = resolve_artifact_path(file_path).resolve()

    # 沙盒：解析後必須落在 BIO_DB_ROOT 內（防 ../ 越界）
    root = BIO_DB_ROOT.resolve()
    if root not in abs_path.parents and abs_path != root:
        raise ArtifactResourceError(f"路徑越界，拒絕讀取：{file_path!r}")
    if not abs_path.is_file():
        raise ArtifactResourceError(f"檔案不存在或非一般檔：{file_path!r}")

    size = abs_path.stat().st_size
    max_bytes = int(ARTIFACT_RESOURCE_MAX_MB * 1_048_576)
    if size > max_bytes:
        raise ArtifactResourceError(
            f"檔案 {size / 1_048_576:.1f} MB 超過 inline 上限 "
            f"{ARTIFACT_RESOURCE_MAX_MB} MB。請改用 web_app 下載："
            f"/api/engram/artifact/{artifact_id}/inline 或 /results/<analysis_id>。"
        )

    if _is_text_mime(mime):
        return abs_path.read_text(encoding="utf-8", errors="replace"), mime
    return abs_path.read_bytes(), mime or "application/octet-stream"


def get_artifact_handle(con, artifact_id: str, preview_lines: int = 20) -> dict:
    """
    取得 artifact 的「取用 handle」——給任何 MCP client（含不支援 resources 者）的備援。

    回傳 metadata + 本地路徑 + web_app 下載 URL + 文字預覽（不倒整個檔案進 context）。

    Returns dict:
        {found, artifact_id, label, subtype, mime_type, size_kb,
         local_path, web_url, preview}
    """
    if not _UUID_RE.match(artifact_id or ""):
        raise ArtifactResourceError(f"artifact_id 格式錯誤：{artifact_id!r}")

    row = con.execute(
        """
        SELECT label, artifact_subtype, mime_type, file_size_kb, file_path
        FROM analysis_artifacts WHERE artifact_id = ?
        """,
        [artifact_id],
    ).fetchone()
    if not row:
        return {"found": False, "artifact_id": artifact_id}

    label, subtype, mime, size_kb, file_path = row
    abs_path = resolve_artifact_path(file_path).resolve() if file_path else None

    preview = None
    if abs_path and abs_path.is_file() and _is_text_mime(mime):
        with abs_path.open("r", encoding="utf-8", errors="replace") as fh:
            head = [next(fh, "") for _ in range(preview_lines)]
        preview = "".join(head).rstrip()

    return {
        "found": True,
        "artifact_id": artifact_id,
        "label": label,
        "subtype": subtype,
        "mime_type": mime,
        "size_kb": size_kb,
        "local_path": str(abs_path) if abs_path else None,
        "web_url": f"{WEB_APP_BASE_URL}/api/engram/artifact/{artifact_id}/inline",
        "preview": preview,
    }
