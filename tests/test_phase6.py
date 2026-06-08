"""
Tests for Phase 6 — Telegram Bot。

Strategy:
  - mock telegram.Update / ContextTypes（不需真實 Bot token）
  - 測試白名單過濾、指令 handler、訊息分段、on_message 分派
  - handle_message 用 mock 取代（不消耗 Claude API）
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_update(user_id: int, text: str = "hello", username: str = "testuser"):
    """建立最小化的 mock Update 物件。"""
    user = SimpleNamespace(id=user_id, username=username)
    message = AsyncMock()
    message.text = text
    message.reply_text = AsyncMock()
    message.chat = AsyncMock()
    message.chat.send_action = AsyncMock()
    update = MagicMock()
    update.effective_user = user
    update.message = message
    return update


def _make_context(args: list[str] | None = None):
    ctx = MagicMock()
    ctx.args = args or []
    return ctx


# ═══════════════════════════════════════════════════════════════════════════════
# Part 1 — 白名單邏輯
# ═══════════════════════════════════════════════════════════════════════════════


class TestIsAllowed:
    def test_allowed_user(self):
        with patch("server.telegram_bot.TELEGRAM_ALLOWED_USER_IDS", [111, 222]):
            from server.telegram_bot import _is_allowed

            assert _is_allowed(111)
            assert _is_allowed(222)

    def test_rejected_user(self):
        with patch("server.telegram_bot.TELEGRAM_ALLOWED_USER_IDS", [111]):
            from server.telegram_bot import _is_allowed

            assert not _is_allowed(999)

    def test_empty_whitelist_rejects_all(self):
        with patch("server.telegram_bot.TELEGRAM_ALLOWED_USER_IDS", []):
            from server.telegram_bot import _is_allowed

            assert not _is_allowed(0)
            assert not _is_allowed(123456)


# ═══════════════════════════════════════════════════════════════════════════════
# Part 2 — 文字分段
# ═══════════════════════════════════════════════════════════════════════════════


class TestSplitText:
    def test_short_text_no_split(self):
        from server.telegram_bot import _split_text

        parts = _split_text("hello", max_len=100)
        assert parts == ["hello"]

    def test_exact_limit_no_split(self):
        from server.telegram_bot import _split_text

        text = "a" * 100
        parts = _split_text(text, max_len=100)
        assert len(parts) == 1

    def test_long_text_splits(self):
        from server.telegram_bot import _split_text

        text = "x" * 250
        parts = _split_text(text, max_len=100)
        assert len(parts) == 3
        assert all(len(p) <= 100 for p in parts)
        assert "".join(parts) == text

    def test_empty_string(self):
        from server.telegram_bot import _split_text

        parts = _split_text("", max_len=100)
        assert parts == [""]


# ═══════════════════════════════════════════════════════════════════════════════
# Part 3 — 指令 Handler
# ═══════════════════════════════════════════════════════════════════════════════


class TestCmdStart:
    @pytest.mark.asyncio
    async def test_allowed_user_gets_welcome(self):
        from server.telegram_bot import cmd_start

        update = _make_update(111)
        with patch("server.telegram_bot.TELEGRAM_ALLOWED_USER_IDS", [111]):
            await cmd_start(update, _make_context())
        call_text = update.message.reply_text.call_args[0][0]
        assert "BioAgent" in call_text

    @pytest.mark.asyncio
    async def test_rejected_user_gets_denied(self):
        from server.telegram_bot import cmd_start

        update = _make_update(999)
        with patch("server.telegram_bot.TELEGRAM_ALLOWED_USER_IDS", [111]):
            await cmd_start(update, _make_context())
        call_text = update.message.reply_text.call_args[0][0]
        assert "⛔" in call_text


class TestCmdHelp:
    @pytest.mark.asyncio
    async def test_allowed_user_gets_help(self):
        from server.telegram_bot import cmd_help

        update = _make_update(111)
        with patch("server.telegram_bot.TELEGRAM_ALLOWED_USER_IDS", [111]):
            await cmd_help(update, _make_context())
        call_text = update.message.reply_text.call_args[0][0]
        assert "/history" in call_text
        assert "/status" in call_text

    @pytest.mark.asyncio
    async def test_rejected_user(self):
        from server.telegram_bot import cmd_help

        update = _make_update(999)
        with patch("server.telegram_bot.TELEGRAM_ALLOWED_USER_IDS", [111]):
            await cmd_help(update, _make_context())
        call_text = update.message.reply_text.call_args[0][0]
        assert "⛔" in call_text


class TestCmdHistory:
    @pytest.mark.asyncio
    async def test_no_records(self):
        import pandas as pd
        from server.telegram_bot import cmd_history

        update = _make_update(111)
        with (
            patch("server.telegram_bot.TELEGRAM_ALLOWED_USER_IDS", [111]),
            patch("analysis.history_query.recent_analyses", return_value=pd.DataFrame()),
        ):
            await cmd_history(update, _make_context())
        call_text = update.message.reply_text.call_args[0][0]
        assert "無記錄" in call_text or "10 筆" in call_text

    @pytest.mark.asyncio
    async def test_with_sample_id_arg(self):
        import pandas as pd
        from server.telegram_bot import cmd_history

        update = _make_update(111)
        df = pd.DataFrame(
            [
                {
                    "sample_id": "crc_test",
                    "analysis_type": "spatial_eda",
                    "status": "completed",
                    "completed_at": "2026-05-15 10:00:00",
                    "summary": "CRC EDA 摘要",
                }
            ]
        )
        with (
            patch("server.telegram_bot.TELEGRAM_ALLOWED_USER_IDS", [111]),
            patch("analysis.history_query.recent_analyses", return_value=df),
        ):
            await cmd_history(update, _make_context(args=["crc_test"]))
        call_text = update.message.reply_text.call_args[0][0]
        assert "crc_test" in call_text

    @pytest.mark.asyncio
    async def test_rejected_user(self):
        from server.telegram_bot import cmd_history

        update = _make_update(999)
        with patch("server.telegram_bot.TELEGRAM_ALLOWED_USER_IDS", [111]):
            await cmd_history(update, _make_context())
        call_text = update.message.reply_text.call_args[0][0]
        assert "⛔" in call_text


class TestCmdStatus:
    @pytest.mark.asyncio
    async def test_health_shown(self):
        from server.telegram_bot import cmd_status

        update = _make_update(111)
        fake_health = {
            "sample_count": 4,
            "history_count": 10,
            "l2_ready_count": 3,
            "stale_count": 0,
            "running_count": 0,
        }
        with (
            patch("server.telegram_bot.TELEGRAM_ALLOWED_USER_IDS", [111]),
            patch("config.db_utils.db_health_check", return_value=fake_health),
            patch("analysis.embed.server_health", return_value=True),
        ):
            await cmd_status(update, _make_context())
        call_text = update.message.reply_text.call_args[0][0]
        assert "4" in call_text
        assert "Embedding" in call_text

    @pytest.mark.asyncio
    async def test_embedding_offline(self):
        from server.telegram_bot import cmd_status

        update = _make_update(111)
        fake_health = {
            "sample_count": 1,
            "history_count": 0,
            "l2_ready_count": 0,
            "stale_count": 0,
            "running_count": 0,
        }
        with (
            patch("server.telegram_bot.TELEGRAM_ALLOWED_USER_IDS", [111]),
            patch("config.db_utils.db_health_check", return_value=fake_health),
            patch("analysis.embed.server_health", side_effect=Exception("offline")),
        ):
            await cmd_status(update, _make_context())
        call_text = update.message.reply_text.call_args[0][0]
        assert "🔴" in call_text


# ═══════════════════════════════════════════════════════════════════════════════
# Part 4 — on_message（自然語言訊息分派）
# ═══════════════════════════════════════════════════════════════════════════════


class TestOnMessage:
    @pytest.mark.asyncio
    async def test_rejected_user_denied(self):
        from server.telegram_bot import on_message

        update = _make_update(999, text="hello")
        with patch("server.telegram_bot.TELEGRAM_ALLOWED_USER_IDS", [111]):
            await on_message(update, _make_context())
        call_text = update.message.reply_text.call_args[0][0]
        assert "⛔" in call_text

    @pytest.mark.asyncio
    async def test_empty_message_ignored(self):
        from server.telegram_bot import on_message

        update = _make_update(111, text="   ")
        with patch("server.telegram_bot.TELEGRAM_ALLOWED_USER_IDS", [111]):
            await on_message(update, _make_context())
        update.message.reply_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_message_dispatched_to_agent(self):
        from server.telegram_bot import on_message, _history

        _history.clear()
        update = _make_update(111, text="crc 做了什麼分析？")
        fake_result = SimpleNamespace(
            text="crc_official_v4 做過 spatial_eda。",
            tool_calls=[],
            total_tokens=50,
            messages=[
                {"role": "user", "content": "crc 做了什麼分析？"},
                {"role": "assistant", "content": "crc_official_v4 做過 spatial_eda。"},
            ],
        )
        with (
            patch("server.telegram_bot.TELEGRAM_ALLOWED_USER_IDS", [111]),
            patch("server.telegram_bot.handle_message", return_value=fake_result),
        ):
            await on_message(update, _make_context())
        call_text = update.message.reply_text.call_args[0][0]
        assert "spatial_eda" in call_text

    @pytest.mark.asyncio
    async def test_history_updated_after_message(self):
        from server.telegram_bot import on_message, _history

        _history.clear()
        update = _make_update(222, text="第一則訊息")
        fake_result = SimpleNamespace(
            text="回覆。",
            tool_calls=[],
            total_tokens=10,
            messages=[
                {"role": "user", "content": "第一則訊息"},
                {"role": "assistant", "content": "回覆。"},
            ],
        )
        with (
            patch("server.telegram_bot.TELEGRAM_ALLOWED_USER_IDS", [222]),
            patch("server.telegram_bot.handle_message", return_value=fake_result),
        ):
            await on_message(update, _make_context())
        assert 222 in _history
        assert len(_history[222]) == 2
        assert _history[222][0]["role"] == "user"
        assert _history[222][1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_agent_error_handled_gracefully(self):
        from server.telegram_bot import on_message, _history

        _history.clear()
        update = _make_update(111, text="爆炸測試")
        with (
            patch("server.telegram_bot.TELEGRAM_ALLOWED_USER_IDS", [111]),
            patch("server.telegram_bot.handle_message", side_effect=RuntimeError("boom")),
        ):
            await on_message(update, _make_context())
        call_text = update.message.reply_text.call_args[0][0]
        assert "失敗" in call_text or "⚠️" in call_text

    @pytest.mark.asyncio
    async def test_tool_calls_appended_to_reply(self):
        from server.telegram_bot import on_message, _history

        _history.clear()
        update = _make_update(111, text="跑 EDA")
        fake_result = SimpleNamespace(
            text="EDA 完成。",
            tool_calls=[{"name": "bio_run_spatial_eda", "input": {}, "result": "ok"}],
            total_tokens=100,
            messages=[
                {"role": "user", "content": "跑 EDA"},
                {"role": "assistant", "content": "EDA 完成。"},
            ],
        )
        with (
            patch("server.telegram_bot.TELEGRAM_ALLOWED_USER_IDS", [111]),
            patch("server.telegram_bot.handle_message", return_value=fake_result),
        ):
            await on_message(update, _make_context())
        call_text = update.message.reply_text.call_args[0][0]
        assert "tools: 1" in call_text

    @pytest.mark.asyncio
    async def test_history_capped_at_max(self):
        from server.telegram_bot import on_message, _history, _MAX_HISTORY

        _history.clear()
        _history[333] = [{"role": "user", "content": f"msg{i}"} for i in range(_MAX_HISTORY)]
        update = _make_update(333, text="新訊息")
        fake_result = SimpleNamespace(
            text="回覆。",
            tool_calls=[],
            total_tokens=5,
            messages=[{"role": "user", "content": f"msg{i}"} for i in range(_MAX_HISTORY)]
            + [{"role": "assistant", "content": "回覆。"}],
        )
        with (
            patch("server.telegram_bot.TELEGRAM_ALLOWED_USER_IDS", [333]),
            patch("server.telegram_bot.handle_message", return_value=fake_result),
        ):
            await on_message(update, _make_context())
        assert len(_history[333]) <= _MAX_HISTORY
