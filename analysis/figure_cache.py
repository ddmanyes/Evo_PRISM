"""
Figure cache — 把報告 markdown 內嵌的 base64 圖片從「文字 context」搬到「按需索取」通道。

動機：analysis/ 的函數依 CLAUDE.md 規範以 inline base64 data URI 回傳圖片，供 Web UI 渲染。
但這些 base64 一旦進入純文字 LLM 的 context（如本機 llama.cpp，16k 視窗），一份多圖報告
輕易就是 20 萬 token，直接爆掉。

策略：MCP 工具回傳給 LLM 之前，用 strip_base64_for_llm() 把每張 inline 圖換成緊湊佔位符
（含可回溯的 figure_id），同時把圖解碼快取到 gold/figure_cache/<id>.<ext>。需要看圖時，
由 bio_get_figure(figure_id) 走 MCP image content 通道單張回傳——而不是把整份報告灌回 context。

設計要點：
- figure_id = sha256(base64)[:12]，content-addressed → 同一張圖只快取一次（idempotent）
- 本地獨立 .png 仍由 analysis 函數各自保留（供 result_path 記錄）；本快取是 LLM 索取專用副本
"""
from __future__ import annotations

import hashlib
import re

from config.settings import L1_ROOT

# 快取目錄：L1 (gold/) 之下，與語意快取同層
FIGURE_CACHE_DIR = L1_ROOT / "figure_cache"

# markdown inline 圖片：![alt](data:image/<subtype>;base64,<payload>)
# base64 payload 不含 ')'，以 [^)] 安全擷取
_INLINE_IMG_RE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\(data:image/(?P<subtype>[A-Za-z0-9.+-]+);base64,(?P<b64>[^)]+)\)"
)

# data URI subtype → 副檔名（正規化）
_SUBTYPE_TO_EXT = {
    "png": "png",
    "jpeg": "jpg",
    "jpg": "jpg",
    "gif": "gif",
    "webp": "webp",
    "svg+xml": "svg",
}
_EXT_TO_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "svg": "image/svg+xml",
}


def _figure_id(b64_payload: str) -> str:
    """以 base64 內容的 sha256 前 12 碼為穩定 id（content-addressed）。"""
    return hashlib.sha256(b64_payload.encode("utf-8")).hexdigest()[:12]


def cache_figure(b64_payload: str, subtype: str = "png") -> str:
    """
    將單張 base64 圖片解碼快取到 FIGURE_CACHE_DIR，回傳 figure_id。

    idempotent：同內容只寫一次。b64_payload 不含 'data:image/...;base64,' 前綴。
    """
    import base64

    ext = _SUBTYPE_TO_EXT.get(subtype.lower(), "png")
    fig_id = _figure_id(b64_payload)
    out_path = FIGURE_CACHE_DIR / f"{fig_id}.{ext}"
    if not out_path.exists():
        FIGURE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        # 容忍 b64 內可能夾帶的空白／換行
        raw = base64.b64decode("".join(b64_payload.split()))
        out_path.write_bytes(raw)
    return fig_id


def strip_base64_for_llm(text: str) -> str:
    """
    把文字中所有 inline base64 圖片換成緊湊佔位符，並快取原圖供 bio_get_figure 索取。

    佔位符格式：[圖片:<alt> | id=<figure_id> | 用 bio_get_figure 索取]
    非字串或不含圖片時原樣回傳。
    """
    if not isinstance(text, str) or "base64," not in text:
        return text

    def _replace(m: re.Match) -> str:
        alt = m.group("alt").strip() or "figure"
        subtype = m.group("subtype")
        b64 = m.group("b64")
        try:
            fig_id = cache_figure(b64, subtype)
        except Exception:
            # 快取失敗時不要破壞整段文字；仍剝除 base64 避免爆 context
            return f"[圖片:{alt} | (快取失敗，base64 已移除)]"
        return f"[圖片:{alt} | id={fig_id} | 用 bio_get_figure 索取]"

    return _INLINE_IMG_RE.sub(_replace, text)


def load_figure(figure_id: str) -> tuple[bytes, str]:
    """
    依 figure_id 讀回快取圖片，回傳 (raw_bytes, mime_type)。

    figure_id 只允許 hex；查無檔案時 raise FileNotFoundError。
    """
    if not re.fullmatch(r"[0-9a-f]{6,64}", figure_id or ""):
        raise ValueError(f"figure_id 格式錯誤：{figure_id!r}（只允許 hex）")

    matches = sorted(FIGURE_CACHE_DIR.glob(f"{figure_id}.*"))
    if not matches:
        raise FileNotFoundError(
            f"figure_id={figure_id!r} 不在快取中。"
            "可能已過期清理，或報告尚未經 strip_base64_for_llm() 處理。"
        )
    path = matches[0]
    mime = _EXT_TO_MIME.get(path.suffix.lstrip(".").lower(), "application/octet-stream")
    return path.read_bytes(), mime


def load_figure_b64(figure_id: str) -> tuple[str, str]:
    """同 load_figure，但回傳 (base64_payload, mime_type)，供 MCP ImageContent 使用。"""
    import base64

    raw, mime = load_figure(figure_id)
    return base64.b64encode(raw).decode("ascii"), mime


def prune_stale_figures(ttl_days: int, *, dry_run: bool = False) -> tuple[int, int]:
    """
    刪除快取中 mtime 超過 ttl_days 的圖檔。

    圖檔是 content-addressed 副本，過期清掉後若報告重跑會自動重建，故依檔案 mtime
    age-based 刪除是安全的（不影響 analysis_history / result_path 的原始 png）。

    Returns:
        (deleted_count, freed_bytes) — dry_run 時為「預計」刪除的數量與位元組
    """
    import time

    if not FIGURE_CACHE_DIR.exists():
        return 0, 0

    cutoff = time.time() - ttl_days * 86400
    deleted = 0
    freed = 0
    for f in FIGURE_CACHE_DIR.iterdir():
        if not f.is_file():
            continue
        st = f.stat()
        if st.st_mtime < cutoff:
            freed += st.st_size
            deleted += 1
            if not dry_run:
                f.unlink()
    return deleted, freed
