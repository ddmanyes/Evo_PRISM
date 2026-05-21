"""Fast-Path 意圖路由 — 在 LLM 推理前攔截簡單唯讀查詢。

設計動機（PROGRESS.md P0-C）：
    4500-token SYSTEM_PROMPT 帶來本機 Gemma 推理 ~12s prompt eval + 多輪 tool round-trip，
    簡單的「列出最近 5 筆分析」整體耗時可達 15s 且有列表自我截斷風險。
    本模組以 regex 對使用者訊息做意圖匹配，命中時直接呼叫既有工具並回傳結果，
    完全跳過 LLM —— 響應由 ~15s 降至毫秒級，0 token、0 幻覺截斷。

範圍刻意保守：只處理「結構明確、回傳格式固定、無需推理」的唯讀查詢。
模糊或需要組合判斷的問題仍走 LLM。

匹配優先序（由具體到一般）：
    1. timeline      — 「最近 N 天時間軸 / 這週的分析」
    2. sample_list   — 「列出樣本 / 樣本清單」
    3. recent_lookup — 「最近 N 筆分析 / 最近的分析歷史」  (fallback，最寬鬆)

使用：
    hit = try_fast_path(user_msg)
    if hit is not None:
        result_text = execute_tool(hit.tool_name, hit.args)
        ...  # 略過 LLM，直接回傳
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# ── 數字解析 ─────────────────────────────────────────────────────────────────

# 中文數字 → int（只覆蓋常見小數字，N 通常 ≤ 30）
_CN_DIGITS: dict[str, int] = {
    "一": 1, "兩": 2, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    "廿": 20, "卅": 30,
}


def _parse_int(token: str, default: int) -> int:
    """解析阿拉伯或中文數字；失敗回 default。"""
    token = token.strip()
    if not token:
        return default
    if token.isdigit():
        return int(token)
    # 「十五」「二十」之類粗略處理
    if token in _CN_DIGITS:
        return _CN_DIGITS[token]
    if token.startswith("十") and len(token) == 2 and token[1] in _CN_DIGITS:
        return 10 + _CN_DIGITS[token[1]]
    if len(token) == 3 and token[1] == "十" and token[0] in _CN_DIGITS and token[2] in _CN_DIGITS:
        return _CN_DIGITS[token[0]] * 10 + _CN_DIGITS[token[2]]
    if len(token) == 2 and token[1] == "十" and token[0] in _CN_DIGITS:
        return _CN_DIGITS[token[0]] * 10
    return default


# ── 意圖規則 ─────────────────────────────────────────────────────────────────

# 數字 token：阿拉伯或中文一/兩/三...廿/卅
_NUM = r"([0-9]+|[一二兩三四五六七八九十廿卅]{1,3})"

# 1) timeline：強信號為「時間軸」或「最近 N 天/週/this week」
#    必須出現「天/日/週/week」這類時間單位，避免吞掉「最近 5 筆」
_RE_TIMELINE_CN = re.compile(
    rf"(?:時間軸|時間線)|(?:最近|近|過去|這)\s*{_NUM}?\s*(?:天|日|週|周)"
)
_RE_TIMELINE_EN_NUM = re.compile(
    rf"\b(?:last|past|recent)\s*{_NUM}?\s*(?:day|days|week|weeks)\b",
    re.IGNORECASE,
)
_RE_TIMELINE_EN_BARE = re.compile(r"\btimeline\b", re.IGNORECASE)
_RE_TIMELINE_THIS_WEEK = re.compile(r"(?:這週|本週|this\s+week)", re.IGNORECASE)

# 2) sample_list：「列出樣本 / 樣本清單 / list samples」
_RE_SAMPLE_LIST = re.compile(
    # 「列出 / 顯示 / 看 / 有哪些 ... 樣本」之間允許少量字（如「最近的」「所有的」）
    r"(?:列出|顯示|看看?|查詢?|有哪些|哪些|所有)[^\n]{0,8}?樣本"
    r"|樣本\s*(?:列表|清單|一覽)"
    r"|\blist\s+(?:all\s+)?samples?\b"
    r"|\ball\s+samples?\b",
    re.IGNORECASE,
)

# 3) recent_lookup：「最近 N 筆分析 / 最近的分析」
#    要求出現「分析|歷史|紀錄|記錄|跑過」這類分析關鍵字，避免吞掉純閒聊
_RE_RECENT_CN = re.compile(
    rf"(?:最近|近期?|最新|上次)\s*{_NUM}?\s*(?:筆|個|則|次)?\s*"
    r"(?:的)?\s*(?:分析|歷史|紀錄|記錄|跑過)"
)
_RE_RECENT_EN = re.compile(
    rf"\b(?:recent|latest|last)\s*{_NUM}?\s*"
    r"(?:analyses|analysis|history|runs?|records?)\b",
    re.IGNORECASE,
)


# ── 對外型別 ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FastPathHit:
    """Fast-path 命中結果。

    Attributes:
        intent:    意圖標籤（timeline / sample_list / recent_lookup），主要供記錄
        tool_name: 將被呼叫的既有工具名稱（與 BIO_TOOLS 對齊）
        args:      傳給工具的參數
    """
    intent: str
    tool_name: str
    args: dict


# ── 主入口 ───────────────────────────────────────────────────────────────────

def try_fast_path(user_msg: str) -> Optional[FastPathHit]:
    """嘗試將 user_msg 匹配到 fast-path 意圖；沒命中回 None。

    Args:
        user_msg: 使用者原始自然語言訊息

    Returns:
        FastPathHit 或 None。None 代表需走 LLM。
    """
    if not user_msg or not user_msg.strip():
        return None

    msg = user_msg.strip()

    # 訊息過長視為複雜查詢，交給 LLM（避免 regex 在長句裡誤命中）
    if len(msg) > 80:
        return None

    # ── timeline ────────────────────────────────────────────────────────────
    m = _RE_TIMELINE_CN.search(msg) or _RE_TIMELINE_EN_NUM.search(msg)
    if m:
        n_days_raw = m.group(1) if m.lastindex else None
        n_days = _parse_int(n_days_raw or "", default=7)
        n_days = max(1, min(n_days, 90))
        return FastPathHit(
            intent="timeline",
            tool_name="bio_history_timeline",
            args={"n_days": n_days, "limit": 50},
        )
    if _RE_TIMELINE_EN_BARE.search(msg):
        return FastPathHit(
            intent="timeline",
            tool_name="bio_history_timeline",
            args={"n_days": 7, "limit": 50},
        )
    if _RE_TIMELINE_THIS_WEEK.search(msg):
        return FastPathHit(
            intent="timeline",
            tool_name="bio_history_timeline",
            args={"n_days": 7, "limit": 50},
        )

    # ── sample_list ─────────────────────────────────────────────────────────
    if _RE_SAMPLE_LIST.search(msg):
        return FastPathHit(
            intent="sample_list",
            tool_name="bio_sample_list",
            args={"limit": 50},
        )

    # ── recent_lookup ───────────────────────────────────────────────────────
    m = _RE_RECENT_CN.search(msg) or _RE_RECENT_EN.search(msg)
    if m:
        n_raw = m.group(1) if m.lastindex else None
        n = _parse_int(n_raw or "", default=10)
        n = max(1, min(n, 100))
        return FastPathHit(
            intent="recent_lookup",
            tool_name="bio_history_lookup",
            args={"limit": n},
        )

    return None


def render_header(hit: FastPathHit) -> str:
    """為 fast-path 結果加一行極簡標頭，讓使用者知道走了快速通道。

    刻意精簡：標頭只佔一行，後接工具原始輸出。
    """
    label = {
        "timeline": "⚡ 時間軸",
        "sample_list": "⚡ 樣本清單",
        "recent_lookup": "⚡ 最近分析",
    }.get(hit.intent, "⚡ 快速查詢")
    return f"{label}（fast-path，未經 LLM）\n"
