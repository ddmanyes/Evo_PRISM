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
METHOD_HTML_ROOT = REPO_ROOT / "docs" / "analysis_methods"
METHOD_IMAGE_ROOT = REPO_ROOT / "docs" / "images" / "analysis_methods"

DOMAIN_TITLES = {
    "bulk": "Bulk RNA-seq",
    "spatial": "空間轉錄體",
    "single_cell": "單細胞／AnnData",
    "mcseg": "Visium HD／MCseg",
}
VALID_DOMAINS = frozenset({"overview", *DOMAIN_TITLES})
METHOD_NAMES = (
    "bio_run_bulk_eda",
    "bio_run_deg",
    "bio_run_heatmaps",
    "bio_run_enrichment",
    "bio_run_spatial_eda",
    "bio_compute_spatial_nn_distance",
    "bio_run_sc_clustering",
    "bio_get_marker_genes",
    "bio_run_celltypist",
    "bio_run_geneset_score",
    "bio_run_mcseg_roi",
    "bio_run_mcseg_fullslide",
    "bio_run_mcseg_merge",
    "bio_run_mcseg_qc",
    "bio_compute_crc_metrics",
)
VALID_METHODS = frozenset(METHOD_NAMES)
VALID_LANGUAGES = frozenset({"zh-Hant", "en"})
ENGLISH_METHOD_TITLES = {
    "bio_run_bulk_eda": "Exploratory analysis and QC",
    "bio_run_deg": "Differential expression",
    "bio_run_heatmaps": "Expression heatmaps",
    "bio_run_enrichment": "Functional enrichment",
    "bio_run_spatial_eda": "Spatial exploration and QC",
    "bio_compute_spatial_nn_distance": "Cell-population nearest-neighbor distance",
    "bio_run_sc_clustering": "QC, clustering and UMAP",
    "bio_get_marker_genes": "Cluster marker genes",
    "bio_run_celltypist": "Automated CellTypist annotation",
    "bio_run_geneset_score": "Custom gene-module scoring",
    "bio_run_mcseg_roi": "Complete single-ROI pipeline",
    "bio_run_mcseg_fullslide": "Whole-slide cell segmentation",
    "bio_run_mcseg_merge": "Multi-ROI integration",
    "bio_run_mcseg_qc": "Segmentation quality control",
    "bio_compute_crc_metrics": "CRC RNA quality metrics",
}


def _require_assets() -> None:
    missing = [
        str(path) for path in (HTML_PATH, SVG_PATH, PNG_PATH, GUIDE_PATH) if not path.is_file()
    ]
    if missing:
        raise FileNotFoundError("EP workflow guide assets missing: " + ", ".join(missing))


def _method_paths(method: str, language: str) -> tuple[Path, Path, Path]:
    suffix = ".en" if language == "en" else ""
    return (
        METHOD_HTML_ROOT / method / f"index{suffix}.html",
        METHOD_IMAGE_ROOT / f"{method}{suffix}.svg",
        METHOD_IMAGE_ROOT / f"{method}{suffix}.png",
    )


def _require_method_assets(method: str, language: str) -> tuple[Path, Path, Path]:
    paths = _method_paths(method, language)
    missing = [str(path) for path in (*paths, GUIDE_PATH) if not path.is_file()]
    if missing:
        raise FileNotFoundError("EP method workflow assets missing: " + ", ".join(missing))
    return paths


def _markdown_section(markdown: str, domain: str) -> str:
    if domain == "overview":
        stop = markdown.find("\n## Bulk RNA-seq")
        lead = markdown[:stop] if stop >= 0 else markdown
        index = "\n".join(f"- `{key}` — {title}" for key, title in DOMAIN_TITLES.items())
        return f"{lead.strip()}\n\n## 可查詢領域\n\n{index}"

    heading = f"## {DOMAIN_TITLES[domain]}"
    start = markdown.find(heading)
    if start < 0:
        raise RuntimeError(f"workflow guide section not found: {heading}")
    next_heading = markdown.find("\n## ", start + len(heading))
    return markdown[start : next_heading if next_heading >= 0 else None].strip()


