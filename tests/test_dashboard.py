"""控制面板資料層 + HTTP 路由的單元測試。

策略：在 tmp_path 建一個最小 schema 的 DuckDB，避開真實 DB 路徑依賴；
覆蓋 overview / dynamic_code / cache / system / helix 與聚合 full_snapshot。
"""
from __future__ import annotations

import duckdb
import pytest

import server.dashboard as dash


@pytest.fixture
def con(tmp_path, monkeypatch):
    """建一個最小 schema（含 analysis_history / tools / analysis_artifacts）。"""
    # 把 BIO_DB_ROOT 導向 tmp_path，避免 system_panel 真的去看 logs/磁碟
    monkeypatch.setattr(dash, "BIO_DB_ROOT", tmp_path)

    db = tmp_path / "t.duckdb"
    c = duckdb.connect(str(db))
    c.execute(
        """
        CREATE TABLE sample_registry(
            sample_id VARCHAR PRIMARY KEY, project VARCHAR, data_type VARCHAR,
            platform VARCHAR, species VARCHAR, tissue VARCHAR, l3_path VARCHAR,
            l2_ready BOOL, analysis_done BOOL, added_by VARCHAR, notes VARCHAR,
            last_updated TIMESTAMP
        );
        CREATE TABLE analysis_history(
            analysis_id UUID PRIMARY KEY, sample_id VARCHAR, analysis_type VARCHAR,
            parameters JSON, status VARCHAR, result_path VARCHAR,
            l1_cache_id UUID, requested_by VARCHAR,
            started_at TIMESTAMP, completed_at TIMESTAMP, summary VARCHAR,
            tool_id UUID
        );
        CREATE TABLE tools(
            tool_id UUID PRIMARY KEY, tool_name VARCHAR, version VARCHAR,
            content_hash VARCHAR, module_path VARCHAR, function_name VARCHAR,
            description VARCHAR, parameters JSON,
            status VARCHAR, created_at TIMESTAMP, deprecated_at TIMESTAMP,
            revision_count INTEGER, stability_note VARCHAR
        );
        CREATE TABLE tool_change_log(
            log_id UUID, tool_name VARCHAR, old_hash VARCHAR, new_hash VARCHAR,
            revision_number INTEGER, change_reason VARCHAR, changed_at TIMESTAMP
        );
        CREATE TABLE tool_stabilization_log(
            log_id UUID, tool_name VARCHAR, trigger_revision INTEGER,
            diagnosis VARCHAR, action_taken VARCHAR, outcome VARCHAR,
            revision_before INTEGER, revision_after INTEGER,
            diagnosis_img VARCHAR,
            complexity_before INTEGER, complexity_after INTEGER,
            created_at TIMESTAMP, closed_at TIMESTAMP
        );
        CREATE TABLE analysis_artifacts(
            artifact_id UUID PRIMARY KEY, analysis_id UUID, artifact_type VARCHAR,
            artifact_subtype VARCHAR, label VARCHAR, file_path VARCHAR,
            file_size_kb INTEGER, mime_type VARCHAR,
            created_at TIMESTAMP
        );
        """
    )

    # 樣本
    c.execute(
        "INSERT INTO sample_registry VALUES "
        "('s1','p','bulk_rnaseq','kallisto','human','skin','/x',true,false,'t',NULL,now()),"
        "('s2','p','bulk_rnaseq','kallisto','human','skin','/y',true,false,'t',NULL,now()),"
        "('s3','p','visium_hd','10x','human','skin','/z',false,false,'t',NULL,now())"
    )

    # 分析歷史：3 筆 dynamic_code（含 1 failed + 同 description 重複 2 次）+ 1 筆 bulk_eda
    c.execute(
        """
        INSERT INTO analysis_history VALUES
          (gen_random_uuid(),'s1','dynamic_code',
           '{"description":"top var genes","code_lines":12,"fig_count":1}',
           'completed','results/dynamic_code/a',NULL,'agent',
           now() - INTERVAL 2 HOUR, now() - INTERVAL 2 HOUR,
           'top var genes', NULL),
          (gen_random_uuid(),'s1','dynamic_code',
           '{"description":"top var genes","code_lines":15,"fig_count":1}',
           'completed','results/dynamic_code/b',NULL,'agent',
           now() - INTERVAL 1 HOUR, now() - INTERVAL 1 HOUR,
           'top var genes', NULL),
          (gen_random_uuid(),'s2','dynamic_code',
           '{"description":"broken script","code_lines":5,"fig_count":0,"error_summary":"NameError"}',
           'failed','results/dynamic_code/c',NULL,'agent',
           now() - INTERVAL 30 MINUTE, now() - INTERVAL 30 MINUTE,
           '[FAILED] broken script', NULL),
          (gen_random_uuid(),'s1','bulk_eda',
           '{}','completed','results/bulk_eda/x.md',NULL,'agent',
           now() - INTERVAL 3 HOUR, now() - INTERVAL 3 HOUR,
           'bulk eda', NULL)
        """
    )

    # 工具：1 active + 1 deprecated
    # 欄位順序: tool_id, tool_name, version, content_hash, module_path, function_name,
    #          description, parameters, status, created_at, deprecated_at,
    #          revision_count, stability_note
    c.execute(
        """
        INSERT INTO tools VALUES
          (gen_random_uuid(),'bio_run_bulk_eda','1.0.0','hash_a',
           'analysis.bulk_eda','generate_bulk_report',
           NULL, NULL, 'active', now(), NULL, 2, NULL),
          (gen_random_uuid(),'bio_run_spatial_eda','0.9.0','hash_b',
           'analysis.spatial_eda','run_eda',
           NULL, NULL, 'deprecated', now() - INTERVAL 30 DAY, now(), 1, NULL)
        """
    )

    # Artifacts
    c.execute(
        """
        INSERT INTO analysis_artifacts VALUES
          (gen_random_uuid(), gen_random_uuid(),'table','qc','QC',
           'results/qc.csv',12,'text/csv',now()),
          (gen_random_uuid(), gen_random_uuid(),'image','pca','PCA',
           'results/pca.png',300,'image/png',now())
        """
    )

    yield c
    c.close()


