# Evo PRISM 可選 SSD 掛載

Evo PRISM 的主專案、DuckDB、分析結果、工作佇列與 playbooks 均位於
`/Volumes/KINGSTON/Evo_PRISM`。`/Volumes/SSD` 只保存部分 MCseg 任務需要的
外部原始 L3 資料，不是核心服務的持久化來源。

MSseg 的 runtime backend 會先從獨立來源 repo 同步到
`/Volumes/KINGSTON/Evo_PRISM/MSseg/backend`，再由 Compose 唯讀掛入
`/app/MSseg/backend`。Docker 使用 image 內自己的 Linux Python 依賴；macOS 的
`.venv` 不會也不應進入容器。

## 核心模式（預設）

核心模式不建立 `/data/external_ssd` 掛載，因此 Docker 或電腦重啟時不要求
SSD 必須存在：

```bash
cd /Volumes/KINGSTON/Evo_PRISM
docker compose up -d --force-recreate
docker compose ps
curl -fsS http://100.87.59.15:8000/health
```

適用情境：MCP 查詢、歷史與 artifact 操作、一般分析，以及不讀取 SSD 原始檔的
工作。

## SSD／MCseg 模式（明確選用）

確認 SSD 已掛載後，合併 override 並重建同一個服務：

```bash
cd /Volumes/KINGSTON/Evo_PRISM
test -d /Volumes/SSD
docker compose -f docker-compose.yml -f docker-compose.ssd.yml \
  up -d --force-recreate
docker compose ps
```

此模式只把 `/Volumes/SSD` 唯讀掛載到容器的 `/data/external_ssd`；容器不會寫回
SSD。

可用以下指令確認掛載來源與唯讀旗標：

```bash
docker inspect --format \
  '{{range .Mounts}}{{println .Source "->" .Destination "rw=" .RW}}{{end}}' \
  evo_prism-evo-prism-1
```

輸出應包含：

```text
/Volumes/SSD -> /data/external_ssd rw= false
```

## 拔除 SSD 前

先切回核心模式，確認容器掛載列表不再包含 `/Volumes/SSD`，再退出硬碟：

```bash
cd /Volumes/KINGSTON/Evo_PRISM
docker compose up -d --force-recreate
docker inspect --format \
  '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}' \
  evo_prism-evo-prism-1 | grep /Volumes/SSD
```

最後一個指令應沒有輸出。不要使用空目錄假裝 SSD 已掛載；這會讓 MCseg 把
「原始資料缺失」誤判成「資料集為空」。

## SSD 被意外拔除

- 核心 MCP 程序可能暫時仍在，但依賴 SSD 的 MCseg 操作會失敗。
- 若容器是在 SSD 模式建立，Docker 下次重啟仍會嘗試恢復該掛載。
- 直接執行 `docker compose up -d --force-recreate` 可用基礎設定重建回核心模式；
  不需要修改或搬動 KINGSTON 上的資料。
