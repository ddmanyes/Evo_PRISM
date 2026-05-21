# MCP HTTP Transport 使用指南

> Phase 10 — 將 `server/bio_memory_server.py` 以 HTTP transport 暴露於 `http://<host>:8000/mcp/`，
> 供 Claude Code CLI、curl、Python httpx 等客戶端直接呼叫 7 個生資記憶工具。

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

## 5. 已暴露的工具（7 個）

| 工具 | 用途 |
|------|------|
| `bio_history_lookup` | 依 sample_id 查 analysis_history 列表 |
| `bio_history_timeline` | 時序視圖（GROUP BY 日期） |
| `bio_history_check` | 檢查是否已分析過某個（sample, analysis_type） |
| `bio_history_search` | L1 cache 語意搜尋（threshold 預設 0.88，對齊 agent.py Cache Hit Protocol） |
| `bio_memory_query` | L1 memory_recent 語意查詢 |
| `bio_memory_write` | 寫入 L1 cache |
| `bio_register_sample` | 登記新樣本到 sample_registry |

> 分析工具（`bio_run_spatial_eda` / `bio_run_bulk_eda` / `bio_execute_code` / ENGRAM 5 件套）
> 目前僅 agent.py 內呼叫，未透過 MCP 暴露。PROGRESS.md MCP P0-2 已記錄此覆蓋缺口。

## 6. 常見錯誤排查

| 症狀 | 原因 | 處置 |
|------|------|------|
| `500 Internal Server Error` | session_manager 未啟動（FastAPI 不傳遞 lifespan 給 mount 的子 ASGI app） | 已修：`web_app._lifespan` 統一驅動 `mcp_lifespan_cm` |
| `406 Not Acceptable` | `Accept` 缺少 `text/event-stream` | 補上正確 Accept header |
| `400 Bad Request` | 缺 `initialize` 或 `protocolVersion` | 第一個請求必須是 initialize |
| 回應卡住 | 沒帶 `-N`（curl 不關閉 SSE 流） | curl 加 `-N`，或讀 `r.iter_lines()` |

## 7. 部署注意

- **綁定主機**：env `MCP_BIND_HOST`，預設 `127.0.0.1`（僅本機）；對外開放改 `0.0.0.0`
- **認證**：目前無 token 驗證（P1 待辦：`MCP_AUTH_TOKEN`），對外開放前務必加上
- **速率限制**：embedding 與 search 工具每次都打 llama-server，未做 rate limiting（MCP P1-4）
