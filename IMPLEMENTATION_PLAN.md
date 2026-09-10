# Evo-PRISM 多使用者分析佇列與資料匯入實作計畫

## 目標摘要

讓 Evo-PRISM 能安全提供實驗室成員遠端使用：查詢維持即時，耗時分析透過持久化佇列執行；每位成員使用獨立 API key 與角色權限；大型 analysis-ready 資料經 Tailscale SFTP／rsync 傳入個人 staging，驗證後才提升為不可變 L3 樣本。

第一版明確不包含 FASTQ 定量、Space Ranger／Cell Ranger pipeline 執行、NDPI 轉檔、瀏覽器大型檔案上傳，以及任意 Python／任意工具排隊。

## 已批准的設計決策

1. 只有分析型工具排隊；樣本查詢、歷史查詢、狀態與報告讀取維持同步即時。
2. 第一版只接受 analysis-ready 資料：Kallisto／Salmon、Space Ranger／Cell Ranger、MaxQuant 已處理輸出。
3. 資料傳輸不走 MCP body，改走同一 tailnet 上的 SFTP／rsync。
4. 每位使用者有獨立 key；角色為 viewer、analyst、admin。
5. Production metadata 使用 Postgres；DuckDB 保留本機相容與隔離測試。
6. 共用 owner env token 保留為相容的 admin 身分；DB key 只保存 SHA-256 hash。
7. 遠端 analyst 只能透過佇列執行 queueable 工具；危險工具永不 queueable。
8. 呼叫者身分由認證層注入並覆寫 `requested_by`，防止冒名。

## 文件架構圖

```text
Evo_PRISM/
├── IMPLEMENTATION_PLAN.md
├── config/settings.py
├── scripts/27_migrate_schema_v27_auth.py
├── scripts/28_migrate_schema_v28_job_queue.py
├── scripts/pg_schema.sql
├── store/{base,duckdb_store,postgres_store}.py
├── server/{auth,identity,tool_catalog,bio_memory_server}.py
├── analysis/{job_queue,staged_ingest}.py
├── worker/{__init__,analysis_worker}.py
├── scripts/provision_staging_user.py
├── tests/{test_auth_rbac,test_job_queue,test_staged_ingest,test_worker_dispatch}.py
├── docs/guides/{LAB_MEMBER_ONBOARDING,STAGED_DATA_INGEST}.md
└── docs/logs/execution_trace.md
```

## 安全與資料不變式

- Raw key 不落 DB、不寫 log，只保存 SHA-256 hash與短 prefix。
- Revoked key 不得退回 env-admin 相容分支。
- viewer 只能讀；analyst 可驗證 staging、提交／查看／取消自己的工作；admin 可管理 key、查看全部工作與執行維運操作。
- 使用者傳入的 `requested_by` 不可信，必須由 identity 覆寫。
- staging 路徑限制在使用者自己的根目錄，拒絕絕對路徑、`..`、symlink escape、AppleDouble 控制檔與未知格式。
- manifest 包含 sample_id、project、data_type、platform、檔案清單與 SHA-256；伺服器重新計算 checksum。
- 驗證後以同檔案系統 atomic rename 提升到 L3；既有 sample_id 拒絕覆寫。
- job payload 只接受 tool catalog 中 `queueable=true` 的工具，拒絕 dangerous 工具與任意函式。
- job 狀態為 `queued → running → succeeded／failed／canceled`；running job 具有 lease、heartbeat 與 retry_count。
- worker crash 後只有 lease 過期工作可重新排隊；已取消工作不可再執行。
- 預設 CPU concurrency=1、GPU concurrency=1、每位使用者最多 3 個未完成工作，均可由環境變數調整。
- `analysis_history` 仍只經 analysis_run seam 寫入；job table 不取代分析生命週期。
- 測試只使用 tmp fixture，不讀寫真實研究資料。

## 任務列表

- [x] 任務 1：建立隔離 worktree與基線證據（completed 2026-09-09）
  - 預期行為：從部署分支 HEAD 建立 feature branch，不攜入既有 dirty files。
  - 驗證指令：`git status --short --branch`、既有核心測試。
  - 相關檔案：`IMPLEMENTATION_PLAN.md`、`docs/logs/execution_trace.md`
  - 提交：`docs(plan): define multi-user queue and ingest rollout`

- [x] 任務 2：以失敗測試定義 per-key identity與撤銷語意（completed 2026-09-09）
  - 預期行為：DB key 可認證；撤銷 key 回 401；未知 key 不會取得 admin。
  - 驗證指令：`python -m pytest tests/test_auth_rbac.py -v`
  - 相關檔案：`tests/test_auth_rbac.py`
  - 提交：`test(auth): define per-key identity contract`

