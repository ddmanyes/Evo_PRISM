#!/usr/bin/env python3
"""Render one standalone workflow diagram for each core EP analysis method."""

from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


WIDTH = 1600
HEIGHT = 2107
PAPER = "#ffffff"
INK = "#0f172a"
MUTED = "#475569"
WEAK = "#64748b"
BORDER = "#dbe3ee"
PANEL = "#f8fafc"
ORANGE = "#eb6c36"
BLUE = "#2e5aa8"
GREEN = "#15803d"


@dataclass(frozen=True)
class MethodDetail:
    resource: str
    preflight: tuple[str, ...]
    stages: tuple[tuple[str, str], ...]
    guard: str
    history: str
    real_result: Path | None = None


@dataclass(frozen=True)
class LocalizedStep:
    title: str
    tool: str
    inputs: tuple[str, ...]
    data_outputs: tuple[str, ...]
    figure_outputs: tuple[str, ...]


@dataclass(frozen=True)
class LocalizedDomain:
    title: str
    subtitle: str
    color: str


RESULTS = Path("/Volumes/KINGSTON/Evo_PRISM/results")

METHOD_DETAILS: dict[str, MethodDetail] = {
    "bio_run_bulk_eda": MethodDetail(
        "CPU · 約 10–60 秒",
        ("sample_id 已登記", "gene_counts.tsv 可讀", "coldata_path 選用"),
        (
            ("讀取計數矩陣", "gene × sample，驗證樣本 ID"),
            ("計算 QC 與 Top genes", "library size、偵測基因數"),
            ("建立樣本相關矩陣", "評估離群與重複一致性"),
            ("執行 PCA", "coldata group 或樣本名稱著色"),
            ("判讀品質旗標", "低深度、低複雜度、低相關"),
            ("產生報告與圖檔", "圖檔失敗時記錄警告並續行"),
        ),
        "單張圖失敗不終止整體 EDA；缺少 coldata 時改用名稱前綴。",
        "analysis_history(bulk_eda) · canonical",
        RESULTS / "Kallisto_v1/bulk_eda/pca_Kallisto_v1_20260904_073140.png",
    ),
    "bio_run_deg": MethodDetail(
        "CPU · 每組 comparison 依資料量",
        ("counts 與 coldata 可讀", "comparisons 至少一組", "group 名稱通過驗證"),
        (
            ("對齊輸入", "gene counts 與 sample metadata"),
            ("過濾低表現基因", "保留可估計的基因集合"),
            ("建立 Mean–Variance 圖", "全體過濾後 counts 僅畫一次"),
            ("逐組執行 DEG", "DESeq2／ttest／wilcox"),
            ("輸出 DEG 表", "每組 A vs B 一份 CSV"),
            ("繪製 Volcano 與 MA", "依 FC 與 q-value 閾值著色"),
            ("彙整多閾值統計", "標記無 DEG 或 DEG 過多"),
        ),
        "單一 comparison 或圖檔失敗會留下狀態，其餘 comparisons 繼續。",
        "analysis_history(bulk_deg) · canonical",
        RESULTS / "Kallisto_v1/bulk_deg/Volcano_pw120hr_vs_ctrl_20260603_004126.png",
    ),
    "bio_run_heatmaps": MethodDetail(
        "CPU · 秒至數分鐘",
        ("counts 可讀", "至少一份 DEG CSV", "coldata_path 選用"),
        (
            ("讀取 counts", "保留 gene × sample 結構"),
            ("收集顯著基因 Union", "套用 FC 與 p-value 閾值"),
            ("建立顯著基因熱圖", "z-score 與階層式群聚"),
            ("選取 Top-N 變異基因", "依跨樣本變異排序"),
            ("建立變異基因熱圖", "固定輸出第二張 clustermap"),
            ("加入樣本 annotation", "coldata 存在時顯示 group／batch"),
        ),
        "沒有顯著基因時跳過第一張圖；Top-N 變異熱圖仍會執行。",
        "analysis_history(bulk_heatmap)",
        RESULTS / "Kallisto_v1/bulk_heatmap/Heatmap_Top50_Variable_Genes_20260603_004417.png",
    ),
    "bio_run_enrichment": MethodDetail(
        "CPU + 網路 · Enrichr API",
        ("DEG CSV 存在", "libraries 非空且名稱安全", "可連線 Enrichr"),
        (
            ("讀取 DEG 表", "辨識 log2FC 與 q-value 欄位"),
            ("拆分上調／下調基因", "套用 FC 與顯著性閾值"),
            ("建立方向 × library 工作", "up/down × GO/KEGG/Reactome"),
            ("呼叫 Enrichr ORA", "每組工作獨立查詢"),
            ("輸出富集表", "保留 term、p-value、genes"),
            ("繪製 Dot／Bar plot", "顯示 top_term 路徑"),
            ("彙整報告", "記錄成功、空集合與失敗分支"),
        ),
        "空基因清單直接跳過；網路或單一 library 失敗不偽造結果。",
        "analysis_history(bulk_enrichment)",
        RESULTS
        / "Kallisto_v1/bulk_enrichment/DEG_pw96hr_vs_ctrl_20260526_080400__up__Reactome_2022_20260526_084049.png",
    ),
    "bio_run_spatial_eda": MethodDetail(
        "CPU · 約 10–30 秒",
        ("sample_id 已登記", "L2 l2_ready=true", "expression/*.parquet 與 obs metadata"),
        (
            ("解析 L2 路徑", "定位 expression 與 obs Parquet"),
            ("計算空間 QC", "counts、genes、座標分布"),
            ("選出 Top genes", "依全域表現量排序"),
            ("繪製 QC distributions", "檢查訊號品質與離群值"),
            ("繪製空間基因圖", "將候選基因映射至座標"),
            ("彙整 EDA 報告", "資料表、圖片與品質摘要"),
            ("更新 L1 摘要", "供後續語意搜尋"),
        ),
        "L2 未就緒時停止，先執行資料轉換；不直接讀原始 Space Ranger 巨檔。",
        "analysis_history(eda_report) + L1 cache",
        RESULTS / "external_import/kangyu_visiumhd_sds_lipolysis/figures/qc_spatial.png",
    ),
    "bio_compute_spatial_nn_distance": MethodDetail(
        "CPU · cKDTree · 通常數秒",
        ("AnnData h5ad 可讀", "obsm['spatial'] 存在", "source／target filters 有匹配細胞"),
        (
            ("載入 AnnData", "讀取 obs 與空間座標"),
            ("篩選來源與目標群", "依 obs_column + values"),
            ("依 group_by 分層", "避免跨切片距離"),
            ("建立目標 cKDTree", "每個群組獨立建立索引"),
            ("查詢最近鄰", "每個來源細胞找到最近目標"),
            ("換算距離單位", "套用 distance_scale 與 unit"),
            ("輸出明細表", "保留來源、目標與距離"),
        ),
        "跨 capture area 強烈建議指定 group_by；空群組或缺座標直接拒絕。",
        "analysis_history(spatial_nn_distance)",
    ),
    "bio_run_sc_clustering": MethodDetail(
        "CPU · 數十秒至數分鐘",
        ("raw-count h5ad 可讀", "輸出目錄在 BIO_DB_ROOT", "marker_gene_sets 選用"),
        (
            ("計算並套用 QC", "counts、genes、n_bins 門檻"),
            ("保存 raw counts", "供後續子集重新正規化"),
            ("Normalize + log1p", "轉換單細胞表現矩陣"),
            ("HVG + PCA", "裁切至資料允許的維度"),
            ("Neighbors + Leiden", "建立圖結構並分群"),
            ("建立 UMAP", "依指定欄位視覺化"),
            ("排名 Marker genes", "Wilcoxon rank_genes_groups"),
            ("選用 cluster 標注", "marker module score 的 cluster argmax"),
        ),
        "未提供 marker_gene_sets 時只輸出 markers，不寫死細胞型別對照。",
        "analysis_history(sc_clustering)",
        RESULTS / "mcseg/dpcp01_vh_v114_02_hd_r004/fullslide/clustering/umap_leiden_20260721_102206.png",
    ),
    "bio_get_marker_genes": MethodDetail(
        "CPU · 秒至數分鐘",
        ("ROI umap_computed.h5ad 存在", "groupby 欄位存在", "至少兩個群組"),
        (
            ("載入 AnnData", "沿用 ROI 正規化結果"),
            ("驗證分群欄位", "leiden 或 cell_type"),
            ("執行 rank_genes_groups", "預設 Wilcoxon"),
            ("整理欄位", "cluster、gene、score、FC、FDR"),
            ("擷取每群 Top-N", "另產生 inline Top-5 摘要"),
            ("輸出 CSV 與報告", "供人工標注與驗證"),
        ),
        "groupby 缺失或只有單一群組時停止，不產生無效 marker 排名。",
        "analysis_history(marker_genes)",
    ),
    "bio_run_celltypist": MethodDetail(
        "CPU + 模型下載 · 需 celltypist",
        ("ROI umap_computed.h5ad 存在", "celltypist 套件可用", "模型與物種相容"),
        (
            ("下載／載入模型", "預設 Immune_All_Low.pkl"),
            ("檢查基因交集", "計算資料與模型 features overlap"),
            ("執行 CellTypist", "以正規化 log1p 表現量預測"),
            ("選用 majority voting", "決定最終 label 欄位"),
            ("原子更新 h5ad", "新增 celltypist_cell_type"),
            ("繪製標注 UMAP", "依預測細胞型別著色"),
            ("輸出報告", "列出基因匹配與細胞型別分布"),
        ),
        "多數預訓練模型為人類資料；小鼠須檢查 gene overlap，避免誤標。",
        "analysis_history(celltypist)",
    ),
    "bio_run_geneset_score": MethodDetail(
        "CPU · 秒至數分鐘",
        ("AnnData h5ad 可讀", "modules 至少一組", "輸出目錄在 BIO_DB_ROOT"),
        (
            ("載入並選用篩細胞", "依 obs_column + values 子集"),
            ("選擇表現層", "優先使用 counts layer"),
            ("重新 Normalize + log1p", "子集後重新正規化"),
            ("比對模組基因", "排除資料中不存在的 genes"),
            ("逐模組 score_genes", "使用 matched genes 與 ctrl_size"),
            ("附加分組欄位", "group_by 僅供輸出比較"),
            ("輸出 scores 表", "每細胞一列、每模組一欄"),
        ),
        "counts layer 不存在時才回退到現有 .X，並在紀錄中留下 warning。",
        "analysis_history(geneset_score)",
    ),
    "bio_run_mcseg_roi": MethodDetail(
        "GPU · 約 30–90 分鐘",
        ("H&E BTF／TIFF 可讀", "2 µm binned_outputs 完整", "ROI x／y 與 sample_id 合法"),
        (
            ("Stage 0 · 裁切 ROI", "換算 virtual_fullres ↔ TIFF 座標"),
            ("Stage 1 · 7-pass 分割", "cyto3×4 + cpsam×3 ensemble"),
            ("Stage 2 · RNA 計數", "mask → 2 µm bin attribution"),
            ("Stage 3 · Scanpy", "QC、normalize、HVG、UMAP、Leiden"),
            ("Stage 4 · 基因 score 標注", "依 marker modules 標細胞型別"),
            ("Stage 5 · 空間品質", "NED、niche、permutation test"),
            ("Stage 6 · 匯出", "UMAP、dotplot、Xenium bundle"),
            ("Stage 7 · H&E overlay", "細胞型別著色與邊界圖"),
        ),
        "GPU 重量工作應走 analysis queue；座標或影像比例異常時停止。",
        "analysis_history(mcseg_roi)",
        RESULTS / "mcseg/Human_CRC/roi/crc_roi1/mask_he_overlay.png",
    ),
    "bio_run_mcseg_fullslide": MethodDetail(
        "GPU · 通常數小時",
        ("全片 BTF／TIFF 可讀", "2 µm binned_outputs 完整", "tile_size／overlap 合理"),
        (
            ("Stage 0 · 讀取全片", "以 tile 方式避免一次載入全部"),
            ("Stage 1 · Tiled MCseg", "1024 px、128 px overlap、7-pass"),
            ("合併 tile 遮罩", "處理重疊區與全片 cell IDs"),
            ("Stage 2 · RNA 計數", "全片 mask → bin attribution"),
            ("輸出分割預覽", "overlay／preview 供快速 QC"),
            ("保存 AnnData", "交給獨立 sc clustering downstream"),
        ),
        "全片細胞可超過十萬；此工具不包含 downstream Scanpy 分群。",
        "analysis_history(mcseg_fullslide)",
        RESULTS
        / "mcseg/yousheng_vh_v113_12/fullslide/V11312_02_20mJ/fullslide_segmentation_preview.png",
    ),
    "bio_run_mcseg_merge": MethodDetail(
        "CPU · 依 ROI 細胞數",
        ("至少兩個 ROI", "每個 cellpose_cells.h5ad 存在", "integrate 為 auto/harmony/bbknn/none"),
        (
            ("載入多個 ROI", "為每批細胞保留 roi_name"),
            ("AnnData concat", "outer join + 唯一 cell index"),
            ("Filter + normalize", "移除稀有基因、normalize、log1p"),
            ("HVG + PCA", "以 roi_name 作 batch key"),
            ("批次整合", "Harmony／BBKNN／none fallback"),
            ("Leiden + UMAP", "建立整合後分群"),
            ("輸出 merged.h5ad", "保存跨 ROI 分析矩陣"),
            ("繪製兩張 UMAP", "依 ROI 與 Leiden 著色"),
        ),
        "auto 會依安裝狀況降級；none 代表沒有 batch correction，報告會警示。",
        "analysis_history(mcseg_merge)",
    ),
    "bio_run_mcseg_qc": MethodDetail(
        "CPU · 讀既有遮罩，不重跑分割",
        ("qc_dir 存在", "每個 ROI 有 *_nuc.npy", "同名 *_mcseg.npy 成對存在"),
        (
            ("掃描 ROI 遮罩對", "前置失敗不污染 history"),
            ("載入 NUC 與 MCseg", "逐 ROI 讀取 label masks"),
            ("繪製對比圖", "NUC vs MCseg 空間比較"),
            ("計算細胞指標", "數量、面積、前景占比"),
            ("彙整面積分布", "跨 ROI 比較 cell size"),
            ("產生 QC 報告", "表格、比較圖與 sizedist"),
        ),
        "沒有完整遮罩配對即停止；此方法不會即時重跑 MCseg。",
        "analysis_history(mcseg_qc)",
    ),
    "bio_compute_crc_metrics": MethodDetail(
        "CPU · 約 1–5 分鐘",
        ("segmentation_masks.npy 存在", "cellpose_cells.h5ad 存在", "tissue positions／ROI metadata 可解析"),
        (
            ("解析 ROI 座標", "優先讀 crop_meta／roi_info"),
            ("計算 FTC", "in-tissue bins 落在 mask 的比例"),
            ("計算 UMI Density", "依細胞遮罩面積正規化"),
            ("計算 NED", "鄰近細胞表現 Hellinger 差異"),
            ("計算 C1", "譜系互斥基因對共表達率"),
            ("選用 ENACT Precision", "提供 GT centroids 時才計算"),
            ("輸出 JSON 與報告", "彙整數值、警示與來源"),
        ),
        "舊 ROI 缺 metadata 時 FTC 可能不準；應補座標或重跑 ROI 產生資訊。",
        "analysis_history(crc_metrics)",
    ),
}


