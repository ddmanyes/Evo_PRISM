---
name: mcseg
version: 2.1.0
data_type: imaging
when_to_use: |
  Visium HD 空間轉錄組 H&E 影像細胞分割與下游分析。適用情境：
  - 「幫我對某個 ROI 做細胞分割分析」→ bio_run_mcseg_roi
  - 「做全片分割」→ bio_run_mcseg_fullslide
  - 「看分割品質 / NUC vs MCseg 比較」→ bio_run_mcseg_qc
  - 「看 HE 上的遮罩分布圖」→ overlay 視覺化
agent_tools:
  - bio_run_mcseg_roi
  - bio_run_mcseg_fullslide
  - bio_run_mcseg_qc
---

# MCseg 分割說明書（Visium HD）

定義 MCseg 7-Pass Cellpose 集成分割的**完整執行流程**與**品質評估**。  
分割依賴 MSseg 原專案（`K:/plan_a/MSseg`）+ RTX 4090 GPU；  
座標橋接與 downstream 分析由 `analysis/mcseg_wrapper.py` 統一處理。

---

## 重要：Visium HD 雙座標系統

Visium HD 有兩個像素座標空間，**必須正確對應否則 ROI 裁切完全空白**：

| 座標空間 | 解析度 | 來源 | 用途 |
|---------|--------|------|------|
| **virtual_fullres** | ~16,461×40,560 px（0.4201 µm/px） | `tissue_positions.parquet` 的 `pxl_col/row_in_fullres` | ROI 定義、bin attribution |
| **raw TIFF** | ~25,600×62,464 px（0.2737 µm/px） | 原始 BTF/TIFF 檔案 | H&E 影像讀取、Cellpose 輸入 |

**Scale factor 計算**（自動，由 `_compute_tiff_scale()` 處理）：
```
tiff_scale = avg(W_TIFF / W_vfr,  H_TIFF / H_vfr)  ≈ 1.5476
ROI 座標傳入時用 virtual_fullres px → 乘以 tiff_scale → 換算為 TIFF px 裁切
mask 輸出後再 nearest-neighbor downscale 回 virtual_fullres（供 bin attribution 用）
```

計算依據：`tissue_hires_scalef`（from `scalefactors_json.json`）+ `tissue_hires_image.png` 尺寸。

---

## 工具 A：bio_run_mcseg_roi（單 ROI 完整管線）

**耗時**：30–90 分鐘（GPU），Stage 1 最長  
**必要參數**：`sample_id`, `roi_x`, `roi_y`（virtual_fullres px）  
**選填**：

| 參數 | 預設 | 說明 |
| --- | --- | --- |
| `roi_width_px` | 1500 | ROI 寬度（virtual_fullres px） |
| `roi_height_px` | 1500 | ROI 高度（virtual_fullres px） |
| `roi_name` | `roi_<x>_<y>` | 輸出目錄識別名稱 |
| `use_cpsam` | `true` | false → 4-pass only；true → 7-pass（含 cpsam） |
| `btf_image_path` | *(auto)* | BTF/TIFF 全圖路徑；省略則從 `sample_registry.l3_path` 自動解析 |
| `binned_dir` | *(auto)* | Visium HD `binned_outputs` 目錄；省略則同上自動解析 |
| `output_base` | *(auto)* | 輸出根目錄；省略則用 `MCSEG_RESULTS_ROOT/<sample_id>` |

> **⚠️ `pixel_size_um` 限制**：RNA 計數的像素物理尺寸固定為 **0.2737 µm/px**（TIFF 解析度），目前 MCP 工具未開放此參數，僅適用 10x Visium HD SDS-D0D1D2 數據集。若分析其他解析度的樣本需修改 `agent_bulk.py`。

### Stage 0 — ROI 裁切

- 函數：`mcseg_wrapper.crop_visium_hd_roi`
- 從 BTF 讀取 H&E（tile-based，避免全圖載入）
- 自動計算 tiff_scale 並存入 `crop_meta.json`
- 輸出：`he_crop.tif`（TIFF 解析度）、`adata_002um.h5ad`（虛擬 fullres 座標）

**品質關卡**：`he_crop.tif` 必須有組織（非純白），否則座標設定錯誤

### Stage 1 — 7-Pass MCseg 集成分割