- [x] 任務 3：實作 identity、hash key registry與認證 middleware（completed 2026-09-09）
  - 預期行為：owner env key向下相容；registered key可用；revoked key立即失效。
  - 驗證指令：`python -m pytest tests/test_auth_rbac.py -v`
  - 相關檔案：`server/identity.py`、`server/auth.py`、雙後端 store與schema。
  - 提交：`feat(auth): add per-key identity lifecycle`

- [x] 任務 4：以失敗測試定義 viewer／analyst／admin工具權限（completed 2026-09-09）
  - 預期行為：viewer不能提交分析；analyst不能管理key或執行危險工具；admin可管理。
  - 驗證指令：`python -m pytest tests/test_auth_rbac.py -v`
  - 相關檔案：`tests/test_auth_rbac.py`
  - 提交：`test(rbac): define tool authorization matrix`

- [x] 任務 5：在tool catalog加入required_role與queueable單一真相來源（completed 2026-09-09）
  - 預期行為：兩個入口共用權限分類，authenticated identity覆寫`requested_by`。
  - 驗證指令：RBAC與tool catalog parity tests。
  - 相關檔案：`server/tool_catalog.py`、`server/bio_memory_server.py`
  - 提交：`feat(rbac): enforce catalog roles and actor identity`

- [x] 任務 6：以失敗測試定義analysis_jobs狀態機與配額（completed 2026-09-09）
  - 預期行為：合法轉移、所有權、每人上限、取消與lease reclaim均可驗證。
  - 驗證指令：`python -m pytest tests/test_job_queue.py -v`
  - 相關檔案：`tests/test_job_queue.py`
  - 提交：`test(queue): define durable job lifecycle`

- [x] 任務 7：新增雙後端job schema與store API（completed 2026-09-09）
  - 預期行為：DuckDB與Postgres具等價enqueue、claim、heartbeat、finish、cancel、list。
  - 驗證指令：store parity與job queue tests。
  - 相關檔案：migration、`scripts/pg_schema.sql`、`store/`、`analysis/job_queue.py`
  - 提交：`feat(queue): add durable job storage`

- [x] 任務 8：新增submit/status/list/cancel MCP工具（completed 2026-09-09）
  - 預期行為：只接受queueable allowlist；成員只能操作自己的job；admin可跨使用者查詢。
  - 驗證指令：MCP handler與catalog parity tests。
  - 相關檔案：`server/tool_catalog.py`、`server/bio_memory_server.py`
  - 提交：`feat(queue): expose analysis job tools`

- [x] 任務 9：以失敗測試定義worker dispatch、失敗與取消（completed 2026-09-09）
  - 預期行為：worker只走既有handler；完成／失敗正確落庫；取消與lease過期安全處理。
  - 驗證指令：`python -m pytest tests/test_worker_dispatch.py -v`
  - 相關檔案：`tests/test_worker_dispatch.py`
  - 提交：`test(worker): define safe queued dispatch`

- [x] 任務 10：實作獨立analysis worker與Docker service（completed 2026-09-09）
  - 預期行為：worker可恢復lease，CPU/GPU資源類別限流，不阻塞MCP server。
  - 驗證指令：worker tests與`docker compose config`。
  - 相關檔案：`worker/`、`docker-compose.yml`
  - 提交：`feat(worker): execute queued analyses out of process`

- [x] 任務 11：以失敗測試定義staging manifest與路徑安全（completed 2026-09-09）
  - 預期行為：接受支援格式；拒絕checksum不符、未知類型、traversal、symlink escape與重複sample。
  - 驗證指令：`python -m pytest tests/test_staged_ingest.py -v`
  - 相關檔案：`tests/test_staged_ingest.py`
  - 提交：`test(ingest): define staging trust boundary`

- [x] 任務 12：實作staging驗證與atomic L3 promotion（completed 2026-09-09）
  - 預期行為：驗證成功才移入L3並由store登記；失敗保留staging且不寫registry。
  - 驗證指令：staged ingest tests與sample registry assertions。
  - 相關檔案：`analysis/staged_ingest.py`、`config/settings.py`
  - 提交：`feat(ingest): validate and promote staged datasets`

- [x] 任務 13：新增資料驗證／提升MCP工具與管理腳本（completed 2026-09-09）
  - 預期行為：analyst只能處理自己的staging；admin可建立受限成員目錄與輸出SFTP/rsync指引。
  - 驗證指令：role、ownership與dry-run provisioning tests。
  - 相關檔案：tool catalog、MCP server、`scripts/provision_staging_user.py`
  - 提交：`feat(ingest): expose staged dataset workflow`

- [x] 任務 14：撰寫onboarding、資料傳輸與故障復原文件（completed 2026-09-09）
  - 預期行為：文件涵蓋發key、SFTP/rsync、manifest、提交、查狀態、撤銷與worker恢復。
  - 驗證指令：文件契約字串檢查。
  - 相關檔案：`docs/guides/`
  - 提交：`docs(lab): document member queue and ingest workflow`

