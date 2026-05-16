"""
Tests for Phase 5 — Code Executor + Agent Loop.

Strategy:
  - code_executor: 直接測試 is_safe() / sandbox_exec()，無需 mock
  - agent.execute_tool: 用 tmp_path 隔離 DB，mock 外部依賴（embedding / Claude API）
  - handle_message: mock anthropic.Anthropic，驗證 Agent Loop 流程
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import duckdb
import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_main_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "bio_memory.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("""
        CREATE TABLE sample_registry (
            sample_id     VARCHAR PRIMARY KEY,
            project       VARCHAR,
            data_type     VARCHAR,
            platform      VARCHAR,
            species       VARCHAR DEFAULT 'human',
            tissue        VARCHAR,
            l3_path       VARCHAR,
            l2_ready      BOOLEAN DEFAULT false,
            analysis_done BOOLEAN DEFAULT false,
            added_by      VARCHAR,
            notes         VARCHAR,
            last_updated  TIMESTAMPTZ
        )
    """)
    con.execute("""
        CREATE TABLE analysis_history (
            analysis_id   UUID PRIMARY KEY,
            sample_id     VARCHAR,
            analysis_type VARCHAR,
            parameters    JSON,
            status        VARCHAR DEFAULT 'pending',
            result_path   VARCHAR,
            l1_cache_id   UUID,
            requested_by  VARCHAR,
            started_at    TIMESTAMPTZ,
            completed_at  TIMESTAMPTZ,
            summary       VARCHAR,
            tool_id       UUID
        )
    """)
    con.execute(
        "INSERT INTO sample_registry VALUES (?,?,?,?,?,?,?,true,false,?,?,?)",
        ["crc_test", "crc", "visium_hd", "10x", "human", "colon",
         "/data/crc", "test", "", datetime.now(timezone.utc)],
    )
    analysis_id = str(uuid.uuid4())
    con.execute(
        """INSERT INTO analysis_history
               (analysis_id,sample_id,analysis_type,parameters,status,
                result_path,requested_by,started_at,completed_at,summary)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        [analysis_id, "crc_test", "spatial_eda", "{}", "completed",
         "/results/eda.md", "test",
         datetime.now(timezone.utc), datetime.now(timezone.utc),
         "CRC EDA 摘要"],
    )
    con.close()
    return db_path


@pytest.fixture()
def main_db(tmp_path: Path):
    return _make_main_db(tmp_path)


# ═══════════════════════════════════════════════════════════════════════════════
# Part 1 — code_executor
# ═══════════════════════════════════════════════════════════════════════════════


class TestIsSafe:
    def test_safe_pandas(self):
        from server.code_executor import is_safe
        ok, reason = is_safe("import pandas as pd\nprint(pd.__version__)")
        assert ok
        assert reason == ""

    def test_safe_numpy(self):
        from server.code_executor import is_safe
        ok, _ = is_safe("import numpy as np\nprint(np.zeros(3))")
        assert ok

    def test_blocked_os_system(self):
        from server.code_executor import is_safe
        ok, reason = is_safe("import os; os.system('ls')")
        assert not ok
        assert "os.system" in reason

    def test_blocked_subprocess(self):
        from server.code_executor import is_safe
        ok, reason = is_safe("import subprocess\nsubprocess.run(['ls'])")
        assert not ok
        assert "subprocess" in reason

    def test_blocked_eval(self):
        from server.code_executor import is_safe
        ok, reason = is_safe("eval('1+1')")
        assert not ok
        assert "eval(" in reason

    def test_blocked_open(self):
        from server.code_executor import is_safe
        ok, reason = is_safe("f = open('/etc/passwd')")
        assert not ok
        assert "open(" in reason

    def test_blocked_requests(self):
        from server.code_executor import is_safe
        ok, reason = is_safe("import requests\nrequests.get('http://x.com')")
        assert not ok

    def test_disallowed_import(self):
        from server.code_executor import is_safe
        ok, reason = is_safe("import flask")
        assert not ok
        assert "flask" in reason

    def test_syntax_error_raises_security_error(self):
        """語法錯誤的程式碼應被拒絕執行（不放行）。"""
        from server.code_executor import is_safe, SecurityError
        with pytest.raises(SecurityError, match="語法錯誤"):
            is_safe("def foo(:\n    pass")

    def test_allowed_stdlib(self):
        from server.code_executor import is_safe
        ok, _ = is_safe("import math\nprint(math.pi)")
        assert ok