- 函數：`mcseg_wrapper.run_mcseg_segmentation` → `cellpose_runner.run_tiled_mcseg_v2`
- **7-pass（預設，use_cpsam=true）**：
  1. cyto3 × RGB-CLAHE，dia=17px（mid）
  2. cyto3 × RGB-CLAHE，dia=13px（small，cellprob 更寬鬆）
  3. cyto3 × RGB-CLAHE，dia=22px（large）
  4. cyto3 × Hematoxylin 通道
  5. cpsam × RGB-CLAHE，dia=auto（~30px）
  6. cpsam × RGB-CLAHE，dia=16px
  7. cpsam × Hematoxylin
- **4-pass（use_cpsam=false）**：只跑上方 Pass 1–4，省略 cpsam
- 合併策略：`merge_masks_fast`（重疊 < 15% 才納入）
- 後處理：clean_mask → Voronoi 擴張（max 9px）→ relabel_sequential
- 輸出：`segmentation_masks.npy`（int32）、`segmentation_masks.tif`（uint16/uint32）
- mask 自動 downscale 回 virtual_fullres（1500×1500）供後續使用

**品質關卡**：`mask.max()` = 細胞數，應 > 0；若 = 0 代表裁切區域無組織

### Stage 2 — RNA 計數

- 函數：`mcseg_wrapper.run_rna_counting`
- 將 2µm bins（`pxl_col/row_in_fullres`）mapping 到 mask 細胞 ID
- 輸出：`cellpose_cells.h5ad`（細胞 × 基因矩陣）

> **注意**：Stages 3–7 透過 `subprocess.run()` 委派至 `scratch/run_visium_hd_showcase.py` 執行（timeout 7200 s），而非在 MCP server 進程內直接執行。子進程的 stdout/stderr 不即時串流至工具回傳值，失敗時需查看 `<roi_dir>/` 下的日誌或錯誤訊息。

### Stage 3 — Scanpy 下游分析

- QC（adaptive p10 threshold）：
  ```
  MIN_COUNTS = max(50, p10(total_counts))
  MIN_GENES  = max(20, p10(n_genes_by_counts))
  ```
- `sc.pp.filter_genes(adata, min_cells=3)`（移除極稀疏基因）
- **提前終止**：若 QC 後 `n_obs < 15`，Stage 3–7 全部中止並回傳告警；ROI 組織過少或裁切有誤
- normalization → log1p → HVG 選取 → PCA → Leiden clustering → UMAP
- 輸出：`umap_computed.h5ad`

### Stage 4 — 細胞類型標注

- `sc.tl.score_genes()` 向量化評分（避免 O(n×g) 逐細胞迴圈）
- **Fallback 行為**：max score ≤ 0.05 的細胞一律標注為 `Dermal_Fibroblasts`（非 "Unknown"），門檻值硬編碼
- 預設 marker 基因（皮膚 / 毛囊）：

  | 類型 | Markers |
  |------|---------|
  | Epidermal_Keratinocytes | Krt14, Krt5, Krt1, Krt10 |
  | Dermal_Fibroblasts | Col1a1, Col1a2, Col3a1, Dcn |
  | Hair_Follicle_Stem_Cells | Lgr5, Sox9, Krt15, Lgr6 |
  | Melanocytes | Mitf, Dct, Pmel |
  | Myofibroblasts_aSMA | Acta2, Tagln |
  | Endothelial_Cells | Pecam1, Cldn5 |
  | Immune_Cells | Ptprc, Lyz2, Cd3e |

### Stage 5 — 空間生態學指標

| 指標 | 方法 | 說明 |
|------|------|------|
| **NED**（鄰域生態距離） | Delaunay 三角化 + Hellinger 距離 | 向量化 numpy，衡量邊界銳利度 |
| **Doublet rate** | Krt14 × Col1a1 共表現 | 分割品質指標 |
| **Stem↔Dermal 距離** | sklearn NearestNeighbors（O(n log n)）| 空間隔離程度 |
| **Permutation p-value** | 100 次 shuffle CSR | 觀測距離是否顯著 < CSR |
| **Spatial niche** | KMeans 聚類（NearestNeighbors 鄰域組成） | `min(4, n_lin)` 個 niche（細胞類型數不足 4 時自動縮減） |

