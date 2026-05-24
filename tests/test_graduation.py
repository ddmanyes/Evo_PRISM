"""動態程式碼畢業層（Phase 3）測試。

涵蓋：slugify / generate_scaffold 純函數、list_candidates 嚴格門檻、
read_archive 沙盒、graduation_plan 組合，以及 web_app 兩條唯讀 route。
"""

from __future__ import annotations

import json

import duckdb
import pytest

import server.graduation as grad


# ── 純函數：slugify / generate_scaffold ───────────────────────────────────────


@pytest.mark.parametrize(
    "desc,expected",
    [
        ("archive smoke", "archive_smoke"),
        ("Top Var Genes!", "top_var_genes"),
        ("  spaced  out  ", "spaced_out"),
        ("", "graduated_analysis"),
        ("123start", "g_123start"),
        ("中文描述", "graduated_analysis"),  # 非 ASCII → 全濾掉 → fallback
    ],
)
def test_slugify(desc, expected):
    assert grad.slugify(desc) == expected


def test_generate_scaffold_structure():
    gen = grad.generate_scaffold(
        "top var genes", "print('hi')\nx = 1 + 2", analysis_id="abcd1234-rest"
    )
    assert gen["fn_name"] == "run_top_var_genes"
    assert gen["tool_name"] == "bio_top_var_genes"
    assert gen["suggested_path"] == "analysis/top_var_genes.py"
    s = gen["scaffold"]
    # 原始碼被縮排嵌入
    assert "    print('hi')" in s
    assert "    x = 1 + 2" in s
    # register_tool 片段（註解形式，避免誤執行）
    assert "register_tool(" in s
    assert 'tool_name="bio_top_var_genes"' in s
    assert "abcd1234" in s  # id8 出現在註解
    # 函數定義存在
    assert "def run_top_var_genes(" in s


def test_generate_scaffold_empty_code_uses_pass():
    gen = grad.generate_scaffold("x", "", analysis_id="deadbeef")
    assert "    pass" in gen["scaffold"]


# ── DB fixture ───────────────────────────────────────────────────────────────


@pytest.fixture
def con(tmp_path, monkeypatch):
    """最小 analysis_history + tmp 沙盒路徑。"""
    bio_root = tmp_path
    dyn_dir = tmp_path / "results" / "dynamic_code"
    dyn_dir.mkdir(parents=True)
    monkeypatch.setattr(grad, "BIO_DB_ROOT", bio_root)
    monkeypatch.setattr(grad, "DYNAMIC_CODE_DIR", dyn_dir)

    c = duckdb.connect(str(tmp_path / "t.duckdb"))
    c.execute(
        """
        CREATE TABLE analysis_history(
            analysis_id UUID PRIMARY KEY, sample_id VARCHAR, analysis_type VARCHAR,
            parameters JSON, status VARCHAR, result_path VARCHAR,
            l1_cache_id UUID, requested_by VARCHAR,
            started_at TIMESTAMP, completed_at TIMESTAMP, summary VARCHAR, tool_id UUID
        );
        """
    )
    yield c
    c.close()


def _insert(c, analysis_id, desc, status, code_lines, result_path, when="now()"):
    c.execute(
        f"""
        INSERT INTO analysis_history VALUES
        (?, 's1', 'dynamic_code',
         json_object('description', ?, 'code_lines', ?),
         ?, ?, NULL, 'agent', {when}, {when}, ?, NULL)
        """,
        [analysis_id, desc, code_lines, status, result_path, desc],
    )


# ── list_candidates 嚴格門檻 ──────────────────────────────────────────────────


def test_list_candidates_filters_noise(con):
    # 達標：real analysis ×2 completed, 10 lines
    _insert(
        con,
        "11111111-1111-1111-1111-111111111111",
        "real analysis",
        "completed",
        10,
        "results/dynamic_code/r1",
    )
    _insert(
        con,
        "11111111-1111-1111-1111-111111111112",
        "real analysis",
        "completed",
        12,
        "results/dynamic_code/r2",
    )
    # 噪音：1 行 → 過濾（即使 completed 3 次）
    for i in range(3):
        _insert(
            con,
            f"22222222-2222-2222-2222-22222222220{i}",
            "loop",
            "completed",
            1,
            f"results/dynamic_code/l{i}",
        )
    # 只跑 1 次 → 過濾（completed < 2）
    _insert(
        con,
        "33333333-3333-3333-3333-333333333333",
        "once",
        "completed",
        20,
        "results/dynamic_code/o1",
    )

    cands = grad.list_candidates(con)
    assert len(cands) == 1
    c0 = cands[0]
    assert c0["description"] == "real analysis"
    assert c0["completed_runs"] == 2
    assert c0["max_code_lines"] == 12
    # 代表性執行為最新 completed（r2，12 行那筆）
    assert c0["rep_result_path"] in ("results/dynamic_code/r1", "results/dynamic_code/r2")


