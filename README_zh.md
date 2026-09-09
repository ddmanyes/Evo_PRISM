# Evo_PRISM

**Evolutionary Platform for Runtime Intelligence & Semantic Memory**

> **語言：** [English](README.md) · 繁體中文

[![CI](https://github.com/ddmanyes/Evo_PRISM/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/ddmanyes/Evo_PRISM/actions/workflows/ci.yml)
[![Python ≥ 3.10](https://img.shields.io/badge/Python-%E2%89%A53.10-blue)](https://www.python.org/)
[![Version](https://img.shields.io/badge/version-0.1.0-blue)](https://github.com/ddmanyes/Evo_PRISM/releases/tag/v.0.1)
[![MCP](https://img.shields.io/badge/MCP-stdio%20%2B%20HTTP-green)](https://modelcontextprotocol.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-lightgrey)](LICENSE)

[為什麼選擇 Evo_PRISM](#為什麼選擇-evo_prism) · [核心能力](#核心能力) · [快速開始](#快速開始) · [參與貢獻](#參與貢獻)

Evo_PRISM 是一套 local-first 執行環境，透過 [Model Context Protocol（MCP）](https://modelcontextprotocol.io/)把自然語言需求連接到具版本管理的分析工具與可搜尋、可追溯的永久記憶。

兩個核心子系統讓執行環境能長期維持可信度：**HELIX** 管理工具探索、版本、健康度與人工審核後的晉升；**ENGRAM** 歸檔分析產物，並保留產物與工具版本之間的來源關係。此 repo 以生物資訊作為旗艦展示，涵蓋空間轉錄體、bulk RNA-seq、scRNA-seq 與 MCseg 輔助空間分析流程。

## 為什麼選擇 Evo_PRISM？

LLM 分析流程常在第一次成功執行後開始失控：生成的程式碼消失、方法逐漸漂移、產物難以搜尋，最後也無法確認結果究竟由哪一版工具產生。Evo_PRISM 把這些問題視為執行環境與記憶系統的責任，而不是只靠 prompt 約束。

| 失效模式 | Evo_PRISM 的處理方式 |
| :--- | :--- |
| 對話結束後，生成的分析程式碼隨即消失 | HELIX 登記可重用工具並記錄版本血緣 |
| 方法或實作錯誤產生外觀合理的結果 | 健康度與失敗診斷讓工具行為可被檢查 |
| 不同人或不同時間執行同一分析卻得到不同結果 | 分析歷史把結果、參數與工具版本相互連結 |
| 分析產物散落在不同檔案與目錄 | ENGRAM 登記產物，提供精確與語意檢索 |
| 相似需求反覆執行昂貴計算 | L1 快取與歷史查詢重用已驗證結果 |

## 核心能力

- **MCP 原生介面**：以 stdio 或 HTTP 連接相容的 Agent 與 IDE。
- **三層資料路徑**：從不可變原始資料（L3），到結構化特徵（L2）與語意快取（L1）。
- **HELIX 工具生命週期**：支援語意探索、版本追蹤、健康監測與人工審核晉升。
- **ENGRAM 產物記憶**：結合 Exact SQL、HNSW 向量搜尋、BM25 全文搜尋與 RRF 排序融合。
- **版本感知影響分析**：找出工具變更可能影響的歷史產物。
- **生物資訊工作流**：涵蓋樣本歷史、空間與 bulk RNA 分析、差異表現、富集分析、熱圖、細胞註解及 MCseg 整合。

## 快速開始

此公開 checkout 目前可驗證的路徑是手動建立 Python 環境。建議使用 Python 3.11；專案支援 Python 3.10 以上版本。

### 1. 安裝

```bash
git clone https://github.com/ddmanyes/Evo_PRISM.git
cd Evo_PRISM

python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install "uv>=0.4,<1"
uv sync --no-install-project

cp .env.example .env
python scripts/00_init_db.py
for script in $(ls scripts/[0-9][0-9]_migrate_schema_*.py | sort -V); do
    python "$script"
done
```

若 repo 位於 ExFAT 或同步資料夾，請把虛擬環境建立在本機 APFS/ext4 磁碟，再以 `.venv` symlink 連回專案。

### 2. 設定 Embedding

預設 provider 是本機 [llama.cpp](https://github.com/ggml-org/llama.cpp) server，並使用 [`bge-m3-Q8_0.gguf`](https://huggingface.co/ggml-org/bge-m3-Q8_0-GGUF)：

```bash
~/llama.cpp/build/bin/llama-server \
  --model ~/llama.cpp/models/bge-m3-Q8_0.gguf \
  --embedding \
  --port 8081 \
  --ctx-size 8192 \
  --n-gpu-layers 99
```

確認服務已經就緒：

```bash
curl http://localhost:8081/health
```

系統也支援 Google 與 OpenAI embedding provider。請在 `.env` 設定對應 provider 與 API key；可用變數請見 [`.env.example`](.env.example)。

### 3. 連接 MCP 客戶端

使用 stdio transport 時，先複製設定範本，再替換其中所有絕對路徑佔位文字：

```bash
cp .mcp.json.example .mcp.json
```

各客戶端設定方式請見 [MCP JSON 設定指南](docs/guides/MCP_JSON_SETUP.md)。若要獨立提供 HTTP transport：

```bash
.venv/bin/python server/bio_memory_server.py --transport http --port 8082
```

### 4. 選用 Web UI

在 `.env` 設定 `INFERENCE_BACKEND` 與對應 API key，再啟動相符的後端：

```bash
VENV_PYTHON="$PWD/.venv/bin/python" bash start_bioagent.sh --claude
# 其他選項：--google 或 --local
```

看到 readiness 訊息後，開啟 <http://localhost:8000>。本機模式還需要設定 [`.env.example`](.env.example) 列出的視覺模型路徑。

### Docker 目前狀態

Repo 內包含 [`Dockerfile`](Dockerfile) 與 [`docker-compose.yml`](docker-compose.yml)，但目前 Compose entrypoint 會啟動 stdio MCP，而 Compose 檔案暴露的是 Web UI 與 HTTP ports。在 transport wiring 完成對齊前，不應把 `docker compose up` 視為已驗證的完整服務快速啟動方式。

更多環境細節、其他 embedding provider 與 HPC／Singularity 說明，請繼續閱讀 [SETUP.md](SETUP.md)。

## 參與貢獻

歡迎提出 issue 與 pull request。送出變更前，請先閱讀 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 授權

MIT License — © 2026 詹麒儒（Chan Chi Ru）。詳見 [LICENSE](LICENSE)。