### Stage 6 — 圖片輸出與 Xenium 匯出

- `umap_spatial_combined.png`：UMAP + 空間分布並排
- `skin_markers_dotplot.png`：Marker gene dotplot
- Xenium Explorer bundle（`export/xenium/roi_<name>/`）：
  - `experiment.xenium`（直接用 Xenium Explorer 開啟）
  - `morphology.ome.tif`（6 層 H&E 影像金字塔）
  - `cells.zarr.zip`、`transcripts.zarr.zip`、`cell_feature_matrix.zarr.zip`
  - `analysis_zarr.zip`（Leiden + cell_type + spatial_niche）

### Stage 7 — H&E Overlay 視覺化

- 函數：`_generate_mask_overlay`（`scratch/run_visium_hd_showcase.py`）
- mask 從 virtual_fullres（1500×1500）nearest-neighbor upscale 回原始 H&E TIFF 解析度疊圖（尺寸動態讀取，非固定值）
- 輸出兩張：
  - `mask_he_overlay.png`：細胞類型著色填色（alpha=0.38）+ 白色邊界（alpha=0.85）
  - `mask_he_boundary.png`：純白邊界版（適合論文）

---

## 工具 B：bio_run_mcseg_fullslide（全片分割）

**耗時**：數小時（GPU）  
**必要參數**：`sample_id`  
**選填**：`tile_size`（預設 1024）、`overlap`（預設 128）、`use_cpsam`

- 呼叫 `run_tiled_mcseg_v2` 對全片執行 tiled 分割
- 輸出：`fullslide/segmentation_masks_fullslide.npy`
- ⚠️ 全片細胞數可能超過 10 萬，downstream Scanpy 需另行分批執行
- ⚠️ 全片 BTF 可達 10–80 GB，確認磁碟空間

---

## 工具 C：bio_run_mcseg_qc（既有遮罩品質評估）

**前提**：分割遮罩已產生（`*_nuc.npy` + `*_mcseg.npy` 成對放在 `qc_dir`）  
**不重跑分割**，只做視覺化與量化比較。

### 步驟
1. **探索 ROI 對**：`mcseg_quality.discover_roi_pairs`
2. **細胞量化**：細胞數、平均/中位面積、前景占比
3. **NUC vs MCseg 對比圖**：紅色邊界疊在 H&E 底圖
4. **面積分布圖**：多 ROI 面積直方圖疊圖

**品質判讀**：
- MCseg 細胞數遠少於 NUC → 過度合併
- 面積中位數異常大 → 分割不足
- 前景占比 > 80% → Voronoi 擴張過度

---

## 輸出目錄結構

```
results/mcseg/<sample_id>/
├── roi/<roi_name>/
│   ├── crop_meta.json          ← tiff_scale + vfr 尺寸
│   ├── he_crop.tif             ← H&E 裁切（TIFF 解析度）
│   ├── segmentation_masks.npy  ← mask（virtual_fullres）
│   ├── segmentation_masks.tif
│   ├── adata_002um.h5ad        ← 原始 bin AnnData
│   ├── cellpose_cells.h5ad     ← 細胞計數 AnnData
│   ├── umap_computed.h5ad      ← 完整分析 AnnData
│   ├── transcripts_roi.csv
│   ├── cellpose_polygons.json
│   ├── analysis_summary.txt
│   ├── umap_spatial_combined.png
│   ├── skin_markers_dotplot.png
│   ├── mask_he_overlay.png     ← 細胞類型著色疊圖
│   └── mask_he_boundary.png    ← 純邊界版
└── export/xenium/<roi_name>/
    ├── experiment.xenium       ← Xenium Explorer 入口
    ├── morphology.ome.tif
    ├── cells.zarr.zip
    ├── transcripts.zarr.zip
    ├── cell_feature_matrix.zarr.zip
    ├── analysis_zarr.zip
    └── cell_metadata.json
```

---

## 完成後

- 確認 `analysis_history` 寫入（`analysis_type=mcseg_roi` / `mcseg_fullslide` / `mcseg_qc`，`tool_id` 回填）
- 繁中摘要：ROI 座標、細胞數、NED、doublet rate、permutation p-value、cell type 分布
- 指出 `result_path`（`umap_computed.h5ad`）與 Xenium bundle 路徑