- [x] 任務 15：完整驗證與安全回歸（completed 2026-09-09；feature gate green，repo baseline blockers recorded）
  - 預期行為：完整測試、lint、schema parity、compose config與敏感資訊掃描通過。
  - 驗證指令：repo完整validation gate；測試不得讀寫真實資料。
  - 相關檔案：測試與必要修正。
  - 提交：`test(system): close multi-user queue validation gaps`

- [x] 任務 16：部署前dry-run與交付（completed 2026-09-09；isolated fixture only）
  - 預期行為：備份metadata後，只用隔離fixture做submit→worker→result驗證；不建立真實成員或搬移研究資料。
  - 驗證指令：health、MCP initialize、三角色smoke、queue與staging smoke。
  - 相關檔案：`docs/logs/execution_trace.md`
  - 提交：`docs(trace): record verified queue rollout`

## 驗收標準

1. viewer無法提交或取消；analyst可提交並只看／取消自己的工作；admin可管理key與所有工作。
2. revoked key立即401；`requested_by`冒名測試失敗。
3. 查詢工具不經job table。
4. Queueable分析由worker執行，MCP submit立即回job_id。
5. worker重啟不遺失工作，lease過期可恢復，重複claim不會同時執行。
6. 未驗證資料不能進L3或sample_registry；promotion不可覆寫既有樣本。
7. 大檔只走SFTP／rsync，MCP只傳manifest引用與控制命令。
8. Dangerous工具永不queueable，成員key無法啟用。
9. DuckDB與Postgres schema／store contract一致。
10. 不修改既有未提交的`analysis/mcseg_wrapper.py`與`scripts/start_webserver.py`。

## 分析紀錄可見性補強（2026-09-09 已批准）

### 已批准的權限契約

- `viewer`／`analyst` 僅可讀取 `analysis_history.requested_by` 等於自己 `user_id` 的分析紀錄與衍生內容。
- `admin` 可讀取所有成員與既有 legacy 紀錄。
- 樣本基本資料（`sample_registry`、alias、L2/L3 readiness）維持實驗室共用，不依分析 owner 切割。
- 直接以 `analysis_id`、`artifact_id`、`figure_id` 或 `result_path` 取用時，必須套用與列表相同的 owner 檢查；拒絕訊息不得洩漏目標是否屬於他人。
- L1 語意快取必須保存 owner；未歸屬的 legacy cache 對非 admin 視為 cache miss。
- Tailscale 對外的 Web record API 與 MCP 使用同一套 key 身分；健康檢查與靜態入口可維持公開，分析紀錄 API 不可匿名讀取。

### 補強任務

- [x] 任務 17：以失敗測試定義 owner visibility contract（completed 2026-09-09）
  - 預期行為：Alice 看不到 Bob 的歷史、cache、artifact、report、figure、impact／compare 結果；admin 全部可見；sample registry 仍共用。
  - 驗證指令：`python -m pytest tests/test_analysis_record_visibility.py -v`
  - 相關檔案：`tests/test_analysis_record_visibility.py`、`docs/logs/execution_trace.md`

- [x] 任務 18：建立共用 record visibility seam 並補齊雙 store／歷史查詢（completed 2026-09-09）
  - 預期行為：角色轉換只在認證邊界發生；下層查詢接收可選 `requested_by`，`None` 代表 admin 全域視圖。
  - 驗證指令：visibility contract + store parity tests。
  - 相關檔案：`server/auth.py`、`store/`、`analysis/history_query.py`
  - 提交：`feat(privacy): scope analysis history by owner`

- [x] 任務 19：封住 artifact／report／figure／resource 與 L1 cache 直接取用旁路（completed 2026-09-09）
  - 預期行為：搜尋、摘要、直接 ID、路徑讀取及 MCP resources 都只回 own records；figure handle 持久記錄可存取 owner；L1 migration 將 legacy rows 標為不可供一般成員讀取。
  - 驗證指令：visibility、artifact resources、figure cache、L1 cache tests。
  - 相關檔案：`analysis/{artifact_registry,artifact_resources,figure_cache,l1_cache}.py`、`scripts/03_init_l1_cache.py`、新 migration、MCP handlers。
  - 提交：`feat(privacy): protect derived analysis artifacts`

- [x] 任務 20：補齊跨分析工具與 Tailscale Web record API 邊界（completed 2026-09-09）
  - 預期行為：sample compare、impact、cascade、version compare、failure summary 只使用 own records；對外 REST record endpoints 驗證同一組 Bearer key 並套用 owner filter；admin 維運 dashboard 維持 admin-only。
  - 驗證指令：visibility、HTTP auth、impact/version compare regressions。
  - 相關檔案：`server/{agent_history,bio_memory_server,web_app}.py`、`analysis/{impact,version_compare}.py`
  - 提交：`feat(privacy): enforce record visibility across clients`

