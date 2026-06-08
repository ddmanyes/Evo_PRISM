"""Integration test：handle_message 命中 fast-path 時應跳過 LLM。

驗收標準：
  - input_tokens == 0、output_tokens == 0
  - tool_calls 中的 entry 有 fast_path=True 標記
  - 三個 LLM client 工廠（_make_local_call / _make_claude_call / _make_google_call）
    都沒被呼叫
  - fallback：fast-path 工具 raise 時應退回 LLM
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from server import agent
from server.agent import handle_message


def _fake_local_response(text: str = "FALLBACK_LLM_REPLY") -> SimpleNamespace:
    """構造 OpenAI ChatCompletion 形狀的 mock response，無 tool_calls 即終止 loop。"""
    msg = SimpleNamespace(content=text, tool_calls=None)
    choice = SimpleNamespace(message=msg, finish_reason="stop")
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
    return SimpleNamespace(choices=[choice], usage=usage)


@pytest.fixture
def mock_tool_call():
    """Mock execute_tool 回固定字串，避免實際打 DuckDB。"""
    with patch.object(agent, "execute_tool", return_value="MOCK_TOOL_OUTPUT") as m:
        yield m


@pytest.fixture
def assert_no_llm():
    """斷言三個 LLM 路徑都不會被呼叫。"""
    with (
        patch.object(agent, "_make_local_call") as ml,
        patch.object(agent, "_make_claude_call") as mc,
        patch.object(agent, "_make_google_call") as mg,
    ):
        yield (ml, mc, mg)


class TestFastPathBypassesLLM:
    def test_recent_lookup_bypasses_llm(self, mock_tool_call, assert_no_llm) -> None:
        ml, mc, mg = assert_no_llm
        resp = handle_message("最近 5 筆分析", backend="local")

        assert resp.input_tokens == 0
        assert resp.output_tokens == 0
        assert "MOCK_TOOL_OUTPUT" in resp.text
        assert "fast-path" in resp.text
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0]["name"] == "bio_history_lookup"
        assert resp.tool_calls[0]["fast_path"] is True
        assert resp.tool_calls[0]["input"]["limit"] == 5

        ml.assert_not_called()
        mc.assert_not_called()
        mg.assert_not_called()

        mock_tool_call.assert_called_once_with("bio_history_lookup", {"limit": 5})

    def test_timeline_bypasses_llm(self, mock_tool_call, assert_no_llm) -> None:
        resp = handle_message("最近 14 天的時間軸", backend="local")
        assert resp.input_tokens == 0 and resp.output_tokens == 0
        assert resp.tool_calls[0]["name"] == "bio_history_timeline"
        assert resp.tool_calls[0]["input"]["n_days"] == 14
        mock_tool_call.assert_called_once_with("bio_history_timeline", {"n_days": 14, "limit": 50})

    def test_sample_list_bypasses_llm(self, mock_tool_call, assert_no_llm) -> None:
        resp = handle_message("列出樣本", backend="local")
        assert resp.tool_calls[0]["name"] == "bio_sample_list"
        mock_tool_call.assert_called_once_with("bio_sample_list", {"limit": 50})


class TestFastPathFallback:
    def test_tool_failure_falls_back_to_llm(self) -> None:
        """fast-path 工具 raise 時應退回 LLM 路徑（而非把例外往外丟）。"""
        with (
            patch.object(
                agent, "_make_local_call", return_value=_fake_local_response("FALLBACK_LLM_REPLY")
            ) as ml,
            patch.object(agent, "execute_tool", side_effect=RuntimeError("DB down")),
        ):
            resp = handle_message("最近 5 筆分析", backend="local")
        ml.assert_called()
        assert resp.text == "FALLBACK_LLM_REPLY"

    def test_image_input_skips_fast_path(self) -> None:
        """多模態訊息一律不走 fast-path（即使文字命中也要交給 VLM）。"""
        with (
            patch.object(
                agent, "_make_local_call", return_value=_fake_local_response("VLM_REPLY")
            ) as ml,
            patch.object(agent, "execute_tool") as et,
        ):
            resp = handle_message(
                "最近 5 筆分析",
                backend="local",
                image_base64="iVBORw0KGgoAAAANS",
            )
        et.assert_not_called()
        ml.assert_called()
        assert resp.text == "VLM_REPLY"

    def test_non_matching_message_goes_to_llm(self) -> None:
        with (
            patch.object(
                agent, "_make_local_call", return_value=_fake_local_response("REGULAR_LLM_REPLY")
            ) as ml,
            patch.object(agent, "execute_tool") as et,
        ):
            resp = handle_message("幫我做 PCA 並解釋結果", backend="local")
        et.assert_not_called()
        ml.assert_called()
        assert resp.text == "REGULAR_LLM_REPLY"