class TestSandboxExec:
    def test_successful_execution(self):
        from server.code_executor import sandbox_exec
        result = sandbox_exec("print('hello hermes')", timeout=10)
        assert result.success
        assert "hello hermes" in result.output
        assert result.duration_sec >= 0

    def test_import_numpy(self):
        from server.code_executor import sandbox_exec
        result = sandbox_exec(
            "import numpy as np\nprint(np.arange(3).tolist())", timeout=10
        )
        assert result.success
        assert "[0, 1, 2]" in result.output

    def test_runtime_error(self):
        from server.code_executor import sandbox_exec
        result = sandbox_exec("raise ValueError('boom')", timeout=10)
        assert not result.success
        assert "ValueError" in result.traceback

    def test_security_error_raised(self):
        from server.code_executor import sandbox_exec, SecurityError
        with pytest.raises(SecurityError):
            sandbox_exec("import os; os.system('ls')", timeout=5)

    def test_timeout(self):
        from server.code_executor import sandbox_exec
        # time.sleep via a while loop (no import needed) triggers timeout
        result = sandbox_exec("while True: pass", timeout=2)
        assert not result.success
        assert "Timeout" in result.traceback


# ═══════════════════════════════════════════════════════════════════════════════
# Part 2 — agent.execute_tool（直接測試工具分發）
# ═══════════════════════════════════════════════════════════════════════════════


class TestExecuteToolDispatch:
    def test_safe_code_success(self):
        from server.agent import execute_tool
        result = execute_tool("bio_execute_code", {
            "code": "print('agent exec ok')",
            "description": "smoke test",
            "timeout": 10,
        })
        assert "執行成功" in result
        assert "agent exec ok" in result

    def test_blocked_code_returns_security_error(self):
        from server.agent import execute_tool
        result = execute_tool("bio_execute_code", {
            "code": "import os; os.system('ls')",
            "description": "bad code",
            "timeout": 5,
        })
        assert "SecurityError" in result

    def test_unknown_tool_returns_error(self):
        from server.agent import execute_tool
        result = execute_tool("bio_nonexistent_tool", {})
        assert "未知工具" in result or "Error" in result

    def test_bio_history_check_exists(self, main_db):
        """透過 patch.dict 替換 _TOOL_HANDLERS 使用 tmp DB，避免全域污染。"""
        import server.agent as ag
        import duckdb as _ddb

        def _handler(args):
            with _ddb.connect(str(main_db), read_only=True) as con:
                row = con.execute(
                    """SELECT analysis_id FROM analysis_history
                       WHERE sample_id=? AND analysis_type=? AND status='completed'
                       ORDER BY completed_at DESC LIMIT 1""",
                    [args["sample_id"], args["analysis_type"]],
                ).fetchone()
            if row:
                return f"exists: true\nanalysis_id: {row[0]}"
            return f"exists: false\n{args['sample_id']!r} × {args['analysis_type']!r} 尚無完成存檔。"

        with patch.dict(ag._TOOL_HANDLERS, {"bio_history_check": _handler}):
            result = ag.execute_tool(
                "bio_history_check",
                {"sample_id": "crc_test", "analysis_type": "spatial_eda"},
            )
        assert "exists: true" in result

    def test_bio_history_check_not_found(self, main_db):
        import server.agent as ag
        import duckdb as _ddb

        def _handler(args):
            with _ddb.connect(str(main_db), read_only=True) as con:
                row = con.execute(
                    """SELECT 1 FROM analysis_history
                       WHERE sample_id=? AND analysis_type=? AND status='completed'
                       LIMIT 1""",
                    [args["sample_id"], args["analysis_type"]],
                ).fetchone()
            return "exists: true" if row else f"exists: false\n{args['sample_id']!r} × {args['analysis_type']!r} 尚無完成存檔。"

        with patch.dict(ag._TOOL_HANDLERS, {"bio_history_check": _handler}):
            result = ag.execute_tool(
                "bio_history_check",
                {"sample_id": "no_such", "analysis_type": "bulk_eda"},
            )
        assert "exists: false" in result

    def test_bio_register_sample_new(self, main_db):
        import server.agent as ag
        import duckdb as _ddb
        from datetime import datetime, timezone

        def _handler(args):
            with _ddb.connect(str(main_db)) as con:
                if con.execute("SELECT 1 FROM sample_registry WHERE sample_id=?",
                               [args["sample_id"]]).fetchone():
                    return f"樣本 {args['sample_id']!r} 已存在，跳過。"
                con.execute(
                    """INSERT INTO sample_registry
                       (sample_id,project,data_type,platform,species,tissue,
                        l3_path,l2_ready,analysis_done,added_by,notes,last_updated)
                       VALUES (?,?,?,?,?,?,?,false,false,?,?,?)""",
                    [args["sample_id"], args.get("project", ""), args["data_type"],
                     args.get("platform", ""), args.get("species", "human"),
                     args.get("tissue", ""), args["l3_path"],
                     "agent", args.get("notes", ""), datetime.now(timezone.utc)],
                )
                con.execute("CHECKPOINT")
            return f"樣本 {args['sample_id']!r} 已登記。data_type={args['data_type']!r}"

        with patch.dict(ag._TOOL_HANDLERS, {"bio_register_sample": _handler}):
            result = ag.execute_tool("bio_register_sample", {
                "sample_id": "new_sample_01",
                "data_type": "bulk_rnaseq",
                "l3_path": "/data/new",
            })
        assert "已登記" in result

    def test_bio_register_sample_duplicate(self, main_db):
        import server.agent as ag
        import duckdb as _ddb

        def _handler(args):
            with _ddb.connect(str(main_db), read_only=True) as con:
                exists = con.execute("SELECT 1 FROM sample_registry WHERE sample_id=?",
                                     [args["sample_id"]]).fetchone()
            return f"樣本 {args['sample_id']!r} 已存在，跳過。" if exists else "已登記"

        with patch.dict(ag._TOOL_HANDLERS, {"bio_register_sample": _handler}):
            result = ag.execute_tool("bio_register_sample", {
                "sample_id": "crc_test",
                "data_type": "visium_hd",
                "l3_path": "/data/crc",
            })
        assert "已存在" in result


