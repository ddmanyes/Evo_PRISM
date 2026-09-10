"""Read-only EP analysis workflow guide for MCP and Web UI conversations."""

from __future__ import annotations

import base64
from pathlib import Path

from analysis.tool_registry import register_tool_on_import


REPO_ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = REPO_ROOT / "docs" / "analysis_workflows.html"
SVG_PATH = REPO_ROOT / "docs" / "images" / "analysis_workflows.svg"
PNG_PATH = REPO_ROOT / "docs" / "images" / "analysis_workflows.png"
GUIDE_PATH = REPO_ROOT / "docs" / "guides" / "ANALYSIS_WORKFLOW_GUIDE.md"

DOMAIN_TITLES = {
    "bulk": "Bulk RNA-seq",
    "spatial": "空間轉錄體",
    "single_cell": "單細胞／AnnData",
    "mcseg": "Visium HD／MCseg",
}
VALID_DOMAINS = frozenset({"overview", *DOMAIN_TITLES})


def _require_assets() -> None:
    missing = [
        str(path)
        for path in (HTML_PATH, SVG_PATH, PNG_PATH, GUIDE_PATH)
        if not path.is_file()
    ]
    if missing:
        raise FileNotFoundError("EP workflow guide assets missing: " + ", ".join(missing))


def _markdown_section(markdown: str, domain: str) -> str:
    if domain == "overview":
        stop = markdown.find("\n## Bulk RNA-seq")
        lead = markdown[:stop] if stop >= 0 else markdown
        index = "\n".join(
            f"- `{key}` — {title}" for key, title in DOMAIN_TITLES.items()
        )
        return f"{lead.strip()}\n\n## 可查詢領域\n\n{index}"

    heading = f"## {DOMAIN_TITLES[domain]}"
    start = markdown.find(heading)
    if start < 0:
        raise RuntimeError(f"workflow guide section not found: {heading}")
    next_heading = markdown.find("\n## ", start + len(heading))
    return markdown[start : next_heading if next_heading >= 0 else None].strip()


@register_tool_on_import(
    tool_name="bio_get_analysis_workflow",
    version="1.0.0",
    description=(
        "取得 EP 主要分析流程圖與輸出檔案指南。支援 overview、bulk、spatial、"
        "single_cell、mcseg；回傳指定領域說明與合成示意 PNG。唯讀且不存取研究資料。"
    ),
)
def get_analysis_workflow(args: dict) -> str:
    """Return one workflow section plus an inline PNG for MCP figure extraction."""
    domain = str(args.get("domain", "overview")).strip().lower()
    if domain not in VALID_DOMAINS:
        allowed = ", ".join(sorted(VALID_DOMAINS))
        raise ValueError(f"domain 必須是以下其中之一：{allowed}")

    _require_assets()
    guide = GUIDE_PATH.read_text(encoding="utf-8")
    section = _markdown_section(guide, domain)
    png_b64 = base64.b64encode(PNG_PATH.read_bytes()).decode("ascii")

    return (
        "# Evo-PRISM 分析流程指南\n\n"
        f"domain: {domain}\n"
        "SCHEMATIC / 示意：圖中的結果縮圖為合成圖形，不是真實研究結果。\n\n"
        "內容包含：分析順序、輸入條件、資料檔、圖檔與建議後續步驟。\n\n"
        "## 可用檔案\n\n"
        f"- HTML：`{HTML_PATH}`\n"
        f"- SVG：`{SVG_PATH}`\n"
        f"- PNG：`{PNG_PATH}`\n"
        f"- Markdown：`{GUIDE_PATH}`\n\n"
        f"{section}\n\n"
        "## 圖片預覽\n\n"
        f"![EP 主要分析流程與輸出檔案](data:image/png;base64,{png_b64})\n"
    )