ENGLISH_DOMAINS = {
    "bulk": (
        "Bulk RNA-seq",
        "Sample-level expression → QC / PCA → DEG → heatmaps and pathway enrichment",
    ),
    "spatial": (
        "Spatial transcriptomics",
        "L2 Parquet → spatial QC / gene maps → selected cell-population distances",
    ),
    "single_cell": (
        "Single-cell / AnnData",
        "Raw cell × gene counts → QC / Leiden / UMAP → markers, cell types and module scores",
    ),
    "mcseg": (
        "Visium HD / MCseg",
        "H&E + 2 µm bins → segmentation / RNA counting → integration, QC and CRC metrics",
    ),
}


ENGLISH_STEPS: dict[str, tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
    "bio_run_bulk_eda": (
        "Exploratory analysis and QC",
        ("gene_counts.tsv", "run_info.json (optional)"),
        ("bulk_eda_<sample>_<ts>.md",),
        ("qc_*.png", "corr_*.png", "pca_*.png", "dist_*.png"),
    ),
    "bio_run_deg": (
        "Differential expression",
        ("counts.csv / TSV", "coldata.tsv", "comparisons"),
        ("DEG_<A>_vs_<B>_<ts>.csv", "bulk_deg_<sample>_<ts>.md"),
        ("Volcano_*.png", "MA_*.png", "MeanVariance_*.png"),
    ),
    "bio_run_heatmaps": (
        "Expression heatmaps",
        ("counts", "DEG_*.csv"),
        ("bulk_heatmap_<sample>_<ts>.md",),
        ("Heatmap_Significant_Genes_*.png", "Heatmap_TopN_Variable_Genes_*.png"),
    ),
    "bio_run_enrichment": (
        "Functional enrichment",
        ("DEG_*.csv", "GO / KEGG / Reactome libraries"),
        ("<direction>_<library>_<ts>.csv", "bulk_enrichment_<sample>_<ts>.md"),
        ("*_bar_<ts>.png", "*_<ts>.png (dot plot)"),
    ),
    "bio_run_spatial_eda": (
        "Spatial exploration and QC",
        ("expression/*.parquet", "obs_metadata.parquet"),
        ("qc_stats.parquet", "top_N_genes.csv", "eda_report_<ts>.md"),
        ("qc_distributions.png", "spatial_<gene>.png", "coexpr_<A>_<B>.png"),
    ),
    "bio_compute_spatial_nn_distance": (
        "Cell-population nearest-neighbor distance",
        ("AnnData .h5ad", "obsm['spatial']", "source / target filters"),
        ("spatial_nn_distance.csv",),
        ("— (tabular output only)",),
    ),
    "bio_run_sc_clustering": (
        "QC, clustering and UMAP",
        ("cellpose_cells*.h5ad or any raw-count h5ad",),
        ("clustered.h5ad", "top_markers_per_cluster.csv", "clustering_report_*.md"),
        ("umap_<color>_<ts>.png",),
    ),
    "bio_get_marker_genes": (
        "Cluster marker genes",
        ("umap_computed.h5ad", "groupby=leiden / cell_type"),
        ("marker_genes_<sample>_<roi>_<ts>.csv", "marker_genes_*.md"),
        ("— (tabular output only)",),
    ),
    "bio_run_celltypist": (
        "Automated CellTypist annotation",
        ("umap_computed.h5ad", "CellTypist model"),
        ("umap_computed.h5ad (adds celltypist_cell_type)", "celltypist_*.md"),
        ("umap_celltypist_*.png",),
    ),
    "bio_run_geneset_score": (
        "Custom gene-module scoring",
        ("AnnData .h5ad", "{module: genes}"),
        ("geneset_scores.csv",),
        ("— (tabular output only)",),
    ),
    "bio_run_mcseg_roi": (
        "Complete single-ROI pipeline",
        ("H&E TIFF", "2 µm binned_outputs", "ROI x / y / w / h"),
        ("adata_002um.h5ad", "segmentation_masks.npy", "cellpose_cells.h5ad", "Xenium bundle"),
        ("umap_spatial_combined.png", "mask_he_overlay.png", "mask_he_boundary.png", "skin_markers_dotplot.png"),
    ),
    "bio_run_mcseg_fullslide": (
        "Whole-slide cell segmentation",
        ("H&E TIFF / BTF", "2 µm binned_outputs"),
        ("segmentation_masks_fullslide.npy", "segmentation_masks_fullslide_vfr.npy", "cellpose_cells_fullslide.h5ad"),
        ("— (overlay can be registered separately)",),
    ),
    "bio_run_mcseg_merge": (
        "Multi-ROI integration",
        ("multiple ROI/cellpose_cells.h5ad files",),
        ("merged.h5ad", "merge_report_<ts>.md"),
        ("umap_by_roi_<ts>.png", "umap_by_leiden_<ts>.png"),
    ),
    "bio_run_mcseg_qc": (
        "Segmentation quality control",
        ("<roi>_nuc.npy", "<roi>_mcseg.npy"),
        ("mcseg_qc_<sample>_<ts>.md",),
        ("cmp_<sample>_<roi>_<ts>.png", "sizedist_<sample>_<ts>.png"),
    ),
    "bio_compute_crc_metrics": (
        "CRC RNA quality metrics",
        ("segmentation_masks.npy", "cellpose_cells.h5ad", "tissue_positions.parquet"),
        ("crc_metrics.json", "crc_metrics_report.md"),
        ("— (FTC / UMI Density / NED / C1 values)",),
    ),
}


