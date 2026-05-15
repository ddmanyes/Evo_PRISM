"""
Phase 6 — Hermes Bio-Memory Telegram Bot。

架構：
    Telegram 訊息
        │
        ├─ 白名單過濾（TELEGRAM_ALLOWED_USER_IDS）
        ├─ /start /help /history /status 指令
        └─ 自然語言訊息 → handle_message()（server/agent.py）→ 回傳

功能：
    - 白名單用戶驗證（拒絕非授權用戶）
    - /start、/help — 說明
    - /history [sample_id] — 查詢最近分析記錄（0 token）
    - /status — Bot + DB 健檢
    - 自然語言訊息 → Agent Loop（Claude API）
    - 長文字自動分段（Telegram 4096 字元上限）
    - typing... 狀態提示

啟動：
    python server/telegram_bot.py

環境變數（.env）：
    TELEGRAM_BOT_TOKEN        — Bot token（BotFather 申請）
    TELEGRAM_ALLOWED_USER_IDS — 白名單 user_id，逗號分隔（例如 123456,789012）
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config.settings import TELEGRAM_ALLOWED_USER_IDS, TELEGRAM_BOT_TOKEN
from server.agent import handle_message

logger = logging.getLogger(__name__)

# 對話歷史（per user，保留最近 12 輪）
_history: dict[int, list[dict]] = {}
_history_locks: dict[int, asyncio.Lock] = {}
_MAX_HISTORY = 12
_TELEGRAM_MAX_CHARS = 4000  # 留 96 chars buffer


def _get_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _history_locks:
        _history_locks[user_id] = asyncio.Lock()
    return _history_locks[user_id]


# ── 白名單 ────────────────────────────────────────────────────────────────────


def _is_allowed(user_id: int) -> bool:
    if not TELEGRAM_ALLOWED_USER_IDS:
        return False  # 未設定白名單 → 全部拒絕（安全預設）
    return user_id in TELEGRAM_ALLOWED_USER_IDS


async def _reject(update: Update) -> None:
    user = update.effective_user
    logger.warning("Rejected user_id=%s username=%s", user.id, user.username)
    await update.message.reply_text(
        "⛔ 抱歉，您沒有使用此 Bot 的權限。請聯絡實驗室管理員。"
    )


# ── 文字分段 ──────────────────────────────────────────────────────────────────


def _split_text(text: str, max_len: int = _TELEGRAM_MAX_CHARS) -> list[str]:
    """把長文字切成多段，每段 ≤ max_len 字元。"""
    if len(text) <= max_len:
        return [text]
    parts = []
    while text:
        parts.append(text[:max_len])
        text = text[max_len:]
    return parts


# ── 指令 Handler ──────────────────────────────────────────────────────────────


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update.effective_user.id):
        await _reject(update)
        return
    await update.message.reply_text(
        "👋 你好！我是 **Hermes Bio-Memory**，實驗室生資分析助理。\n\n"
        "**可用指令：**\n"
        "• /help — 使用說明\n"
        "• /history [sample_id] — 查詢分析歷史\n"
        "• /status — 系統狀態\n\n"
        "直接傳送自然語言訊息即可查詢或發起分析。",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update.effective_user.id):
        await _reject(update)
        return
    await update.message.reply_text(
        "**Hermes Bio-Memory 使用說明**\n\n"
        "**自然語言查詢範例：**\n"
        "• `crc_official_v4 做過哪些分析？`\n"
        "• `幫我看 crc_official_v4 的空間 EDA`\n"
        "• `最近 7 天做了什麼分析？`\n"
        "• `搜尋 CD8A 相關的分析結果`\n\n"
        "**指令說明：**\n"
        "• `/history` — 列出最近 10 筆分析（全部樣本）\n"
        "• `/history crc_official_v4` — 列出指定樣本的分析\n"
        "• `/status` — 查看 DB 健檢與系統狀態\n\n"
        "**注意事項：**\n"
        "• L3 原始數據唯讀，絕不修改\n"
        "• 語意搜尋需要 embedding server 在線（port 8081）\n"
        "• 分析可能需要 10–60 秒，請耐心等候",
        parse_mode="Markdown",
    )


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update.effective_user.id):
        await _reject(update)
        return

    sample_id = " ".join(context.args).strip() if context.args else None
    await update.message.chat.send_action(ChatAction.TYPING)

    try:
        from analysis.history_query import recent_analyses
        df = recent_analyses(n=10, sample_id=sample_id)
        header = f"📋 **{sample_id}** 的最近 10 筆分析" if sample_id else "📋 最近 10 筆分析記錄"

        if df.empty:
            await update.message.reply_text(
                f"{header}\n\n（無記錄）", parse_mode="Markdown"
            )
            return

        lines = [header, ""]
        for _, row in df.iterrows():
            completed = str(row.get("completed_at", ""))[:16]
            summary = (row.get("summary") or "")[:40]
            status_icon = "✅" if row.get("status") == "completed" else "⏳"
            lines.append(
                f"{status_icon} `{row['sample_id']}` / {row['analysis_type']}\n"
                f"   {completed}  {summary}"
            )
        text = "\n".join(lines)
    except Exception:
        logger.exception("cmd_history failed for user_id=%s", update.effective_user.id)
        text = "⚠️ 查詢失敗，請稍後再試。"

    for part in _split_text(text):
        await update.message.reply_text(part, parse_mode="Markdown")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update.effective_user.id):
        await _reject(update)
        return

    await update.message.chat.send_action(ChatAction.TYPING)
    try:
        from config.db_utils import db_health_check
        health = db_health_check()
        lines = [
            "🟢 **系統狀態**\n",
            f"• 樣本數：{health.get('sample_count', '?')}",
            f"• 分析記錄：{health.get('history_count', '?')}",
            f"• L2 就緒：{health.get('l2_ready_count', '?')}",
            f"• 殭屍狀態：{health.get('stale_count', 0)}",
            f"• 執行中：{health.get('running_count', 0)}",
        ]
        try:
            from analysis.embed import server_health
            emb_status = "🟢 在線" if server_health().get("ok") else "🔴 離線"
        except Exception:
            emb_status = "🔴 無法連線"
        lines.append(f"• Embedding server：{emb_status}")
        text = "\n".join(lines)
    except Exception:
        logger.exception("cmd_status failed for user_id=%s", update.effective_user.id)
        text = "⚠️ 健檢失敗，請稍後再試。"

    await update.message.reply_text(text, parse_mode="Markdown")


# ── 自然語言訊息 Handler ──────────────────────────────────────────────────────


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update.effective_user.id):
        await _reject(update)
        return

    user_id = update.effective_user.id
    user_msg = update.message.text or ""
    if not user_msg.strip():
        return

    await update.message.chat.send_action(ChatAction.TYPING)

    async with _get_lock(user_id):
        history = list(_history.get(user_id, []))

    reply = ""
    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, lambda: handle_message(user_msg, history)
        )
        reply = result.text
        tool_info = (
            f"\n\n_[tools: {len(result.tool_calls)} | tokens: {result.total_tokens}]_"
            if result.tool_calls
            else ""
        )
        full_reply = reply + tool_info
    except Exception:
        logger.exception("handle_message failed for user_id=%s", user_id)
        full_reply = "⚠️ Agent 執行失敗，請稍後再試。"

    async with _get_lock(user_id):
        if reply:  # 只在成功取得回覆時才更新歷史，避免空字串污染 Claude API
            # 使用 handle_message 回傳的完整 messages（含 tool 輪次），確保 API 合規
            _history[user_id] = result.messages[-_MAX_HISTORY:]

    for part in _split_text(full_reply):
        try:
            await update.message.reply_text(part, parse_mode="Markdown")
        except Exception:
            await update.message.reply_text(part)


# ── 啟動 ──────────────────────────────────────────────────────────────────────


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN 未設定，請在 .env 填入 Bot token。")
        sys.exit(1)

    if not TELEGRAM_ALLOWED_USER_IDS:
        logger.warning(
            "TELEGRAM_ALLOWED_USER_IDS 未設定 → 所有用戶將被拒絕。"
            "請在 .env 填入授權 user_id（逗號分隔）。"
        )

    try:
        import duckdb as _ddb
        from config.settings import DUCKDB_PATH
        from config.db_utils import cleanup_stale_runs
        with _ddb.connect(str(DUCKDB_PATH)) as _con:
            cleanup_stale_runs(_con)
    except Exception:
        logger.warning("startup cleanup_stale_runs failed", exc_info=True)

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    logger.info("Hermes Bot 啟動（allowed_users=%s）", TELEGRAM_ALLOWED_USER_IDS)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
