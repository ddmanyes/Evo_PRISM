# Evo_PRISM Windows 安裝指南

本指南說明如何在 Windows 系統（外接硬碟，如 `D:\Evo_PRISM\`）完整部署 Evo_PRISM。

---

## 前置需求

| 需求 | 版本 | 說明 |
|------|------|------|
| Windows | 10 / 11 (64-bit) | 建議 Windows 11 |
| Python | 3.11 或 3.12 | 從 [python.org](https://www.python.org/downloads/) 安裝，**務必勾選 Add to PATH** |
| uv | 最新版 | Python 套件管理器 |
| Git | 任意版本 | 選用，用於版本控制 |
| llama.cpp | 最新 release | Embedding server 與本機推理 |

---

## 步驟一：解鎖 PowerShell 執行權限

以**系統管理員身份**開啟 PowerShell，執行一次：

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

---

## 步驟二：安裝 uv

```powershell
pip install uv
```

或使用官方安裝腳本：

```powershell
irm https://astral.sh/uv/install.ps1 | iex
```

---

## 步驟三：建立 Python 虛擬環境

```powershell
# 在家目錄建立 venv（避免外接硬碟 ExFAT 權限問題）
uv venv "$env:USERPROFILE\.venvs\evo-prism" --python 3.11

# 切換到專案目錄（D 槽外接硬碟）
cd D:\Evo_PRISM

# 安裝所有依賴
uv sync --no-install-project
```

> **注意**：Windows 上不需要建立 symlink，venv 直接以完整路徑引用。

---

## 步驟四：下載 llama.cpp Windows 執行檔

1. 前往 [llama.cpp GitHub Releases](https://github.com/ggerganov/llama.cpp/releases)
2. 下載最新版 `llama-*-win-cuda-cu12.*.zip`（NVIDIA GPU）或 `llama-*-win-noavx512-x64.zip`（純 CPU）
3. 解壓至 `%USERPROFILE%\llama.cpp\`，確認路徑：
   ```
   C:\Users\你的使用者名稱\llama.cpp\llama-server.exe
   ```

### 下載 Embedding 模型（必要）

```powershell
# 建立模型目錄
New-Item -ItemType Directory -Force "$env:USERPROFILE\llama.cpp\models"

# 下載 bge-m3 Q8（約 605 MB）— 從 Hugging Face
# 建議使用 huggingface-cli 或直接瀏覽器下載：
# https://huggingface.co/gpustack/bge-m3-GGUF/resolve/main/bge-m3-Q8_0.gguf
# 下載後放至：
# C:\Users\你的使用者名稱\llama.cpp\models\bge-m3-Q8_0.gguf
```

### 下載 Vision 模型（僅 `--local` 模式需要）

Gemma 4 Vision 模型（約 16 GB）可跳過，改用 Claude API 或 Google Gemini API。

---

## 步驟五：設定環境變數

```powershell
cd D:\Evo_PRISM

# 複製 Windows 設定範本
Copy-Item .env.windows.example .env
```

用記事本或 VS Code 開啟 `.env`，填入實際路徑：

```ini
BIO_DB_ROOT=D:\Evo_PRISM
LLAMACPP_BIN=C:\Users\你的使用者名稱\llama.cpp\llama-server.exe
LLAMACPP_MODEL_PATH=C:\Users\你的使用者名稱\llama.cpp\models\bge-m3-Q8_0.gguf
INFERENCE_BACKEND=claude
ANTHROPIC_API_KEY=你的金鑰
```

---

## 步驟六：初始化資料庫

```powershell
cd D:\Evo_PRISM

# 使用 venv 的 Python 初始化 Schema
& "$env:USERPROFILE\.venvs\evo-prism\Scripts\python.exe" scripts\00_init_db.py

# 套用所有後續 migration
Get-ChildItem scripts\[0-9][0-9]_migrate_schema_*.py | Sort-Object Name | ForEach-Object {
    & "$env:USERPROFILE\.venvs\evo-prism\Scripts\python.exe" $_.FullName
}
```

預期輸出：`✅ Schema initialized successfully`

### 健康檢查

```powershell
& "$env:USERPROFILE\.venvs\evo-prism\Scripts\python.exe" config\db_utils.py
```

---

## 步驟七：啟動所有服務

```powershell
cd D:\Evo_PRISM