- [x] 任務 21：文件、Second Brain 與完整驗證（completed 2026-09-09）
  - 預期行為：成員指南清楚說明 own-only、admin-all、legacy 行為與 Bearer 下載方式；SB 計畫追加成果；feature gate 維持全綠。
  - 驗證指令：完整 feature suite、scoped Ruff、migration dry-run、文件契約檢查。
  - 相關檔案：`docs/guides/LAB_MEMBER_ANALYSIS.md`、`docs/logs/execution_trace.md`
  - 提交：`docs(privacy): document member record isolation`

## 部署前整合續行（2026-09-09 已批准）

正式 checkout 目前包含既有未提交的 `analysis/mcseg_wrapper.py` 與未追蹤 `scripts/start_webserver.py`。為避免把使用者工作混入整合，先以隔離檢查證明 feature 可由目前正式 HEAD 安全 fast-forward，並確認檔案集合不重疊；只有通過後才更新正式分支指標，且不自動啟停服務。

- [x] 任務 22：隔離整合演練與 fast-forward 安全檢查（completed 2026-09-09）
  - 預期行為：確認 base／HEAD ancestry、合併衝突與 dirty-path overlap；正式 checkout 的既有修改保持原樣。
  - 驗證指令：`git merge-base --is-ancestor`、變更路徑交集、隔離 worktree feature gate。
  - 完成條件：可證明 fast-forward 且無 dirty-path overlap，才允許正式 branch pointer 前進。

- [x] 任務 23：正式環境唯讀盤點與可恢復備份（completed 2026-09-09）
  - 預期行為：確認實際 backend、容器／服務狀態、DB/L1 路徑、mount 類型與容量；依 backend 建立時間戳備份並驗證可開啟，不讀取或輸出 key secret。
  - 安全界線：若 active DuckDB 無法取得一致備份，停在 maintenance-window 閘門，不直接停止服務。

- [x] 任務 24：暫存副本 migration 與 Alice／Bob／admin smoke（completed 2026-09-09）
  - 預期行為：在隔離副本重跑 auth v27／job queue v28 與 L1 owner migration；驗證 own-only、admin-all、shared sample registry、queue ownership、revocation 與 staging promotion。
  - 安全界線：只用 fixture key／資料，不建立真實成員或改寫正式資料。
  - 已排除阻擋：從正式 EXPORT 還原的主庫含 HNSW index，且既有 version 26 屬於 embedding migration。auth/job migration 已改為 v27/v28 並補上 VSS 初始化；乾淨副本、冪等與角色 smoke 均已通過。

- [x] 任務 25：readiness 報告與 Second Brain 回寫（completed 2026-09-09）
  - 預期行為：列出已通過項目、阻擋、實際部署／重啟命令與 rollback 點；中央 SB 原計畫追加續行結果並讀回驗證。
  - 安全界線：正式 rebuild/restart、發真實 key 與資料搬移另以 readiness 結果為閘門。

## 正式維護部署（2026-09-09 已批准）

- [x] 任務 26：建立最新 rollback point（completed 2026-09-09）
  - 預期行為：在現行服務仍運作時，重新 EXPORT 主 DB 與 L1 cache 並完成還原驗證；將現行映像另存為具日期的 rollback tag。
  - 安全界線：備份與舊映像均不得覆寫；不得輸出 `.env` 或任何 raw API key。

- [x] 任務 27：同步乾淨 build context 並完成 build-before-stop（completed 2026-09-09）
  - 預期行為：只從隔離 worktree 同步 Git tracked files 至 APFS build context，保留 `MSseg` 等部署資料；新映像建置成功前不停止正式服務。
  - 驗證指令：tracked-file hash／migration filename 檢查、`docker compose build`。

- [x] 任務 28：維護停機與正式 schema migration（completed 2026-09-09）
  - 預期行為：停止所有 writer 後依序執行 main DB v27/v28 與 L1 owner migration，並以唯讀查詢驗證版本、表、欄位與既有 row counts。
  - 安全界線：任一 migration 或驗證失敗即停止，不啟動不確定 schema 的服務。

- [x] 任務 29：以三服務 topology 重建並驗證 health（completed 2026-09-09）
  - 預期行為：使用 `docker compose up -d --force-recreate` 啟動 MCP server、CPU worker、GPU worker；三者狀態與 `/health` 均通過。
  - 安全界線：健康檢查失敗時保留診斷證據並停止續行。

- [x] 任務 30：live RBAC smoke、撤銷 fixture key 與完成紀錄（completed 2026-09-09）
  - 預期行為：驗證未授權拒絕、viewer/analyst/admin 權限與 owner visibility；所有 smoke key 在結束前撤銷；將不含 secret 的部署證據追加至 Second Brain。
  - 安全界線：不搬移真實研究資料、不留下可用 fixture key、不把 secret 寫入 Git、log 或 SB。