# ═══════════════════════════════════════════════════════════════════════════════
# Part 3 — handle_message（mock Claude API）
# ═══════════════════════════════════════════════════════════════════════════════


def _make_text_response(text: str, input_tokens: int = 10, output_tokens: int = 20):
    """模擬 Claude text-only response（stop_reason='end_turn'）。"""
    block = SimpleNamespace(type="text", text=text)
    usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
    return SimpleNamespace(content=[block], stop_reason="end_turn", usage=usage)


def _make_tool_response(tool_name: str, tool_input: dict):
    """模擬 Claude tool_use response（stop_reason='tool_use'）。"""
    tool_block = SimpleNamespace(
        type="tool_use", id="tu_001", name=tool_name, input=tool_input,
    )
    usage = SimpleNamespace(input_tokens=50, output_tokens=30)
    return SimpleNamespace(content=[tool_block], stop_reason="tool_use", usage=usage)


class TestHandleMessage:
    def _mock_client(self, *responses):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = list(responses)
        return mock_client

    def test_simple_text_reply(self):
        from server.agent import handle_message
        mock_client = self._mock_client(_make_text_response("你好，我是 Hermes。"))

        with patch("anthropic.Anthropic", return_value=mock_client), \
             patch("config.settings.ANTHROPIC_API_KEY", "sk-test"):
            result = handle_message("你好")

        assert "Hermes" in result.text
        assert result.input_tokens > 0
        assert result.output_tokens > 0
        assert result.total_tokens == result.input_tokens + result.output_tokens
        assert len(result.tool_calls) == 0

    def test_tool_call_then_text(self):
        from server.agent import handle_message
        mock_client = self._mock_client(
            _make_tool_response("bio_execute_code",
                                {"code": "print('42')", "description": "test"}),
            _make_text_response("程式碼執行完成，輸出為 42。"),
        )

        with patch("anthropic.Anthropic", return_value=mock_client), \
             patch("config.settings.ANTHROPIC_API_KEY", "sk-test"):
            result = handle_message("執行 print('42')")

        assert "42" in result.text
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["name"] == "bio_execute_code"

    def test_history_passed_to_api(self):
        from server.agent import handle_message
        mock_client = self._mock_client(_make_text_response("好的。"))
        history = [
            {"role": "user", "content": "第一則訊息"},
            {"role": "assistant", "content": "了解。"},
        ]

        with patch("anthropic.Anthropic", return_value=mock_client), \
             patch("config.settings.ANTHROPIC_API_KEY", "sk-test"):
            handle_message("第二則訊息", history=history)

        call_args = mock_client.messages.create.call_args
        messages_sent = call_args.kwargs["messages"]
        assert len(messages_sent) == 4  # 2 history + 1 new user msg + 1 final assistant reply

    def test_max_tool_rounds_exceeded(self):
        from server.agent import handle_message
        always_tool = _make_tool_response(
            "bio_execute_code", {"code": "print(1)", "description": "loop"}
        )
        mock_client = self._mock_client(*[always_tool] * 5)

        with patch("anthropic.Anthropic", return_value=mock_client), \
             patch("config.settings.ANTHROPIC_API_KEY", "sk-test"):
            result = handle_message("無限工具", max_tool_rounds=3)

        assert "分析步驟較多" in result.text
        assert len(result.tool_calls) == 3

    def test_agent_response_total_tokens(self):
        from server.agent import AgentResponse
        r = AgentResponse(text="ok", tool_calls=[], input_tokens=5, output_tokens=10)
        assert r.total_tokens == 15

    def test_tool_result_appended_to_messages(self):
        """驗證工具結果有正確附回給 Claude（messages 長度增加）。"""
        from server.agent import handle_message

        call_count = {"n": 0}
        captured_messages = {}

        def mock_create(**kwargs):
            n = call_count["n"]
            call_count["n"] += 1
            captured_messages[n] = list(kwargs["messages"])
            if n == 0:
                return _make_tool_response("bio_execute_code",
                                           {"code": "print('x')", "description": "t"})
            return _make_text_response("完成。")

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = mock_create

        with patch("anthropic.Anthropic", return_value=mock_client), \
             patch("config.settings.ANTHROPIC_API_KEY", "sk-test"):
            handle_message("test tool result")

        # 第二次呼叫的 messages 應比第一次多（assistant tool_use + user tool_result）
        assert len(captured_messages[1]) > len(captured_messages[0])
