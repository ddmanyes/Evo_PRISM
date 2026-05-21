"""分析說明書載入層（analysis/playbook.py）測試。

策略：
  - 真實 playbooks/ 下的 bulk_rnaseq / spatial_visium 能正確載入、必填欄位齊。
  - frontmatter 解析：缺 frontmatter / 未閉合 / 缺必填欄位 → PlaybookError。
  - get_playbook 三段解析（檔名 / name / data_type）+ miss 報錯附可用清單。
  - 用 tmp 目錄 monkeypatch PLAYBOOKS_DIR，測試壞檔不中斷列舉。
"""

from __future__ import annotations

import importlib
import re

import pytest

from analysis import playbook as pb


# ── 真實 playbooks/ ───────────────────────────────────────────────────────────


def test_list_playbooks_includes_builtin():
    names = {m["name"] for m in pb.list_playbooks()}
    assert {"bulk_rnaseq", "spatial_visium"} <= names


def test_get_bulk_playbook_required_fields():
    p = pb.get_playbook("bulk_rnaseq")
    for key in ("name", "version", "data_type", "when_to_use"):
        assert key in p.meta
    assert p.meta["data_type"] == "bulk_rnaseq"
    assert "bio_run_bulk_eda" in p.body or p.meta.get("agent_tool") == "bio_run_bulk_eda"


def test_get_playbook_by_data_type():
    p = pb.get_playbook("visium_hd")  # 以 data_type 而非 name 查
    assert p.name == "spatial_visium"


def test_as_markdown_contains_header_and_body():
    md = pb.get_playbook("bulk_rnaseq").as_markdown()
    assert "分析說明書：bulk_rnaseq" in md
    assert "標準步驟" in md


def test_get_playbook_miss_raises_with_available():
    with pytest.raises(pb.PlaybookError) as exc:
        pb.get_playbook("does_not_exist")
    assert "bulk_rnaseq" in str(exc.value)


# ── frontmatter 解析（合成檔）─────────────────────────────────────────────────


def _write(tmp_path, name, text, monkeypatch):
    d = tmp_path / "playbooks"
    d.mkdir(exist_ok=True)
    (d / name).write_text(text, encoding="utf-8")
    monkeypatch.setattr(pb, "PLAYBOOKS_DIR", d)
    return d


def test_missing_frontmatter_raises(tmp_path, monkeypatch):
    _write(tmp_path, "bad.md", "no frontmatter here\n", monkeypatch)
    with pytest.raises(pb.PlaybookError, match="缺少 frontmatter"):
        pb.get_playbook("bad")


def test_unclosed_frontmatter_raises(tmp_path, monkeypatch):
    _write(tmp_path, "bad.md", "---\nname: x\nbody never closes\n", monkeypatch)
    with pytest.raises(pb.PlaybookError, match="未正確以 '---' 結尾"):
        pb.get_playbook("bad")


def test_missing_required_keys_raises(tmp_path, monkeypatch):
    _write(tmp_path, "bad.md", "---\nname: x\n---\nbody\n", monkeypatch)
    with pytest.raises(pb.PlaybookError, match="缺必填欄位"):
        pb.get_playbook("bad")


def test_list_skips_broken_file(tmp_path, monkeypatch):
    d = _write(
        tmp_path,
        "good.md",
        "---\nname: good\nversion: 1.0.0\ndata_type: t\nwhen_to_use: u\n---\nbody\n",
        monkeypatch,
    )
    (d / "broken.md").write_text("no frontmatter\n", encoding="utf-8")
    names = {m["name"] for m in pb.list_playbooks()}
    assert names == {"good"}  # broken.md 被跳過，不中斷


# ── 防漂移：playbook 引用的函數必須真實存在 ──────────────────────────────────────

_REF_RE = re.compile(r"analysis\.([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)")


def test_playbook_function_refs_resolve():
    """每份 playbook 正文引用的 `analysis.<mod>.<func>` 都必須 import 得到。

    防止有人改名/刪除 analysis 函數後，playbook 說明書無聲過期。
    """
    refs: set[tuple[str, str, str]] = set()
    for meta in pb.list_playbooks():
        body = pb.get_playbook(meta["name"]).body
        for mod, func in _REF_RE.findall(body):
            refs.add((meta["name"], mod, func))

    assert refs, "未從 playbook 抽到任何 analysis.X.Y 引用（正則或內容有誤）"

    broken: list[str] = []
    for pb_name, mod, func in sorted(refs):
        try:
            module = importlib.import_module(f"analysis.{mod}")
        except ModuleNotFoundError:
            broken.append(f"{pb_name}: analysis.{mod}（模組不存在）")
            continue
        if not hasattr(module, func):
            broken.append(f"{pb_name}: analysis.{mod}.{func}（函數不存在）")

    assert not broken, "playbook 引用已漂移：\n" + "\n".join(broken)
