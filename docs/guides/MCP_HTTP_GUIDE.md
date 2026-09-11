# MCP HTTP Transport 使用指南

> Phase 10 — 將 `server/bio_memory_server.py` 以 HTTP transport 暴露於 `http://<host>:8000/mcp/`，
> 供 Claude Code CLI、curl、Python httpx 等客戶端直接呼叫 Evo_PRISM 工具。

## 1. 啟動方式

### 1.1 透過 web_app（推薦，與 Web UI 共用 port 8000）
`server/web_app.py` 已在 `lifespan` 中驅動 MCP `session_manager.run()`，並 mount 於 `/mcp`。
launchd `com.hermes.webserver` 啟動後即可用：

```bash
curl http://localhost:8000/health      # web_app 健康檢查
curl -X POST http://localhost:8000/mcp/   # MCP endpoint（需正確 headers，見下）
```

### 1.2 獨立 HTTP server（debug 用）
```bash
~/.venvs/hermes-bio-memory/bin/python -m server.bio_memory_server --transport http --port 8082
```

## 2. 必填 Headers

`StreamableHTTPSessionManager` 預期 SSE 風格回應，**Accept 必須同時包含**
`application/json` 與 `text/event-stream`，否則伺服器會回 400/500。

```
Content-Type: application/json
Accept: application/json, text/event-stream
```

## 3. 最小可行 curl 範例

### 3.1 Initialize（必先呼叫）
```bash
curl -N -X POST http://localhost:8000/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
      "protocolVersion": "2024-11-05",
      "capabilities": {},
      "clientInfo": {"name": "curl", "version": "1"}
    }
  }'
```

回應（SSE）：
```
event: message
data: {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05",...,"serverInfo":{"name":"bio-memory","version":"1.27.1"}}}
```

### 3.2 列出所有工具
```bash
curl -N -X POST http://localhost:8000/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
```

### 3.3 呼叫工具
```bash
curl -N -X POST http://localhost:8000/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{
    "jsonrpc": "2.0",
    "id": 3,
    "method": "tools/call",
    "params": {
      "name": "bio_history_lookup",
      "arguments": {"sample_id": "crc_official_v4", "limit": 5}
    }
  }'
```

## 4. Python httpx 範例

```python
import httpx

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}

with httpx.Client(timeout=30) as client:
    r = client.post(
        "http://localhost:8000/mcp/",
        headers=HEADERS,
        json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "py", "version": "1"}},
        },
    )
    for line in r.text.splitlines():
        if line.startswith("data: "):
            print(line[6:])
```

## 5. 已暴露的工具

預設暴露 36 個安全工具，涵蓋歷史查詢、記憶、樣本登記、ENGRAM artifact、分析執行、
MCseg 與結果交付；完整清單以 `tools/list` 回應為準。`bio_execute_code` 是第 37 個高權限
工具，只有 `MCP_ENABLE_DANGEROUS_TOOLS=true` 時才會出現。

### 5.1 遠端圖片與數據交付

| 工具 | 用途 |
|------|------|
| `bio_get_figure` | 依 `figure_id` 回傳 `ImageContent`，並附 `figure://` ResourceLink |
| `bio_get_artifact` | 依 `artifact_id` 回傳 metadata、預覽與 `artifact://` ResourceLink |
| `bio_deliver_results` | 依 analysis/artifact/figure selector 交付圖片與數據；小檔可 embedded，多檔可建立 ZIP |

MCP server initialization 會把下列契約交給客戶端 Agent：

- 只有使用者明確要求「查看、給我、附上、下載、全部或打包」時才呼叫
  `bio_deliver_results`；一般分析與摘要不夾帶二進位內容。
- `server_local_path` 是 MCP 主機上的診斷資訊，不代表遠端使用者已取得附件。
- selector 無法唯一判定時必須先澄清，不可跨樣本猜測。
- 只要求數據時使用 `include=data`；只要求圖片時使用 `include=images`；明確要求全部才用
  `include=all`。
- 單一數據檔用 `package=never`；多個數據檔可用 `package=auto`；明確要求打包則用
  `package=zip`。
- 圖片預設直接顯示且附下載 ResourceLink；只有使用者要求圖片也進壓縮檔時，才設
  `include_images_in_zip=true`。

範例：交付一次分析的所有已登記數據；若超過一個數據檔，自動建立 ZIP：

```json
{
  "name": "bio_deliver_results",
  "arguments": {
    "analysis_id": "<analysis UUID>",
    "include": "data",
    "package": "auto",
    "include_images_in_zip": false
  }
}
```

ZIP 使用 content-addressed `delivery://<sha256>` URI，可透過 `resources/read` 取得；ZIP
內含 `manifest.json`，記錄來源 ID、檔名、MIME、大小與 SHA-256。相同內容與選取集合會
重用同一個 bundle。`artifact://`、`figure://`、`delivery://` 都由 MCP server 驗證路徑後
讀取，不接受客戶端提供任意主機路徑。未超過 `ARTIFACT_RESOURCE_MAX_MB` 的數據或 ZIP
也會附 `EmbeddedResource`，供尚未顯示 ResourceLink 的客戶端相容使用。

## 6. 常見錯誤排查

| 症狀 | 原因 | 處置 |
|------|------|------|
| `500 Internal Server Error` | session_manager 未啟動（FastAPI 不傳遞 lifespan 給 mount 的子 ASGI app） | 已修：`web_app._lifespan` 統一驅動 `mcp_lifespan_cm` |
| `406 Not Acceptable` | `Accept` 缺少 `text/event-stream` | 補上正確 Accept header |
| `400 Bad Request` | 缺 `initialize` 或 `protocolVersion` | 第一個請求必須是 initialize |
| 回應卡住 | 沒帶 `-N`（curl 不關閉 SSE 流） | curl 加 `-N`，或讀 `r.iter_lines()` |

## 7. 部署注意

- **綁定主機**：env `MCP_BIND_HOST`，預設 `127.0.0.1`（僅本機）；對外開放改 `0.0.0.0`
- **認證**：設定 `MCP_AUTH_TOKEN` 後，HTTP MCP 請求必須帶 `Authorization: Bearer <token>`
- **速率限制**：embedding、分析與沙盒等重量級工具受 `MCP_RATE_LIMIT_PER_MIN` 保護
- **交付限制**：`ARTIFACT_RESOURCE_MAX_MB`、`DELIVERY_MAX_ITEMS` 與
  `DELIVERY_MAX_TOTAL_MB` 可依部署資源調整