## 實作限制

- 使用`apply_patch`修改文字檔。
- 每個任務完成後執行驗證並建立原子commit。
- Repo 的 `.gitignore` 明定 `tests/` 為本機測試、不上傳 GitHub；因此純 Red 測試任務只建立本機驗證 checkpoint，不強制加入 Git。對應 Green 任務的正式程式 commit 必須附上通過的測試證據。
- 驗證失敗時先保留證據、修正計畫，再繼續。
- 部署、服務重啟、建立OS/SFTP帳號與真實資料搬移需獨立驗證；測試fixture不得寫入正式資料庫。

## EP 分析流程圖與 MCP 對話取圖（2026-09-10 已批准）

### 目標摘要

建立一份繁體中文、可存取且可縮放的 EP 分析流程指南，讓第一次使用系統的人能從總覽理解 Bulk RNA-seq、空間轉錄體、單細胞與 Visium HD／MCseg 的分析順序，並在每個步驟看見實際會產生的資料檔與圖檔。圖中加入 PCA、火山圖、熱圖、UMAP、空間 QC 與 segmentation overlay 的示意縮圖，全部標示為示意而非真實研究結果。新增唯讀 MCP 方法 `bio_get_analysis_workflow`，讓使用者可透過自然語言取得總覽或指定領域的流程說明與圖片。

### 文件架構圖

```text
Evo_PRISM/
├── IMPLEMENTATION_PLAN.md
├── analysis/
│   └── workflow_guide.py
├── server/
│   ├── bio_memory_server.py
│   └── tool_catalog.py
├── scripts/
│   └── generate_analysis_workflow_guide.py
├── docs/
│   ├── analysis_workflows.html
│   ├── guides/
│   │   └── ANALYSIS_WORKFLOW_GUIDE.md
│   └── images/
│       ├── analysis_workflows.svg
│       └── analysis_workflows.png
└── tests/
    ├── test_workflow_guide.py
    └── test_tool_catalog_parity.py
```

### 已批准的設計決策

1. 主圖採用「共通入口＋四條領域泳道」的 Flowchart／Data-flow 混合模式，白底、由左至右閱讀。
2. HTML 是完整單檔指南；SVG 是可縮放圖；PNG 是 MCP 多模態對話的主要預覽圖；Markdown 提供可搜尋的純文字說明。
3. 每個分析節點同時標示 MCP 方法、必要輸入、主要資料檔、主要圖檔與下一步，不把查詢、佇列或管理工具混入核心分析方法數量。
4. 圖片 sample 使用合成示意資料，不讀取真實研究樣本，也不暗示任何生物結論。
5. `bio_get_analysis_workflow` 為 viewer 可用、唯讀、不可排隊且不需 embedding server；接受 `overview`、`bulk`、`spatial`、`single_cell`、`mcseg`。
6. MCP 方法回傳文字索引、HTML／SVG／PNG 絕對路徑與 inline PNG；統一 MCP 邊界會將 inline 圖轉為 `figure_id`，使用者可再呼叫 `bio_get_figure` 取得圖片。
7. 原始資產隨程式碼版本管理；正式部署副本的同步與服務重啟分開驗證，避免直接改寫執行中容器。

### 任務列表

- [x] 任務 31：以失敗測試定義流程指南與 MCP 契約（completed 2026-09-10）
  - 預期行為：五個 domain 均可查詢；非法 domain 被拒絕；回傳包含輸出檔說明、示意聲明與 inline PNG；catalog 工具數更新為 53，兩端 dispatch 保持一致。
  - 驗證指令：`uv run pytest tests/test_workflow_guide.py tests/test_tool_catalog_parity.py -q`
  - 相關檔案：`tests/test_workflow_guide.py`、`tests/test_tool_catalog_parity.py`
  - 提交：`test(workflow): define MCP diagram retrieval contract`

- [x] 任務 32：產生流程圖、結果示意縮圖與文字指南（completed 2026-09-10）
  - 預期行為：HTML／SVG／PNG／Markdown 完整涵蓋四個領域；檔名以現有分析程式實作為準；HTML 通過 Diagram Design 自檢，PNG 以有效內容邊界緊湊裁切。
  - 驗證指令：`python3 /Users/lab_center/.agents/skills/diagram-design/scripts/self_check.py docs/analysis_workflows.html`，並實際渲染檢視 PNG。
  - 相關檔案：`docs/analysis_workflows.html`、`docs/images/analysis_workflows.svg`、`docs/images/analysis_workflows.png`、`docs/guides/ANALYSIS_WORKFLOW_GUIDE.md`
  - 提交：`docs(diagrams): add EP analysis workflow guide`