ENGLISH_METHOD_DETAILS: dict[str, MethodDetail] = {
    "bio_run_bulk_eda": MethodDetail(
        "CPU · approx. 10–60 sec",
        ("sample_id is registered", "gene_counts.tsv is readable", "coldata_path is optional"),
        (
            ("Read count matrix", "gene × sample; validate sample IDs"),
            ("Compute QC and top genes", "library size and detected-gene count"),
            ("Build sample correlation", "assess outliers and replicate consistency"),
            ("Run PCA", "color by coldata group or sample name"),
            ("Evaluate quality flags", "low depth, complexity or correlation"),
            ("Create report and figures", "record a warning if one plot fails"),
        ),
        "A single plot failure does not stop EDA; without coldata, name prefixes are used.",
        "analysis_history(bulk_eda) · canonical",
    ),
    "bio_run_deg": MethodDetail(
        "CPU · scales by comparison",
        ("counts and coldata are readable", "at least one comparison", "group names pass validation"),
        (
            ("Align inputs", "gene counts and sample metadata"),
            ("Filter low-expression genes", "retain genes that can be estimated"),
            ("Create mean–variance plot", "draw once for all filtered counts"),
            ("Run each DEG comparison", "DESeq2 / t-test / Wilcoxon"),
            ("Export DEG tables", "one CSV for every A versus B"),
            ("Draw volcano and MA plots", "color by FC and q-value thresholds"),
            ("Summarize threshold counts", "flag no-DEG or excessive-DEG cases"),
        ),
        "One failed comparison or plot is recorded while the remaining comparisons continue.",
        "analysis_history(bulk_deg) · canonical",
    ),
    "bio_run_heatmaps": MethodDetail(
        "CPU · seconds to minutes",
        ("counts are readable", "at least one DEG CSV", "coldata_path is optional"),
        (
            ("Read counts", "preserve the gene × sample structure"),
            ("Union significant genes", "apply FC and p-value thresholds"),
            ("Build significant-gene heatmap", "z-score and hierarchical clustering"),
            ("Select top-variable genes", "rank by variance across samples"),
            ("Build variable-gene heatmap", "always create a second clustermap"),
            ("Add sample annotations", "show group / batch when coldata exists"),
        ),
        "If no genes are significant, the first plot is skipped; the top-variable heatmap still runs.",
        "analysis_history(bulk_heatmap)",
    ),
    "bio_run_enrichment": MethodDetail(
        "CPU + network · Enrichr API",
        ("DEG CSV exists", "libraries are non-empty and safe", "Enrichr is reachable"),
        (
            ("Read DEG table", "identify log2FC and q-value columns"),
            ("Split up- and down-regulated genes", "apply FC and significance thresholds"),
            ("Create direction × library jobs", "up/down × GO/KEGG/Reactome"),
            ("Call Enrichr ORA", "query each job independently"),
            ("Export enrichment tables", "keep term, p-value and genes"),
            ("Draw dot and bar plots", "display top pathway terms"),
            ("Compile report", "record success, empty sets and failures"),
        ),
        "Empty gene lists are skipped; network or library failures never create fabricated results.",
        "analysis_history(bulk_enrichment)",
    ),
    "bio_run_spatial_eda": MethodDetail(
        "CPU · approx. 10–30 sec",
        ("sample_id is registered", "L2 l2_ready=true", "expression/*.parquet and obs metadata exist"),
        (
            ("Resolve L2 paths", "locate expression and obs Parquet files"),
            ("Compute spatial QC", "counts, genes and coordinate distributions"),
            ("Select top genes", "rank by global expression"),
            ("Plot QC distributions", "inspect signal quality and outliers"),
            ("Plot spatial gene maps", "map candidate genes to coordinates"),
            ("Compile EDA report", "tables, figures and quality summary"),
            ("Update L1 summary", "support downstream semantic search"),
        ),
        "Stop when L2 is not ready and convert data first; do not read raw Space Ranger files directly.",
        "analysis_history(eda_report) + L1 cache",
    ),
    "bio_compute_spatial_nn_distance": MethodDetail(
        "CPU · cKDTree · usually seconds",
        ("AnnData h5ad is readable", "obsm['spatial'] exists", "source and target filters match cells"),
        (
            ("Load AnnData", "read obs and spatial coordinates"),
            ("Filter source and target", "use obs_column plus values"),
            ("Stratify by group_by", "prevent cross-section distances"),
            ("Build target cKDTree", "create one index per group"),
            ("Query nearest neighbors", "find the closest target for each source"),
            ("Convert distance units", "apply distance_scale and unit"),
            ("Export detail table", "retain source, target and distance"),
        ),
        "Specify group_by across capture areas; empty groups or missing coordinates are rejected.",
        "analysis_history(spatial_nn_distance)",
    ),
    "bio_run_sc_clustering": MethodDetail(
        "CPU · tens of seconds to minutes",
        ("raw-count h5ad is readable", "output is under BIO_DB_ROOT", "marker_gene_sets is optional"),
        (
            ("Compute and apply QC", "threshold counts, genes and n_bins"),
            ("Preserve raw counts", "support renormalization after subsetting"),
            ("Normalize + log1p", "transform the single-cell expression matrix"),
            ("HVG + PCA", "clip dimensions to available data"),
            ("Neighbors + Leiden", "build a graph and clusters"),
            ("Create UMAP", "visualize selected color fields"),
            ("Rank marker genes", "Wilcoxon rank_genes_groups"),
            ("Optionally annotate clusters", "argmax cluster marker-module score"),
        ),
        "Without marker_gene_sets, only markers are exported; cell-type mappings are not hard-coded.",
        "analysis_history(sc_clustering)",
    ),
    "bio_get_marker_genes": MethodDetail(
        "CPU · seconds to minutes",
        ("ROI umap_computed.h5ad exists", "groupby column exists", "at least two groups"),
        (
            ("Load AnnData", "reuse the normalized ROI result"),
            ("Validate grouping field", "Leiden or cell_type"),
            ("Run rank_genes_groups", "Wilcoxon by default"),
            ("Normalize output fields", "cluster, gene, score, FC and FDR"),
            ("Select per-cluster top N", "also create an inline top-five summary"),
            ("Export CSV and report", "support manual annotation and review"),
        ),
        "Stop if groupby is absent or has one group; do not produce invalid marker rankings.",
        "analysis_history(marker_genes)",
    ),
    "bio_run_celltypist": MethodDetail(
        "CPU + model download · CellTypist",
        ("ROI umap_computed.h5ad exists", "celltypist package is available", "model matches the species"),
        (
            ("Download or load model", "default Immune_All_Low.pkl"),
            ("Check gene overlap", "compare data and model features"),
            ("Run CellTypist", "predict from normalized log1p expression"),
            ("Optionally use majority voting", "choose the final label field"),
            ("Atomically update h5ad", "add celltypist_cell_type"),
            ("Plot annotated UMAP", "color by predicted cell type"),
            ("Export report", "list gene matching and label distribution"),
        ),
        "Most pretrained models target human data; check gene overlap before labeling mouse data.",
        "analysis_history(celltypist)",
    ),
    "bio_run_geneset_score": MethodDetail(
        "CPU · seconds to minutes",
        ("AnnData h5ad is readable", "at least one module", "output is under BIO_DB_ROOT"),
        (
            ("Load and optionally subset cells", "filter by obs_column plus values"),
            ("Choose expression layer", "prefer the counts layer"),
            ("Renormalize + log1p", "normalize after subsetting"),
            ("Match module genes", "exclude genes absent from the data"),
            ("Run score_genes per module", "use matched genes and ctrl_size"),
            ("Attach grouping fields", "group_by supports output comparison"),
            ("Export score table", "one row per cell and column per module"),
        ),
        "Only fall back to the current .X when no counts layer exists, and record a warning.",
        "analysis_history(geneset_score)",
    ),
    "bio_run_mcseg_roi": MethodDetail(
        "GPU · approx. 30–90 min",
        ("H&E BTF / TIFF is readable", "2 µm binned_outputs are complete", "ROI coordinates and sample_id are valid"),
        (
            ("Stage 0 · Crop ROI", "convert virtual_fullres ↔ TIFF coordinates"),
            ("Stage 1 · Seven-pass segmentation", "cyto3×4 + cpsam×3 ensemble"),
            ("Stage 2 · Count RNA", "attribute 2 µm bins through the mask"),
            ("Stage 3 · Scanpy", "QC, normalize, HVG, UMAP and Leiden"),
            ("Stage 4 · Gene-score annotation", "label cell types using marker modules"),
            ("Stage 5 · Spatial quality", "NED, niches and permutation tests"),
            ("Stage 6 · Export", "UMAP, dot plot and Xenium bundle"),
            ("Stage 7 · H&E overlay", "cell-type colors and boundary map"),
        ),
        "Route GPU-heavy work through the analysis queue; stop on coordinate or scale anomalies.",
        "analysis_history(mcseg_roi)",
    ),
    "bio_run_mcseg_fullslide": MethodDetail(
        "GPU · usually several hours",
        ("whole-slide BTF / TIFF is readable", "2 µm binned_outputs are complete", "tile_size and overlap are valid"),
        (
            ("Stage 0 · Read whole slide", "tile the image instead of loading it all"),
            ("Stage 1 · Tiled MCseg", "1024 px, 128 px overlap, seven passes"),
            ("Merge tile masks", "resolve overlaps and slide-wide cell IDs"),
            ("Stage 2 · Count RNA", "attribute bins through the whole-slide mask"),
            ("Export segmentation preview", "overlay / preview for rapid QC"),
            ("Save AnnData", "hand off to standalone sc clustering"),
        ),
        "A slide may exceed 100,000 cells; downstream Scanpy clustering is not included here.",
        "analysis_history(mcseg_fullslide)",
    ),
    "bio_run_mcseg_merge": MethodDetail(
        "CPU · scales by ROI cell count",
        ("at least two ROIs", "each cellpose_cells.h5ad exists", "integrate is auto/harmony/bbknn/none"),
        (
            ("Load multiple ROIs", "retain roi_name for every cell"),
            ("Concatenate AnnData", "outer join plus unique cell index"),
            ("Filter + normalize", "remove rare genes, normalize and log1p"),
            ("HVG + PCA", "use roi_name as the batch key"),
            ("Integrate batches", "Harmony / BBKNN / none fallback"),
            ("Leiden + UMAP", "cluster the integrated graph"),
            ("Export merged.h5ad", "save the cross-ROI matrix"),
            ("Draw two UMAPs", "color by ROI and Leiden"),
        ),
        "auto degrades by installed packages; none means no batch correction and triggers a warning.",
        "analysis_history(mcseg_merge)",
    ),
    "bio_run_mcseg_qc": MethodDetail(
        "CPU · reads masks; no rerun",
        ("qc_dir exists", "each ROI has *_nuc.npy", "a matching *_mcseg.npy exists"),
        (
            ("Scan ROI mask pairs", "preflight failures do not pollute history"),
            ("Load NUC and MCseg", "read label masks per ROI"),
            ("Draw comparison panels", "spatially compare NUC and MCseg"),
            ("Compute cell metrics", "count, area and foreground coverage"),
            ("Summarize area distributions", "compare cell size across ROIs"),
            ("Create QC report", "tables, comparisons and size distributions"),
        ),
        "Stop without complete mask pairs; this method never reruns MCseg on demand.",
        "analysis_history(mcseg_qc)",
    ),
    "bio_compute_crc_metrics": MethodDetail(
        "CPU · approx. 1–5 min",
        ("segmentation_masks.npy exists", "cellpose_cells.h5ad exists", "tissue positions / ROI metadata are parseable"),
        (
            ("Resolve ROI coordinates", "prefer crop_meta / roi_info"),
            ("Compute FTC", "fraction of in-tissue bins inside masks"),
            ("Compute UMI density", "normalize by cell-mask area"),
            ("Compute NED", "neighboring-cell expression Hellinger distance"),
            ("Compute C1", "coexpression of lineage-exclusive gene pairs"),
            ("Optionally compute ENACT precision", "only when GT centroids are supplied"),
            ("Export JSON and report", "summarize values, warnings and sources"),
        ),
        "FTC may be inaccurate for old ROIs without metadata; restore coordinates or rerun the ROI.",
        "analysis_history(crc_metrics)",
    ),
}


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _svg_text(
    x: int,
    y: int,
    lines: list[str],
    *,
    size: int = 18,
    color: str = INK,
    weight: int = 400,
    family: str = "sans",
    line_height: int | None = None,
    anchor: str = "start",
) -> str:
    line_height = line_height or int(size * 1.35)
    spans = []
    for index, line in enumerate(lines):
        dy = 0 if index == 0 else line_height
        spans.append(f'<tspan x="{x}" dy="{dy}">{_esc(line)}</tspan>')
    return (
        f'<text x="{x}" y="{y}" font-family="var(--{family})" '
        f'font-size="{size}" font-weight="{weight}" fill="{color}" '
        f'text-anchor="{anchor}">{"".join(spans)}</text>'
    )


