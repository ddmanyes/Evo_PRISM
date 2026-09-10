#!/usr/bin/env python3
"""Generate the versioned EP analysis workflow guide assets.

The SVG/HTML/Markdown and raster preview share one semantic data model so their
tool names and output-file descriptions do not drift. Real result examples are
linked in place and are never copied into the versioned guide assets.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "docs" / "analysis_workflows.html"
SVG_PATH = ROOT / "docs" / "images" / "analysis_workflows.svg"
PNG_PATH = ROOT / "docs" / "images" / "analysis_workflows.png"
GUIDE_PATH = ROOT / "docs" / "guides" / "ANALYSIS_WORKFLOW_GUIDE.md"

WIDTH = 2400
PAPER = "#ffffff"
INK = "#0f172a"
MUTED = "#475569"
WEAK = "#64748b"
BORDER = "#dbe3ee"
PANEL = "#f8fafc"
ORANGE = "#eb6c36"
BLUE = "#2e5aa8"
GREEN = "#15803d"
PURPLE = "#7c3aed"
CYAN = "#0e7490"


@dataclass(frozen=True)
class Step:
    title: str
    tool: str
    inputs: tuple[str, ...]
    data_outputs: tuple[str, ...]
    figure_outputs: tuple[str, ...]
    next_step: str


@dataclass(frozen=True)
class Domain:
    key: str
    title: str
    subtitle: str
    color: str
    steps: tuple[Step, ...]


@dataclass(frozen=True)
class ResultSample:
    label: str
    caption: str
    path: Path

    @property
    def href(self) -> str:
        return self.path.as_posix()


@dataclass(frozen=True)
class StepLayout:
    method_lines: tuple[str, ...]
    sections: tuple[tuple[str, tuple[str, ...]], ...]
    content_bottom: int


@dataclass(frozen=True)
class LaneLayout:
    domain: Domain
    y: int
    height: int
    card_y: int
    card_height: int
    card_width: int
    gap: int
    start_x: int
    steps: tuple[StepLayout, ...]


@dataclass(frozen=True)
class DiagramLayout:
    lanes: tuple[LaneLayout, ...]
    mcp_y: int
    height: int


DOMAINS = (
    Domain(
        "bulk",
        "Bulk RNA-seq",
        "樣本層級表現量 → QC／PCA → DEG → 熱圖與路徑富集",
        BLUE,
        (
            Step(
                "探索與品質檢查",
                "bio_run_bulk_eda",
                ("gene_counts.tsv", "run_info.json（選用）"),
                ("bulk_eda_<sample>_<ts>.md",),
                ("qc_*.png", "corr_*.png", "pca_*.png", "dist_*.png"),
                "差異表現分析",
            ),
            Step(
                "差異表現基因",
                "bio_run_deg",
                ("counts.csv／TSV", "coldata.tsv", "comparisons"),
                ("DEG_<A>_vs_<B>_<ts>.csv", "bulk_deg_<sample>_<ts>.md"),
                ("Volcano_*.png", "MA_*.png", "MeanVariance_*.png"),
                "熱圖／富集分析",
            ),
            Step(
                "基因表現熱圖",
                "bio_run_heatmaps",
                ("counts", "DEG_*.csv"),
                ("bulk_heatmap_<sample>_<ts>.md",),
                ("Heatmap_Significant_Genes_*.png", "Heatmap_TopN_Variable_Genes_*.png"),
                "群聚模式判讀",
            ),
            Step(
                "功能與路徑富集",
                "bio_run_enrichment",
                ("DEG_*.csv", "GO／KEGG／Reactome libraries"),
                ("<direction>_<library>_<ts>.csv", "bulk_enrichment_<sample>_<ts>.md"),
                ("*_bar_<ts>.png", "*_<ts>.png（dot plot）"),
                "生物機制解讀",
            ),
        ),
    ),
    Domain(
        "spatial",
        "空間轉錄體",
        "L2 Parquet → 空間 QC／基因分布 → 指定細胞群距離分析",
        CYAN,
        (
            Step(
                "空間探索與 QC",
                "bio_run_spatial_eda",
                ("expression/*.parquet", "obs_metadata.parquet"),
                ("qc_stats.parquet", "top_N_genes.csv", "eda_report_<ts>.md"),
                ("qc_distributions.png", "spatial_<gene>.png", "coexpr_<A>_<B>.png"),
                "候選基因／區域判讀",
            ),
            Step(
                "細胞群最近鄰距離",
                "bio_compute_spatial_nn_distance",
                ("AnnData .h5ad", "obsm['spatial']", "source／target filters"),
                ("spatial_nn_distance.csv",),
                ("—（目前輸出資料表）",),
                "統計比較／外部繪圖",
            ),
        ),
    ),
    Domain(
        "single_cell",
        "單細胞／AnnData",
        "原始 cell×gene → QC／Leiden／UMAP → marker／細胞型別／模組分數",
        PURPLE,
        (
            Step(
                "QC、分群與 UMAP",
                "bio_run_sc_clustering",
                ("cellpose_cells*.h5ad 或任意 raw-count h5ad",),
                ("clustered.h5ad", "top_markers_per_cluster.csv", "clustering_report_*.md"),
                ("umap_<color>_<ts>.png",),
                "Marker 與細胞型別",
            ),
            Step(
                "Cluster marker genes",
                "bio_get_marker_genes",
                ("umap_computed.h5ad", "groupby=leiden／cell_type"),
                ("marker_genes_<sample>_<roi>_<ts>.csv", "marker_genes_*.md"),
                ("—（目前輸出資料表）",),
                "人工標注／驗證",
            ),
            Step(
                "CellTypist 自動標注",
                "bio_run_celltypist",
                ("umap_computed.h5ad", "CellTypist model"),
                ("umap_computed.h5ad（新增 celltypist_cell_type）", "celltypist_*.md"),
                ("umap_celltypist_*.png",),
                "細胞型別比較",
            ),
            Step(
                "自訂基因模組評分",
                "bio_run_geneset_score",
                ("AnnData .h5ad", "{module: genes}"),
                ("geneset_scores.csv",),
                ("—（目前輸出資料表）",),
                "分組統計／外部繪圖",
            ),
        ),
    ),
    Domain(
        "mcseg",
        "Visium HD／MCseg",
        "H&E＋2 µm bins → 細胞分割／RNA 計數 → 整合、QC 與 CRC metrics",
        ORANGE,
        (
            Step(
                "單一 ROI 完整管線",
                "bio_run_mcseg_roi",
                ("H&E TIFF", "2 µm binned_outputs", "ROI x／y／w／h"),
                ("adata_002um.h5ad", "segmentation_masks.npy", "cellpose_cells.h5ad", "Xenium bundle"),
                ("umap_spatial_combined.png", "mask_he_overlay.png", "mask_he_boundary.png", "skin_markers_dotplot.png"),
                "標注／QC／CRC metrics",
            ),
            Step(
                "全片細胞分割",
                "bio_run_mcseg_fullslide",
                ("H&E TIFF／BTF", "2 µm binned_outputs"),
                ("segmentation_masks_fullslide.npy", "segmentation_masks_fullslide_vfr.npy", "cellpose_cells_fullslide.h5ad"),
                ("—（可另登記 overlay）",),
                "單細胞分群",
            ),
            Step(
                "多 ROI 整合",
                "bio_run_mcseg_merge",
                ("多個 ROI/cellpose_cells.h5ad",),
                ("merged.h5ad", "merge_report_<ts>.md"),
                ("umap_by_roi_<ts>.png", "umap_by_leiden_<ts>.png"),
                "跨 ROI 比較",
            ),
            Step(
                "分割品質 QC",
                "bio_run_mcseg_qc",
                ("<roi>_nuc.npy", "<roi>_mcseg.npy"),
                ("mcseg_qc_<sample>_<ts>.md",),
                ("cmp_<sample>_<roi>_<ts>.png", "sizedist_<sample>_<ts>.png"),
                "品質判定",
            ),
            Step(
                "CRC RNA 品質指標",
                "bio_compute_crc_metrics",
                ("segmentation_masks.npy", "cellpose_cells.h5ad", "tissue_positions.parquet"),
                ("crc_metrics.json", "crc_metrics_report.md"),
                ("—（FTC／UMI Density／NED／C1 數值）",),
                "品質報告",
            ),
        ),
    ),
)

EP_RESULTS_ROOT = Path("/Volumes/KINGSTON/Evo_PRISM/results")
SAMPLES = (
    ResultSample(
        "PCA",
        "Bulk EDA 樣本分群",
        EP_RESULTS_ROOT / "Kallisto_v1/bulk_eda/pca_Kallisto_v1_20260904_073140.png",
    ),
    ResultSample(
        "VOLCANO",
        "Bulk DEG 火山圖",
        EP_RESULTS_ROOT
        / "Kallisto_v1/bulk_deg/Volcano_pw120hr_vs_ctrl_20260603_004126.png",
    ),
    ResultSample(
        "HEATMAP",
        "Bulk 基因群聚熱圖",
        EP_RESULTS_ROOT
        / "Kallisto_v1/bulk_heatmap/Heatmap_Top50_Variable_Genes_20260603_004417.png",
    ),
    ResultSample(
        "UMAP",
        "MCseg 全片單細胞分群",
        EP_RESULTS_ROOT
        / "mcseg/dpcp01_vh_v114_02_hd_r004/fullslide/clustering/umap_leiden_20260721_102206.png",
    ),
    ResultSample(
        "SPATIAL QC",
        "真實空間訊號分布",
        EP_RESULTS_ROOT
        / "external_import/kangyu_visiumhd_sds_lipolysis/figures/qc_spatial.png",
    ),
    ResultSample(
        "SEGMENTATION",
        "Human CRC ROI 細胞邊界",
        EP_RESULTS_ROOT / "mcseg/Human_CRC/roi/crc_roi1/mask_he_overlay.png",
    ),
)


def esc(value: str) -> str:
    return html.escape(str(value), quote=True)


def svg_text(x: int, y: int, lines: list[str], *, size: int = 18, color: str = INK,
             weight: int = 400, family: str = "sans", line_height: int | None = None,
             anchor: str = "start") -> str:
    line_height = line_height or int(size * 1.35)
    spans = []
    for index, line in enumerate(lines):
        dy = 0 if index == 0 else line_height
        spans.append(f'<tspan x="{x}" dy="{dy}">{esc(line)}</tspan>')
    return (
        f'<text x="{x}" y="{y}" font-family="var(--{family})" font-size="{size}" '
        f'font-weight="{weight}" fill="{color}" text-anchor="{anchor}">'
        + "".join(spans)
        + "</text>"
    )


def wrap_chars(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    lines: list[str] = []
    current = ""
    for char in text:
        current += char
        if len(current) >= limit:
            lines.append(current)
            current = ""
    if current:
        lines.append(current)
    return lines


def _card_dimensions(step_count: int) -> tuple[int, int, int]:
    if step_count == 2:
        return 920, 210, 175
    if step_count == 4:
        return 440, 120, 110
    return 352, 112, 98


def _step_layout(step: Step, step_count: int) -> StepLayout:
    method_lines = tuple(wrap_chars(step.tool, 31 if step_count == 5 else 38))
    cursor = 104 + (len(method_lines) - 1) * 19
    sections: list[tuple[str, tuple[str, ...]]] = []
    content_bottom = cursor
    char_limit = 31 if step_count == 5 else (39 if step_count == 4 else 72)
    max_lines = 3 if step_count == 5 else 4

    for label, values in (
        ("INPUT", step.inputs),
        ("DATA", step.data_outputs),
        ("FIG", step.figure_outputs),
    ):
        cursor += 24
        shown_lines: list[str] = []
        for value in values:
            shown_lines.extend(wrap_chars("• " + value, char_limit))
        if len(shown_lines) > max_lines:
            shown_lines = shown_lines[: max_lines - 1] + ["• 詳見 HTML／Markdown"]
        if not shown_lines:
            shown_lines = ["• —"]
        content_bottom = cursor + (len(shown_lines) - 1) * 20
        sections.append((label, tuple(shown_lines)))
        cursor += len(shown_lines) * 20 + 16

    return StepLayout(method_lines, tuple(sections), content_bottom)


def build_layout() -> DiagramLayout:
    lanes: list[LaneLayout] = []
    y = 865
    for domain in DOMAINS:
        step_count = len(domain.steps)
        card_width, gap, start_x = _card_dimensions(step_count)
        step_layouts = tuple(_step_layout(step, step_count) for step in domain.steps)
        card_height = max(340, max(step.content_bottom + 62 for step in step_layouts))
        lane_height = card_height + 142
        lanes.append(
            LaneLayout(
                domain=domain,
                y=y,
                height=lane_height,
                card_y=y + 112,
                card_height=card_height,
                card_width=card_width,
                gap=gap,
                start_x=start_x,
                steps=step_layouts,
            )
        )
        y += lane_height + 35

    mcp_y = y + 5
    return DiagramLayout(tuple(lanes), mcp_y=mcp_y, height=mcp_y + 425)


def build_svg() -> str:
    layout = build_layout()
    out: list[str] = [
        f'<svg viewBox="0 0 {WIDTH} {layout.height}" xmlns="http://www.w3.org/2000/svg" role="img" aria-labelledby="ep-workflow-title ep-workflow-desc">',
        '<title id="ep-workflow-title">Evo-PRISM 主要分析流程與輸出檔案</title>',
        '<desc id="ep-workflow-desc">從資料登記與前置檢查進入 Bulk RNA-seq、空間轉錄體、單細胞與 Visium HD MCseg 四條分析泳道，列出十五個主要工具、資料檔與圖檔輸出，並提供六個 EP 主機真實結果圖檔連結。</desc>',
        "<defs>",
        '<style>:root{--sans:Geist,"PingFang TC","Noto Sans TC",system-ui,sans-serif;--serif:"Instrument Serif","Songti TC",serif;--mono:"Geist Mono",ui-monospace,monospace}</style>',
        f'<marker id="ep-workflow-arrow" markerWidth="12" markerHeight="10" refX="10" refY="5" orient="auto"><path d="M0,0 L12,5 L0,10 Z" fill="{MUTED}"/></marker>',
        '<filter id="ep-workflow-shadow" x="-10%" y="-10%" width="120%" height="130%"><feDropShadow dx="0" dy="6" stdDeviation="8" flood-color="#0f172a" flood-opacity=".08"/></filter>',
        "</defs>",
        f'<rect width="{WIDTH}" height="{layout.height}" fill="{PAPER}"/>',
    ]

    out.append(svg_text(90, 92, ["EVO-PRISM · ANALYSIS MAP"], size=18, color=BLUE, weight=700, family="mono"))
    out.append(svg_text(90, 160, ["主要分析流程與輸出檔案"], size=54, weight=700, family="serif"))
    out.append(svg_text(90, 205, ["15 個核心分析方法 · 4 條資料泳道 · 6 個真實圖檔連結"], size=22, color=MUTED))
    out.append('<rect x="1900" y="78" width="410" height="116" rx="24" fill="#fff7ed" stroke="#fed7aa"/>')
    out.append(svg_text(1932, 115, ["FLOW / 示意流程"], size=17, color=ORANGE, weight=700, family="mono"))
    out.append(svg_text(1932, 151, ["結果範例為真實圖檔", "點擊卡片開啟原始檔"], size=17, color=MUTED, line_height=25))

    # Common entry flow: connectors first, then nodes.
    out.append(f'<rect x="70" y="250" width="2260" height="235" rx="28" fill="{PANEL}" stroke="{BORDER}"/>')
    out.append(svg_text(100, 292, ["共通入口"], size=17, color=WEAK, weight=700, family="mono"))
    flow_nodes = [
        (110, "登記樣本", "bio_register_sample"),
        (560, "檢查資料", "lookup／L2 sufficiency"),
        (1010, "選擇領域", "bulk · spatial · sc · mcseg"),
        (1460, "執行分析", "直接／analysis queue"),
        (1910, "保存與取回", "history · artifacts · figures"),
    ]
    for index in range(len(flow_nodes) - 1):
        x1 = flow_nodes[index][0] + 330
        x2 = flow_nodes[index + 1][0] - 24
        out.append(f'<line x1="{x1}" y1="382" x2="{x2}" y2="382" stroke="{MUTED}" stroke-width="3" marker-end="url(#ep-workflow-arrow)"/>')
    for x, title, sub in flow_nodes:
        out.append(f'<rect x="{x}" y="322" width="330" height="122" rx="22" fill="#ffffff" stroke="{BORDER}" filter="url(#ep-workflow-shadow)"/>')
        out.append(svg_text(x + 24, 362, [title], size=22, weight=700))
        out.append(svg_text(x + 24, 402, [sub], size=15, color=MUTED, family="mono"))

    # Link to real result files without copying research images into Git.
    out.append(svg_text(90, 548, ["真實結果圖檔（連結）"], size=30, weight=700, family="serif"))
    out.append(svg_text(90, 582, ["點擊卡片開啟 EP 主機上的原始圖檔；研究影像不會被複製進本文件。"], size=17, color=MUTED))
    sx, sy, sw, sh, gap = 90, 620, 342, 198, 48
    for index, sample in enumerate(SAMPLES):
        x = sx + index * (sw + gap)
        filename_lines = wrap_chars(sample.path.name, 38)[:2]
        out.append(
            f'<a href="{esc(sample.href)}" target="_blank" rel="noopener" '
            f'aria-label="開啟 {esc(sample.label)} 真實圖檔">'
        )
        out.append(f'<rect x="{x}" y="{sy}" width="{sw}" height="{sh}" rx="22" fill="{PANEL}" stroke="{BORDER}"/>')
        out.append(f'<rect x="{x+20}" y="{sy+18}" width="112" height="28" rx="14" fill="#dcfce7"/>')
        out.append(svg_text(x + 34, sy + 38, ["REAL RESULT"], size=12, color=GREEN, weight=700, family="mono"))
        out.append(svg_text(x + 20, sy + 76, [sample.label], size=17, color=INK, weight=700, family="mono"))
        out.append(svg_text(x + 20, sy + 104, [sample.caption], size=14, color=MUTED))
        out.append(svg_text(x + 20, sy + 128, filename_lines, size=11, color=WEAK, family="mono", line_height=15))
        out.append(f'<rect x="{x+20}" y="{sy+158}" width="{sw-40}" height="26" rx="13" fill="#dbeafe"/>')
        out.append(svg_text(x + sw // 2, sy + 177, ["開啟真實圖檔  ↗"], size=12, color=BLUE, weight=700, anchor="middle", family="mono"))
        out.append("</a>")

    for lane in layout.lanes:
        domain = lane.domain
        y, h = lane.y, lane.height
        out.append(f'<rect x="70" y="{y}" width="2260" height="{h}" rx="30" fill="#ffffff" stroke="{BORDER}"/>')
        out.append(f'<rect x="70" y="{y}" width="18" height="{h}" rx="9" fill="{domain.color}"/>')
        out.append(svg_text(110, y + 49, [domain.title], size=30, color=domain.color, weight=700, family="serif"))
        out.append(svg_text(110, y + 82, [domain.subtitle], size=16, color=MUTED))

        count = len(domain.steps)
        card_w, gap, start_x = lane.card_width, lane.gap, lane.start_x
        card_y, card_h = lane.card_y, lane.card_height
        center_y = card_y + card_h // 2
        for index in range(count - 1):
            x1 = start_x + index * (card_w + gap) + card_w + 8
            x2 = start_x + (index + 1) * (card_w + gap) - 20
            out.append(f'<line x1="{x1}" y1="{center_y}" x2="{x2}" y2="{center_y}" stroke="{domain.color}" stroke-width="3" marker-end="url(#ep-workflow-arrow)"/>')
        for index, (step, step_layout) in enumerate(zip(domain.steps, lane.steps, strict=True)):
            x = start_x + index * (card_w + gap)
            card_bottom = card_y + card_h
            content_bottom = card_y + step_layout.content_bottom
            out.append(
                f'<g id="ep-workflow-step-{esc(step.tool)}" '
                f'data-card-bottom="{card_bottom}" data-content-bottom="{content_bottom}">'
            )
            out.append(f'<rect x="{x}" y="{card_y}" width="{card_w}" height="{card_h}" rx="24" fill="{PANEL}" stroke="{domain.color}" stroke-width="2"/>')
            out.append(svg_text(x + 22, card_y + 37, [step.title], size=19 if count == 5 else 21, weight=700))
            out.append(svg_text(x + 22, card_y + 68, list(step_layout.method_lines), size=14, color=domain.color, weight=700, family="mono", line_height=19))
            content_y = card_y + 104 + (len(step_layout.method_lines) - 1) * 19
            for label, shown_lines in step_layout.sections:
                out.append(svg_text(x + 22, content_y, [label], size=12, color=WEAK, weight=700, family="mono"))
                content_y += 24
                out.append(svg_text(x + 22, content_y, list(shown_lines), size=14 if count == 5 else 15, color=MUTED, line_height=20))
                content_y += len(shown_lines) * 20 + 16
            out.append(svg_text(x + 22, card_y + card_h - 18, ["NEXT  " + step.next_step], size=12, color=domain.color, weight=700, family="mono"))
            out.append("</g>")

    mcp_y = layout.mcp_y
    out.append(f'<rect x="70" y="{mcp_y}" width="2260" height="340" rx="30" fill="{INK}"/>')
    out.append(svg_text(110, mcp_y + 55, ["如何透過 MCP 對話取圖"], size=31, color="#ffffff", weight=700, family="serif"))
    mcp_steps = [
        ("1", "使用者提問", "「顯示 EP 分析流程」"),
        ("2", "取回指南", "bio_get_analysis_workflow"),
        ("3", "取得圖片 handle", "回傳 figure_id"),
        ("4", "顯示圖片", "bio_get_figure"),
    ]
    for index in range(len(mcp_steps) - 1):
        x1 = 140 + index * 540 + 425
        x2 = 140 + (index + 1) * 540 - 32
        out.append(f'<line x1="{x1}" y1="{mcp_y+190}" x2="{x2}" y2="{mcp_y+190}" stroke="#94a3b8" stroke-width="3" marker-end="url(#ep-workflow-arrow)"/>')
    for index, (number, title, detail) in enumerate(mcp_steps):
        x = 140 + index * 540
        out.append(f'<circle cx="{x+28}" cy="{mcp_y+139}" r="28" fill="{ORANGE}"/>')
        out.append(svg_text(x + 28, mcp_y + 147, [number], size=19, color="#ffffff", weight=700, anchor="middle", family="mono"))
        out.append(svg_text(x + 72, mcp_y + 140, [title], size=20, color="#ffffff", weight=700))
        out.append(f'<rect x="{x}" y="{mcp_y+170}" width="425" height="84" rx="18" fill="#1e293b" stroke="#334155"/>')
        out.append(svg_text(x + 22, mcp_y + 221, [detail], size=16, color="#e2e8f0", family="mono"))
    out.append(svg_text(110, mcp_y + 307, ["圖例：INPUT＝輸入 · DATA＝資料檔 · FIG＝圖檔 · —＝目前不直接產圖"], size=16, color="#cbd5e1"))
    out.append(svg_text(2290, mcp_y + 367, ["Generated from EP source definitions · 2026-09-10"], size=14, color="#94a3b8", anchor="end", family="mono"))
    out.append("</svg>")
    return "".join(out)


def table_rows(domain: Domain) -> str:
    rows = []
    for step in domain.steps:
        rows.append(
            "<tr>"
            f"<th><span>{esc(step.title)}</span><code>{esc(step.tool)}</code></th>"
            f"<td>{'<br>'.join(esc(v) for v in step.inputs)}</td>"
            f"<td>{'<br>'.join(esc(v) for v in step.data_outputs)}</td>"
            f"<td>{'<br>'.join(esc(v) for v in step.figure_outputs)}</td>"
            f"<td>{esc(step.next_step)}</td>"
            "</tr>"
        )
    return "".join(rows)


def build_html(svg: str) -> str:
    sections = []
    for domain in DOMAINS:
        sections.append(
            f'<section id="{domain.key}"><div class="section-head">'
            f'<p class="eyebrow" style="color:{domain.color}">{esc(domain.key)}</p>'
            f'<h2>{esc(domain.title)}</h2><p>{esc(domain.subtitle)}</p></div>'
            '<div class="table-wrap"><table><thead><tr><th>分析方法</th><th>輸入</th>'
            '<th>資料檔輸出</th><th>圖檔輸出</th><th>後續</th></tr></thead><tbody>'
            f'{table_rows(domain)}</tbody></table></div></section>'
        )
    sample_links = "".join(
        f'<li><a href="{esc(sample.href)}" target="_blank" rel="noopener">'
        f'<strong>{esc(sample.label)}</strong><span>{esc(sample.caption)}</span>'
        f'<code>{esc(sample.path.name)}</code></a></li>'
        for sample in SAMPLES
    )
    return f'''<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Evo-PRISM 主要分析流程與輸出檔案</title>
  <link href="https://fonts.googleapis.com/css2?family=Instrument+Serif&family=Geist:wght@400;500;600;700&family=Geist+Mono:wght@500;700&display=swap" rel="stylesheet">
  <style>
    *,*::before,*::after{{box-sizing:border-box}}
    :root{{--ink:#0f172a;--muted:#475569;--line:#dbe3ee;--paper:#fff;--panel:#f8fafc;--blue:#2e5aa8;--orange:#eb6c36;--sans:Geist,"PingFang TC","Noto Sans TC",system-ui,sans-serif;--serif:"Instrument Serif","Songti TC",serif;--mono:"Geist Mono",ui-monospace,monospace}}
    body{{margin:0;background:#f1f5f9;color:var(--ink);font-family:var(--sans);line-height:1.55}}
    main{{max-width:1540px;margin:0 auto;padding:32px 24px 80px}}
    .hero{{background:#fff;border:1px solid var(--line);border-radius:26px;padding:24px;box-shadow:0 18px 50px rgba(15,23,42,.08);overflow:auto;-webkit-overflow-scrolling:touch}}
    .hero svg{{display:block;width:100%;min-width:1120px;height:auto}}
    .notice{{margin:24px 0 46px;padding:18px 22px;border-left:6px solid var(--orange);border-radius:12px;background:#fff7ed;color:#7c2d12}}
    .real-links ul{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;list-style:none;margin:20px 0 0;padding:0}}
    .real-links a{{display:block;height:100%;padding:16px;border:1px solid var(--line);border-radius:14px;background:var(--panel);color:var(--ink);text-decoration:none}}
    .real-links a:hover,.real-links a:focus-visible{{border-color:var(--blue);outline:3px solid #dbeafe}}
    .real-links span,.real-links code{{display:block;margin-top:6px}}
    .real-links span{{color:var(--muted)}}
    section{{margin-top:32px;background:#fff;border:1px solid var(--line);border-radius:22px;padding:28px}}
    .section-head{{display:grid;grid-template-columns:150px 280px 1fr;align-items:baseline;gap:18px;margin-bottom:22px}}
    .section-head h2{{font-family:var(--serif);font-size:32px;margin:0}}
    .section-head p{{margin:0;color:var(--muted)}}
    .eyebrow{{font-family:var(--mono);font-size:12px;font-weight:700;letter-spacing:.12em;text-transform:uppercase}}
    .table-wrap{{overflow:auto}}
    table{{width:100%;border-collapse:separate;border-spacing:0;font-size:14px}}
    th,td{{padding:15px 16px;text-align:left;vertical-align:top;border-right:1px solid var(--line);border-bottom:1px solid var(--line)}}
    thead th{{background:#f8fafc;font-family:var(--mono);font-size:12px;color:#475569}}
    tr>*:first-child{{border-left:1px solid var(--line)}}
    thead tr:first-child>*{{border-top:1px solid var(--line)}}
    tbody th{{min-width:245px}}
    tbody th span{{display:block;font-size:16px;margin-bottom:7px}}
    code{{display:block;color:var(--blue);font-family:var(--mono);font-size:12px;white-space:normal}}
    footer{{padding:34px 8px 0;color:var(--muted);font-size:13px}}
    @media(max-width:800px){{main{{padding:12px 10px 50px}}.hero,section{{border-radius:14px;padding:14px}}.hero::before{{content:"左右滑動查看完整流程 →";display:block;margin:0 0 10px;color:var(--muted);font-size:13px}}.section-head{{display:block}}.section-head h2{{margin:4px 0}}.real-links ul{{grid-template-columns:1fr}}}}
  </style>
</head>
<body>
<main>
  <div class="hero">{svg}</div>
  <div class="notice"><strong>SCHEMATIC / 示意：</strong>流程關係是導覽示意；結果範例改為 EP 主機上的真實圖檔連結，沒有把研究影像複製進 Git。連結只適用於可存取該主機與檔案的使用者。</div>
  <section class="real-links" aria-labelledby="real-links-title"><div class="section-head"><p class="eyebrow" style="color:var(--blue)">REAL FILES</p><h2 id="real-links-title">真實圖檔連結</h2><p>開啟 PCA、Volcano、Heatmap、UMAP、Spatial QC 與 Segmentation overlay 原始結果。</p></div><ul>{sample_links}</ul></section>
  {''.join(sections)}
  <footer>來源：Evo-PRISM 現行分析模組與 MCP tool catalog。帶時間戳記的檔名以 <code>&lt;ts&gt;</code> 表示；實際路徑依部署設定與 sample_id 而定。</footer>
</main>
</body>
</html>
'''


def build_markdown() -> str:
    lines = [
        "# Evo-PRISM 主要分析流程與輸出檔案",
        "",
        "> **SCHEMATIC / 示意**：流程關係是導覽示意；下列結果範例是 EP 主機上的真實圖檔連結，研究影像本身未複製進 Git。",
        "",
        "![Evo-PRISM 主要分析流程](../images/analysis_workflows.png)",
        "",
        "## 真實圖檔連結",
        "",
        "這些本機連結只適用於可存取 EP 主機與原始結果目錄的使用者。",
        "",
        *(
            f"- [{sample.label} — {sample.caption}]({sample.href})"
            for sample in SAMPLES
        ),
        "",
        "## 共通流程",
        "",
        "1. `bio_register_sample` 登記樣本與 L3 路徑。",
        "2. 用 `bio_lookup_sample` 與 `bio_check_l2_sufficiency` 檢查資料狀態。",
        "3. 選擇 Bulk、Spatial、Single-cell 或 MCseg 領域工具。",
        "4. 重量分析可由 `bio_submit_analysis` 進入持久化佇列。",
        "5. 結果寫入 analysis history；圖片以 `figure_id` 由 `bio_get_figure` 取回。",
        "",
    ]
    for domain in DOMAINS:
        lines += [f"## {domain.title}", "", domain.subtitle, ""]
        for index, step in enumerate(domain.steps, 1):
            lines += [
                f"### {index}. {step.title}",
                "",
                f"- MCP 方法：`{step.tool}`",
                f"- 輸入：{'；'.join(f'`{v}`' for v in step.inputs)}",
                f"- 資料檔：{'；'.join(f'`{v}`' for v in step.data_outputs)}",
                f"- 圖檔：{'；'.join(f'`{v}`' for v in step.figure_outputs)}",
                f"- 後續：{step.next_step}",
                "",
            ]
    lines += [
        "## MCP 對話取圖",
        "",
        "呼叫 `bio_get_analysis_workflow(domain=...)`，其中 domain 可為 `overview`、`bulk`、`spatial`、`single_cell` 或 `mcseg`。工具回傳的 inline PNG 會由 MCP 邊界轉為 `figure_id`；再呼叫 `bio_get_figure(figure_id=...)` 即可在支援多模態的用戶端顯示圖片。",
        "",
    ]
    return "\n".join(lines)


def font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        Path("/Library/Fonts/Arial Unicode.ttf"),
        Path("/System/Library/Fonts/STHeiti Medium.ttc"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default(size=size)


def draw_wrapped(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, *,
                 font_obj: ImageFont.ImageFont, fill: str, max_width: int,
                 line_gap: int = 5, max_lines: int | None = None) -> int:
    lines: list[str] = []
    current = ""
    for char in text:
        probe = current + char
        if current and draw.textbbox((0, 0), probe, font=font_obj)[2] > max_width:
            lines.append(current)
            current = char
        else:
            current = probe
    if current:
        lines.append(current)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:-1] + "…"
    x, y = xy
    line_h = draw.textbbox((0, 0), "國Ag", font=font_obj)[3] + line_gap
    for line in lines:
        draw.text((x, y), line, font=font_obj, fill=fill)
        y += line_h
    return y


def draw_result_link_card(
    draw: ImageDraw.ImageDraw,
    sample: ResultSample,
    box: tuple[int, int, int, int],
    *,
    label_font: ImageFont.ImageFont,
    text_font: ImageFont.ImageFont,
    mono_font: ImageFont.ImageFont,
) -> None:
    x, y, w, h = box
    draw.rounded_rectangle((x, y, x + w, y + h), radius=22, fill=PANEL, outline=BORDER, width=2)
    draw.rounded_rectangle((x + 20, y + 18, x + 132, y + 46), radius=14, fill="#dcfce7")
    draw.text((x + 33, y + 24), "REAL RESULT", font=mono_font, fill=GREEN)
    draw.text((x + 20, y + 56), sample.label, font=label_font, fill=INK)
    draw.text((x + 20, y + 87), sample.caption, font=text_font, fill=MUTED)
    draw_wrapped(
        draw,
        (x + 20, y + 112),
        sample.path.name,
        font_obj=mono_font,
        fill=WEAK,
        max_width=w - 40,
        line_gap=1,
        max_lines=2,
    )
    draw.rounded_rectangle((x + 20, y + 158, x + w - 20, y + 184), radius=13, fill="#dbeafe")
    button = "HTML 開啟真實圖檔"
    bbox = draw.textbbox((0, 0), button, font=mono_font)
    draw.text((x + w / 2 - (bbox[2] - bbox[0]) / 2, y + 163), button, font=mono_font, fill=BLUE)


def build_png() -> None:
    layout = build_layout()
    image = Image.new("RGB", (WIDTH, layout.height), PAPER)
    draw = ImageDraw.Draw(image)
    f12, f14, f16, f18, f20, f22, f28, f32, f48 = [font(n) for n in (12, 14, 16, 18, 20, 22, 28, 32, 48)]
    draw.text((90, 68), "EVO-PRISM · ANALYSIS MAP", font=f18, fill=BLUE)
    draw.text((90, 112), "主要分析流程與輸出檔案", font=f48, fill=INK)
    draw.text((90, 180), "15 個核心分析方法 · 4 條資料泳道 · 6 個真實圖檔連結", font=f22, fill=MUTED)
    draw.rounded_rectangle((1900, 78, 2310, 194), radius=24, fill="#fff7ed", outline="#fed7aa", width=2)
    draw.text((1932, 104), "FLOW / 示意流程", font=f16, fill=ORANGE)
    draw.text((1932, 140), "結果範例為真實圖檔\n點擊 HTML 卡片開啟", font=f16, fill=MUTED, spacing=6)

    draw.rounded_rectangle((70, 250, 2330, 485), radius=28, fill=PANEL, outline=BORDER, width=2)
    draw.text((100, 270), "共通入口", font=f16, fill=WEAK)
    flow_nodes = [
        (110, "登記樣本", "bio_register_sample"),
        (560, "檢查資料", "lookup／L2 sufficiency"),
        (1010, "選擇領域", "bulk · spatial · sc · mcseg"),
        (1460, "執行分析", "直接／analysis queue"),
        (1910, "保存與取回", "history · artifacts · figures"),
    ]
    for index in range(4):
        x1, x2 = flow_nodes[index][0] + 330, flow_nodes[index + 1][0] - 24
        draw.line((x1, 382, x2, 382), fill=MUTED, width=3)
        draw.polygon(((x2, 382), (x2 - 12, 375), (x2 - 12, 389)), fill=MUTED)
    for x, title, sub in flow_nodes:
        draw.rounded_rectangle((x, 322, x + 330, 444), radius=22, fill="#ffffff", outline=BORDER, width=2)
        draw.text((x + 24, 342), title, font=f22, fill=INK)
        draw_wrapped(draw, (x + 24, 389), sub, font_obj=f14, fill=MUTED, max_width=282, max_lines=2)

    draw.text((90, 520), "真實結果圖檔（連結）", font=f28, fill=INK)
    draw.text((90, 565), "HTML 卡片會開啟 EP 主機原始圖檔；研究影像未複製進本文件。", font=f16, fill=MUTED)
    sx, sy, sw, sh, gap = 90, 620, 342, 198, 48
    for index, sample in enumerate(SAMPLES):
        x = sx + index * (sw + gap)
        draw_result_link_card(
            draw,
            sample,
            (x, sy, sw, sh),
            label_font=f16,
            text_font=f14,
            mono_font=f12,
        )

    for lane in layout.lanes:
        domain = lane.domain
        y, h = lane.y, lane.height
        draw.rounded_rectangle((70, y, 2330, y + h), radius=30, fill="#ffffff", outline=BORDER, width=2)
        draw.rounded_rectangle((70, y, 88, y + h), radius=9, fill=domain.color)
        draw.text((110, y + 18), domain.title, font=f28, fill=domain.color)
        draw.text((110, y + 62), domain.subtitle, font=f16, fill=MUTED)
        count = len(domain.steps)
        card_w, gap, start_x = lane.card_width, lane.gap, lane.start_x
        card_y, card_h = lane.card_y, lane.card_height
        center_y = card_y + card_h // 2
        for index in range(count - 1):
            x1 = start_x + index * (card_w + gap) + card_w + 8
            x2 = start_x + (index + 1) * (card_w + gap) - 20
            draw.line((x1, center_y, x2, center_y), fill=domain.color, width=3)
            draw.polygon(((x2, center_y), (x2 - 12, center_y - 7), (x2 - 12, center_y + 7)), fill=domain.color)
        for index, (step, step_layout) in enumerate(zip(domain.steps, lane.steps, strict=True)):
            x = start_x + index * (card_w + gap)
            draw.rounded_rectangle((x, card_y, x + card_w, card_y + card_h), radius=24, fill=PANEL, outline=domain.color, width=2)
            title_font = f18 if count == 5 else f20
            draw_wrapped(draw, (x + 20, card_y + 18), step.title, font_obj=title_font, fill=INK, max_width=card_w - 40, max_lines=2)
            draw.multiline_text(
                (x + 20, card_y + 62),
                "\n".join(step_layout.method_lines),
                font=f14,
                fill=domain.color,
                spacing=3,
            )
            cursor = card_y + 96 + (len(step_layout.method_lines) - 1) * 19
            for label, shown_lines in step_layout.sections:
                draw.text((x + 20, cursor), label, font=f12, fill=WEAK)
                cursor += 22
                draw.multiline_text(
                    (x + 20, cursor),
                    "\n".join(shown_lines),
                    font=f14,
                    fill=MUTED,
                    spacing=4,
                )
                cursor += len(shown_lines) * 20 + 16
            draw.text((x + 20, card_y + card_h - 24), "NEXT  " + step.next_step, font=f12, fill=domain.color)

    mcp_y = layout.mcp_y
    draw.rounded_rectangle((70, mcp_y, 2330, mcp_y + 340), radius=30, fill=INK)
    draw.text((110, mcp_y + 25), "如何透過 MCP 對話取圖", font=f32, fill="#ffffff")
    mcp_steps = [
        ("1", "使用者提問", "「顯示 EP 分析流程」"),
        ("2", "取回指南", "bio_get_analysis_workflow"),
        ("3", "取得圖片 handle", "回傳 figure_id"),
        ("4", "顯示圖片", "bio_get_figure"),
    ]
    for index in range(3):
        x1, x2 = 140 + index * 540 + 425, 140 + (index + 1) * 540 - 32
        draw.line((x1, mcp_y + 190, x2, mcp_y + 190), fill="#94a3b8", width=3)
        draw.polygon(((x2, mcp_y + 190), (x2 - 12, mcp_y + 183), (x2 - 12, mcp_y + 197)), fill="#94a3b8")
    for index, (number, title, detail) in enumerate(mcp_steps):
        x = 140 + index * 540
        draw.ellipse((x, mcp_y + 111, x + 56, mcp_y + 167), fill=ORANGE)
        bbox = draw.textbbox((0, 0), number, font=f18)
        draw.text((x + 28 - (bbox[2] - bbox[0]) / 2, mcp_y + 126), number, font=f18, fill="#ffffff")
        draw.text((x + 72, mcp_y + 120), title, font=f20, fill="#ffffff")
        draw.rounded_rectangle((x, mcp_y + 170, x + 425, mcp_y + 254), radius=18, fill="#1e293b", outline="#334155", width=2)
        draw_wrapped(draw, (x + 20, mcp_y + 195), detail, font_obj=f16, fill="#e2e8f0", max_width=385, max_lines=2)
    draw.text((110, mcp_y + 285), "圖例：INPUT＝輸入 · DATA＝資料檔 · FIG＝圖檔 · —＝目前不直接產圖", font=f16, fill="#cbd5e1")
    draw.text((1680, mcp_y + 373), "Generated from EP source definitions · 2026-09-10", font=f14, fill="#94a3b8")
    image.save(PNG_PATH, format="PNG", optimize=True)


def main() -> None:
    SVG_PATH.parent.mkdir(parents=True, exist_ok=True)
    GUIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
    svg = build_svg()
    SVG_PATH.write_text(svg, encoding="utf-8")
    HTML_PATH.write_text(build_html(svg), encoding="utf-8")
    GUIDE_PATH.write_text(build_markdown(), encoding="utf-8")
    build_png()
    for path in (HTML_PATH, SVG_PATH, PNG_PATH, GUIDE_PATH):
        print(path.relative_to(ROOT))


if __name__ == "__main__":
    main()
