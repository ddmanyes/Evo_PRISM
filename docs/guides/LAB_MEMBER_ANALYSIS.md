# EP 實驗室成員連線、排隊分析與資料匯入

這份流程讓實驗室同仁用個別 EP API key 查詢資料庫、提交分析，並用 Tailscale SFTP 或 rsync 傳入已處理好的資料。第一版不接受 FASTQ 前處理，也不提供 NDPI 轉檔。

## 權限與執行模型

| 角色 | 即時查詢 | 排隊分析 | 自己的 staging | Key/高權限維運 |
|---|---:|---:|---:|---:|
| `viewer` | 是 | 否 | 否 | 否 |
| `analyst` | 是 | 是 | 驗證與提升 | 否 |
| `admin` | 是 | 是 | 全部 | 是 |

查詢工具不進佇列，直接回應。重量分析必須用 `bio_submit_analysis`；CPU 與 GPU worker 各自單件執行，不會佔住 MCP 請求。`requested_by` 來自通過驗證的 key，使用者參數無法冒名。

## 管理員：發行與撤銷 key

1. 先套用 schema v26（key/audit）與 v27（job queue）migration。容器 entrypoint 會用 `scripts/run_pending_migrations.py` 依版本處理。
2. 用 admin 身分呼叫：

```json
{
  "tool": "bio_manage_api_key",
  "arguments": {
    "action": "issue",
    "user_id": "alice@example.org",
    "role": "analyst"
  }
}
```

3. `api_key` 只在這次回應顯示。用實驗室已核准的私密通道交給本人，不要放在 Git、Second Brain、email 群組或截圖。伺服器只儲存 SHA-256 hash 與顯示用 prefix。
4. 只查 metadata：

```json
{"tool":"bio_manage_api_key","arguments":{"action":"list","user_id":"alice@example.org"}}
```

5. 離職、遺失或洩漏時，立即用 list 顯示的 `key_prefix` 撤銷：

```json
{"tool":"bio_manage_api_key","arguments":{"action":"revoke","key_prefix":"epk_examplepref"}}
```

已撤銷 key 會立即失效，不會回退為舊的 owner token。`MCP_AUTH_TOKEN`/`MCP_AUTH_TOKENS` 只是管理員相容入口，不應分發給成員。

## 成員：連入與使用佇列

Tailscale/對外服務必須使用 `MCP_AUTH_REQUIRED=true`（Docker Compose 已強制）。MCP client 連至 `http://<tailscale-host>:8000/mcp`，HTTP header 帶：

```text
Authorization: Bearer epk_...
```

一般查詢（例如 `bio_history_lookup`、`bio_sample_list`）直接呼叫。分析請交給佇列：

```json
{
  "tool": "bio_submit_analysis",
  "arguments": {
    "tool_name": "bio_run_deg",
    "arguments": {"sample_id": "lab-sample-1"},
    "max_retries": 1
  }
}
```

保留回傳的 `job_id`，之後用：

```json
{"tool":"bio_job_status","arguments":{"job_id":"<job-id>"}}
{"tool":"bio_list_jobs","arguments":{"status":"queued","limit":20}}
{"tool":"bio_cancel_job","arguments":{"job_id":"<job-id>"}}
```

`analyst` 只看得到並只能取消自己的 job。已排隊的 job 會立即變成 `canceled`；執行中 job 是協作式取消，現有分析函式不保證在中間強制終止，但完成後不會以成功結案。

## 資料傳入：Tailscale SFTP 或 rsync

先呼叫 `bio_staging_info`取得自己的 actor directory。每次上傳建立獨立 `upload_id`：

```text
<actor-directory>/
└── <upload-id>/
    ├── MANIFEST.json
    └── <analysis-ready files>
```

允許的第一版格式：