# 互動式選擇後端（建議首次使用）
.\start_bioagent.ps1

# 或直接指定後端
.\start_bioagent.ps1 --claude   # 使用 Claude API（推薦）
.\start_bioagent.ps1 --google   # 使用 Google Gemini
.\start_bioagent.ps1 --local    # 使用本機 Gemma 4（需下載模型）
```

服務啟動後開啟瀏覽器：[http://localhost:8000](http://localhost:8000)

### 停止所有服務

```powershell
.\stop_bioagent.ps1
```

---

## 步驟八：設定 Claude Code MCP（stdio 模式）

在 Claude Code CLI 的 `.mcp.json` 加入：

```json
{
  "mcpServers": {
    "bio-memory": {
      "command": "C:\\Users\\你的使用者名稱\\.venvs\\evo-prism\\Scripts\\python.exe",
      "args": ["D:\\Evo_PRISM\\server\\bio_memory_server.py"],
      "env": {
        "BIO_DB_ROOT": "D:\\Evo_PRISM",
        "INFERENCE_BACKEND": "claude",
        "ANTHROPIC_API_KEY": "你的金鑰"
      }
    }
  }
}
```

> **路徑注意**：JSON 中反斜線需雙寫（`\\`）。

---

## 常見問題

### `uv sync` 失敗 — `hatch` build error

```powershell
uv sync --no-install-project
```

必須加 `--no-install-project`，否則 hatchling 會嘗試 build 不存在的 package。

### PowerShell 回報「無法載入檔案，因為在這個系統上停用指令碼執行」

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### llama-server.exe 無法執行（缺少 DLL）

安裝 [Visual C++ Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe)。

### Embedding server health check 失敗

確認防火牆未封鎖 port 8081：

```powershell
New-NetFirewallRule -DisplayName "llama-embed" -Direction Inbound -Protocol TCP -LocalPort 8081 -Action Allow
```

### DuckDB 路徑錯誤（含中文或空格）

若外接硬碟路徑含中文或空格，在 `.env` 用引號包住：

```ini
BIO_DB_ROOT="D:\我的資料\Evo_PRISM"
```

或使用無空格的簡短路徑（建議）：

```powershell
# 建立短路徑 subst（本次工作階段有效）
subst B: "D:\複雜路徑\Evo_PRISM"
# 然後在 .env 使用 BIO_DB_ROOT=B:\
```

---

## macOS ↔ Windows 外接硬碟切換

| 事項 | macOS | Windows |
|------|-------|---------|
| 專案根目錄 | `/Volumes/KINGSTON/Evo_PRISM/` | `D:\Evo_PRISM\` |
| venv Python | `~/.venvs/evo-prism/bin/python` | `%USERPROFILE%\.venvs\evo-prism\Scripts\python.exe` |
| llama-server | `~/llama.cpp/build/bin/llama-server` | `%USERPROFILE%\llama.cpp\llama-server.exe` |
| 啟動腳本 | `bash start_bioagent.sh` | `.\start_bioagent.ps1` |
| 停止腳本 | `bash stop_bioagent.sh` | `.\stop_bioagent.ps1` |
| 環境設定 | `.env`（Unix 格式路徑） | `.env`（Windows 路徑，改用 `.env.windows.example`） |

**DuckDB 資料庫完全跨平台**：`bio_memory.duckdb` 格式相同，直接用外接硬碟搬移即可，無需轉換。

---

## 排程任務（替代 macOS launchd）

macOS 的 `launchd` plist 在 Windows 上需改用**工作排程器（Task Scheduler）**。

### 範例：每日 02:00 備份資料庫

在 PowerShell（系統管理員）執行：

```powershell
$action  = New-ScheduledTaskAction `
    -Execute "$env:USERPROFILE\.venvs\evo-prism\Scripts\python.exe" `
    -Argument "D:\Evo_PRISM\scheduler\backup_db.py" `
    -WorkingDirectory "D:\Evo_PRISM"

$trigger = New-ScheduledTaskTrigger -Daily -At "02:00"

Register-ScheduledTask -TaskName "EvoPRISM_Backup" `
    -Action $action -Trigger $trigger `
    -RunLevel Highest -Force
```

其他排程任務（cleanup_l1_cache、rebuild_hnsw 等）依此類推。
