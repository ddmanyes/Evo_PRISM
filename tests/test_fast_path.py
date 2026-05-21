"""Tests for server/fast_path.py — 意圖路由 Regex Router 純函數測試。

不依賴 DuckDB / LLM；只驗證 regex 匹配與 args 解析。
整合測試（handle_message 真的跳過 LLM）另見 test_handle_message_fast_path。
"""

from __future__ import annotations

import pytest

from server.fast_path import FastPathHit, render_header, try_fast_path


# ── timeline ────────────────────────────────────────────────────────────────

class TestTimelineIntent:
    @pytest.mark.parametrize(
        "msg, expected_days",
        [
            ("最近 7 天的時間軸", 7),
            ("最近三天時間軸", 3),
            ("近 14 天分析時間軸", 14),
            ("過去 30 天", 30),
            ("這週的分析", 7),
            ("本週做了什麼", 7),
            ("timeline last 5 days", 5),
            ("show me the timeline", 7),  # 無數字 → default 7
            ("this week", 7),
            ("最近一週", 1),  # 「週」單位被吃掉，n_days=1（可接受 — 仍是 timeline）
        ],
    )
    def test_hits_timeline(self, msg: str, expected_days: int) -> None:
        hit = try_fast_path(msg)
        assert hit is not None, f"expected hit for {msg!r}"
        assert hit.intent == "timeline"
        assert hit.tool_name == "bio_history_timeline"
        assert hit.args["n_days"] == expected_days
        assert hit.args["limit"] == 50

    def test_clamps_huge_n_days(self) -> None:
        hit = try_fast_path("最近 9999 天")
        assert hit is not None
        assert hit.args["n_days"] == 90  # clamped


# ── sample_list ─────────────────────────────────────────────────────────────

class TestSampleListIntent:
    @pytest.mark.parametrize(
        "msg",
        [
            "列出樣本",
            "顯示所有樣本",
            "列出所有的樣本",
            "看樣本",
            "有哪些樣本",
            "樣本清單",
            "樣本列表",
            "list samples",
            "List all samples",
            "all samples",
        ],
    )
    def test_hits_sample_list(self, msg: str) -> None:
        hit = try_fast_path(msg)
        assert hit is not None, f"expected hit for {msg!r}"
        assert hit.intent == "sample_list"
        assert hit.tool_name == "bio_sample_list"
        assert hit.args == {"limit": 50}


# ── recent_lookup ───────────────────────────────────────────────────────────

class TestRecentLookupIntent:
    @pytest.mark.parametrize(
        "msg, expected_n",
        [
            ("最近 5 筆分析", 5),
            ("最近10筆分析歷史", 10),
            ("最近的分析", 10),  # default
            ("最近三筆分析紀錄", 3),
            ("最新跑過的分析", 10),
            ("上次的分析記錄", 10),
            ("recent 20 analyses", 20),
            ("latest analysis", 10),
            ("last 3 runs", 3),
        ],
    )
    def test_hits_recent(self, msg: str, expected_n: int) -> None:
        hit = try_fast_path(msg)
        assert hit is not None, f"expected hit for {msg!r}"
        assert hit.intent == "recent_lookup"
        assert hit.tool_name == "bio_history_lookup"
        assert hit.args["limit"] == expected_n

    def test_clamps_huge_n(self) -> None:
        hit = try_fast_path("最近 9999 筆分析")
        assert hit is not None
        assert hit.args["limit"] == 100


# ── non-hits（必須回 None，避免吞掉複雜查詢）───────────────────────────────

class TestNonHits:
    @pytest.mark.parametrize(
        "msg",
        [
            "",
            "   ",
            "你好",
            "幫我跑 bulk EDA",
            "幫我畫 CD8A 的空間圖",
            "為什麼 PCA 第一主成分這麼大？",
            "比較 sample_a 跟 sample_b",
            "解釋一下 HELIX 是什麼",
            # 含「分析」但不是查歷史 → 不應命中
            "我想做新的分析",
            "幫我分析這個資料",
            # 過長訊息 → 不命中
            "我想要" + "做" * 100 + "分析",
        ],
    )
    def test_non_hits(self, msg: str) -> None:
        assert try_fast_path(msg) is None, f"unexpected hit for {msg!r}"


# ── render_header ───────────────────────────────────────────────────────────

class TestRenderHeader:
    def test_includes_intent_label(self) -> None:
        hit = FastPathHit(intent="timeline", tool_name="bio_history_timeline", args={})
        header = render_header(hit)
        assert "時間軸" in header
        assert "fast-path" in header
        assert header.endswith("\n")

    def test_unknown_intent_falls_back(self) -> None:
        hit = FastPathHit(intent="unknown_xyz", tool_name="x", args={})
        assert "快速查詢" in render_header(hit)


# ── 優先序：timeline > sample_list > recent_lookup ─────────────────────────

class TestPriority:
    def test_timeline_wins_over_recent(self) -> None:
        # 同時含「最近」與「天」→ 應走 timeline，不走 recent_lookup
        hit = try_fast_path("最近 7 天的分析")
        assert hit is not None
        assert hit.intent == "timeline"

    def test_sample_list_wins_over_recent(self) -> None:
        # 含「樣本」就走 sample_list
        hit = try_fast_path("列出最近的樣本")
        assert hit is not None
        assert hit.intent == "sample_list"