| `data_type` | `platform` | 至少需要 |
|---|---|---|
| `bulk_rnaseq` | `kallisto` | `abundance.tsv` 或 `abundance.h5` |
| `bulk_rnaseq` | `salmon` | `quant.sf` |
| `scrna` | `cellranger` | `filtered_feature_bc_matrix.h5` |
| `visium` | `10x_visium` | filtered matrix + `tissue_positions*` |
| `visium_hd` | `10x_visium_hd` | filtered matrix + `tissue_positions*` |
| `proteomics` | `maxquant` | `proteinGroups.txt` |

Manifest 範例（每個實際檔案都必須列出）：

```json
{
  "manifest_version": 1,
  "sample_id": "lab-sample-1",
  "project": "crc",
  "data_type": "bulk_rnaseq",
  "platform": "kallisto",
  "species": "human",
  "tissue": "colon",
  "files": [
    {"path": "sample-a/abundance.tsv", "sha256": "<64 lowercase hex>"}
  ]
}
```

先傳數據，最後才傳 manifest，避免把半套資料當成完成包：

```bash
rsync -av --partial --exclude MANIFEST.json ./upload/ \
  '<ssh-user>@<tailscale-host>:<actor-directory>/<upload-id>/'
rsync -av ./upload/MANIFEST.json \
  '<ssh-user>@<tailscale-host>:<actor-directory>/<upload-id>/MANIFEST.json'
```

SFTP 也依相同原則：`put -r` 數據後才 `put MANIFEST.json`。EP API key 只用於 MCP；SSH/SFTP 身分由 Tailscale SSH 或伺服器 SSH 帳號另行管理，不共用密鑰。

上傳完成後先做無寫入驗證，再明確提升：

```json
{"tool":"bio_validate_staged_upload","arguments":{"upload_id":"<upload-id>"}}
{"tool":"bio_promote_staged_upload","arguments":{"upload_id":"<upload-id>"}}
```

驗證會拒絕 checksum 不符、未列檔案、未知格式、`..` 路徑、symlink、AppleDouble、其他使用者的 staging 與重複 `sample_id`。提升時先完整重驗，再以同檔案系統 rename 移入 L3；registry 寫入失敗時會搬回 staging。

## 伺服器資料目錄與預先佈建

Docker 把主機 `./lab_exchange` 整個掛載到 `/data/bio_db/lab_exchange`，使 staging 與 promoted L3 位於同一 mount。管理員可先 dry-run：

```bash
python scripts/provision_staging_user.py --user-id alice@example.org
python scripts/provision_staging_user.py --user-id alice@example.org --apply
```

`--apply` 只建立 EP actor directory 並設為 `0700`，不會建立 macOS/Linux 帳號，也不會修改 `sshd_config`。若要在 OS 層隱藏不同成員的原始上傳，應另建獨立 SSH 帳號/chroot；EP 本身在 validation/promotion 層強制 actor ownership。

Compose 以共用 named volume 上的 application lock 序列化 DuckDB server/worker 連線，避免多進程同時開啟檔案。實驗室使用人數或同時分析量增加後，應切換 `ER_DB_BACKEND=postgres`，以 PostgreSQL 作為生產佇列metadata後端。Windows 的單機 DuckDB 模式沒有 POSIX inter-process lock，不可用於這種多worker部署。

## 復原與稽核

- Worker 中斷：job 保留在 `analysis_jobs`；lease 到期後可由同類 CPU/GPU worker 重新領取，最多重試 3 次。
- 卡住：用 `bio_job_status` 看 heartbeat/worker 狀態；管理員再檢查 `docker compose logs analysis-worker-cpu analysis-worker-gpu`。
- 取消：先用 `bio_cancel_job`；不直接改 DB。
- 失敗提升：資料應留在 staging。若遇到極端的 DB/檔案系統雙重失敗，管理員依 `access_audit_log`、sample registry 與目錄實態對帳後再人工修復。
- Audit 只寫 actor、role、tool、target、outcome 與簡短原因，不寫 API key、manifest 內容或分析參數。

本次實作不會自動建立真實成員、不搬移現有研究資料，也不自動重啟正式服務。