def test_list_candidates_threshold_override(con):
    _insert(
        con,
        "44444444-4444-4444-4444-444444444401",
        "loop",
        "completed",
        1,
        "results/dynamic_code/l1",
    )
    _insert(
        con,
        "44444444-4444-4444-4444-444444444402",
        "loop",
        "completed",
        1,
        "results/dynamic_code/l2",
    )
    # 預設 min_code_lines=3 → 過濾
    assert grad.list_candidates(con) == []
    # 放寬到 1 → 命中
    out = grad.list_candidates(con, min_code_lines=1)
    assert len(out) == 1 and out[0]["description"] == "loop"


# ── read_archive 沙盒 ─────────────────────────────────────────────────────────


def _make_archive(con, monkeypatch, *, code="print('archived ok')\nx=1+2\nprint(x)"):
    aid = "55555555-5555-5555-5555-555555555555"
    arc = grad.DYNAMIC_CODE_DIR / "2026-05-19_55555555"
    arc.mkdir()
    (arc / "code.py").write_text(code, encoding="utf-8")
    (arc / "meta.json").write_text(
        json.dumps({"analysis_id": aid, "description": "archive smoke", "code_lines": 3}),
        encoding="utf-8",
    )
    (arc / "output.txt").write_text("5\n", encoding="utf-8")
    _insert(con, aid, "archive smoke", "completed", 3, "results/dynamic_code/2026-05-19_55555555")
    return aid


def test_read_archive_ok(con, monkeypatch):
    aid = _make_archive(con, monkeypatch)
    arc = grad.read_archive(con, aid)
    assert arc["description"] == "archive smoke"
    assert "archived ok" in arc["code"]
    assert arc["output"].strip() == "5"
    assert arc["meta"]["code_lines"] == 3
    assert arc["archive_dir"].replace("\\", "/") == "results/dynamic_code/2026-05-19_55555555"


def test_read_archive_not_found(con):
    with pytest.raises(ValueError, match="找不到"):
        grad.read_archive(con, "00000000-0000-0000-0000-000000000000")


def test_read_archive_sandbox_escape(con):
    # result_path 指向沙盒外
    _insert(con, "66666666-6666-6666-6666-666666666666", "evil", "completed", 5, "/etc/passwd_dir")
    with pytest.raises(ValueError, match="逸出"):
        grad.read_archive(con, "66666666-6666-6666-6666-666666666666")


def test_read_archive_sandbox_sibling_prefix(con):
    """兄弟目錄前綴攻擊：sandbox=.../dynamic_code 時，.../dynamic_code_evil 不得放行。

    回歸測試——舊的 str.startswith 會誤判前綴相符而放行；is_relative_to 正確拒絕。
    刻意建出真實目錄與 code.py，證明擋下的是沙盒檢查而非「目錄不存在」。
    """
    sibling = grad.BIO_DB_ROOT / "results" / "dynamic_code_evil" / "x"
    sibling.mkdir(parents=True)
    (sibling / "code.py").write_text("print('pwned')", encoding="utf-8")
    _insert(
        con,
        "88888888-8888-8888-8888-888888888888",
        "sibling",
        "completed",
        5,
        "results/dynamic_code_evil/x",
    )
    with pytest.raises(ValueError, match="逸出"):
        grad.read_archive(con, "88888888-8888-8888-8888-888888888888")


def test_read_archive_missing_dir(con):
    _insert(
        con,
        "77777777-7777-7777-7777-777777777777",
        "gone",
        "completed",
        5,
        "results/dynamic_code/does_not_exist",
    )
    with pytest.raises(ValueError, match="不存在"):
        grad.read_archive(con, "77777777-7777-7777-7777-777777777777")


def test_graduation_plan_combines(con, monkeypatch):
    aid = _make_archive(con, monkeypatch)
    plan = grad.graduation_plan(con, aid)
    assert plan["fn_name"] == "run_archive_smoke"
    assert plan["tool_name"] == "bio_archive_smoke"
    assert "archived ok" in plan["scaffold"]
    assert "    print('archived ok')" in plan["scaffold"]


# ── web_app 唯讀 route（真實 DB）──────────────────────────────────────────────


def test_graduation_candidates_route(web_app_client):
    r = web_app_client.get("/api/dashboard/graduation")
    assert r.status_code == 200
    body = r.json()
    assert "candidates" in body
    assert body["min_code_lines"] >= 1
    assert body["min_completed"] >= 1


def test_graduation_plan_route_404_on_bad_id(web_app_client):
    r = web_app_client.get("/api/dashboard/graduation/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404
