# Evo PRISM 可選 SSD 掛載實作計畫

## 目標摘要

讓 Evo PRISM 核心 MCP 預設只依賴 `/Volumes/KINGSTON/Evo_PRISM`，即使 `/Volumes/SSD` 未掛載也能啟動；需要 MCseg 讀取 SSD 上原始 L3 資料時，才透過額外的 Compose override 明確加入唯讀掛載。

## 文件架構圖

```text
Evo_PRISM/
├── docker-compose.yml              # 修改：移除核心服務對 SSD 的強制依賴
├── docker-compose.ssd.yml          # 新增：SSD 模式的唯讀掛載 override
├── README.md                       # 修改：補充核心與 SSD 模式指令
├── README_zh.md                    # 修改：補充中文操作說明
├── docs/guides/
│   └── OPTIONAL_SSD_MOUNT.md       # 新增：行為、啟動與拔除流程
└── IMPLEMENTATION_PLAN.md          # 本計畫
```

## 任務列表

- [x] 任務 1：建立可驗證的 Compose 行為基線
  - 預期行為：目前基礎設定包含 `/Volumes/SSD:/data/external_ssd:ro`，作為紅燈證據。
  - 驗證指令：`docker compose config | rg '/Volumes/SSD|/data/external_ssd'`
  - 相關檔案：`docker-compose.yml`
  - 提交：`docs(plan): define optional SSD mount rollout`

- [x] 任務 2：將 SSD 掛載拆成 opt-in override
  - 紅燈：基礎 Compose 仍包含 SSD 掛載，無 SSD 時容器建立失敗。
  - 綠燈：基礎 Compose 不包含 `/Volumes/SSD`；合併 `docker-compose.ssd.yml` 後才包含唯讀掛載。
  - 重構：保留主資料掛載順序與註解，讓核心／SSD 模式的責任清楚分離。
  - 驗證指令：
    - `docker compose config | rg '/Volumes/SSD|/data/external_ssd'` 應無結果。
    - `docker compose -f docker-compose.yml -f docker-compose.ssd.yml config | rg '/Volumes/SSD|/data/external_ssd|read_only'` 應顯示 SSD 唯讀掛載。
  - 相關檔案：`docker-compose.yml`、`docker-compose.ssd.yml`
  - 提交：`fix(compose): make external SSD mount optional`

- [x] 任務 3：文件化兩種啟動模式與安全切換
  - 預期行為：使用者能區分預設核心模式與 SSD/MCseg 模式，並知道拔除 SSD 前要切回核心模式。
  - 驗證指令：`rg -n 'docker-compose.ssd.yml|核心模式|SSD mode' README.md README_zh.md docs/guides/OPTIONAL_SSD_MOUNT.md`
  - 相關檔案：`README.md`、`README_zh.md`、`docs/guides/OPTIONAL_SSD_MOUNT.md`
  - 提交：`docs(compose): document optional SSD workflow`

- [ ] 任務 4：重建並驗證核心 MCP
  - 預期行為：容器使用基礎 Compose 重建後為 healthy，主資料仍掛載自 KINGSTON，容器掛載列表不含 `/Volumes/SSD`，`/health` 回覆 200，授權 MCP initialize 成功。
  - 驗證指令：
    - `docker compose up -d --force-recreate`
    - `docker compose ps`
    - `docker inspect evo_prism-evo-prism-1`
    - `curl http://100.87.59.15:8000/health`
    - 使用既有本機憑證對 `/mcp/` 執行 MCP initialize，只回報狀態、不輸出密鑰。
  - 相關檔案：無額外檔案變更。
  - 提交：不建立額外提交；記錄驗證結果於交付摘要。

- [x] 任務 5：同步 SSD 的 MSseg runtime backend
  - 預期行為：以乾淨的 `/Volumes/SSD/plan_a/MSseg/backend` 為來源，更新 KINGSTON
    的嵌入副本與 APFS Docker build mirror，再將 KINGSTON backend 唯讀掛入容器；
    不包含 frontend、Tauri、資料、結果、快取或任何 host venv。
  - 安全措施：先備份目前 backend；不使用 `--delete`，不修改既有
    `analysis/mcseg_wrapper.py` 工作樹變更。
  - 驗證指令：對兩個目的端執行 `rsync -ainc --delete` checksum dry-run，除目錄
    時戳外應無檔案差異；再執行 MSseg backend 與 Evo PRISM MCseg 相關測試。
  - 相關目錄：`MSseg/backend/`、`/Users/lab_center/evo_prism_build/MSseg/backend/`、
    容器 `/app/MSseg/backend`
  - 提交：runtime 副本受 `.gitignore` 排除；以本計畫的完成勾選記錄同步作業。

## 邊界條件

- 不修改或提交既有的 `analysis/mcseg_wrapper.py` 工作樹變更。
- 不移動、不複製、不刪除 KINGSTON 或 SSD 上的研究資料。
- 預設核心模式不提供 `/data/external_ssd`；依賴 SSD 原始資料的 MCseg 呼叫應明確失敗，而不是靜默使用空目錄。
- SSD 模式仍要求 SSD 在容器建立與重啟時存在；拔除前應切回核心模式。
