# Bio-DB 測試資料庫索引

建立於：2026年5月15日  
總大小：~45GB

## 📊 數據源與結構

### 1. **MSseg 程式碼基礎** (MSseg from /Volumes/SSD/plan_a/)

| 目錄 | 大小 | 說明 |
|------|------|------|
| `analysis_msseg/` | 164MB | 細胞分割、分類、統計分析代碼 |
| `backend_msseg/` | 28MB | FastAPI 後端服務、API 端點實現 |
| `scripts_msseg/` | 4.4MB | 預處理、轉換、工具腳本 |
| `msseg_docs/` | - | 文檔：CLAUDE.md, README.md, pyproject.toml |

**用途**：參考細胞分割、轉錄組分析的實現模式

---

### 2. **ana_VisiumHD 分析結果** (from /Volumes/SSD/plan_a/)

| 目錄 | 大小 | 說明 |
|------|------|------|
| `results_ana/` | 3.9GB | 分析結果：聚類、基因表達、視覺化圖表 |
| `data_ana/` | 1.6GB | 原始/中間數據：H5、CSV、JSON 格式 |
| `scripts_ana/` | 6.1MB | Jupyter notebooks 和分析腳本 |

**用途**：測試數據管道、建立分析結果模板、驗證 L1/L2/L3 快取系統

---

### 3. **CRC Visium HD 官方數據** (moved from /Volumes/NO NAME/CRC/visium/)

| 目錄 | 大小 | 說明 |
|------|------|------|
| `crc_visium_data/` | 39GB | 官方 Visium HD 腸癌組織樣本 |
| └─ `official_v4/` | - | 包含：特徵矩陣、空間座標、分割結果、web 摘要 |

**結構**：
```
official_v4/
├─ binned_outputs/
│  ├─ square_002um/     # 2 微米分辨率
│  ├─ square_008um/     # 8 微米分辨率
│  └─ square_016um/     # 16 微米分辨率
├─ segmented_outputs/
│  ├─ cell_segmentations.geojson
│  ├─ filtered_feature_cell_matrix.h5
│  └─ analysis/
├─ spatial/             # 空間座標
└─ 其他：cloupe、H5、CSV 檔案
```

**用途**：真實 Visium HD 數據測試、管道驗證、性能基準

---

## 🗂️ bio_DB 現有結構

```
bio_DB/
├─ 計劃與報告
│  ├─ PartA_Report.md
│  ├─ PartB_Report.md
│  ├─ plan.md / plan_zh.md
│  └─ PartA_Outline.md
│
├─ 核心代碼
│  ├─ analysis_msseg/        ✨ 測試 - MSseg 分析代碼
│  ├─ backend_msseg/         ✨ 測試 - MSseg 後端服務
│  ├─ scripts_msseg/         ✨ 測試 - MSseg 工具腳本
│  ├─ scripts_ana/           ✨ 測試 - ana_VisiumHD 腳本
│  └─ scripts/               📋 原有 - bio_DB 初始化腳本
│
├─ 測試數據
│  ├─ results_ana/           ✨ 測試 - 分析結果 (3.9GB)
│  ├─ data_ana/              ✨ 測試 - 中間數據 (1.6GB)
│  └─ crc_visium_data/       ✨ 測試 - 官方數據 (39GB)
│
├─ 系統層 (L1/L2/L3)
│  ├─ gold/                  📋 L1 快取（空）
│  ├─ silver/                📋 L2 特徵存儲（空）
│  ├─ analysis/              📋 L3 分析結果（空）
│  ├─ scheduler/             📋 排程系統（空）
│  └─ server/                📋 MCP 服務（空）
│
├─ 文獻與參考
│  └─ references/            📋 13 份論文、markdown 筆記
│
└─ 配置與文檔
   ├─ TEST_DATABASE_INDEX.md ✨ 本文件
   ├─ msseg_docs/            ✨ MSseg 文檔
   └─ .gitignore
```

---

## 🎯 下一步任務

### Phase 1：驗證環境 (1-2 天)
- [ ] 執行 `scripts/00_init_db.py` - 驗證 DuckDB + VSS
- [ ] 檢查 CRC 官方數據的完整性
- [ ] 測試 Parquet 轉換工具（MSseg 基礎）

### Phase 2：建立測試管道 (1 週)
- [ ] 實現 `01_spatial_to_parquet.py` - CRC 數據轉換為 Parquet
- [ ] 導入到 silver/ L2 層
- [ ] 建立分析歷史表（analysis_history）

### Phase 3：快取系統 (2-3 週)
- [ ] 實現 LLMLingua 文本壓縮（中期記憶）
- [ ] 實現 DeepSeek-OCR 視覺編碼（長期記憶）
- [ ] 構建 L1 GOLD 語意索引

### Phase 4：MCP 服務 (2 週)
- [ ] 實現 `bio_history_check()` - 0-token 查詢
- [ ] 實現 `bio_semantic_search()` - L1 快取搜索
- [ ] 實現 `bio_feature_query()` - L2 特徵提取
- [ ] 實現 `bio_pipeline_schedule()` - L3 排程

---

## 📝 測試數據快速查詢

### MSseg 分析示例
```python
# analysis_msseg/ 包含細胞分割結果
import os
os.listdir('analysis_msseg/')  
# → 查看可用的分析模組
```

### CRC Visium HD 樣本
```bash
# 官方數據文件清單
ls -lh crc_visium_data/official_v4/
# → Visium_HD_Human_Colon_Cancer_*.h5
# → filtered_feature_bc_matrix（特徵矩陣）
# → spatial/（空間座標）
```

### 分析結果參考
```bash
# 查看既有的分析流程
ls -lh results_ana/
# → 可作為 L3 分析輸出的樣板
```

---

## ⚠️ 注意事項

1. **CRC 數據已移動** (非複製)
   - 原位置 `/Volumes/NO NAME/CRC/visium/` 已移到 `bio_DB/crc_visium_data/`
   - 若需恢復，請反向 mv

2. **大文件操作**
   - 40GB CRC 數據 + 5.5GB 分析結果 = 45GB 總計
   - 確保磁盤空間充足

3. **依賴環境**
   - MSseg 代碼需要 Python 依賴（見 msseg_docs/pyproject.toml）
   - 分析結果需要 AnnData、Scanpy（見 data_ana/ 格式）

---

## 🔗 相關文件

- [計劃文檔](../plans/plan_zh.md) - 完整系統設計
- [Part A 報告](PartA_Report.md) - 問題分析與解決方案
- [MSseg README](msseg_docs/README_zh.md) - 細胞分割框架文檔