- [x] 任務 33：實作唯讀 MCP 流程圖工具（completed 2026-09-10）
  - 預期行為：`bio_get_analysis_workflow` 從版本化資產讀取資料，依 domain 回傳對應章節、檔案位置與 inline PNG；不連線研究資料庫、不寫 analysis_history，也不繞過 owner visibility。
  - 驗證指令：`uv run pytest tests/test_workflow_guide.py tests/test_tool_catalog_parity.py tests/test_auth_rbac.py -q`
  - 相關檔案：`analysis/workflow_guide.py`、`server/tool_catalog.py`、`server/bio_memory_server.py`
  - 提交：`feat(mcp): expose analysis workflow diagrams`

- [x] 任務 34：完成結構、視覺與 MCP 端到端驗證（completed 2026-09-10）
  - 預期行為：HTML 結構與安全檢查通過；SVG／PNG 無文字截斷、箭頭遮蓋或多餘空白；MCP 呼叫會產生可由 `bio_get_figure` 讀回的 owner-scoped `figure_id`；既有核心測試不回歸。
  - 驗證指令：Diagram Design self-check、HTML 瀏覽器渲染、`uv run pytest tests/test_workflow_guide.py tests/test_tool_catalog_parity.py tests/test_phase4.py -q`、`uv run ruff check analysis/workflow_guide.py server/tool_catalog.py tests/test_workflow_guide.py`
  - 相關檔案：上述所有新增與修改檔案、`docs/logs/execution_trace.md`
  - 提交：`test(workflow): verify diagram rendering and MCP retrieval`

- [x] 任務 35：同步部署副本並執行正式服務 smoke test（completed 2026-09-10）
  - 預期行為：只同步本功能的已提交檔案至 `/Users/lab_center/evo_prism_build`；服務重啟後 `tools/list` 可見新方法，`bio_get_analysis_workflow` 與 `bio_get_figure` 實際可用。
  - 驗證指令：tracked-file hash 比對、MCP `tools/list`、總覽與單一領域取圖 smoke test。
  - 相關檔案：部署副本中的對應檔案、`docs/logs/execution_trace.md`
  - 提交：部署副本不是 Git worktree；正式來源的部署證據追加至 `docs/logs/execution_trace.md` 後提交 `docs(deploy): verify workflow guide retrieval`。

### 驗收標準

1. 使用者可用 MCP 對話取得總覽或四個領域之一，且不需要知道檔案路徑。
2. 圖上每個核心分析方法都有輸入、資料檔輸出、圖檔輸出與下游方向。
3. 六種圖形 sample 清楚標示為示意，不包含真實樣本或個資。
4. HTML、SVG、PNG 與 Markdown 的內容一致，HTML 通過自檢與實際渲染檢查。
5. 新工具為 viewer-safe、唯讀、不可排隊；MCP 與 Web UI 兩端 schema／dispatch 不漂移。
6. 正式服務重啟前保留部署邊界；未經獨立確認不自動中斷執行中的分析工作。

## 15 個核心分析方法獨立流程圖（2026-09-10 已批准）

### 目標摘要

將現有總覽中的 15 個核心分析方法，各自產生一張可獨立閱讀、版本化與 MCP 提取的直式流程圖。每張圖以實際工具程式與 catalog 契約為依據，清楚呈現前置條件、分析步驟、失敗／選用分支、資料檔、圖檔、歷史登記與後續方法；視覺語言沿用 `fig-01.png` 的白底、分區、橘色控制線與藍色執行／保存線。

### 文件架構圖

```text
Evo_PRISM/
├── IMPLEMENTATION_PLAN.md
├── analysis/
│   └── workflow_guide.py
├── server/
│   └── tool_catalog.py
├── scripts/
│   └── generate_analysis_workflow_guide.py
├── docs/
│   ├── analysis_workflows.html
│   ├── analysis_methods/
│   │   └── <tool_name>/
│   │       └── index.html
│   ├── guides/
│   │   └── ANALYSIS_WORKFLOW_GUIDE.md
│   └── images/
│       └── analysis_methods/
│           ├── <tool_name>.svg
│           └── <tool_name>.png
└── tests/
    └── test_workflow_guide.py
```

### 已批准的設計決策

1. 範圍固定為現有總覽中的 15 個核心分析方法，不納入查詢、權限、佇列管理或系統維運工具。
2. 每張圖採 1600×2107 直式 Flowchart／Data-flow 混合模式，分成「輸入與前置檢查、分析管線、產出與品質檢查、保存與提取」四個編號區域。
3. 每個方法輸出單檔 HTML、可縮放 SVG 與 PNG；HTML 可連到已確認存在的真實結果圖，研究圖檔只連結、不複製進 Git。
4. 無直接圖檔輸出的方法明確標示 `DATA ONLY`；選用輸入、外部網路、GPU、跨切片 `group_by` 等限制以分支或警示呈現。
5. `bio_get_analysis_workflow` 新增選用 `method` 參數；指定方法時回傳該方法的文字說明與獨立 PNG，原有 `domain` 與預設總覽行為維持相容。
6. 總覽 HTML、Markdown 與 MCP 回應都提供 15 個方法索引；方法名稱以完整 MCP tool name 作為穩定識別碼。
7. 生成器是唯一內容來源；HTML、SVG、PNG 與索引不可手動分叉維護。

