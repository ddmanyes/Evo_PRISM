# Contributing to Bio_PRISM

歡迎貢獻！以下說明如何在本地設置環境、如何提交 PR，以及需要遵守的規範。

*Contributions are welcome! Below are guidelines for local setup, submitting PRs, and coding conventions.*

---

## 環境設置 / Setup

```bash
# 1. Fork & clone
git clone https://github.com/ddmanyes/Bio_PRISM.git
cd Bio_PRISM

# 2. 建立 venv（若在 ExFAT / 雲端硬碟，需建在 APFS 本機）
python3 -m venv ~/.venvs/hermes-bio-memory
ln -s ~/.venvs/hermes-bio-memory .venv

# 3. 安裝依賴（含 dev tools）
uv sync --no-install-project
# 或：.venv/bin/pip install -e ".[dev]"

# 4. 複製環境變數範本
cp .env.example .env
# 填入 ANTHROPIC_API_KEY 或 GOOGLE_API_KEY（用哪個後端就填哪個）

# 5. 初始化資料庫
.venv/bin/python scripts/00_init_db.py
```

完整安裝說明見 [SETUP.md](SETUP.md)。測試資料請聯絡作者（u9013039@gmail.com）。

---

## 跑測試 / Running Tests

```bash
# 跑全部測試（562 tests）
.venv/bin/python -m pytest tests/ -v --tb=short

# 只跑特定模組
.venv/bin/python -m pytest tests/test_tool_registry.py -v

# 附覆蓋率報告
.venv/bin/python -m pytest tests/ --cov=analysis --cov=server --cov=config --cov=scheduler --cov-report=term-missing
```

**在提交 PR 前，所有測試必須通過。**

---

## 程式碼規範 / Code Style

本專案使用 [Ruff](https://docs.astral.sh/ruff/) 進行 lint 與格式化：

```bash
# 檢查
.venv/bin/ruff check analysis/ server/ config/ scheduler/

# 自動修正（safe fixes only）
.venv/bin/ruff check --fix analysis/ server/ config/ scheduler/
```

**重要規範（見 [CLAUDE.md](CLAUDE.md)）：**

- 所有路徑從 `config/settings.py` 取得，禁止硬編碼
- 分析函數完成後必須呼叫 `register_tool()`（HELIX 規範）
- `analysis_history` 與 `sample_registry` 寫入必須走 `safe_write()`
- matplotlib 圖片回傳 inline base64，不回傳本地路徑

---

## 提交流程 / Submitting a PR

1. **從 `main` 建立 feature branch**
   ```bash
   git checkout -b feat/your-feature-name
   ```

2. **撰寫測試先於實作**（TDD）
   - 新功能必須附帶對應的測試
   - 測試放在 `tests/` 目錄，命名 `test_<module>.py`

3. **修改 `analysis/` 下的工具函數後，執行 `register_tool()`**（見 CLAUDE.md § 7.1）

4. **Commit 格式**（Conventional Commits）
   ```
   feat: add multiomics correlation plot
   fix: handle empty bulk RNA matrix gracefully
   docs: update MCP_HTTP_GUIDE with auth example
   test: add coverage for pathway_scoring ssGSEA
   ```

5. **開 PR，填寫模板**
   - 說明這個 PR 解決什麼問題
   - 列出測試方式
   - 若涉及資料庫 Schema 變更，說明 migration 步驟

---

## 新增分析工具 / Adding a New Analysis Tool

Bio_PRISM 採用統一的四步擴充模式，每個新分析領域（例如 scRNA-seq、ATAC-seq）都遵循相同流程。

*Bio_PRISM uses a four-step extension pattern. Every new analysis domain follows the same flow.*

### 步驟一：撰寫 Playbook / Step 1 — Write the Playbook

在 `playbooks/` 新增 `<domain>.md`，聲明標準分析流程：

```markdown
---
name: scrna
version: 1.0.0
data_type: scrna
when_to_use: 單細胞 RNA-seq 樣本的標準探索分析（clustering、marker gene、UMAP）。
agent_tools: [bio_run_scrna_eda]
---

# scRNA-seq 標準分析說明書

## 步驟

### Step 1 — QC & Filtering
...
```

`when_to_use` 決定 Agent 何時自動讀取此說明書；`agent_tools` 列出對應 MCP 工具名稱。

### 步驟二：實作分析函數 / Step 2 — Implement Analysis Function

在 `analysis/` 新增或擴充對應模組（例如 `analysis/scrna_eda.py`）：

- 函數回傳 Markdown 字串，圖表以 **inline base64** 嵌入（見 CLAUDE.md §6 圖片輸出規則）
- 分析完成後寫入 `analysis_history`（使用 `safe_write()`）
- 不得硬編碼路徑，所有路徑從 `config/settings.py` 取得

### 步驟三：接上 MCP 工具 / Step 3 — Wire Up the MCP Tool

在 `server/bio_memory_server.py` 加入三處：

```python
# 1. 在 BIO_TOOLS 列表新增工具描述
Tool(name="bio_run_scrna_eda", description="...", inputSchema={...})

# 2. 在 _TOOL_HANDLERS 字典對應
"bio_run_scrna_eda": _handle_bio_run_scrna_eda,

# 3. 實作 handler 函數
async def _handle_bio_run_scrna_eda(args: dict) -> list[TextContent]:
    from analysis.scrna_eda import generate_scrna_report
    result = generate_scrna_report(...)
    return [TextContent(type="text", text=result)]
```

### 步驟四：用 HELIX 登記版本 / Step 4 — Register with HELIX

```python
from analysis.tool_registry import register_tool
import duckdb
from config.settings import DUCKDB_PATH

with duckdb.connect(str(DUCKDB_PATH)) as con:
    register_tool(
        con,
        tool_name="bio_run_scrna_eda",
        fn=generate_scrna_report,
        version="1.0.0",
        module_path="analysis.scrna_eda",
        function_name="generate_scrna_report",
        change_reason="初始版本",
    )
```

**這步不可略過**——HELIX 依賴 `register_tool()` 追蹤工具版本、偵測熱區、關聯歷史分析記錄（見 CLAUDE.md §7）。

---

### 現有工具可作為參考 / Existing Tools as Reference

| 工具 | Playbook | 分析函數 |
|------|----------|---------|
| `bio_run_bulk_eda` | `playbooks/bulk_rnaseq.md` | `analysis/bulk_eda.py` |
| `bio_run_deg` | `playbooks/bulk_rnaseq.md` | `analysis/bulk_eda.py` |
| `bio_run_spatial_eda` | `playbooks/spatial_visium.md` | `analysis/spatial_eda.py` |

---

## 哪些地方可以貢獻 / Good First Issues

- **新增分析領域**：依上方四步流程實作 scRNA-seq、ATAC-seq、multiome 等 playbook + 工具
- **新增 gene_sets YAML**：新物種或新路徑（OxPhos / TCA / Wnt 等），供 `bio_run_enrichment` 使用
- **補充測試**：`bulk_eda.py`、`pathway_scoring.py`、`multiomics_integration.py` 覆蓋率偏低
- **改善 Web UI**（`server/static/`）
- **文件翻譯或修正**

---

## 問題回報 / Reporting Issues

請使用 [GitHub Issues](https://github.com/ddmanyes/Bio_PRISM/issues) 回報 bug 或功能請求。

回報 bug 時請附上：
- 系統環境（macOS / Linux、Python 版本）
- 重現步驟
- 完整 error traceback

---

## 授權 / License

提交 PR 即表示你同意你的貢獻以 [MIT License](LICENSE) 授權釋出。
