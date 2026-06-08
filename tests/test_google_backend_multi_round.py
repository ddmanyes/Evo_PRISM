"""P3 NH4 — Google (Gemini) backend 多輪 tool history mock e2e 驗證。

修復背景：早期版本 google backend 在多輪 tool 呼叫時，model 回應的 FunctionCall 與
用戶端的 FunctionResponse parts 會在第二輪呼叫時被 OpenAI-format `messages` 的轉換路徑
覆寫掉（轉成 plain text），導致 Gemini 看不到工具歷史。
修復後：`_google_native` 在 loop 啟動前從 `messages` 預先建立，每輪呼叫都顯式傳入。

呼叫流程（每次 handle_message）：
  Call 0：pre-build native history（_make_google_call(native_history=None)） — 此次回應被丟棄
  Call 1：loop round 0（_make_google_call(native_history=_google_native)） — 真正的對話起點
  Call 2：loop round 1 — 工具結果回傳給 Gemini

本測試 mock `client.models.generate_content` 三次：
  Call 0：text（pre-build 階段，內容無關緊要）
  Call 1：FunctionCall(bio_history_check)
  Call 2：純文字終止

驗證項目：
  1. Call 2 的 `contents` 必須包含 model role 的 FunctionCall part
  2. Call 2 的 `contents` 必須包含 user role 的 FunctionResponse part
  3. tool_calls 累計 1 筆
  4. 最終 AgentResponse.text 為 Call 2 的純文字
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# 環境無 google-genai 時跳過整個檔案，避免 collection error
pytest.importorskip("google.genai")


def _make_fn_call_response(name: str, args: dict):
    """Construct a fake Gemini response with a FunctionCall part."""
    from google.genai import types as gt

    fc = gt.FunctionCall(name=name, args=args)
    part = gt.Part(function_call=fc)
    content = gt.Content(role="model", parts=[part])
    candidate = SimpleNamespace(
        content=content,
        finish_reason=SimpleNamespace(name="STOP"),
    )
    return SimpleNamespace(
        candidates=[candidate],
        usage_metadata=SimpleNamespace(prompt_token_count=12, candidates_token_count=8),
        text="",
    )


def _make_text_response(text: str):
    from google.genai import types as gt

    part = gt.Part(text=text)
    content = gt.Content(role="model", parts=[part])
    candidate = SimpleNamespace(
        content=content,
        finish_reason=SimpleNamespace(name="STOP"),
    )
    return SimpleNamespace(
        candidates=[candidate],
        usage_metadata=SimpleNamespace(prompt_token_count=30, candidates_token_count=15),
        text=text,
    )


class TestGoogleMultiRoundToolHistory:
    def test_native_history_preserves_function_call_and_response(self, monkeypatch):
        """第二輪呼叫時 contents 必須含 model FunctionCall + user FunctionResponse parts。"""
        monkeypatch.setenv("GOOGLE_API_KEY", "fake-key-for-test")
        monkeypatch.setenv("INFERENCE_BACKEND", "google")

        from server import agent

        captured_calls = []

        fake_client = MagicMock()

        def _generate_content_side_effect(*, model, contents, config):
            captured_calls.append(
                {
                    "model": model,
                    "contents": contents,
                    "config": config,
                }
            )
            # Call 0：pre-build；Call 1：loop round 0（回 FunctionCall）；Call 2：loop round 1（終止）
            if len(captured_calls) == 1:
                # pre-build：回傳什麼都不影響（response 被丟棄）
                return _make_text_response("(pre-build, discarded)")
            if len(captured_calls) == 2:
                return _make_fn_call_response(
                    "bio_history_check",
                    {
                        "sample_id": "crc_official_v4",
                        "analysis_type": "spatial_eda",
                    },
                )
            return _make_text_response("已確認樣本尚無 spatial_eda 完成存檔。")

        fake_client.models.generate_content.side_effect = _generate_content_side_effect

        with (
            patch.object(agent, "_get_google_client", return_value=fake_client),
            patch.object(
                agent, "execute_tool", return_value="exists: false\nsample_id='crc_official_v4'…"
            ),
        ):
            resp = agent.handle_message(
                "請確認 crc_official_v4 的 spatial_eda 是否已完成",
                history=[],
                backend="google",
            )

        assert len(captured_calls) == 3, (
            f"預期 3 次呼叫（pre-build + 2 rounds），實際 {len(captured_calls)}"
        )

        # 最後一次呼叫（Call 2 = round 1）的 contents 必須含完整工具歷史
        round1_contents = captured_calls[2]["contents"]
        assert isinstance(round1_contents, list)

        # 1. model FunctionCall part 必須存在於 Round 1 contents
        has_model_fn_call = any(
            getattr(c, "role", None) == "model"
            and any(getattr(p, "function_call", None) is not None for p in c.parts)
            for c in round1_contents
        )
        assert has_model_fn_call, (
            "Round 1 contents 缺少 model FunctionCall part；tool history 被覆寫了（NH4 regression）"
        )

        # 2. user FunctionResponse part 必須存在於 Round 1 contents
        has_user_fn_resp = any(
            getattr(c, "role", None) == "user"
            and any(getattr(p, "function_response", None) is not None for p in c.parts)
            for c in round1_contents
        )
        assert has_user_fn_resp, (
            "Round 1 contents 缺少 user FunctionResponse part；"
            "tool result 未回傳給 Gemini（NH4 regression）"
        )

        # 3. tool_calls 累計
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0]["name"] == "bio_history_check"

        # 4. 最終回應為 Round 1 的純文字
        assert "已確認樣本尚無 spatial_eda 完成存檔" in resp.text

    def test_native_history_carries_prior_messages(self, monkeypatch):
        """history 中既有的 user/assistant 訊息必須在 Round 0 就已建入 native history。"""
        monkeypatch.setenv("GOOGLE_API_KEY", "fake-key-for-test")
        monkeypatch.setenv("INFERENCE_BACKEND", "google")

        from server import agent

        captured_calls = []
        fake_client = MagicMock()

        def _side_effect(*, model, contents, config):
            captured_calls.append({"contents": contents})
            return _make_text_response("OK")

        fake_client.models.generate_content.side_effect = _side_effect

        prior_history = [
            {"role": "user", "content": "幫我看一下樣本列表"},
            {"role": "assistant", "content": "目前 sample_registry 中有 91 個樣本。"},
        ]

        with patch.object(agent, "_get_google_client", return_value=fake_client):
            _ = agent.handle_message(
                "再幫我確認 crc_official_v4 狀態",
                history=prior_history,
                backend="google",
            )

        # Call 0 = pre-build；Call 1 = loop round 0（真正 round 0 of conversation）
        # 兩者 contents 來源相同（都從 messages 建）；任一檢查皆可
        round0_contents = captured_calls[0]["contents"]
        text_parts = []
        for c in round0_contents:
            for p in c.parts:
                t = getattr(p, "text", None)
                if t:
                    text_parts.append(t)
        joined = "\n".join(text_parts)
        assert "幫我看一下樣本列表" in joined, "歷史 user 訊息丟失"
        assert "目前 sample_registry 中有 91 個樣本" in joined, "歷史 assistant 訊息丟失"
        assert "再幫我確認 crc_official_v4 狀態" in joined, "當前用戶訊息丟失"
