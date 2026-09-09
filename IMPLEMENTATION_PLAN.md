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
├── scripts/27_migrate_schema_v26_auth.py
├── scripts/28_migrate_schema_v27_job_queue.py
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

- [ ] 任務 14：撰寫onboarding、資料傳輸與故障復原文件
  - 預期行為：文件涵蓋發key、SFTP/rsync、manifest、提交、查狀態、撤銷與worker恢復。
  - 驗證指令：文件契約字串檢查。
  - 相關檔案：`docs/guides/`
  - 提交：`docs(lab): document member queue and ingest workflow`

- [ ] 任務 15：完整驗證與安全回歸
  - 預期行為：完整測試、lint、schema parity、compose config與敏感資訊掃描通過。
  - 驗證指令：repo完整validation gate；測試不得讀寫真實資料。
  - 相關檔案：測試與必要修正。
  - 提交：`test(system): close multi-user queue validation gaps`

- [ ] 任務 16：部署前dry-run與交付
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

## 實作限制

- 使用`apply_patch`修改文字檔。
- 每個任務完成後執行驗證並建立原子commit。
- Repo 的 `.gitignore` 明定 `tests/` 為本機測試、不上傳 GitHub；因此純 Red 測試任務只建立本機驗證 checkpoint，不強制加入 Git。對應 Green 任務的正式程式 commit 必須附上通過的測試證據。
- 驗證失敗時先保留證據、修正計畫，再繼續。
- 部署、服務重啟、建立OS/SFTP帳號與真實資料搬移需獨立驗證；測試fixture不得寫入正式資料庫。