def _wrap_chars(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    if " " in text and all(len(word) <= limit for word in text.split()):
        lines: list[str] = []
        current = ""
        for word in text.split():
            probe = f"{current} {word}".strip()
            if current and len(probe) > limit:
                lines.append(current)
                current = word
            else:
                current = probe
        if current:
            lines.append(current)
        return lines
    return [text[index : index + limit] for index in range(0, len(text), limit)]


def _localized_method(
    step: Any,
    domain: Any,
    detail: MethodDetail,
    language: str,
) -> tuple[Any, Any, MethodDetail]:
    if language == "zh-Hant":
        return step, domain, detail
    if language != "en":
        raise ValueError(f"unsupported diagram language: {language}")

    title, inputs, data_outputs, figure_outputs = ENGLISH_STEPS[step.tool]
    domain_title, domain_subtitle = ENGLISH_DOMAINS[domain.key]
    english_detail = ENGLISH_METHOD_DETAILS[step.tool]
    return (
        LocalizedStep(title, step.tool, inputs, data_outputs, figure_outputs),
        LocalizedDomain(domain_title, domain_subtitle, domain.color),
        MethodDetail(
            english_detail.resource,
            english_detail.preflight,
            english_detail.stages,
            english_detail.guard,
            english_detail.history,
            detail.real_result,
        ),
    )


def _section(out: list[str], number: int, label: str, y: int, height: int) -> None:
    out.append(
        f'<rect x="45" y="{y}" width="1510" height="{height}" rx="26" '
        f'fill="{PANEL}" stroke="{BORDER}"/>'
    )
    out.append(f'<circle cx="78" cy="{y + 34}" r="22" fill="{ORANGE if number < 4 else BLUE}"/>')
    out.append(
        _svg_text(
            78,
            y + 41,
            [str(number)],
            size=17,
            color="#ffffff",
            weight=700,
            family="mono",
            anchor="middle",
        )
    )
    out.append(_svg_text(112, y + 41, [label], size=15, color=WEAK, weight=700, family="mono"))


def _list_card(
    out: list[str],
    *,
    x: int,
    y: int,
    width: int,
    height: int,
    title: str,
    values: tuple[str, ...],
    accent: str,
    footer: str | None = None,
) -> None:
    out.append(
        f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="22" '
        f'fill="#ffffff" stroke="{accent}" stroke-width="2"/>'
    )
    out.append(_svg_text(x + 26, y + 42, [title], size=22, weight=700))
    cursor = y + 78
    for value in values:
        lines = _wrap_chars("• " + value, 53 if width >= 700 else 46)
        out.append(_svg_text(x + 26, cursor, lines, size=16, color=MUTED, line_height=22))
        cursor += len(lines) * 22 + 8
    if footer:
        footer_lines = _wrap_chars(footer, 58 if width >= 700 else 48)[:3]
        out.append(
            f'<rect x="{x + 20}" y="{y + height - 78}" width="{width - 40}" height="58" '
            'rx="14" fill="#fff7ed" stroke="#fed7aa"/>'
        )
        out.append(_svg_text(x + 36, y + height - 52, footer_lines, size=14, color="#9a3412", line_height=19))


def build_method_svg(
    step: Any,
    domain: Any,
    detail: MethodDetail,
    *,
    language: str = "zh-Hant",
) -> str:
    step, domain, detail = _localized_method(step, domain, detail, language)
    english = language == "en"
    slug = step.tool
    arrow_id = f"{slug}-arrow"
    title_id = f"{slug}-title"
    desc_id = f"{slug}-desc"
    out = [
        f'<svg viewBox="0 0 {WIDTH} {HEIGHT}" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" aria-labelledby="{title_id} {desc_id}">',
        f'<title id="{title_id}">{_esc(step.title)} — {_esc(step.tool)} {"workflow" if english else "流程圖"}</title>',
        f'<desc id="{desc_id}">{_esc(step.tool)} {"input checks, analysis stages, data and figure outputs, persistence and MCP retrieval." if english else "的輸入檢查、分析步驟、資料與圖檔產出，以及 MCP 保存與提取流程。"}</desc>',
        "<defs>",
        '<style>:root{--sans:Geist,"PingFang TC","Noto Sans TC",system-ui,sans-serif;--serif:"Instrument Serif","Songti TC",serif;--mono:"Geist Mono",ui-monospace,monospace}</style>',
        f'<marker id="{arrow_id}" markerWidth="12" markerHeight="10" refX="10" refY="5" orient="auto"><path d="M0,0 L12,5 L0,10 Z" fill="{MUTED}"/></marker>',
        "</defs>",
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{PAPER}"/>',
    ]
    out.append(_svg_text(55, 66, [f"EVO-PRISM — {domain.title}"], size=17, color=BLUE, weight=700, family="mono"))
    out.append(_svg_text(55, 125, [step.title], size=46, weight=700, family="serif"))
    out.append(_svg_text(55, 167, [step.tool], size=19, color=domain.color, weight=700, family="mono"))
    out.append(_svg_text(55, 199, _wrap_chars(domain.subtitle, 78)[:2], size=17, color=MUTED, line_height=23))
    pill_x, pill_width, pill_center = (1190, 355, 1367) if english else (1270, 275, 1407)
    out.append(f'<rect x="{pill_x}" y="65" width="{pill_width}" height="54" rx="27" fill="#eff6ff" stroke="#bfdbfe"/>')
    out.append(_svg_text(pill_center, 98, [detail.resource], size=13, color=BLUE, weight=700, family="mono", anchor="middle"))

    _section(out, 1, "INPUT & PREFLIGHT", 230, 330)
    _list_card(out, x=78, y=300, width=700, height=220, title="Input data" if english else "輸入資料", values=step.inputs, accent=domain.color)
    _list_card(
        out,
        x=820,
        y=300,
        width=700,
        height=220,
        title="Preflight checks" if english else "前置條件與防呆",
        values=detail.preflight,
        accent=ORANGE,
        footer=detail.guard,
    )

    _section(out, 2, "ANALYSIS PIPELINE", 590, 700)
    node_x, node_w, node_h = 280, 1040, 62
    count = len(detail.stages)
    gap = min(24, (600 - node_h * count) // max(1, count - 1))
    total = node_h * count + gap * (count - 1)
    start_y = 660 + (590 - total) // 2
    center_x = node_x + node_w // 2
    for index in range(count - 1):
        y1 = start_y + index * (node_h + gap) + node_h
        y2 = start_y + (index + 1) * (node_h + gap) - 7
        out.append(
            f'<line x1="{center_x}" y1="{y1}" x2="{center_x}" y2="{y2}" '
            f'stroke="{MUTED}" stroke-width="2.5" marker-end="url(#{arrow_id})"/>'
        )
    for index, (title, description) in enumerate(detail.stages, 1):
        y = start_y + (index - 1) * (node_h + gap)
        accent = ORANGE if index == 1 else BLUE
        out.append(
            f'<rect x="{node_x}" y="{y}" width="{node_w}" height="{node_h}" rx="18" '
            f'fill="#ffffff" stroke="{accent}" stroke-width="2"/>'
        )
        out.append(f'<circle cx="{node_x + 34}" cy="{y + node_h // 2}" r="19" fill="{accent}"/>')
        out.append(_svg_text(node_x + 34, y + 38, [str(index)], size=14, color="#ffffff", weight=700, family="mono", anchor="middle"))
        out.append(_svg_text(node_x + 70, y + 38, [title], size=18, weight=700))
        out.append(_svg_text(node_x + 390, y + 38, _wrap_chars(description, 61)[:1], size=15, color=MUTED))

    _section(out, 3, "OUTPUTS & QUALITY", 1320, 450)
    _list_card(out, x=78, y=1390, width=700, height=330, title="DATA · Files and reports" if english else "DATA · 數據與報告", values=step.data_outputs, accent=BLUE)
    figure_values = step.figure_outputs
    data_only = all(value.startswith("—") for value in figure_values)
    sample_footer = None
    if detail.real_result and detail.real_result.is_file():
        sample_footer = f"REAL RESULT ↗ {detail.real_result.name}"
    elif data_only:
        sample_footer = "DATA ONLY · This method does not directly create figures" if english else "DATA ONLY · 此方法目前不直接產生圖檔"
    _list_card(
        out,
        x=820,
        y=1390,
        width=700,
        height=330,
        title="FIG · Figures" if english else "FIG · 圖檔",
        values=figure_values,
        accent=GREEN if detail.real_result else BLUE,
        footer=sample_footer,
    )

    _section(out, 4, "PERSISTENCE & RETRIEVAL", 1800, 245)
    image_name = f"{step.tool}{'.en' if english else ''}.png"
    persistence = (
        ("Analysis complete" if english else "完成分析", detail.history),
        ("Versioned method diagram" if english else "版本化方法圖", f"docs/images/analysis_methods/{image_name}"),
        ("MCP retrieval" if english else "MCP 對話提取", f"method={step.tool} → figure_id"),
    )
    card_xs = (78, 585, 1092)
    for index in range(2):
        out.append(
            f'<line x1="{card_xs[index] + 410}" y1="1935" x2="{card_xs[index + 1] - 22}" y2="1935" '
            f'stroke="{BLUE}" stroke-width="2.5" marker-end="url(#{arrow_id})"/>'
        )
    for x, (title, value) in zip(card_xs, persistence, strict=True):
        out.append(f'<rect x="{x}" y="1880" width="430" height="112" rx="20" fill="#ffffff" stroke="{BLUE}" stroke-width="2"/>')
        out.append(_svg_text(x + 24, 1918, [title], size=18, weight=700))
        out.append(_svg_text(x + 24, 1954, _wrap_chars(value, 42)[:2], size=13, color=BLUE, weight=700, family="mono", line_height=18))

    if detail.real_result and detail.real_result.is_file():
        out.append(
            f'<a href="{_esc(detail.real_result.as_posix())}" target="_blank" rel="noopener" '
            f'aria-label="{("Open real result for " if english else "開啟 ")}{_esc(step.tool)}{("" if english else " 真實結果圖檔")}"><rect x="850" y="1650" width="640" height="48" rx="24" fill="#dcfce7" stroke="#86efac"/>'
        )
        out.append(_svg_text(1170, 1681, ["Open the real EP result ↗" if english else "開啟 EP 真實結果圖檔 ↗"], size=14, color=GREEN, weight=700, family="mono", anchor="middle"))
        out.append("</a>")

    legend = "Orange = preflight / quality warning   Blue = analysis / persistence   Green = real result link" if english else "橘色＝前置控制／品質警示   藍色＝分析／保存   綠色＝真實結果連結"
    out.append(_svg_text(55, 2082, [legend], size=13, color=WEAK, family="mono"))
    out.append(_svg_text(1545, 2082, ["Generated from EP method definitions · 2026-09-10"], size=12, color=WEAK, family="mono", anchor="end"))
    out.append("</svg>")
    return "".join(out)


def build_method_html(
    step: Any,
    domain: Any,
    detail: MethodDetail,
    svg: str,
    *,
    language: str = "zh-Hant",
) -> str:
    step, _, detail = _localized_method(step, domain, detail, language)
    english = language == "en"
    real_link = ""
    if detail.real_result and detail.real_result.is_file():
        real_link = (
            f'<a class="real" href="{_esc(detail.real_result.as_posix())}" target="_blank" rel="noopener">'
            f'{"Open real result" if english else "開啟真實結果"}：<code>{_esc(detail.real_result.name)}</code> ↗</a>'
        )
    lang_link = '<a href="index.html">繁體中文</a>' if english else '<a href="index.en.html">English</a>'
    back_label = "Back to EP analysis overview" if english else "返回 EP 分析總覽"
    mobile_hint = "Swipe horizontally to view the complete workflow →" if english else "左右滑動查看完整流程 →"
    note = (
        "The workflow reflects current EP tool definitions. Actual paths and timestamps depend on sample_id and deployment settings. Research figures are linked, not copied into Git."
        if english
        else "流程依 EP 現行工具定義整理；實際路徑與時間戳依 sample_id 和部署設定而定。研究圖只連結、不複製進 Git。"
    )
    return f'''<!DOCTYPE html>
<html lang="{language}">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{_esc(step.title)} — {_esc(step.tool)}</title>
  <style>
    *,*::before,*::after{{box-sizing:border-box}}
    body{{margin:0;background:#eef2f7;color:{INK};font-family:Geist,"PingFang TC","Noto Sans TC",system-ui,sans-serif}}
    main{{max-width:1160px;margin:0 auto;padding:28px 20px 64px}}
    nav{{display:flex;justify-content:space-between;gap:16px;align-items:center;margin-bottom:16px}}
    nav a,.real{{color:{BLUE};font-weight:700;text-decoration:none}}
    .sheet{{background:#fff;border:1px solid {BORDER};border-radius:24px;padding:20px;box-shadow:0 18px 50px rgba(15,23,42,.08);overflow:auto}}
    svg{{display:block;width:100%;min-width:780px;height:auto}}
    .note{{margin-top:18px;padding:16px 18px;border-left:6px solid {ORANGE};border-radius:12px;background:#fff7ed;color:#7c2d12;line-height:1.55}}
    .real{{display:block;margin-top:14px;padding:16px 18px;border:1px solid #86efac;border-radius:12px;background:#f0fdf4;color:{GREEN}}}
    code{{font-family:"SFMono-Regular",Consolas,monospace}}
    @media(max-width:800px){{main{{padding:10px}}.sheet{{border-radius:14px;padding:10px}}.sheet::before{{content:"{mobile_hint}";display:block;margin-bottom:8px;color:{MUTED};font-size:13px}}}}
  </style>
</head>
<body>
<main>
  <nav><a href="../../analysis_workflows.html">← {back_label}</a><code>{_esc(step.tool)}</code>{lang_link}</nav>
  <div class="sheet">{svg}</div>
  <div class="note"><strong>{"SCHEMATIC:" if english else "SCHEMATIC / 示意："}</strong>{note}</div>
{f"  {real_link}" if real_link else ""}
</main>
</body>
</html>
'''


def _font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in (
        Path("/Library/Fonts/Arial Unicode.ttf"),
        Path("/System/Library/Fonts/STHeiti Medium.ttc"),
    ):
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default(size=size)


def _wrap_pixels(draw: ImageDraw.ImageDraw, text: str, font_obj: ImageFont.ImageFont, width: int) -> list[str]:
    if " " in text and all(
        draw.textbbox((0, 0), word, font=font_obj)[2] <= width
        for word in text.split()
    ):
        lines: list[str] = []
        current = ""
        for word in text.split():
            probe = f"{current} {word}".strip()
            if current and draw.textbbox((0, 0), probe, font=font_obj)[2] > width:
                lines.append(current)
                current = word
            else:
                current = probe
        if current:
            lines.append(current)
        return lines
    lines: list[str] = []
    current = ""
    for char in text:
        probe = current + char
        if current and draw.textbbox((0, 0), probe, font=font_obj)[2] > width:
            lines.append(current)
            current = char
        else:
            current = probe
    if current:
        lines.append(current)
    return lines


def _draw_lines(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    font_obj: ImageFont.ImageFont,
    fill: str,
    width: int,
    *,
    max_lines: int = 3,
    gap: int = 5,
) -> int:
    lines = _wrap_pixels(draw, text, font_obj, width)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:-1] + "…"
    line_h = draw.textbbox((0, 0), "國Ag", font=font_obj)[3] + gap
    for line in lines:
        draw.text((x, y), line, font=font_obj, fill=fill)
        y += line_h
    return y


def _draw_arrow(draw: ImageDraw.ImageDraw, start: tuple[int, int], end: tuple[int, int], color: str) -> None:
    draw.line((*start, *end), fill=color, width=3)
    x, y = end
    if start[0] == end[0]:
        draw.polygon(((x, y), (x - 8, y - 14), (x + 8, y - 14)), fill=color)
    else:
        draw.polygon(((x, y), (x - 14, y - 8), (x - 14, y + 8)), fill=color)


def _draw_section(draw: ImageDraw.ImageDraw, number: int, label: str, y: int, height: int, fonts: dict[int, ImageFont.ImageFont]) -> None:
    draw.rounded_rectangle((45, y, 1555, y + height), radius=26, fill=PANEL, outline=BORDER, width=2)
    color = ORANGE if number < 4 else BLUE
    draw.ellipse((56, y + 12, 100, y + 56), fill=color)
    bbox = draw.textbbox((0, 0), str(number), font=fonts[17])
    draw.text((78 - (bbox[2] - bbox[0]) / 2, y + 23), str(number), font=fonts[17], fill="#ffffff")
    draw.text((112, y + 22), label, font=fonts[15], fill=WEAK)


def _draw_list_card(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    title: str,
    values: tuple[str, ...],
    accent: str,
    fonts: dict[int, ImageFont.ImageFont],
    footer: str | None = None,
) -> None:
    x, y, w, h = box
    draw.rounded_rectangle((x, y, x + w, y + h), radius=22, fill="#ffffff", outline=accent, width=2)
    draw.text((x + 26, y + 20), title, font=fonts[22], fill=INK)
    cursor = y + 64
    for value in values:
        cursor = _draw_lines(draw, x + 26, cursor, "• " + value, fonts[16], MUTED, w - 52, max_lines=2, gap=3) + 5
    if footer:
        draw.rounded_rectangle((x + 20, y + h - 78, x + w - 20, y + h - 20), radius=14, fill="#fff7ed", outline="#fed7aa")
        _draw_lines(draw, x + 36, y + h - 62, footer, fonts[13], "#9a3412", w - 72, max_lines=2, gap=2)


def build_method_png(
    path: Path,
    step: Any,
    domain: Any,
    detail: MethodDetail,
    *,
    language: str = "zh-Hant",
) -> None:
    step, domain, detail = _localized_method(step, domain, detail, language)
    english = language == "en"
    image = Image.new("RGB", (WIDTH, HEIGHT), PAPER)
    draw = ImageDraw.Draw(image)
    fonts = {size: _font(size) for size in (12, 13, 14, 15, 16, 17, 18, 20, 22, 40)}
    draw.text((55, 42), f"EVO-PRISM — {domain.title}", font=fonts[17], fill=BLUE)
    draw.text((55, 82), step.title, font=fonts[40], fill=INK)
    draw.text((55, 145), step.tool, font=fonts[20], fill=domain.color)
    _draw_lines(draw, 55, 184, domain.subtitle, fonts[16], MUTED, 1050, max_lines=2)
    pill_x, pill_center = (1190, 1367) if english else (1270, 1407)
    draw.rounded_rectangle((pill_x, 65, 1545, 119), radius=27, fill="#eff6ff", outline="#bfdbfe")
    bbox = draw.textbbox((0, 0), detail.resource, font=fonts[13])
    draw.text((pill_center - (bbox[2] - bbox[0]) / 2, 84), detail.resource, font=fonts[13], fill=BLUE)

    _draw_section(draw, 1, "INPUT & PREFLIGHT", 230, 330, fonts)
    _draw_list_card(draw, (78, 300, 700, 220), "Input data" if english else "輸入資料", step.inputs, domain.color, fonts)
    _draw_list_card(draw, (820, 300, 700, 220), "Preflight checks" if english else "前置條件與防呆", detail.preflight, ORANGE, fonts, detail.guard)

    _draw_section(draw, 2, "ANALYSIS PIPELINE", 590, 700, fonts)
    node_x, node_w, node_h = 280, 1040, 62
    count = len(detail.stages)
    gap = min(24, (600 - node_h * count) // max(1, count - 1))
    total = node_h * count + gap * (count - 1)
    start_y = 660 + (590 - total) // 2
    cx = node_x + node_w // 2
    for index in range(count - 1):
        y1 = start_y + index * (node_h + gap) + node_h
        y2 = start_y + (index + 1) * (node_h + gap) - 7
        _draw_arrow(draw, (cx, y1), (cx, y2), MUTED)
    for index, (title, description) in enumerate(detail.stages, 1):
        y = start_y + (index - 1) * (node_h + gap)
        accent = ORANGE if index == 1 else BLUE
        draw.rounded_rectangle((node_x, y, node_x + node_w, y + node_h), radius=18, fill="#ffffff", outline=accent, width=2)
        draw.ellipse((node_x + 15, y + 12, node_x + 53, y + 50), fill=accent)
        bbox = draw.textbbox((0, 0), str(index), font=fonts[14])
        draw.text((node_x + 34 - (bbox[2] - bbox[0]) / 2, y + 22), str(index), font=fonts[14], fill="#ffffff")
        draw.text((node_x + 70, y + 19), title, font=fonts[18], fill=INK)
        _draw_lines(draw, node_x + 390, y + 21, description, fonts[14], MUTED, 620, max_lines=1)

    _draw_section(draw, 3, "OUTPUTS & QUALITY", 1320, 450, fonts)
    _draw_list_card(draw, (78, 1390, 700, 330), "DATA · Files and reports" if english else "DATA · 數據與報告", step.data_outputs, BLUE, fonts)
    data_only = all(value.startswith("—") for value in step.figure_outputs)
    footer = None
    if detail.real_result and detail.real_result.is_file():
        footer = f"REAL RESULT · {detail.real_result.name}"
    elif data_only:
        footer = "DATA ONLY · This method does not directly create figures" if english else "DATA ONLY · 此方法目前不直接產生圖檔"
    _draw_list_card(draw, (820, 1390, 700, 330), "FIG · Figures" if english else "FIG · 圖檔", step.figure_outputs, GREEN if detail.real_result else BLUE, fonts, footer)

    _draw_section(draw, 4, "PERSISTENCE & RETRIEVAL", 1800, 245, fonts)
    image_name = f"{step.tool}{'.en' if english else ''}.png"
    persistence = (
        ("Analysis complete" if english else "完成分析", detail.history),
        ("Versioned method diagram" if english else "版本化方法圖", f"analysis_methods/{image_name}"),
        ("MCP retrieval" if english else "MCP 對話提取", f"method={step.tool} → figure_id"),
    )
    card_xs = (78, 585, 1092)
    for index in range(2):
        _draw_arrow(draw, (card_xs[index] + 430, 1935), (card_xs[index + 1] - 22, 1935), BLUE)
    for x, (title, value) in zip(card_xs, persistence, strict=True):
        draw.rounded_rectangle((x, 1880, x + 430, 1992), radius=20, fill="#ffffff", outline=BLUE, width=2)
        draw.text((x + 24, 1900), title, font=fonts[18], fill=INK)
        _draw_lines(draw, x + 24, 1940, value, fonts[13], BLUE, 382, max_lines=2, gap=2)

    legend = "Orange = preflight / quality warning   Blue = analysis / persistence   Green = real result link" if english else "橘色＝前置控制／品質警示   藍色＝分析／保存   綠色＝真實結果連結"
    draw.text((55, 2060), legend, font=fonts[13], fill=WEAK)
    image.save(path, format="PNG", optimize=True)


def generate_method_assets(root: Path, domains: tuple[Any, ...]) -> list[Path]:
    html_root = root / "docs" / "analysis_methods"
    image_root = root / "docs" / "images" / "analysis_methods"
    html_root.mkdir(parents=True, exist_ok=True)
    image_root.mkdir(parents=True, exist_ok=True)

    steps = {step.tool: (step, domain) for domain in domains for step in domain.steps}
    if set(steps) != set(METHOD_DETAILS) or set(steps) != set(ENGLISH_METHOD_DETAILS) or set(steps) != set(ENGLISH_STEPS):
        missing = sorted(set(steps) - set(METHOD_DETAILS))
        extra = sorted(set(METHOD_DETAILS) - set(steps))
        raise RuntimeError(
            f"method detail drift: missing={missing}, extra={extra}, "
            f"english_detail={sorted(set(steps) ^ set(ENGLISH_METHOD_DETAILS))}, "
            f"english_step={sorted(set(steps) ^ set(ENGLISH_STEPS))}"
        )

    written: list[Path] = []
    for method, (step, domain) in steps.items():
        detail = METHOD_DETAILS[method]
        svg = build_method_svg(step, domain, detail)
        method_dir = html_root / method
        method_dir.mkdir(parents=True, exist_ok=True)
        html_path = method_dir / "index.html"
        svg_path = image_root / f"{method}.svg"
        png_path = image_root / f"{method}.png"
        html_path.write_text(build_method_html(step, domain, detail, svg), encoding="utf-8")
        svg_path.write_text(svg, encoding="utf-8")
        build_method_png(png_path, step, domain, detail)
        written.extend((html_path, svg_path, png_path))

        english_svg = build_method_svg(step, domain, detail, language="en")
        english_html_path = method_dir / "index.en.html"
        english_svg_path = image_root / f"{method}.en.svg"
        english_png_path = image_root / f"{method}.en.png"
        english_html_path.write_text(
            build_method_html(
                step,
                domain,
                detail,
                english_svg,
                language="en",
            ),
            encoding="utf-8",
        )
        english_svg_path.write_text(english_svg, encoding="utf-8")
        build_method_png(
            english_png_path,
            step,
            domain,
            detail,
            language="en",
        )
        written.extend((english_html_path, english_svg_path, english_png_path))
    return written
