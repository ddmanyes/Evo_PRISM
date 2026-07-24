"""Tool Catalog —— MCP 與 Web UI 兩端 tool schema/dispatch 的單一真相來源。

2026-07-24 架構審查後建立（候選 1+3）：`server/bio_memory_server.py`（MCP 端）與
`server/agent.py`（Web UI 端）原本各自手刻 tool schema 與 dispatch，已經漂移
（Web UI 只有 21/44 個工具、descriptions 內容也各自不同）。本模組把 description、
JSON Schema、handler 對應關係收斂成一份，兩端都從這裡生成，不再各自維護。

每個工具恰好用兩種呼叫方式之一：
- **delegate**（`module`+`func`）：業務邏輯在獨立的 sync 函式（`agent*.py`），MCP 端用
  `asyncio.to_thread(fn, args)` 呼叫，Web UI 端直接同步呼叫，兩端共用同一份實作。
- **mcp_handler**：業務邏輯內嵌在 `bio_memory_server.py` 的 async handler 裡（無獨立
  sync 函式可 import）。Web UI 端用 `asyncio.run(handler(args))` 橋接既有 async handler，
  不搬動任何邏輯、不重複實作（sp-brainstorming 決策 7-A）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ToolSpec:
    description: str
    json_schema: dict
    dangerous: bool = False
    rate_limited: bool = False
    module: Optional[str] = None
    func: Optional[str] = None
    mcp_handler: Optional[str] = None

    def __post_init__(self) -> None:
        has_delegate = self.module is not None and self.func is not None
        has_mcp_handler = self.mcp_handler is not None
        if has_delegate == has_mcp_handler:
            raise ValueError(
                "ToolSpec 必須恰好指定 (module+func) 或 mcp_handler 其中一種呼叫方式"
            )


TOOL_CATALOG: dict[str, "ToolSpec"] = {
    "bio_artifact_search": ToolSpec(
        description="搜尋 ENGRAM 分析產出（圖、CSV、報告）— RRF hybrid（exact subtype + HNSW cosine）。"
                "回傳 artifact 列表含 score、file_path、artifact_subtype、analysis_id；不含檔案內容。"
                "需要 embedding server 在線（port 8081）；artifact_subtype 提供時走 Layer 1 + Layer 2 融合。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "自然語言查詢，例如「腫瘤微環境細胞密度圖」。",
                            },
                            "n": {
                                "type": "integer",
                                "description": "回傳筆數上限（預設 5）。",
                                "default": 5,
                            },
                            "threshold": {
                                "type": "number",
                                "description": "RRF 分數門檻（預設 0.01；範圍約 0.008–0.033）。",
                                "default": 0.01,
                            },
                            "artifact_subtype": {
                                "type": "string",
                                "description": "限定 subtype（Layer 1 exact match），例如 gene_spatial_map | qc_stats。",
                            },
                            "sample_id": {
                                "type": "string",
                                "description": "限定樣本 ID（可選，透過 JOIN analysis_history 過濾）。",
                            },
                        },
                        "required": ["query"],
                    },
        rate_limited=True,
        mcp_handler="_handle_bio_artifact_search",
    ),
    "bio_artifact_summary": ToolSpec(
        description="回傳指定樣本的 ENGRAM artifact 概覽（0 token，純 SQL）。"
                "顯示總執行次數、總 artifact 數、各 subtype 分佈、最新一次執行資訊。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "sample_id": {
                                "type": "string",
                                "description": "樣本 ID，例如 crc_official_v4。",
                            },
                        },
                        "required": ["sample_id"],
                    },
        mcp_handler="_handle_bio_artifact_summary",
    ),
    "bio_cascade_impact": ToolSpec(
        description="從某個分析出發，找出所有依賴它的下游分析（artifact lineage 反向走訪）。"
                "補充 bio_impact：bio_impact 回答「哪些分析用了舊工具」，"
                "bio_cascade_impact 回答「哪些下游分析依賴這個分析的產物、需要連帶重跑」。"
                "走訪 artifact_relations（derived_from 邊），深度上限 20 防環。"
                "典型場景：bulk_eda 重跑後，找出依賴它的 bulk_deg 與 bulk_enrichment。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "analysis_id": {
                                "type": "string",
                                "description": "起始分析的 UUID，例如 bio_history_check / bio_history_lookup 查到的 analysis_id。",
                            },
                        },
                        "required": ["analysis_id"],
                    },
        mcp_handler="_handle_bio_cascade_impact",
    ),
    "bio_check_l2_sufficiency": ToolSpec(
        description="確認樣本的 L2 Parquet 是否已就緒（l2_ready = true）。"
                "在執行 bio_run_spatial_eda 之前必須先呼叫；l2_ready=false 時回傳需要執行的轉換命令。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "sample_id": {
                                "type": "string",
                                "description": "樣本 ID，例如 crc_official_v4。",
                            },
                        },
                        "required": ["sample_id"],
                    },
        module="server.agent",
        func="_exec_bio_check_l2_sufficiency",
    ),
    "bio_compare_versions": ToolSpec(
        description="跨版本分析結果比對。工具從 vA 升到 vB 後，自動並排比較兩版的分析結果，"
                "回答「哪些樣本的結果真的改變了、需要重跑？」。"
                "補充 bio_impact（影響面）的量化依據：Jaccard/Spearman/Procrustes + verdict 決策表。"
                "Layer 0 顯示 source_hash 與 change_reason；"
                "Layer 1 找可比較分析對（含去重防笛卡兒積）；"
                "Layer 2 依 analysis_type 比對（DEG Jaccard/Spearman、enrichment Jaccard、EDA QC/PCA）；"
                "Layer 3 輸出 Agent 可直接使用的探索建議。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "tool_name": {
                                "type": "string",
                                "description": "要比對的工具名稱，例如 bio_run_deg。",
                            },
                            "version_a": {
                                "type": "string",
                                "description": "舊版本號，例如 1.0.0。",
                            },
                            "version_b": {
                                "type": "string",
                                "description": "新版本號，例如 1.1.0。",
                            },
                            "sample_id": {
                                "type": "string",
                                "description": "指定樣本 ID；不填則比對所有有記錄的樣本。",
                            },
                            "analysis_type": {
                                "type": "string",
                                "description": "進一步篩選 analysis_type（如 bulk_deg）；不填則不限。",
                            },
                        },
                        "required": ["tool_name", "version_a", "version_b"],
                    },
        mcp_handler="_handle_bio_compare_versions",
    ),
    "bio_compute_crc_metrics": ToolSpec(
        description="計算 MCseg 分割品質指標（CRC RNA metrics）：\n"
                "  FTC  — Tissue Capture Fraction（in-tissue bins 落在遮罩內的比例）\n"
                "  UMI Density — 中位 UMI/µm²（按細胞遮罩面積正規化）\n"
                "  NED  — Neighbor Expression Divergence（Hellinger）邊界銳利度\n"
                "  C1   — 譜系互斥共表達率（生物不可能基因對）\n"
                "  ENACT Precision — 可選，需提供 gt_centroids_csv\n\n"
                "輸入：bio_run_mcseg_roi 已執行完畢的 ROI 目錄\n"
                "（segmentation_masks.npy + cellpose_cells.h5ad + crop_meta.json）。\n"
                "結果寫入 analysis_history(crc_metrics) 並輸出 Markdown 報告。\n"
                "耗時約 1–5 分鐘（CPU only，不需 GPU）。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "sample_id": {
                                "type": "string",
                                "description": "樣本 ID，需已登記於 sample_registry。",
                            },
                            "roi_name": {
                                "type": "string",
                                "description": "ROI 名稱，對應 bio_run_mcseg_roi 使用的 roi_name。",
                            },
                            "roi_dir": {
                                "type": "string",
                                "description": "ROI 輸出目錄的絕對路徑。省略則自動推算 MCSEG_RESULTS_ROOT/<sample_id>/roi/<roi_name>。",
                            },
                            "roi_x": {
                                "type": "integer",
                                "description": "ROI 左上角 X (virtual_fullres px)。省略則從 crop_meta.json 讀取。",
                            },
                            "roi_y": {
                                "type": "integer",
                                "description": "ROI 左上角 Y (virtual_fullres px)。",
                            },
                            "roi_w": {
                                "type": "integer",
                                "description": "ROI 寬度 (virtual_fullres px)，預設 1500。",
                                "default": 1500,
                            },
                            "roi_h": {
                                "type": "integer",
                                "description": "ROI 高度 (virtual_fullres px)，預設 1500。",
                                "default": 1500,
                            },
                            "tp_parquet_path": {
                                "type": "string",
                                "description": "tissue_positions.parquet 路徑。省略則從 sample_registry.l3_path 自動解析。",
                            },
                            "impossible_pairs": {
                                "type": "array",
                                "description": "譜系互斥基因對清單，格式 [[geneA, geneB], ...]。省略則使用 CRC 預設（EPCAM/CD3E 等 4 對）。",
                                "items": {"type": "array", "items": {"type": "string"}},
                            },
                            "enact_gt_csv": {
                                "type": "string",
                                "description": "ENACT 專家標注質心 CSV 路徑（x_centroid, y_centroid 欄位）。提供則額外計算 GT Precision。",
                            },
                            "n_hvgs": {
                                "type": "integer",
                                "description": "NED 計算用的 HVG 數目，預設 1000。",
                                "default": 1000,
                            },
                            "requested_by": {
                                "type": "string",
                                "default": "mcp_client",
                            },
                        },
                        "required": ["sample_id", "roi_name"],
                    },
        rate_limited=True,
        module="server.agent_bulk",
        func="_exec_bio_compute_crc_metrics",
    ),
    "bio_compute_spatial_nn_distance": ToolSpec(
        description="任意兩群細胞的空間最近鄰距離（cKDTree），適用任何有 obsm['spatial'] "
                "的 AnnData h5ad。source_filter/target_filter 各自指定 obs 欄位+值來"
                "篩出兩群細胞。**強烈建議**在 h5ad 橫跨多個 capture area/切片時指定 "
                "group_by（例如 sample），否則會算出跨切片、沒有物理意義的距離。\n"
                "耗時數秒（CPU only，cKDTree）。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "sample_id": {"type": "string", "description": "樣本 ID，需已登記於 sample_registry。"},
                            "h5ad_path": {"type": "string", "description": "輸入 AnnData 絕對路徑（需在 BIO_DB_ROOT 底下）。"},
                            "source_filter": {
                                "type": "object",
                                "description": "來源細胞篩選，例如 {\"obs_column\": \"cell_type\", \"values\": [\"Fibroblast (Saa3+)\"]}。",
                                "properties": {
                                    "obs_column": {"type": "string"},
                                    "values": {"type": "array", "items": {"type": "string"}},
                                },
                                "required": ["obs_column", "values"],
                            },
                            "target_filter": {
                                "type": "object",
                                "description": "目標細胞篩選，格式同 source_filter。",
                                "properties": {
                                    "obs_column": {"type": "string"},
                                    "values": {"type": "array", "items": {"type": "string"}},
                                },
                                "required": ["obs_column", "values"],
                            },
                            "out_dir": {"type": "string", "description": "輸出目錄絕對路徑（需在 BIO_DB_ROOT 底下）。"},
                            "group_by": {"type": "string", "description": "強烈建議：依此 obs 欄位分組獨立計算距離（例如 sample）。"},
                            "spatial_key": {"type": "string", "default": "spatial", "description": "adata.obsm 裡座標欄位名稱。"},
                            "distance_scale": {
                                "type": "number", "default": 1.0,
                                "description": "距離換算係數（obsm['spatial'] 常常是像素，不是物理單位）。例如像素→µm 比例，可從 adata.obs 的 centroid_x_um / centroid_x_px 中位數算出。",
                            },
                            "distance_unit": {"type": "string", "description": "distance_scale 換算後的單位標籤（例如 \"µm\"），純粹顯示用。"},
                            "requested_by": {"type": "string", "default": "mcp_client"},
                        },
                        "required": ["sample_id", "h5ad_path", "source_filter", "target_filter", "out_dir"],
                    },
        rate_limited=True,
        module="server.agent_bulk",
        func="_exec_bio_compute_spatial_nn_distance",
    ),
    "bio_convert_ndpi_to_tiff": ToolSpec(
        description="將 Hamamatsu .ndpi 全片掃描影像轉換為 tile 化、金字塔結構的 BigTIFF，"
                "供 bio_run_mcseg_fullslide / bio_run_mcseg_roi / Loupe Browser 讀取。\n"
                "優先使用 libvips（快、低記憶體）；系統無 libvips 時降級用專案內建的 "
                "Bio-Formats bfconvert。輸出預設與輸入同資料夾、同檔名換副檔名，"
                "維持既有 L3 原地轉檔慣例，下游工具不需另外指定路徑。\n"
                "耗時視檔案大小而定（GB 級 NDPI 約數分鐘）。結果寫入 "
                "analysis_history(image_format_conversion)，並更新 sample_registry.notes。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "sample_id": {
                                "type": "string",
                                "description": "樣本 ID，需已登記於 sample_registry。",
                            },
                            "input_ndpi": {
                                "type": "string",
                                "description": "輸入 .ndpi 檔案的絕對路徑。",
                            },
                            "output_tiff": {
                                "type": "string",
                                "description": "輸出 .tiff 路徑（省略則沿用輸入路徑、換副檔名為 .tiff）。",
                            },
                            "compression": {
                                "type": "string",
                                "description": "壓縮格式：jpeg / lzw / deflate / none（預設 jpeg）。",
                                "default": "jpeg",
                            },
                            "quality": {
                                "type": "integer",
                                "description": "JPEG 壓縮品質 1-100（預設 85，僅 compression=jpeg 時生效）。",
                                "default": 85,
                            },
                            "tile_size": {
                                "type": "integer",
                                "description": "Tile 瓦片大小 px（預設 256）。",
                                "default": 256,
                            },
                        },
                        "required": ["sample_id", "input_ndpi"],
                    },
        rate_limited=True,
        module="server.agent_bulk",
        func="_exec_bio_convert_ndpi_to_tiff",
    ),
    "bio_execute_code": ToolSpec(
        description="沙盒執行動態生成的 Python 程式碼（用於非標準分析）。"
                "只允許白名單 import（duckdb 除外，pandas/numpy/scipy/anndata/scanpy 等）。"
                "禁止 os.system, subprocess, open(), eval, exec, glob.glob 等危險操作。"
                "timeout 預設 60 秒，最大 300 秒；rate-limited。"
                "⚠️ 高權限工具：預設**不對外暴露**。必須設定 env `MCP_ENABLE_DANGEROUS_TOOLS=true` 才會出現在 tools/list；"
                "同時建議搭配 `MCP_AUTH_TOKEN` 與 `MCP_BIND_HOST=127.0.0.1`。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "code": {
                                "type": "string",
                                "description": "要執行的 Python 程式碼。",
                            },
                            "description": {
                                "type": "string",
                                "description": "此程式碼的分析目的（用於 analysis_history 記錄）。",
                            },
                            "timeout": {
                                "type": "integer",
                                "description": "執行超時秒數（預設 60，最大 300）。",
                                "default": 60,
                            },
                        },
                        "required": ["code", "description"],
                    },
        dangerous=True,
        rate_limited=True,
        module="server.agent_history",
        func="_exec_bio_execute_code",
    ),
    "bio_export_loupe": ToolSpec(
        description="匯出 MCseg ROI 分割結果為 Loupe Browser 格式。"
                "必定產出：cells.geojson（細胞多邊形 + 標注）+ cell_metadata.csv。"
                "若已安裝 loupepy + 10x loupe_converter 則額外產出 .cloupe 檔案。"
                "需先完成 bio_run_mcseg_roi。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "sample_id": {"type": "string", "description": "樣本 ID"},
                            "roi_name": {"type": "string", "description": "ROI 名稱"},
                            "pixel_size_um": {
                                "type": "number",
                                "description": "像素物理尺寸（µm/px，預設 0.2737）",
                            },
                            "roi_dir": {"type": "string", "description": "ROI 目錄（省略則自動解析）"},
                        },
                        "required": ["sample_id", "roi_name"],
                    },
        rate_limited=True,
        module="server.agent_bulk",
        func="_exec_bio_export_loupe",
    ),
    "bio_failure_summary": ToolSpec(
        description="PM1 診斷彙整工具（EvolveMem Phase 13）。\n"
                "聚合 analysis_history.failure_diagnosis 欄位，統計各失敗類型的數量分佈，\n"
                "供 Agent 自我診斷並引導 HELIX 重構決策。\n"
                "failure type: cache_miss_semantic | wrong_tool_version | insufficient_context | "
                "L3_not_ready | hallucination | success\n"
                "可選擇按 sample_id、analysis_type 或時間範圍過濾。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "sample_id": {
                                "type": "string",
                                "description": "限定特定樣本，留空則統計所有樣本。",
                            },
                            "analysis_type": {
                                "type": "string",
                                "description": "限定分析類型（如 bulk_eda / eda_report / bulk_deg），留空則全部。",
                            },
                            "since_days": {
                                "type": "integer",
                                "description": "只計算最近 N 天的記錄，預設 30。",
                                "default": 30,
                            },
                            "top_n": {
                                "type": "integer",
                                "description": "回傳最頻繁失敗的前 N 個 detail 樣本，預設 5。",
                                "default": 5,
                            },
                        },
                        "required": [],
                    },
        mcp_handler="_handle_bio_failure_summary",
    ),
    "bio_find_tool": ToolSpec(
        description="語意搜尋既有可重用的分析函數（tool discovery）。"
                "寫 bio_execute_code 前務必先呼叫：描述分析意圖，回傳最相關的既有函數 "
                "+ 簽名 + import 方式。命中就 import 重用，勿從零重寫。"
                "本地 embedding + HNSW，0 LLM token；全 miss 才需自行撰寫。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "要做的分析意圖（自然語言）。",
                            },
                            "n": {
                                "type": "integer",
                                "description": "回傳候選數上限（預設 5）。",
                                "default": 5,
                            },
                        },
                        "required": ["query"],
                    },
        rate_limited=True,
        module="server.agent",
        func="_exec_bio_find_tool",
    ),
    "bio_get_artifact": ToolSpec(
        description="取得分析數據檔的取用 handle（任何 client 皆可用，含不支援 MCP resources 者）。"
                "回傳檔案 metadata + 本地絕對路徑 + web_app 下載 URL + 文字檔的前幾行預覽。"
                "用於：使用者想下載/取得分析產出的 csv/parquet/報告等數據檔。"
                "artifact_id 由 bio_artifact_search 取得。"
                "（支援 resources 的 client 可改用 resources/read artifact://<id> 直接取回內容。）",
        json_schema={
                        "type": "object",
                        "properties": {
                            "artifact_id": {
                                "type": "string",
                                "description": "artifact 的 UUID（來自 bio_artifact_search）。",
                            },
                            "preview_lines": {
                                "type": "integer",
                                "description": "文字檔預覽行數（預設 20）。",
                                "default": 20,
                            },
                        },
                        "required": ["artifact_id"],
                    },
        mcp_handler="_handle_bio_get_artifact",
    ),
    "bio_get_figure": ToolSpec(
        description="依 figure_id 取回單張圖片（MCP image content，供多模態模型視覺推理）。"
                "報告類工具回傳的文字裡，圖片以佔位符 [圖片:... | id=<figure_id> | 用 bio_get_figure 索取] 呈現——"
                "base64 已從文字 context 剝除以節省 token。需要看某張圖時，用該 figure_id 呼叫此工具單張取回。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "figure_id": {
                                "type": "string",
                                "description": "佔位符中的 id（hex），例如 a1b2c3d4e5f6。",
                            },
                        },
                        "required": ["figure_id"],
                    },
        mcp_handler="_handle_bio_get_figure",
    ),
    "bio_get_marker_genes": ToolSpec(
        description="對 bio_run_mcseg_roi 的 umap_computed.h5ad 執行 rank_genes_groups，"
                "匯出每個 cluster 的 top marker genes（CSV + inline 摘要表）。"
                "支援 groupby leiden 或 cell_type；需先完成 bio_run_mcseg_roi。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "sample_id": {"type": "string", "description": "樣本 ID"},
                            "roi_name": {"type": "string", "description": "ROI 名稱"},
                            "groupby": {"type": "string", "description": "分群欄位（預設 leiden）"},
                            "n_genes": {"type": "integer", "description": "每群 top-N genes（預設 20）"},
                            "method": {"type": "string", "description": "統計方法（預設 wilcoxon）"},
                            "roi_dir": {"type": "string", "description": "ROI 目錄（省略則自動解析）"},
                        },
                        "required": ["sample_id", "roi_name"],
                    },
        rate_limited=True,
        module="server.agent_bulk",
        func="_exec_bio_get_marker_genes",
    ),
    "bio_get_playbook": ToolSpec(
        description="取得某分析領域的『技能說明書』（標準步驟順序 + 每步該呼叫的函數 + 該產出的圖 + 品質關卡）。"
                "**執行任何領域分析（bulk / 空間 / mcseg）前先呼叫**，依說明書分步進行，確保每步出圖、不漏步。"
                "省略 domain 則列出所有可用說明書。省略 section 取完整說明書；指定 section 只取該段（省 token）。"
                "可用 section 名稱：overview / prerequisites / steps / template / appendix。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "domain": {
                                "type": "string",
                                "description": "說明書名稱或 data_type，如 bulk_rnaseq / spatial_visium / mcseg（省略則列出全部）",
                            },
                            "section": {
                                "type": "string",
                                "description": "只取特定段落（省 token）：overview / prerequisites / steps / template / appendix。省略取完整說明書。",
                            },
                        },
                        "required": [],
                    },
        module="server.agent_history",
        func="_exec_bio_get_playbook",
    ),
    "bio_history_check": ToolSpec(
        description="確認某樣本的某分析類型是否已有完成存檔（0 token，純 SQL）。"
                "回傳 True/False 及最新完成時間與結果路徑（若存在）。"
                "Agent 應在每次分析前呼叫此工具避免重複運算。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "sample_id": {
                                "type": "string",
                                "description": "樣本 ID，例如 crc_official_v4。",
                            },
                            "analysis_type": {
                                "type": "string",
                                "description": "分析類型，例如 spatial_eda。",
                            },
                            "format": {
                                "type": "string",
                                "enum": ["text", "json"],
                                "description": "回傳格式：text（YAML-like，預設）或 json。",
                                "default": "text",
                            },
                        },
                        "required": ["sample_id", "analysis_type"],
                    },
        mcp_handler="_handle_bio_history_check",
    ),
    "bio_history_lookup": ToolSpec(
        description="查詢樣本分析歷史（0 token，純 SQL）。"
                "回傳指定樣本的所有分析記錄，含分析類型、狀態、完成時間、摘要、結果路徑。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "sample_id": {
                                "type": "string",
                                "description": "樣本 ID，例如 crc_official_v4。若省略則回傳所有樣本。",
                            },
                            "analysis_type": {
                                "type": "string",
                                "description": "分析類型篩選，例如 spatial_eda。省略則回傳所有類型。",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "最多回傳筆數（預設 20）。",
                                "default": 20,
                            },
                            "format": {
                                "type": "string",
                                "enum": ["text", "json"],
                                "description": "回傳格式：text（Markdown 表格，預設）或 json（結構化字串，供客戶端解析）。",
                                "default": "text",
                            },
                        },
                        "required": [],
                    },
        mcp_handler="_handle_bio_history_lookup",
    ),
    "bio_history_search": ToolSpec(
        description="以自然語言語意搜尋 L1 語意快取（HNSW cosine）。"
                "只回傳 50 字 summary，不回傳完整報告，節省 token。"
                "需要 embedding server 在線（port 8081）。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "自然語言查詢，例如「PTPRC 在腫瘤微環境的空間分佈」。",
                            },
                            "n": {
                                "type": "integer",
                                "description": "回傳筆數上限（預設 5）。",
                                "default": 5,
                            },
                            "threshold": {
                                "type": "number",
                                "description": "相似度門檻 0~1（預設 0.88，對齊 agent.py Cache Hit Protocol L1_COSINE_THRESHOLD）。",
                                "default": 0.88,
                            },
                            "sample_id": {
                                "type": "string",
                                "description": "限定樣本 ID（可選）。",
                            },
                        },
                        "required": ["query"],
                    },
        rate_limited=True,
        mcp_handler="_handle_bio_history_search",
    ),
    "bio_history_timeline": ToolSpec(
        description="回傳最近 N 天的分析時間軸（0 token，純 SQL）。顯示誰在何時對哪個樣本做了什麼分析。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "n_days": {
                                "type": "integer",
                                "description": "往回查幾天（預設 7）。",
                                "default": 7,
                            },
                            "limit": {
                                "type": "integer",
                                "description": "回傳筆數上限（預設 50，最大 500）。n_days 大時可調高避免漏掉早期紀錄。",
                                "default": 50,
                            },
                            "format": {
                                "type": "string",
                                "enum": ["text", "json"],
                                "description": "回傳格式：text（Markdown 表格，預設）或 json。",
                                "default": "text",
                            },
                        },
                        "required": [],
                    },
        mcp_handler="_handle_bio_history_timeline",
    ),
    "bio_impact": ToolSpec(
        description="影響分析 / 爆炸範圍。改版/deprecate 工具或重跑/撤回樣本前,查會影響哪些分析與產物。"
                "每條影響邊帶 confidence(tool_id 精確 1.0 / 同分析 0.9 / analysis_type 啟發式 0.6)。"
                "恰好給一個目標:tool_name 或 artifact_id 或 sample_id。0 token 純 SQL,唯讀。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "tool_name": {"type": "string"},
                            "artifact_id": {"type": "string"},
                            "sample_id": {"type": "string"},
                        },
                        "required": [],
                    },
        module="server.agent",
        func="_exec_bio_impact",
    ),
    "bio_lookup_sample": ToolSpec(
        description="查詢樣本資訊：支援新 ID、舊 ID（alias）、模糊查詢及依 project/data_type 列出。"
                "可解答「這個 ID 是什麼樣本」、「舊名 ctrl_1_Hair_germ 對應哪個新 ID」等問題。"
                "若只需要整份清單，優先讀取 registry://snapshot resource 以節省 token。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": (
                                    "查詢字串：新 ID（HF01_HG_T0_R1）、舊 ID（ctrl_1_Hair_germ）"
                                    "或部分字串（fuzzy=true 時）。list_all=true 時可省略。"
                                ),
                            },
                            "fuzzy": {
                                "type": "boolean",
                                "description": "True 時用 LIKE 模糊匹配（預設 false）。",
                                "default": False,
                            },
                            "list_all": {
                                "type": "boolean",
                                "description": "True 時列出所有符合 project/data_type 的樣本（忽略 query）。",
                                "default": False,
                            },
                            "project": {
                                "type": "string",
                                "description": "限定 project，例如 hair_follicle_exp1、MQ250428。",
                            },
                            "data_type": {
                                "type": "string",
                                "description": "限定資料類型：bulk_rnaseq | visium | visium_hd | scrna。",
                            },
                        },
                    },
        mcp_handler="_handle_bio_lookup_sample",
    ),
    "bio_memory_query": ToolSpec(
        description="從 L1 語意快取取回完整報告（HNSW cosine ≥ 0.88 命中）。"
                "cache miss 時回傳空結果，Agent 應繼續呼叫分析工具。"
                "需要 embedding server 在線（port 8081）。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "自然語言查詢或分析參數描述。",
                            },
                            "sample_id": {
                                "type": "string",
                                "description": "限定樣本 ID（可選，可縮小搜尋範圍）。",
                            },
                            "threshold": {
                                "type": "number",
                                "description": "相似度門檻（預設使用 L1_COSINE_THRESHOLD = 0.88）。",
                            },
                        },
                        "required": ["query"],
                    },
        rate_limited=True,
        mcp_handler="_handle_bio_memory_query",
    ),
    "bio_memory_write": ToolSpec(
        description="將分析報告寫入 L1 語意快取（TTL 7 天）。"
                "分析完成後呼叫此工具，讓後續相似查詢可以直接命中快取。"
                "需要 embedding server 在線（port 8081）。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "sample_id": {
                                "type": "string",
                                "description": "樣本 ID，例如 crc_official_v4。",
                            },
                            "query_text": {
                                "type": "string",
                                "description": "代表此分析的查詢文字（用於 embedding，供語意搜尋命中）。",
                            },
                            "report_text": {
                                "type": "string",
                                "description": "完整報告 Markdown 文字。",
                            },
                            "summary": {
                                "type": "string",
                                "description": "≤50 字中文摘要（語意搜尋時展示）。",
                            },
                            "analysis_id": {
                                "type": "string",
                                "description": "對應 analysis_history 的 UUID（可選）。",
                            },
                        },
                        "required": ["sample_id", "query_text", "report_text", "summary"],
                    },
        rate_limited=True,
        mcp_handler="_handle_bio_memory_write",
    ),
    "bio_read_report": ToolSpec(
        description="讀取分析報告（.md/.txt/.log）原文。路徑必須位於 results/ 或 results_ana/ 內，"
                "其他路徑會被沙盒拒絕。超過 max_chars 時自動截斷為 head+tail 兩段。"
                "用於：使用者問「報告裡寫了什麼」「打開 xxx.md」等需要原文佐證的請求。"
                "禁止憑檔名推測內容——務必呼叫此工具取得真實文字。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "result_path": {
                                "type": "string",
                                "description": (
                                    "報告路徑。可絕對路徑或 BIO_DB_ROOT-relative，"
                                    "例如 results/bulk_eda/bulk_eda_xxx.md。"
                                ),
                            },
                            "max_chars": {
                                "type": "integer",
                                "description": "回傳字元數上限（預設 8000）。",
                                "default": 8000,
                            },
                            "head_fraction": {
                                "type": "number",
                                "description": "head 比例（預設 0.75，其餘為 tail）。",
                                "default": 0.75,
                            },
                        },
                        "required": ["result_path"],
                    },
        mcp_handler="_handle_bio_read_report",
    ),
    "bio_register_external_analysis_result": ToolSpec(
        description="登記在 EP 系統外部完成的任意類型分析結果（不限 mcseg，例如另一台機器跑的"
                "bulk RNA-seq/DEG/enrichment 等），只補寫 analysis_history + "
                "artifact_registry，不重新執行任何運算。\n"
                "⚠️ 只做路徑安全檢查（result_path/artifact_paths 都必須先複製到 BIO_DB_ROOT "
                "底下，否則容器讀不到），**不做內容層級交叉核對**（不會驗證 summary/params "
                "跟實際檔案內容是否吻合）——呼叫端要自己保證統計數字正確。\n"
                "如果是 mcseg 全片結果，優先用 bio_register_external_mcseg_result（會核對 "
                "n_cells 對不對得上 mask 檔案，更安全）。\n"
                "耗時數秒（純 DB 寫入，不含任何運算）。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "sample_id": {
                                "type": "string",
                                "description": "樣本 ID，需已登記於 sample_registry。",
                            },
                            "analysis_type": {
                                "type": "string",
                                "description": "分析類型自由文字，例如 bulk_deg、bulk_eda、enrichment、custom_analysis。",
                            },
                            "result_path": {
                                "type": "string",
                                "description": "主要輸出檔案的絕對路徑（需在 BIO_DB_ROOT 底下）。",
                            },
                            "summary": {
                                "type": "string",
                                "description": "人類可讀的一行摘要，原樣存入 analysis_history.summary。",
                            },
                            "params": {
                                "type": "object",
                                "description": "分析參數，存進 analysis_history.parameters 供之後查詢。",
                            },
                            "artifact_paths": {
                                "type": "array",
                                "description": "選填，要登記進 artifact_registry 的檔案清單。",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "path": {"type": "string", "description": "絕對路徑（需在 BIO_DB_ROOT 底下）"},
                                        "artifact_type": {"type": "string", "description": "預設 figure"},
                                        "label": {"type": "string", "description": "顯示名稱，預設用檔名"},
                                        "artifact_subtype": {"type": "string"},
                                    },
                                    "required": ["path"],
                                },
                            },
                            "requested_by": {
                                "type": "string",
                                "default": "mcp_client",
                            },
                            "supersedes_analysis_id": {
                                "type": "string",
                                "description": (
                                    "選填。登記某筆分析的修正版時傳入被取代那筆的 analysis_id，"
                                    "會把該筆標記 tags='superseded'、這筆新的標記 tags='canonical'，"
                                    "並記錄 parent_analysis_id 譜系。不傳則兩筆都是普通紀錄，"
                                    "查詢時無法自動分辨哪筆是最新正確版本。"
                                ),
                            },
                        },
                        "required": ["sample_id", "analysis_type", "result_path", "summary"],
                    },
        rate_limited=True,
        module="server.agent_bulk",
        func="_exec_bio_register_external_analysis_result",
    ),
    "bio_register_external_mcseg_result": ToolSpec(
        description="登記在 EP 系統外部完成的 mcseg 全片分割結果（例如另一台有 CUDA GPU 的機器、"
                "或修 bug 後手動補算的 host-native 結果），只補寫 analysis_history + "
                "artifact_registry，不重新執行任何運算。\n"
                "mask_path / mask_vfr_path / cells_path 都必須先複製到 BIO_DB_ROOT 底下"
                "（results/mcseg/<sample_id>/fullslide/，跟 bio_run_mcseg_fullslide 的輸出"
                "佈局一致），否則容器讀不到。n_cells 會跟 mask 檔案的實際 max label 交叉核對，"
                "不符會直接失敗（避免統計數字跟檔案對不上）。\n"
                "耗時數秒（純 DB 寫入，不含任何運算）。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "sample_id": {
                                "type": "string",
                                "description": "樣本 ID，需已登記於 sample_registry。",
                            },
                            "mask_path": {
                                "type": "string",
                                "description": "segmentation_masks_fullslide.npy 的絕對路徑（需在 BIO_DB_ROOT 底下）。",
                            },
                            "mask_vfr_path": {
                                "type": "string",
                                "description": "segmentation_masks_fullslide_vfr.npy 的絕對路徑（需在 BIO_DB_ROOT 底下）。",
                            },
                            "cells_path": {
                                "type": "string",
                                "description": "cellpose_cells_fullslide.h5ad 的絕對路徑（需在 BIO_DB_ROOT 底下）。",
                            },
                            "n_cells": {
                                "type": "integer",
                                "description": "細胞數（會跟 mask_path 實際 max label 交叉核對）。",
                            },
                            "n_bins_total": {
                                "type": "integer",
                                "description": "全片 2µm bin 總數。",
                            },
                            "n_bins_assigned": {
                                "type": "integer",
                                "description": "成功分配到細胞的 bin 數。",
                            },
                            "params": {
                                "type": "object",
                                "description": "分割參數（tile_size/overlap/use_cpsam 等），存進 analysis_history.parameters 供之後查詢。",
                            },
                            "notes": {
                                "type": "string",
                                "description": "來源說明（例如「CUDA 機器 XXX 跑的」「修 scanpy 缺套件後補算」），會併入 summary。",
                            },
                            "overlay_paths": {
                                "type": "array",
                                "description": "選填，H&E+mask overlay 圖的絕對路徑清單，會登記進 artifact_registry。",
                                "items": {"type": "string"},
                            },
                            "requested_by": {
                                "type": "string",
                                "default": "mcp_client",
                            },
                        },
                        "required": ["sample_id", "mask_path", "mask_vfr_path", "cells_path", "n_cells", "n_bins_total", "n_bins_assigned"],
                    },
        rate_limited=True,
        module="server.agent_bulk",
        func="_exec_bio_register_external_mcseg_result",
    ),
    "bio_register_sample": ToolSpec(
        description="登記新樣本至 sample_registry（L3 Bronze 目錄）。"
                "每個樣本只需登記一次。若 sample_id 已存在則回報並跳過。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "sample_id": {
                                "type": "string",
                                "description": "唯一樣本 ID，格式 {project}_{sample}（全小寫底線），例如 crc_official_v4。",
                            },
                            "data_type": {
                                "type": "string",
                                "description": "資料大類：visium_hd | visium | scrna | bulk_rnaseq | multiome | atac | proteomics | imaging | other",
                            },
                            "l3_path": {
                                "type": "string",
                                "description": "L3 原始數據絕對路徑（唯讀）。",
                            },
                            "project": {
                                "type": "string",
                                "description": "專案代號（可選），例如 crc_visium。",
                            },
                            "platform": {
                                "type": "string",
                                "description": "分析平台，例如 10x_visium_hd | cellranger（可選）。",
                            },
                            "species": {
                                "type": "string",
                                "description": "物種，例如 human | mouse（可選，預設 human）。",
                                "default": "human",
                            },
                            "tissue": {
                                "type": "string",
                                "description": "組織類型，例如 colon | liver（可選）。",
                            },
                            "notes": {
                                "type": "string",
                                "description": "備註（可選）。",
                            },
                        },
                        "required": ["sample_id", "data_type", "l3_path"],
                    },
        mcp_handler="_handle_bio_register_sample",
    ),
    "bio_relabel_clusters": ToolSpec(
        description="依 label_map 手動重標 MCseg ROI 的 cluster，寫入 cell_type_manual 欄位並重繪 UMAP。"
                "label_map 格式：{\"0\": \"Keratinocyte\", \"1\": \"Fibroblast\", ...}。"
                "未在 label_map 中的 cluster 保留原標籤。需先完成 bio_run_mcseg_roi。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "sample_id": {"type": "string", "description": "樣本 ID"},
                            "roi_name": {"type": "string", "description": "ROI 名稱"},
                            "label_map": {
                                "type": "object",
                                "description": "cluster ID（字串）→ 標籤名稱的對應",
                                "additionalProperties": {"type": "string"},
                            },
                            "groupby": {"type": "string", "description": "來源分群欄位（預設 leiden）"},
                            "roi_dir": {"type": "string", "description": "ROI 目錄（省略則自動解析）"},
                        },
                        "required": ["sample_id", "roi_name", "label_map"],
                    },
        rate_limited=True,
        module="server.agent_bulk",
        func="_exec_bio_relabel_clusters",
    ),
    "bio_run_bulk_eda": ToolSpec(
        description="對 Bulk RNA-seq 樣本集執行 EDA（QC 統計 + top genes + 樣本相關 + PCA）。"
                "完成後自動寫入 analysis_history。需要先執行 scripts/bulk_rna/ pipeline 產生 gene_counts.tsv。"
                "耗時約 10–60 秒；rate-limited（會寫 embedding）。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "sample_id": {
                                "type": "string",
                                "description": "樣本集 ID，例如 Kallisto_v1。",
                            },
                            "requested_by": {
                                "type": "string",
                                "description": "請求者（預設 mcp_client）。",
                                "default": "mcp_client",
                            },
                        },
                        "required": ["sample_id"],
                    },
        rate_limited=True,
        module="server.agent",
        func="_exec_bio_run_bulk_eda",
    ),
    "bio_run_celltypist": ToolSpec(
        description="用 CellTypist 預訓練模型自動標注 MCseg ROI 的細胞類型，"
                "結果寫入 celltypist_cell_type 欄位。"
                "注意：大多數模型為人類資料；小鼠樣本請確認基因匹配率。"
                "需先安裝 celltypist（uv add celltypist）且完成 bio_run_mcseg_roi。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "sample_id": {"type": "string", "description": "樣本 ID"},
                            "roi_name": {"type": "string", "description": "ROI 名稱"},
                            "model": {
                                "type": "string",
                                "description": "CellTypist 模型名稱（預設 Immune_All_Low.pkl）",
                            },
                            "majority_voting": {
                                "type": "boolean",
                                "description": "啟用 majority voting（預設 true）",
                            },
                            "roi_dir": {"type": "string", "description": "ROI 目錄（省略則自動解析）"},
                        },
                        "required": ["sample_id", "roi_name"],
                    },
        rate_limited=True,
        module="server.agent_bulk",
        func="_exec_bio_run_celltypist",
    ),
    "bio_run_deg": ToolSpec(
        description="Bulk RNA-seq DEG（DESeq2 via omicverse.pyDEG）+ 火山圖。對多組對照逐一跑，"
                "每組產出 DEG CSV + Volcano PNG，彙整報告寫 analysis_history(bulk_deg)。"
                "對齊 ddmanyes/bulk-rnaseq-pipeline。rate-limited。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "sample_id": {"type": "string"},
                            "counts_path": {"type": "string"},
                            "coldata_path": {"type": "string"},
                            "comparisons": {
                                "type": "array",
                                "items": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "minItems": 2,
                                    "maxItems": 2,
                                },
                                "minItems": 1,
                            },
                            "method": {"type": "string", "default": "DEseq2"},
                            "fc_threshold": {"type": "number", "default": 1.0},
                            "pval_threshold": {"type": "number", "default": 0.05},
                            "requested_by": {"type": "string", "default": "mcp_client"},
                        },
                        "required": ["sample_id", "counts_path", "coldata_path", "comparisons"],
                    },
        rate_limited=True,
        module="server.agent",
        func="_exec_bio_run_deg",
    ),
    "bio_run_enrichment": ToolSpec(
        description="對 DEG 表跑 ORA（gseapy.enrichr 線上）。up/down × N library(GO/KEGG/Reactome)，"
                "輸出 CSV + dot plot，寫 analysis_history(bulk_enrichment)。需網路。rate-limited。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "sample_id": {"type": "string"},
                            "deg_table_path": {"type": "string"},
                            "libraries": {"type": "array", "items": {"type": "string"}},
                            "organism": {"type": "string", "default": "human"},
                            "fc_threshold": {"type": "number", "default": 1.0},
                            "pval_threshold": {"type": "number", "default": 0.05},
                            "top_term": {"type": "integer", "default": 10},
                            "requested_by": {"type": "string", "default": "mcp_client"},
                        },
                        "required": ["sample_id", "deg_table_path"],
                    },
        rate_limited=True,
        module="server.agent",
        func="_exec_bio_run_enrichment",
    ),
    "bio_run_geneset_score": ToolSpec(
        description="任意基因模組評分（scanpy score_genes 封裝），適用任何 AnnData h5ad"
                "（例如 mcseg 產出的細胞層級結果）。輸入 {模組名: [基因清單]} 字典自訂,"
                "不綁定特定生物情境（可用於 M1/M2 巨噬極化、代謝分數等任意 gene-set"
                "評分）。可選 cell_filter 先篩子集再評分。\n"
                "有 layers['counts'] 時會用原始 counts 重新正規化再評分（子集後重新"
                "正規化比沿用子集前的正規化更正確）；沒有的話直接用現有 .X。\n"
                "耗時數秒到數分鐘（視細胞數/模組數，CPU only）。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "sample_id": {"type": "string", "description": "樣本 ID，需已登記於 sample_registry。"},
                            "h5ad_path": {"type": "string", "description": "輸入 AnnData 絕對路徑（需在 BIO_DB_ROOT 底下）。"},
                            "modules": {
                                "type": "object",
                                "description": "{模組名: [基因清單]}，例如 {\"M1\": [\"Il1b\",\"Cd86\"], \"M2\": [\"Mrc1\",\"Cd163\"]}。",
                                "additionalProperties": {"type": "array", "items": {"type": "string"}},
                            },
                            "out_dir": {"type": "string", "description": "輸出目錄絕對路徑（需在 BIO_DB_ROOT 底下）。"},
                            "cell_filter": {
                                "type": "object",
                                "description": "選填，先篩子集再評分。例如 {\"obs_column\": \"cell_type\", \"values\": [\"Macrophage\"]}。",
                                "properties": {
                                    "obs_column": {"type": "string"},
                                    "values": {"type": "array", "items": {"type": "string"}},
                                },
                            },
                            "group_by": {"type": "string", "description": "選填，輸出表格附帶的分組欄位（例如 day/sample），不影響評分本身。"},
                            "ctrl_size": {"type": "integer", "default": 50, "description": "score_genes 的對照基因集大小。"},
                            "counts_layer": {
                                "type": "string", "default": "counts",
                                "description": "adata.layers 裡原始 counts 的欄位名稱，篩子集後會用這層重新正規化再評分。不存在的話 fallback 用現有 .X（會 log warning）。設成空字串直接跳過查找、一律用 .X。",
                            },
                            "requested_by": {"type": "string", "default": "mcp_client"},
                        },
                        "required": ["sample_id", "h5ad_path", "modules", "out_dir"],
                    },
        rate_limited=True,
        module="server.agent_bulk",
        func="_exec_bio_run_geneset_score",
    ),
    "bio_run_heatmaps": ToolSpec(
        description="Bulk RNA 兩張熱圖：union DEG 顯著基因 + top N 變異基因，皆 z-score + sns.clustermap。"
                "寫 analysis_history(bulk_heatmap)。rate-limited。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "sample_id": {"type": "string"},
                            "counts_path": {"type": "string"},
                            "deg_tables": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                            "top_n": {"type": "integer", "default": 50},
                            "fc_threshold": {"type": "number", "default": 1.0},
                            "pval_threshold": {"type": "number", "default": 0.05},
                            "requested_by": {"type": "string", "default": "mcp_client"},
                        },
                        "required": ["sample_id", "counts_path", "deg_tables"],
                    },
        rate_limited=True,
        module="server.agent",
        func="_exec_bio_run_heatmaps",
    ),
    "bio_run_mcseg_fullslide": ToolSpec(
        description="對 Visium HD 樣本執行全片 tiled MCseg 分割（不含 Scanpy downstream）：\n"
                "  Stage 0 — BTF/TIFF 全圖讀取\n"
                "  Stage 1 — run_tiled_mcseg_v2（tile=1024px，overlap=128px，7-pass ensemble）\n"
                "  Stage 2 — 全片 2µm bin RNA 計數\n"
                "  輸出：segmentation_masks.npy / .tif、bin attribution h5ad、overlay PNG\n"
                "結果寫入 analysis_history(mcseg_fullslide)。耗時數小時（GPU）。\n"
                "⚠️ 全片細胞數可能超過 10 萬，downstream Scanpy 需另行分批執行。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "sample_id": {
                                "type": "string",
                                "description": "樣本 ID，需已登記於 sample_registry。",
                            },
                            "tile_size": {
                                "type": "integer",
                                "description": "分割 tile 大小（px），預設 1024。",
                                "default": 1024,
                            },
                            "overlap": {
                                "type": "integer",
                                "description": "Tile 重疊像素，預設 128。",
                                "default": 128,
                            },
                            "use_cpsam": {
                                "type": "boolean",
                                "description": "是否啟用 cpsam（7-pass）。預設 true。",
                                "default": True,
                            },
                            "btf_image_path": {
                                "type": "string",
                                "description": "BTF/TIFF H&E 全圖路徑（省略則從 sample_registry 解析）。",
                            },
                            "binned_dir": {
                                "type": "string",
                                "description": "Visium HD binned_outputs 目錄路徑。",
                            },
                            "output_base": {
                                "type": "string",
                                "description": "輸出根目錄。",
                            },
                            "requested_by": {
                                "type": "string",
                                "default": "mcp_client",
                            },
                        },
                        "required": ["sample_id"],
                    },
        rate_limited=True,
        module="server.agent_bulk",
        func="_exec_bio_run_mcseg_fullslide",
    ),
    "bio_run_mcseg_merge": ToolSpec(
        description="合併多個 MCseg ROI 的 cellpose_cells.h5ad，執行整合 Scanpy 管線"
                "（normalize → HVG → PCA → harmony/bbknn → leiden → UMAP）。"
                "integrate 可選 auto/harmony/bbknn/none；auto 依安裝狀況自動選擇。"
                "需先對每個 ROI 完成 bio_run_mcseg_roi。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "sample_id": {"type": "string", "description": "樣本 ID"},
                            "roi_names": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "要合併的 ROI 名稱清單（至少 2 個）",
                            },
                            "merged_name": {"type": "string", "description": "合併結果的識別名稱"},
                            "integrate": {
                                "type": "string",
                                "description": "整合策略：auto/harmony/bbknn/none（預設 auto）",
                            },
                            "output_base": {"type": "string", "description": "輸出根目錄（省略則自動）"},
                        },
                        "required": ["sample_id", "roi_names", "merged_name"],
                    },
        rate_limited=True,
        module="server.agent_bulk",
        func="_exec_bio_run_mcseg_merge",
    ),
    "bio_run_mcseg_qc": ToolSpec(
        description="MCseg 細胞分割品質視覺化（讀既有 .npy 遮罩，**不**即時重跑分割）。"
                "掃 qc_dir 內成對的 *_nuc.npy / *_mcseg.npy，產出 NUC vs MCseg 對比圖 + "
                "細胞面積分布 + 量化表，寫入 analysis_history（analysis_type=mcseg_qc）。"
                "先 bio_get_playbook(mcseg) 取方法學。需先有分割輸出檔。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "sample_id": {"type": "string", "description": "樣本 ID"},
                            "qc_dir": {
                                "type": "string",
                                "description": "分割遮罩目錄（省略則用預設 results/mcseg_qc/）",
                            },
                        },
                        "required": ["sample_id"],
                    },
        module="server.agent_bulk",
        func="_exec_bio_run_mcseg_qc",
    ),
    "bio_run_mcseg_roi": ToolSpec(
        description="對 Visium HD 樣本執行單一 ROI 的完整 MCseg 分析管線：\n"
                "  Stage 0 — BTF/TIFF H&E ROI 裁切（自動計算 virtual_fullres↔TIFF 座標縮放比）\n"
                "  Stage 1 — 7-Pass Cellpose 集成分割（cyto3×4 + cpsam×3，tile=1024px，RTX 4090）\n"
                "  Stage 2 — 2µm bin RNA 計數（mask→bin attribution）\n"
                "  Stage 3 — Scanpy QC / normalization / HVG / UMAP / Leiden clustering\n"
                "  Stage 4 — 基因 score 細胞類型標注\n"
                "  Stage 5 — NED 邊界銳利度 + 空間 niche + permutation test\n"
                "  Stage 6 — UMAP 圖、dotplot、Xenium Explorer bundle 匯出\n"
                "  Stage 7 — H&E overlay 圖（細胞類型著色 + 邊界版）\n"
                "結果寫入 analysis_history(mcseg_roi)。耗時約 30–90 分鐘（GPU）。\n"
                "btf_image_path / binned_dir / output_base 省略時從 sample_registry 自動解析。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "sample_id": {
                                "type": "string",
                                "description": "樣本 ID，需已登記於 sample_registry。",
                            },
                            "roi_x": {
                                "type": "integer",
                                "description": "ROI 左上角 X 座標（virtual_fullres px）。",
                            },
                            "roi_y": {
                                "type": "integer",
                                "description": "ROI 左上角 Y 座標（virtual_fullres px）。",
                            },
                            "roi_width_px": {
                                "type": "integer",
                                "description": "ROI 寬度（virtual_fullres px，預設 1500）。",
                                "default": 1500,
                            },
                            "roi_height_px": {
                                "type": "integer",
                                "description": "ROI 高度（virtual_fullres px，預設 1500）。",
                                "default": 1500,
                            },
                            "roi_name": {
                                "type": "string",
                                "description": "ROI 識別名稱（用於輸出目錄），省略時自動生成。",
                            },
                            "use_cpsam": {
                                "type": "boolean",
                                "description": "是否啟用 cpsam（7-pass）；false 則 4-pass cyto3 only。預設 true。",
                                "default": True,
                            },
                            "btf_image_path": {
                                "type": "string",
                                "description": "BTF/TIFF H&E 全圖路徑（省略則從 sample_registry.l3_path 解析）。",
                            },
                            "binned_dir": {
                                "type": "string",
                                "description": "Visium HD binned_outputs 目錄路徑（省略則從 sample_registry 解析）。",
                            },
                            "output_base": {
                                "type": "string",
                                "description": "輸出根目錄（省略則用 I:/Evo_PRISM/visium_hd_results/<sample_id>）。",
                            },
                            "requested_by": {
                                "type": "string",
                                "default": "mcp_client",
                            },
                        },
                        "required": ["sample_id", "roi_x", "roi_y"],
                    },
        rate_limited=True,
        module="server.agent_bulk",
        func="_exec_bio_run_mcseg_roi",
    ),
    "bio_run_sc_clustering": ToolSpec(
        description="任意單細胞 h5ad 的 QC 過濾 + Leiden clustering + UMAP（標準 scanpy 管線："
                "normalize→log1p→HVG→PCA→neighbors→leiden→umap），並輸出每個 cluster 的 "
                "top marker genes（wilcoxon rank_genes_groups）供人工判讀。"
                "可選 marker_gene_sets 對每個 cluster 做 score_genes-based 自動細胞型別評分"
                "（cluster 層級 argmax，非 cell 層級）——沒給就只輸出 marker genes，"
                "細胞型別對照表刻意不寫死。\n"
                "天然銜接 bio_run_mcseg_fullslide/roi 的 RNA counting 輸出"
                "（cellpose_cells_fullslide.h5ad / cellpose_cells.h5ad，都還沒經過任何 "
                "QC/clustering）。若輸入有 obs['n_bins'] 欄位，可用 min_bins 篩掉沒有 "
                "任何 Visium bin 對應到的細胞（n_bins=0，無 RNA 訊號）。\n"
                "耗時數十秒到數分鐘（視細胞數，CPU only）。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "sample_id": {"type": "string", "description": "樣本 ID，需已登記於 sample_registry。"},
                            "h5ad_path": {"type": "string", "description": "輸入 AnnData 絕對路徑（原始 counts 在 .X，需在 BIO_DB_ROOT 底下）。"},
                            "out_dir": {"type": "string", "description": "輸出目錄絕對路徑（需在 BIO_DB_ROOT 底下）。"},
                            "min_counts": {"type": "integer", "description": "QC 硬門檻：每細胞最低 UMI 數。省略則用 qc_percentile 自動抓。"},
                            "min_genes": {"type": "integer", "description": "QC 硬門檻：每細胞最低基因數。省略則用 qc_percentile 自動抓。"},
                            "qc_percentile": {"type": "integer", "default": 10, "description": "min_counts/min_genes 省略時，取觀測分布此百分位數當門檻（下限 counts≥50, genes≥20）。"},
                            "min_bins": {
                                "type": "integer", "default": 0,
                                "description": "篩掉 obs['n_bins'] 小於此值的細胞（僅當該欄位存在時適用；0=不篩，mcseg 輸出常見要篩掉 n_bins=0 的無訊號細胞）。",
                            },
                            "n_top_genes": {"type": "integer", "default": 1000, "description": "HVG 數量（PCA 輸入）。"},
                            "n_pcs": {"type": "integer", "default": 15, "description": "PCA 主成分數（自動裁切到 n_obs/n_vars 允許範圍）。"},
                            "n_neighbors": {"type": "integer", "default": 15, "description": "sc.pp.neighbors 的鄰居數。"},
                            "resolution": {"type": "number", "default": 0.5, "description": "Leiden clustering resolution。"},
                            "n_top_markers": {"type": "integer", "default": 10, "description": "每個 cluster 輸出的 top marker gene 數。"},
                            "marker_gene_sets": {
                                "type": "object",
                                "description": "選填，{細胞型別: [基因清單]}，例如 {\"Macrophage\": [\"Adgre1\",\"Cd68\"], \"Fibroblast\": [\"Col1a1\",\"Pdgfra\"]}。給了才會做 cluster 層級自動細胞型別標注。",
                                "additionalProperties": {"type": "array", "items": {"type": "string"}},
                            },
                            "score_threshold": {"type": "number", "default": 0.05, "description": "marker_gene_sets 評分低於此值時標成 unassigned_label（而非最高分模組）。"},
                            "unassigned_label": {"type": "string", "default": "Unassigned", "description": "cluster 分數都不夠高時的標籤。"},
                            "requested_by": {"type": "string", "default": "mcp_client"},
                        },
                        "required": ["sample_id", "h5ad_path", "out_dir"],
                    },
        rate_limited=True,
        module="server.agent_bulk",
        func="_exec_bio_run_sc_clustering",
    ),
    "bio_run_spatial_eda": ToolSpec(
        description="對指定樣本執行空間轉錄體 EDA（QC 統計 + top genes + 報告生成）。"
                "完成後自動寫入 analysis_history + L1 快取。需要 L2 Parquet 已轉換（l2_ready = true）。"
                "耗時約 10–30 秒；rate-limited（會寫 embedding）。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "sample_id": {
                                "type": "string",
                                "description": "樣本 ID，例如 crc_official_v4。",
                            },
                            "requested_by": {
                                "type": "string",
                                "description": "請求者（預設 mcp_client）。",
                                "default": "mcp_client",
                            },
                        },
                        "required": ["sample_id"],
                    },
        rate_limited=True,
        module="server.agent",
        func="_exec_bio_run_spatial_eda",
    ),
    "bio_sample_compare": ToolSpec(
        description="比較兩個或多個樣本的分析歷史摘要，回傳各樣本最新各類型分析的摘要對照表。"
                "協助判斷不同樣本的分析狀態差異，無需閱讀完整報告。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "sample_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "要比較的樣本 ID 列表（2 個以上）",
                            },
                        },
                        "required": ["sample_ids"],
                    },
        module="server.agent_history",
        func="_exec_bio_sample_compare",
    ),
    "bio_sample_list": ToolSpec(
        description="列出 sample_registry 中已登記的樣本（0 token，純 SQL）。"
                "支援 data_type / tissue / condition 過濾，方便快速瀏覽現有資料集。",
        json_schema={
                        "type": "object",
                        "properties": {
                            "data_type": {
                                "type": "string",
                                "description": "資料類型篩選（可選，如 visium_hd / bulk_rnaseq）",
                            },
                            "tissue": {"type": "string", "description": "組織類型篩選（可選，模糊比對）"},
                            "condition": {
                                "type": "string",
                                "description": "樣本條件篩選（可選，對應 notes 欄位模糊比對）",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "最多回傳筆數（預設 50）",
                                "default": 50,
                            },
                        },
                        "required": [],
                    },
        module="server.agent_history",
        func="_exec_bio_sample_list",
    ),
    "bio_tool_health": ToolSpec(
        description="HELIX 工具庫健康報告與穩定化迭代管理。支援六個 action：\n"
                "  'report'          — 健康狀態總覽（active/deprecated/熱區/進行中迭代/VLM 快照）\n"
                "  'diagnose'        — 寫入 stability_note（需 tool_name + note）\n"
                "  'stabilize'       — 開啟穩定化迭代（需 tool_name + diagnosis + action_taken）\n"
                "  'close_stabilize' — 關閉迭代（需 log_id + outcome；outcome: stabilized/ongoing/reverted）\n"
                "  'trend'           — 複雜度改善趨勢（可選 tool_name 過濾）\n"
                "  'prune'           — 清理未被引用的 deprecated 紀錄（需 tool_name）",
        json_schema={
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": [
                                    "report",
                                    "diagnose",
                                    "stabilize",
                                    "close_stabilize",
                                    "trend",
                                    "prune",
                                ],
                                "description": "操作類型。",
                            },
                            "tool_name": {
                                "type": "string",
                                "description": "diagnose/stabilize/prune 時必填。",
                            },
                            "note": {
                                "type": "string",
                                "description": "diagnose 時必填：說明為何頻繁變動及穩定化方向。",
                            },
                            "diagnosis": {
                                "type": "string",
                                "description": "stabilize 時必填：問題診斷描述。",
                            },
                            "action_taken": {
                                "type": "string",
                                "description": "stabilize 時必填：計畫採取的行動。",
                            },
                            "log_id": {
                                "type": "string",
                                "description": "close_stabilize 時必填：open_stabilization 回傳的 UUID。",
                            },
                            "outcome": {
                                "type": "string",
                                "enum": ["stabilized", "ongoing", "reverted"],
                                "description": "close_stabilize 時必填：迭代結果。",
                            },
                        },
                        "required": ["action"],
                    },
        module="server.agent",
        func="_exec_bio_tool_health",
    ),
}