def _markdown_method_section(markdown: str, method: str) -> str:
    marker = f"- MCP 方法：`{method}`"
    marker_at = markdown.find(marker)
    if marker_at < 0:
        raise RuntimeError(f"workflow guide method section not found: {method}")
    start = markdown.rfind("\n### ", 0, marker_at)
    start = start + 1 if start >= 0 else 0
    next_heading = markdown.find("\n### ", marker_at)
    next_domain = markdown.find("\n## ", marker_at)
    stops = [position for position in (next_heading, next_domain) if position >= 0]
    stop = min(stops) if stops else len(markdown)
    return markdown[start:stop].strip()


@register_tool_on_import(
    tool_name="bio_get_analysis_workflow",
    version="1.2.0",
    description=(
        "取得 EP 主要分析流程圖與輸出檔案指南。支援 overview、bulk、spatial、"
        "single_cell、mcseg，或用 method 取得 15 個核心分析方法之一的獨立流程圖；"
        "個別方法支援繁體中文與英文，回傳指定領域／方法說明、流程 PNG 與真實結果圖檔連結。"
        "唯讀且不複製研究圖檔。"
    ),
)
def get_analysis_workflow(args: dict) -> str:
    """Return one workflow section plus an inline PNG for MCP figure extraction."""
    language = str(args.get("language", "zh-Hant")).strip()
    if language not in VALID_LANGUAGES:
        allowed = ", ".join(sorted(VALID_LANGUAGES))
        raise ValueError(f"language must be one of: {allowed}")

    method = str(args.get("method", "")).strip()
    if method:
        if method not in VALID_METHODS:
            allowed = ", ".join(METHOD_NAMES)
            raise ValueError(f"method 必須是以下其中之一：{allowed}")

        html_path, svg_path, png_path = _require_method_assets(method, language)
        png_b64 = base64.b64encode(png_path.read_bytes()).decode("ascii")
        if language == "en":
            section = (
                f"## {ENGLISH_METHOD_TITLES[method]}\n\n"
                "The diagram covers input and preflight checks, the analysis "
                "pipeline, data and figure outputs, quality guards, persistence, "
                "and MCP retrieval. Technical field names and file patterns are "
                "kept exactly as implemented in EP."
            )
            return (
                "# Evo-PRISM Individual Analysis Workflow\n\n"
                f"method: {method}\n"
                "language: en\n"
                "SCHEMATIC: This guide reflects current EP tool definitions. "
                "Real research figures are linked from their original paths and "
                "are not copied into the guide.\n\n"
                "Input and preflight checks, analysis stages, failure or optional "
                "branches, output files, persistence and retrieval are included.\n\n"
                "## Available files\n\n"
                f"- HTML: `{html_path}`\n"
                f"- SVG: `{svg_path}`\n"
                f"- PNG: `{png_path}`\n\n"
                f"{section}\n\n"
                "## Image preview\n\n"
                f"![{method} workflow](data:image/png;base64,{png_b64})\n"
            )

        guide = GUIDE_PATH.read_text(encoding="utf-8")
        section = _markdown_method_section(guide, method)
        return (
            "# Evo-PRISM 個別分析方法流程\n\n"
            f"method: {method}\n"
            "language: zh-Hant\n"
            "SCHEMATIC / 示意：依 EP 現行工具定義整理；真實研究圖只以原始路徑連結，不複製進指南。\n\n"
            "內容包含：前置條件、分析主線、失敗／選用分支、資料檔、圖檔、保存與提取方式。\n\n"
            "## 可用檔案\n\n"
            f"- HTML：`{html_path}`\n"
            f"- SVG：`{svg_path}`\n"
            f"- PNG：`{png_path}`\n\n"
            f"{section}\n\n"
            "## 圖片預覽\n\n"
            f"![{method} 獨立流程圖](data:image/png;base64,{png_b64})\n"
        )

    if language == "en":
        raise ValueError("language=en requires an individual method")

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
        "SCHEMATIC / 示意：流程關係為導覽示意；結果範例使用 EP 主機上的真實圖檔連結，研究影像本身不會複製進指南。\n\n"
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
