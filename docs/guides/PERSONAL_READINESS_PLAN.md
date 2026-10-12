# EP 個人單機可用性與發布計畫

日期：2026-10-12。使用者已授權撰寫計畫後完成全部改善。

## 目標與範圍

讓新使用者能依 GitHub 文件建立單機環境，初始化乾淨資料庫、診斷缺少的能力，並完成可重現的示範分析與結果重用。把驗證加入 CI，整合至預設分支。保留正式服務、研究資料及既有未提交變更；不下載大型生成模型、不執行真實研究分析、不啟用 Clef 或改動正式部署。

## 階段與驗收

| 階段 | 工作與主要檔案 | 完成條件 | 狀態 |
| --- | --- | --- | --- |
| P1 | 新 venv、pyproject.toml / uv.lock | 依文件從空環境安裝；記錄 Python/OS 與版本；依賴一致 | 完成 |
| P2 | scripts/ep_local.py 初始化與診斷 | 單一命令建立空資料庫/L1、pending migrations、絕對路徑 MCP JSON；重跑安全；診斷提供可操作修復 | 完成 |
| P3 | 合成 demo、scripts/check_single_user.py | MCP 登記→真實 CPU EDA→產物/來源保存→讀回報告→再次查詢重用；斷言不增加 run 數 | 完成 |
| P4 | 單機中英文指南、README | 實測命令一致；個人與多人入口分開；必要/可選能力分明 | 完成 |
| P5 | CI 與迴歸 | 乾淨 runner 安裝、初始化重入、MCP/EDA/reuse、診斷失敗路徑；既有 CI 問題分類处理 | 本機通過，CI 待驗證 |
| P6 | origin/master 整合與 GitHub | 保留遠端既有變更，整合衝突、推送、PR 檢查通過後合併，回讀 default branch SHA | 整合中 |

## 驗證與失敗處理

- 使用隔離工作目錄與新的暫存資料，每次失敗保留 log；修復後另建 fresh attempt。
- 示例僅為固定 synthetic counts，測試系統工作流，不作科學品質/模型品質證明。
- 狀態診斷不得輸出密鑰；預設不推理，連線檢查只能是 health/read-only。
- 初始化預設不加入實驗室種子資料；不覆寫既有 MCP 設定，重跑保留樣本與分析。
- 發布不 force-push；遠端有新進展則重新整合。執行阻礙需記錄原因與可恢復狀態，不將未測項目標為通過。

## 執行紀錄

- 起點：dfa77e7；隔離 branch codex/personal-ready。正式 checkout 的 mcseg 與 compose 未提交修改保留。
- GitHub default branch 為 master；ADMIN 權限。遠端比現行分支另有 9 個提交，需保留其文件/格式/交付修正。

- P1：全新 .venv 與獨立空套件快取，uv sync --locked --no-install-project --python 3.12 成功（102 packages）；uv pip check 全部相容。macOS Apple Silicon / Python 3.12.13 / DuckDB 1.5.3。
- P2：ep_local init/doctor，初始化保留既有資料與設定；6 個失敗／隔離案例通過。修復首次啟動缺 .locks 導致工具登記跳過的問題。
- P3：MCP 合成 40 genes × 6 samples CPU EDA，4 張圖與 1 報告、非空 tool_id；重啟及重跑 init 後可讀回，run 數仍為 1。加入 counts_path/coldata_path，限制到樣本登記 L3 目錄；越界案例拒絕。
- 合併審查：master 的分析模組差異主要為格式與未用 import；保留新版 analysis_run、owner scope、tool catalog；補回遠端 delivery 指南。按既有 CI 統一格式，未啟動正式服務。
- 本機驗證：6 個 setup 案例、27 個既有 run_context/result_delivery/tool_catalog 迴歸通過；lint 與 format 通過。
- 範圍：不宣稱 embedding 搜尋、生成模型、Clef 品質、MCseg/GPU、Web 或多人並行已在本次驗收。

- 乾淨依賴環境完整驗證 PASS：schema v30、59 tools、5 artifacts（4 figures）、跨連線重用後 1 run；report SHA256 d13db0f8055945e76d5d6d1518fe8784d01e360cd725079d303a9831e28fc6fb。