### 任務列表

- [x] 任務 36：寫入獨立方法圖實作計畫（completed 2026-09-10）
  - 預期行為：記錄範圍、檔案拓樸、相容介面、TDD 與部署邊界。
  - 驗證指令：`git diff --check`。
  - 相關檔案：`IMPLEMENTATION_PLAN.md`。
  - 提交：`docs(plan): define per-method workflow diagrams`

- [x] 任務 37：以失敗測試定義 15 張圖與 MCP method 契約（Red，completed 2026-09-10）
  - 預期行為：每個方法都有 HTML／SVG／PNG；HTML 與 SVG 安全且互相一致；合法 method 回傳該方法 PNG，非法 method 被拒絕；省略 method 時舊 domain 契約不變。
  - 驗證指令：`uv run pytest tests/test_workflow_guide.py -q`，確認新增案例在實作前失敗。
  - 相關檔案：`tests/test_workflow_guide.py`。
  - 提交：repo policy 忽略 `tests/`，保留本機 Red checkpoint，不建立空提交。

- [x] 任務 38：建立來源驅動的獨立流程圖產生器與資產（Green，completed 2026-09-10）
  - 預期行為：15 個方法依實際程式定義產生一致的 HTML／SVG／PNG；圖中包含前置條件、實際處理階段、DATA、FIG、失敗／選用分支與保存流程；總覽加入方法連結。
  - 驗證指令：重新執行生成器、15 個 HTML Diagram Design self-check、SVG XML parse、PNG 尺寸／有效邊界檢查與本機 Red 測試。
  - 相關檔案：`scripts/generate_analysis_workflow_guide.py`、`docs/analysis_methods/`、`docs/images/analysis_methods/`、既有總覽與 Markdown 指南。
  - 提交：`docs(diagrams): add per-method EP workflow set`

- [x] 任務 39：擴充 MCP method 提取且維持 domain 相容（Green，completed 2026-09-10）
  - 預期行為：`bio_get_analysis_workflow(method=<tool_name>)` 回傳指定方法說明、版本化檔案路徑與 inline PNG；MCP 邊界仍轉成 owner-scoped `figure_id`，並可由 `bio_get_figure` 讀回。
  - 驗證指令：`uv run pytest tests/test_workflow_guide.py tests/test_tool_catalog_parity.py tests/test_auth_rbac.py -q`、Ruff。
  - 相關檔案：`analysis/workflow_guide.py`、`server/tool_catalog.py`。
  - 提交：`feat(mcp): retrieve individual method workflows`

- [x] 任務 40：重構、完整視覺 QA 與部署 smoke（Refactor，completed 2026-09-10）
  - 預期行為：共用布局無重複漂移；15 張 PNG 皆無溢出、遮掩或大面積空白；來源 checkout、正式 checkout 與 build mirror 資產一致；僅在沒有執行中工作時重建 Web，worker 不重啟；live MCP 能列出並提取方法圖。
  - 驗證指令：相關測試、Ruff、15 個 self-check、SVG XML、PNG 視覺抽查、`git diff --check`、容器 health 與 live MCP smoke。
  - 相關檔案：上述所有檔案、`docs/logs/execution_trace.md`。
  - 提交：`docs(deploy): verify per-method workflow retrieval`

### 驗收標準

1. 15 個方法各自具有可直接開啟的 HTML、SVG 與 PNG，且名稱可穩定對應 MCP tool name。
2. 每張圖可不依賴總覽獨立理解輸入、分析主線、資料／圖檔產物、失敗條件與後續方向。
3. 每個 HTML 通過 Diagram Design self-check；SVG 可解析；PNG 為 1600×2107 且內容沒有超出分區或卡片。
4. `bio_get_analysis_workflow` 同時支援既有 domain 與新的 method 查詢；非法值有清楚錯誤訊息。
5. MCP 回傳圖片經 `figure_id` 提取，文字 context 不暴露 base64；方法圖為 viewer-safe、唯讀、不寫研究資料。
6. 正式部署不覆蓋既有使用者 dirty files，不重啟 CPU／GPU worker，並保留可回滾的 Web image。

## 15 個核心分析方法英文流程圖（2026-09-10 已批准）

### 目標摘要

為既有 15 個繁體中文方法流程圖建立內容一一對應的英文版本，保留同一工具名稱、流程階段、輸入／產出檔名、品質警示與真實結果連結。英文資產可獨立閱讀並透過 MCP 依 `method + language` 提取；既有繁體中文路徑與預設行為不變。

