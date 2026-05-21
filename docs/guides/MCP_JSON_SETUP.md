# MCP JSON 設定指南

> 適用於 `.mcp.json`（Claude Code 等 MCP 客戶端會自動讀取的設定檔）。
> 範本見 `.mcp.json.example`；複製為 `.mcp.json`（已 gitignored）後填入實際路徑。

## 基本步驟

1. 複製範本：
   ```bash
   cp .mcp.json.example .mcp.json
   ```

2. 修改三個欄位為**絕對路徑**：
   - `command`：venv Python 路徑（建議放在 APFS，例如 `~/.venvs/hermes-bio-memory/bin/python`；ExFAT 上的 venv 會壞）
   - `args[0]`：`bio_memory_server.py` 絕對路徑
   - `env.PYTHONPATH`：專案根目錄絕對路徑

3. 重啟 Claude Code 或對應 MCP 客戶端讓設定生效。

## 路徑含空格或中文（macOS Google Drive、ExFAT）

JSON 字串可直接包含空格與中文，**不需要特殊跳脫**。例如：

```json
"args": ["/Users/foo/Library/CloudStorage/GoogleDrive-x@x.com/我的雲端硬碟/PJ_save/bio_DB/server/bio_memory_server.py"]
```

若 MCP 客戶端報告 `cannot find module` 或 `invalid path`，先用 `ls` 確認路徑實際存在後再調整。

Linux 部署時建議移至純 ASCII 路徑（例：`/mnt/space4/bio_lab_db/`），避免任何客戶端解析問題。

## 環境變數說明

| Env Var | 預設 | 說明 |
| ------- | ---- | ---- |
| `PYTHONPATH` | — | 必填。指向專案根目錄，讓 `from server.agent import ...` 可解析 |
| `MCP_AUTH_TOKEN` | 空字串（auth 關閉） | 設定後 HTTP 端必須帶 `Authorization: Bearer <token>`；空字串時為純本機開發模式 |
| `MCP_BIND_HOST` | `127.0.0.1` | HTTP transport 綁定位址。設 `0.0.0.0` 開放區網**前必須**搭配 `MCP_AUTH_TOKEN` |
| `MCP_RATE_LIMIT_PER_MIN` | `30` | 重量級工具（embedding/sandbox）每分鐘呼叫上限 |
| `MCP_ENABLE_DANGEROUS_TOOLS` | 未設（關閉） | 設 `true` 才會將 `bio_execute_code`（沙盒 Python 執行）暴露給 MCP 客戶端。defense in depth — 即使 auth 失誤也不會洩漏沙盒入口 |

## 安全建議

- **本機開發**：`MCP_BIND_HOST=127.0.0.1` 即可，可不設 token
- **區網／團隊共用**：必須設 `MCP_AUTH_TOKEN`（建議 32+ char 隨機字串）且不要設 `MCP_ENABLE_DANGEROUS_TOOLS`
- **真要暴露 `bio_execute_code`**：必須同時 `MCP_AUTH_TOKEN` + `MCP_BIND_HOST=127.0.0.1` + 沙盒白名單嚴格審查

## 工具列表

預設啟動 13 個 MCP 工具；`MCP_ENABLE_DANGEROUS_TOOLS=true` 才會加上第 14 個 `bio_execute_code`。詳細工具表見 [MCP_HTTP_GUIDE.md](MCP_HTTP_GUIDE.md)。
