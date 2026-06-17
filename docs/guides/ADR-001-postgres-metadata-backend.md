# ADR-001: Metadata 後端從 DuckDB 遷移至 PostgreSQL

**狀態**: Accepted  
**日期**: 2026-06-17  
**作者**: @zhanqiru  

---

## 背景

Evo_PRISM 的 metadata（`sample_registry`、`analysis_history`、HELIX 工具表、artifacts）
原存於 `/Volumes/KINGSTON/bio_DB/bio_memory.duckdb`（ExFAT 外接碟上的 DuckDB 文件）。

BAR（Bioinformatics Auto-Research）要求 ≥5 個分析 agent 並發寫入 `analysis_history`。
DuckDB 文件資料庫的寫入序列化模型（單一寫者鎖）在多進程情境下會產生 `BUSY` 錯誤；
ExFAT 無日誌系統在斷電時有 WAL 損壞風險，需要 `CHECKPOINT` 補丁。

---

## 決策

將 **A 類 metadata**（registry / history / tools / artifacts）後端改為
`sb-pg` Docker 容器（`pgvector/pgvector:pg16`，`127.0.0.1:5432`）中的 `evo_prism` 資料庫，
由 `RegistryStore` 抽象層切換（環境變數 `ER_DB_BACKEND=postgres`）。

**保留 DuckDB** 用於：
- B 類：`bio_DB/silver/*.parquet` Parquet 分析引擎（read-only，per-process 臨時開）
- L1 cache：`bio_DB/gold/hermes_cache.duckdb`（embedding / HNSW index）

---

## 遷移階段

| 階段 | 內容 | 狀態 |
| ---- | ---- | ---- |
| P1 | `RegistryStore` 抽象 + `DuckDBStore` 實作；server/agent 層全部改走 `get_store()` | ✅ |
| P1b | 移除所有剩餘 `duckdb.connect(DUCKDB_PATH)` 呼叫點 | ✅ |
| P2 | `PostgresStore` 實作；`pg_schema.sql` 建表；pytest 與 DuckDB 後端對齊 | ✅ |
| P3 | 5-worker 並發壓測：100 rows in 0.8s，零 lock error，DB count 精確 | ✅ |
| P4 | `safe_write`/`CHECKPOINT` ExFAT 補丁改為 Postgres 相容（try/except 靜默跳過） | ✅ |
| P5 | port 只綁 127.0.0.1；launchd 每日 04:30 pg_dump 備份（保留 14 份） | ✅ |
| P6 | CLAUDE.md 更新拓樸說明；本 ADR；BAR §7.2 回填 | ✅ |

---

## 結果

- **並發安全**：Postgres MVCC 允許 ≥5 進程同時寫入，無 lock 衝突
- **耐久性**：Postgres WAL 取代 ExFAT 補丁，不再需要 CHECKPOINT-on-write
- **測試對齊**：`ER_DB_BACKEND=postgres pytest` 與 DuckDB 模式完全相同（36 failed / 600 passed）
- **向下相容**：`ER_DB_BACKEND=duckdb`（預設）行為不變，現有 script 無需修改

---

## 連線資訊

| 項目 | 值 |
| ---- | -- |
| Container | `sb-pg`（`pgvector/pgvector:pg16`）|
| Host port | `127.0.0.1:5432` |
| Production DB | `evo_prism` |
| Test DB | `evo_prism_test` |
| User | `er_rw` |
| Schema | `scripts/pg_schema.sql` |
| Backup | launchd `com.user.evo-prism-pg-backup`，`scripts/pg_backup_evo_prism.sh` |

---

## 替代方案（不採用）

- **DuckDB WAL-only 修法**：不解決多進程寫入鎖問題；ExFAT 斷電風險仍存在
- **SQLite**：無 pgvector；未來 embedding 列遷移困難
- **將 DuckDB 移到 HFS+ 碟**：解決 ExFAT 問題但不解決並發；且 BAR 需在多機運行
