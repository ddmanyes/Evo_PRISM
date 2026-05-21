"""共用視覺化工具：matplotlib 圖 → Markdown inline base64。

集中先前 spatial_eda / bulk_eda / mcseg_quality 各自重複的 fig→b64 helper。
依 CLAUDE.md《圖片輸出規則》：分析函數回傳的 Markdown 須內嵌 base64 data URI，
不得回傳本地檔案路徑給呼叫端（MCP 邊界會再行剝離快取，見 figure_cache.py）。
"""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Union


def fig_to_b64_md(fig, alt: str = "figure") -> str:
    """matplotlib Figure → inline base64 Markdown。

    不關閉 fig（由呼叫端決定何時 ``plt.close``），與原 spatial_eda 行為一致。
    """
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode()
    return f"\n![{alt}](data:image/png;base64,{b64})\n"


def file_to_b64_md(path: Union[Path, str], alt: str = "figure") -> str:
    """已存的 PNG 路徑 → inline base64 Markdown。"""
    b64 = base64.b64encode(Path(path).read_bytes()).decode()
    return f"\n![{alt}](data:image/png;base64,{b64})\n"