def test_overview_counts(con):
    o = dash.overview(con)
    assert o["samples"] == 3
    assert o["l2_ready"] == 2
    assert o["analyses_total"] == 4
    assert o["analyses_by_type"]["dynamic_code"] == 3
    assert o["analyses_by_type"]["bulk_eda"] == 1
    assert o["dynamic_code"] == {"total": 3, "completed": 2, "failed": 1}
    assert o["tools"] == {"active": 1, "deprecated": 1}
    assert o["artifacts"]["count"] == 2
    assert o["artifacts"]["total_kb"] == 312


def test_dynamic_code_panel_lists_recent_and_candidates(con):
    d = dash.dynamic_code_panel(con, limit=10)
    # recent：3 筆，新到舊
    assert len(d["recent"]) == 3
    assert d["recent"][0]["status"] == "failed"  # 最近的是 failed
    assert d["recent"][0]["description"] == "broken script"
    # candidates：只有 "top var genes" 出現 2 次（broken script 只 1 次不入候選）
    assert len(d["promotion_candidates"]) == 1
    cand = d["promotion_candidates"][0]
    assert cand["description"] == "top var genes"
    assert cand["runs"] == 2
    assert cand["completed_runs"] == 2


def test_cache_panel_artifacts_grouped(con):
    c = dash.cache_panel(con)
    subtypes = {a["subtype"]: a for a in c["artifacts_by_subtype"]}
    assert subtypes["pca"]["count"] == 1 and subtypes["pca"]["total_kb"] == 300
    assert subtypes["qc"]["count"] == 1 and subtypes["qc"]["total_kb"] == 12
    # figure_cache / l1_cache 兩鍵存在（具體狀態取決於環境，至少不 raise）
    assert "figure_cache" in c and "l1_cache" in c


def test_helix_panel_includes_tools_ledger(con):
    h = dash.helix_panel(con)
    assert h["total_active"] == 1
    assert h["total_deprecated"] == 1
    names = {t["tool_name"] for t in h["tools"]}
    assert {"bio_run_bulk_eda", "bio_run_spatial_eda"} <= names


def test_system_panel_does_not_raise(con, monkeypatch):
    # _check_port 走真實網路會慢；測試裡 mock 掉
    monkeypatch.setattr(dash, "_check_port", lambda port, timeout=1.0: False)
    s = dash.system_panel(con)
    assert s["servers"] == {"embedding_8081": False, "multimodal_8080": False}
    assert s["db_ok"] is True
    assert s["db"]["sample_count"] == 3


def test_full_snapshot_aggregates_all(con, monkeypatch):
    monkeypatch.setattr(dash, "_check_port", lambda *a, **kw: True)
    snap = dash.full_snapshot(con)
    assert set(snap.keys()) == {"overview", "helix", "dynamic_code", "cache", "system"}


def test_dashboard_routes(web_app_client):
    """HTTP 整合：/dashboard 回 HTML、/api/dashboard 回 JSON。
    用真實 settings.DUCKDB_PATH（本機/CI 皆已建好）；只驗 status code 與形狀。
    """
    r1 = web_app_client.get("/dashboard")
    assert r1.status_code == 200
    assert "<title>BioAgent 控制面板</title>" in r1.text

    r2 = web_app_client.get("/api/dashboard")
    assert r2.status_code == 200
    data = r2.json()
    assert set(data.keys()) == {"overview", "helix", "dynamic_code", "cache", "system"}