### 文件架構圖

```text
Evo_PRISM/
├── IMPLEMENTATION_PLAN.md
├── analysis/workflow_guide.py
├── server/tool_catalog.py
├── scripts/method_workflow_diagrams.py
├── docs/analysis_methods/<tool_name>/
│   ├── index.html
│   └── index.en.html
└── docs/images/analysis_methods/
    ├── <tool_name>.svg
    ├── <tool_name>.png
    ├── <tool_name>.en.svg
    └── <tool_name>.en.png
```

### 已批准的設計決策

1. 英文版只翻譯讀者文字；MCP tool name、資料欄位、檔名 pattern 與分析語意不變。
2. 英文圖沿用 1600×2107、四分區直式版型與既有色彩；較長英文標題／警示必須重新換行並保留卡片安全區。
3. 每個方法的英文 HTML 為 `index.en.html`，英文 SVG／PNG 使用 `.en` 後綴；中英文頁面提供互相切換入口。
4. `bio_get_analysis_workflow` 新增 `language`，可選 `zh-Hant` 或 `en`、預設 `zh-Hant`；英文查詢需同時指定 method，避免把中文版領域總覽誤標為英文。
5. 真實研究圖沿用既有原始路徑，只建立連結、不複製內容；DATA-only 方法維持明確標示。
6. 翻譯內容與中文版共用相同 15 方法集合；生成時若 method key 漂移即失敗。

### 任務列表

- [x] 任務 41：寫入英文方法圖實作計畫（completed 2026-09-10）
  - 預期行為：定義雙語檔名、語言參數、相容性與驗證邊界。
  - 驗證指令：`git diff --check`。
  - 相關檔案：`IMPLEMENTATION_PLAN.md`。
  - 提交：`docs(plan): define English method workflows`

- [x] 任務 42：以失敗測試定義英文資產與 language 契約（Red，completed 2026-09-10）
  - 預期行為：15 個英文 HTML／SVG／PNG 必須存在；不含主要中文標籤；中英文互鏈；`method + language=en` 回傳英文獨立圖；不支援語言與缺 method 的英文查詢被拒絕。
  - 驗證指令：`uv run pytest tests/test_workflow_guide.py -q`，確認新增案例在實作前失敗。
  - 相關檔案：`tests/test_workflow_guide.py`。
  - 提交：repo policy 忽略 `tests/`，保留本機 Red checkpoint。

- [x] 任務 43：建立英文內容模型與 45 個英文資產（Green，completed 2026-09-10）
  - 預期行為：共用布局產生 15 HTML、15 SVG、15 PNG；翻譯涵蓋標題、流程、警示、DATA／FIG 與提取說明，中英文結構一致。
  - 驗證指令：重新執行生成器、30 個 HTML self-check、英文 SVG XML parse、PNG 尺寸／RGB 與視覺檢查。
  - 相關檔案：`scripts/method_workflow_diagrams.py`、`docs/analysis_methods/`、`docs/images/analysis_methods/`。
  - 提交：`docs(diagrams): add English EP method workflows`

- [ ] 任務 44：擴充 MCP language 提取（Green）
  - 預期行為：預設查詢仍回繁中；`method=<tool>&language=en` 回英文文字與 `.en.png`；MCP 邊界仍以 `figure_id` 取圖且不把 base64 留在文字 context。
  - 驗證指令：workflow、catalog parity、RBAC 與 Phase 4 測試及 Ruff。
  - 相關檔案：`analysis/workflow_guide.py`、`server/tool_catalog.py`。
  - 提交：`feat(mcp): retrieve English method workflows`

- [ ] 任務 45：雙語視覺 QA 與部署 smoke（Refactor）
  - 預期行為：英文長字串無碰撞／溢出；生成器重跑無差異；正式 checkout 與 build mirror 同步；只重建 Web，workers 不重啟；live MCP 成功取回英文 PNG。
  - 驗證指令：完整相關測試、30 個 self-check、SVG／PNG 檢查、contact sheet、hash gate、容器 health 與 live MCP smoke。
  - 相關檔案：上述所有檔案與部署驗收紀錄。
  - 提交：`docs(deploy): verify English workflow retrieval`

### 驗收標準

1. 15 個核心方法各有 `index.en.html`、`.en.svg` 與 `.en.png`，且中英文互相可達。
2. 英文圖完整表達原中文圖的流程與限制，不翻譯或改寫穩定的 tool name、檔名與欄位。
3. 所有英文 HTML 通過 Diagram Design self-check，PNG 為 RGB 1600×2107，英文標題及右側 resource pill 不重疊。
4. `language` 預設保持 `zh-Hant`；英文 method 經 `figure_id` 成功取回；既有 domain／method 查詢不回歸。
5. 部署完成後三服務健康、running_count=0，且原有正式 checkout dirty files 完整保留。
