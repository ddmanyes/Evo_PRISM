"""分析技能說明書（Playbook）載入層。

每個分析領域一份 `playbooks/<name>.md`，採 Claude SKILL.md 風格：
YAML frontmatter（機器讀的 metadata）+ Markdown 正文（人 / agent 讀的方法學與有序步驟）。

Agent 在執行領域分析前，先以 `get_playbook()` 取得說明書，依正文步驟逐一呼叫
既有 `analysis.*` 函數，確保分析分步進行且每步產出對應圖。

未來加新分析 = 在 `playbooks/` 放一份 .md（指向重用函數）+ 在工具箱接上對應函數，
不需改動本載入層。

Frontmatter 必填欄位：
    name        — 說明書唯一名稱（kebab-case），如 ``bulk_rnaseq``
    version     — semver，如 ``1.0.0``
    data_type   — 對應 sample_registry.data_type，如 ``bulk_rnaseq``
    when_to_use — 一句話：何時用這份說明書
選填：
    agent_tools — MCP 工具清單，如 ``[bio_run_bulk_eda, bio_execute_code]``
    sections    — 已定義的 section 名稱清單（自動從 body 解析，不需手動填）

Section 標記格式（正文內嵌，供分段取用）：
    <!-- section: overview -->
    ...
    <!-- /section -->

可用 section 名稱慣例：
    overview      — pipeline 示意圖 + 決策流程 + tool 對照表（~400 token）
    prerequisites — 前置條件 + 分析前必呼叫（~200 token）
    steps         — 標準步驟（含品質關卡、回傳格式）（~1500 token）
    template      — 完整分析範本程式碼（~600 token）
    appendix      — 完成後確認事項 + bio_execute_code 場景（~300 token）

主要函數：
    list_playbooks()                          — 列出所有說明書的 frontmatter metadata
    get_playbook(name_or_dtype)               — 依 name 或 data_type 載入單一說明書
    Playbook.as_markdown(section=None)        — 回傳完整或指定 section 的 Markdown
    Playbook.available_sections()             — 列出此說明書已定義的 section 名稱
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import BIO_DB_ROOT  # noqa: E402

logger = logging.getLogger(__name__)

PLAYBOOKS_DIR = BIO_DB_ROOT / "playbooks"

_REQUIRED_KEYS = ("name", "version", "data_type", "when_to_use")
_FRONTMATTER_DELIM = "---"

_SECTION_RE = re.compile(
    r"<!--\s*section:\s*(?P<name>\w+)\s*-->(?P<content>.*?)<!--\s*/section\s*-->",
    re.DOTALL | re.IGNORECASE,
)


class PlaybookError(Exception):
    """說明書缺檔、frontmatter 格式錯誤或缺必填欄位時拋出。"""


@dataclass(frozen=True)
class Playbook:
    """一份載入後的分析說明書。"""

    meta: dict[str, Any]  # frontmatter（已驗證必填欄位）
    body: str  # Markdown 正文（方法學 + 有序步驟）
    path: Path  # 來源檔路徑

    @property
    def name(self) -> str:
        return self.meta["name"]

    @property
    def data_type(self) -> str:
        return self.meta["data_type"]

    def available_sections(self) -> list[str]:
        """回傳此說明書已定義的 section 名稱清單。"""
        return [m.group("name") for m in _SECTION_RE.finditer(self.body)]

    def get_section(self, section_name: str) -> str:
        """取出指定 section 的內容，找不到回傳空字串。"""
        for m in _SECTION_RE.finditer(self.body):
            if m.group("name").lower() == section_name.lower():
                return m.group("content").strip()
        return ""

    def as_markdown(self, section: Optional[str] = None) -> str:
        """回傳「metadata 摘要 + 正文（或指定 section）」的 Markdown。

        section 省略 → 回傳完整正文（自動剝除 section 標記）。
        section 指定 → 只回傳該 section 內容（省 token）。
        """
        m = self.meta
        head = (
            f"# 分析說明書：{m['name']} (v{m['version']})\n\n"
            f"- **適用 data_type**：{m['data_type']}\n"
            f"- **何時使用**：{m['when_to_use']}\n"
        )
        if m.get("agent_tools"):
            tools = m["agent_tools"]
            head += f"- **工具**：{', '.join(tools) if isinstance(tools, list) else tools}\n"
        elif m.get("agent_tool"):
            head += f"- **主要工具**：{m['agent_tool']}\n"

        if section:
            available = self.available_sections()
            content = self.get_section(section)
            if not content:
                if available:
                    return (
                        f"{head}\n---\n\n"
                        f"找不到 section `{section}`。\n"
                        f"可用 sections：{available}\n"
                        f"省略 section 參數可取完整說明書。"
                    )
                return (
                    f"{head}\n---\n\n"
                    f"此說明書尚未劃分 sections，請省略 section 參數取完整內容。"
                )
            return head + f"\n---\n\n*（section: {section}）*\n\n{content}\n"

        # 完整內容：剝除 section 標記，保留內容
        body = _SECTION_RE.sub(lambda mo: mo.group("content"), self.body)
        body = re.sub(r"<!--[^>]*-->", "", body)
        return head + "\n---\n\n" + body.strip() + "\n"


def _parse_frontmatter(text: str, path: Path) -> tuple[dict[str, Any], str]:
    """從 ``---`` 包裹的 frontmatter 切出 (meta, body)。"""
    stripped = text.lstrip()
    if not stripped.startswith(_FRONTMATTER_DELIM):
        raise PlaybookError(f"{path.name}：缺少 frontmatter（檔案須以 '---' 開頭）")

    # 切掉開頭的 '---'，再找下一個 '---' 作為 frontmatter 結尾
    rest = stripped[len(_FRONTMATTER_DELIM) :]
    end = rest.find(f"\n{_FRONTMATTER_DELIM}")
    if end == -1:
        raise PlaybookError(f"{path.name}：frontmatter 未正確以 '---' 結尾")

    fm_raw = rest[:end]
    body = rest[end + len(_FRONTMATTER_DELIM) + 1 :]

    try:
        meta = yaml.safe_load(fm_raw) or {}
    except yaml.YAMLError as exc:
        raise PlaybookError(f"{path.name}：frontmatter YAML 解析失敗 — {exc}") from exc
    if not isinstance(meta, dict):
        raise PlaybookError(f"{path.name}：frontmatter 必須是 key: value 對應表")

    missing = [k for k in _REQUIRED_KEYS if k not in meta]
    if missing:
        raise PlaybookError(f"{path.name}：frontmatter 缺必填欄位 {missing}")

    return meta, body


def _load_file(path: Path) -> Playbook:
    if not path.exists():
        raise PlaybookError(f"找不到說明書：{path}")
    meta, body = _parse_frontmatter(path.read_text(encoding="utf-8"), path)
    return Playbook(meta=meta, body=body, path=path)


def _iter_markdown_files(dir_path: Path):
    """``dir_path`` 下所有 ``*.md``，排除 macOS AppleDouble sidecar 檔（``._*``）。

    playbooks/ 常駐在 exFAT/雲端同步磁碟機上，macOS 會為每個真實檔案自動產生
    一份同名的 ``._<name>`` 資源分岔檔（非 UTF-8 文字，開頭常是 0xb0 等二進位
    位元組）。這些檔案也會被 ``*.md`` glob 命中，讀取時觸發
    ``UnicodeDecodeError`` —— 2026-07-23 實測發現：現有的
    ``except PlaybookError`` 只吃得住本模組自訂的錯誤，接不住這個，
    會讓 ``list_playbooks()`` 整個崩潰而不是優雅跳過單一壞檔。
    """
    for path in sorted(dir_path.glob("*.md")):
        if path.name.startswith("._") or path.name.upper() == "TEMPLATE.MD":
            continue
        yield path


def list_playbooks() -> list[dict[str, Any]]:
    """列出 ``playbooks/`` 下所有說明書的 frontmatter metadata。

    壞掉的單一檔案只記警告、不中斷整體列舉。
    """
    if not PLAYBOOKS_DIR.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in _iter_markdown_files(PLAYBOOKS_DIR):
        try:
            out.append(_load_file(path).meta)
        except (PlaybookError, UnicodeDecodeError, OSError) as exc:
            logger.warning("跳過壞掉的說明書 %s：%s", path.name, exc)
    return out


def get_playbook(name_or_data_type: str) -> Playbook:
    """依 ``name``（檔名 / frontmatter name）或 ``data_type`` 載入單一說明書。

    解析順序：
      1. 直接檔名 ``playbooks/<name>.md``
      2. frontmatter ``name`` 完全相符
      3. frontmatter ``data_type`` 完全相符

    全 miss → 拋 PlaybookError，附上可用清單。
    """
    key = name_or_data_type.strip()

    direct = PLAYBOOKS_DIR / f"{key}.md"
    if direct.exists():
        return _load_file(direct)

    by_name: Optional[Playbook] = None
    by_dtype: Optional[Playbook] = None
    if PLAYBOOKS_DIR.exists():
        for path in _iter_markdown_files(PLAYBOOKS_DIR):
            try:
                pb = _load_file(path)
            except (PlaybookError, UnicodeDecodeError, OSError):
                continue
            if pb.meta.get("name") == key:
                by_name = pb
                break
            if by_dtype is None and pb.meta.get("data_type") == key:
                by_dtype = pb

    found = by_name or by_dtype
    if found is not None:
        return found

    available = [m.get("name") for m in list_playbooks()]
    raise PlaybookError(f"找不到說明書 {key!r}（依 name / data_type 皆 miss）。可用：{available}")
