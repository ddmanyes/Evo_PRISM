# Evo-PRISM 多使用者部署 Readiness 與 Rollback

狀態：`READY_FOR_MAINTENANCE`。程式、正式資料庫還原副本與三角色 fixture 已驗證；正式服務仍在舊 image 上運行，尚未套用 migration、建立成員 key 或啟動 workers。

## 已驗證基線

- 正式 checkout 已 fast-forward，使用者既有的 `analysis/mcseg_wrapper.py` 修改與未追蹤 `scripts/start_webserver.py` 保持原樣；兩者不納入這次 clean build。
- 主 DuckDB 備份：`/Users/lab_center/bio_db_backups/20260909_1918`，146,207,025 bytes；還原副本為 129 個 sample、357 筆 analysis history。
- L1 備份：`/Users/lab_center/evo_prism_preflight_backups/l1_20260909_191917`；還原成功，既有 cache 為 0 筆。
- 正式既有 schema v26 是 embedding migration；多使用者 auth 使用 v27，job queue 使用 v28。兩者會先載入 VSS，已在含 HNSW index 的正式還原副本驗證。
- Migration runner 與 L1 owner migration 各連跑兩次，第二次為安全 no-op；`api_keys`、`access_audit_log`、`analysis_jobs` 與 `memory_recent.requested_by` 均存在。
- Alice／Bob／admin、key revoke、own-only history、shared sample registry、queue／worker 與 staging promotion gate：51 passed；完整相關 gate：234 passed。
- APFS Docker build mirror 仍是舊內容；正式容器目前 healthy，但只有 `evo-prism`，新 CPU/GPU workers 尚未部署。

## Maintenance window 執行順序

以下步驟必須在核准的維護時段依序執行。任何一步失敗就停止，不進下一步。

### 1. 固定 rollback 點

再次執行既有 `scheduler.backup_db.backup()`，並用 VSS 載入後的暫存 `IMPORT DATABASE` 驗證。另對 `gold/hermes_cache.duckdb` 做 DuckDB `EXPORT DATABASE`。保留本文件上方已驗證的 20260909 備份，不覆寫或刪除。

在 build 前保留目前 image：

```bash
docker image tag evo_prism-evo-prism:latest \
  evo_prism-evo-prism:pre-multiuser-20260909
```

### 2. 從 clean worktree 更新 APFS build context

不得從含 dirty files 的 `/Volumes/KINGSTON/Evo_PRISM` 直接同步。只複製隔離 worktree 的 Git tracked files，且不使用 `--delete`，讓既有 `MSseg/` runtime 保留：

```bash
cd /Users/lab_center/evo_prism_queue_worktree
git ls-files -z | rsync -a --from0 --files-from=- ./ /Users/lab_center/evo_prism_build/
```

確認 build mirror 有新檔且沒有已淘汰的碰撞版本：

```bash
test -f /Users/lab_center/evo_prism_build/scripts/27_migrate_schema_v27_auth.py
test -f /Users/lab_center/evo_prism_build/scripts/28_migrate_schema_v28_job_queue.py
test ! -e /Users/lab_center/evo_prism_build/scripts/27_migrate_schema_v26_auth.py
test ! -e /Users/lab_center/evo_prism_build/scripts/28_migrate_schema_v27_job_queue.py
```

`.dockerignore` 必須包含 `.env`、`.env.*`、`*.key` 與 `*.pem`；不得把任何本機 env 備份或 key 送進 image context。

### 3. 先 build，再進停機區段

```bash
cd /Volumes/KINGSTON/Evo_PRISM
docker compose build
```

Build 失敗時正式服務仍保持舊 image，直接停止 rollout。

### 4. 停止 writer 並套用正式 migration

```bash
docker compose stop evo-prism analysis-worker-cpu analysis-worker-gpu
env DUCKDB_PATH=/Volumes/KINGSTON/Evo_PRISM/bio_memory.duckdb \
  /Volumes/KINGSTON/Evo_PRISM/.venv/bin/python scripts/run_pending_migrations.py
/Volumes/KINGSTON/Evo_PRISM/.venv/bin/python \
  scripts/29_migrate_l1_cache_owner.py \
  --cache-path /Volumes/KINGSTON/Evo_PRISM/gold/hermes_cache.duckdb
```

預期主庫新增且各只有一筆 schema v27/v28；L1 應有 `requested_by`。若不是，保持服務停止並進 rollback。

### 5. 建立傳輸根目錄並啟動三個服務

先建立 `lab_exchange` 根目錄；每位成員的 actor directory 仍由發 key／provisioning 流程建立：

```bash
mkdir -p /Volumes/KINGSTON/Evo_PRISM/lab_exchange
docker compose up -d --force-recreate
docker compose ps
curl -fsS http://100.87.59.15:8000/health
```

驗收必須同時看到 `evo-prism`、`analysis-worker-cpu`、`analysis-worker-gpu` 正常；Web service 必須 healthy。

### 6. 暫時三角色 smoke

以既有 admin owner token 呼叫 `bio_manage_api_key`，建立只供 smoke 的 Alice analyst、Bob analyst 與 viewer key。明文 key 只保存在當次安全工作階段，絕不寫入 Git、log、Second Brain 或截圖。

驗收矩陣：

| 檢查 | Alice/Bob | viewer | admin |
|---|---|---|---|
| sample registry | 共用可見 | 共用可見 | 全部 |
| history/artifact/report/figure | 僅本人 | 僅本人 | 全部 |
| submit/cancel queue | 僅本人 | 拒絕 | 全部 |
| 直接讀他人 ID/path | not found | not found | 可讀 |
| revoked key | 401 | 401 | 依 key 狀態 |

再以一包不含真實研究內容的 Kallisto fixture 完成 staging validation → promotion → queue → worker → result。全部通過後撤銷三把 smoke key，再開始 1–2 位真實成員的 pilot。

## Rollback

以下任一條件立即 rollback：migration row／必要 table 不符、任一 worker crash-loop、健康檢查失敗、Alice/Bob 可交叉讀取、revoked key 仍可用，或 staging 可越權。

1. `docker compose stop evo-prism analysis-worker-cpu analysis-worker-gpu`，先停止所有 DuckDB writer。
2. 保留故障後 DB/L1 作為 forensic copy，不覆寫本文件列出的最後已驗證備份。
3. 從最新已驗證 EXPORT 匯入新的 DuckDB 檔案；確認 row counts 後，才在同一 KINGSTON filesystem 以 rename 置換主 DB。L1 依相同方式從獨立 export 還原。
4. 將 `evo_prism-evo-prism:pre-multiuser-20260909` 重新標為正式 image，只啟動 `evo-prism`，不要啟動新 workers。
5. 驗證 `/health`、sample count、history count與舊 MCP 查詢後，才結束 rollback；保留 audit、container logs 與故障 DB 供後續分析。

正式 restore/rename 是破壞性操作，必須依當次最新備份路徑產生明確命令並再次確認，不得直接複製本文件中的日期當成永遠有效的目標。
