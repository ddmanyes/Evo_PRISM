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

## 哪些地方可以貢獻 / Good First Issues

- 補充 `analysis/` 下低覆蓋模組的測試（`bulk_eda.py`、`pathway_scoring.py`、`multiomics_integration.py`）
- 新增 gene_sets YAML 配置（新物種或新路徑）
- 改善 Web UI（`server/static/`）
- 文件翻譯或修正

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
