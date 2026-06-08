"""路徑評分模組（analysis/pathway_scoring.py）測試。

策略：
  - load_gene_sets：YAML 兩種格式（dict.genes / 純 list）、雜質過濾、缺檔、預設檔。
  - zscore_aggregate：用手算可驗證的小矩陣斷言精確值（z=(x-mean)/std, ddof=1）。
  - ssgsea_score：值依實作而定，改斷言「實作無關的單調性質」——
    富含高表現基因的路徑分數 > 富含低表現基因的路徑。
  - score_pathways：方法分派、未知方法報錯、TSV 存檔 round-trip。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analysis import pathway_scoring as ps


# ── load_gene_sets ────────────────────────────────────────────────────────────


def _write_yaml(path, text: str):
    path.write_text(text, encoding="utf-8")
    return path


def test_load_gene_sets_dict_format(tmp_path):
    y = _write_yaml(
        tmp_path / "gs.yaml",
        "OxPhos:\n  description: oxidative phosphorylation\n  genes: [Ndufa1, Sdha, Cox5a]\n",
    )
    gs = ps.load_gene_sets(y)
    assert gs == {"OxPhos": ["Ndufa1", "Sdha", "Cox5a"]}


def test_load_gene_sets_list_format(tmp_path):
    """body 直接是 list 也支援。"""
    y = _write_yaml(tmp_path / "gs.yaml", "Glycolysis: [Hk1, Pkm, Ldha]\n")
    gs = ps.load_gene_sets(y)
    assert gs == {"Glycolysis": ["Hk1", "Pkm", "Ldha"]}


def test_load_gene_sets_filters_non_str_and_bad_body(tmp_path):
    """非字串基因被濾掉；無法解析的 body（純量）整條跳過。"""
    y = _write_yaml(
        tmp_path / "gs.yaml",
        "P1:\n  genes: [GeneA, 123, GeneB, null]\nP2: 42\n",  # 純量 → 跳過
    )
    gs = ps.load_gene_sets(y)
    assert gs == {"P1": ["GeneA", "GeneB"]}
    assert "P2" not in gs


def test_load_gene_sets_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="找不到基因集"):
        ps.load_gene_sets(tmp_path / "does_not_exist.yaml")


def test_load_gene_sets_default_path_loads():
    """預設 gene_sets/hair_follicle.yaml 可載入且結構正確。"""
    gs = ps.load_gene_sets()
    assert len(gs) > 0
    assert all(isinstance(v, list) for v in gs.values())
    assert all(isinstance(g, str) for v in gs.values() for g in v)


# ── zscore_aggregate ──────────────────────────────────────────────────────────


@pytest.fixture
def expr3():
    """Gene × Sample；每列等差 → z 必為 [-1, 0, 1]（ddof=1）。"""
    return pd.DataFrame(
        {"s1": [1, 10], "s2": [2, 20], "s3": [3, 30]},
        index=["GeneA", "GeneB"],
    )


def test_zscore_exact_values(expr3):
    # GeneA[1,2,3] mean2 std1 → [-1,0,1]；GeneB[10,20,30] mean20 std10 → [-1,0,1]
    # pathway = 兩基因平均 → 仍 [-1,0,1]
    out = ps.zscore_aggregate(expr3, {"P": ["GeneA", "GeneB"]})
    assert list(out.columns) == ["s1", "s2", "s3"]
    np.testing.assert_allclose(out.loc["P"].values, [-1.0, 0.0, 1.0])


def test_zscore_skips_no_overlap_pathway(expr3):
    """基因集與矩陣無交集的路徑不出現在結果。"""
    out = ps.zscore_aggregate(expr3, {"P": ["GeneA"], "Empty": ["ZZZ", "YYY"]})
    assert "P" in out.index
    assert "Empty" not in out.index


def test_zscore_zero_variance_gene_is_nan():
    """std=0 的基因 → z 為 NaN（不應除以 0 崩潰）。"""
    expr = pd.DataFrame(
        {"s1": [5, 1], "s2": [5, 2], "s3": [5, 3]},
        index=["Flat", "Var"],
    )
    out = ps.zscore_aggregate(expr, {"Pflat": ["Flat"]})
    assert out.loc["Pflat"].isna().all()


# ── ssgsea_score ──────────────────────────────────────────────────────────────


def test_ssgsea_shape_and_no_hit_nan():
    expr = pd.DataFrame({"s1": [6, 5, 4, 3, 2, 1]}, index=[f"G{i}" for i in range(1, 7)])
    out = ps.ssgsea_score(expr, {"hit": ["G1", "G2"], "miss": ["ZZZ"]})
    assert out.shape == (2, 1)
    assert not np.isnan(out.loc["hit", "s1"])
    assert np.isnan(out.loc["miss", "s1"])


def test_ssgsea_top_genes_outscore_bottom():
    """實作無關性質：富含高表現基因的路徑 ES > 富含低表現基因的路徑。"""
    expr = pd.DataFrame({"s1": [6, 5, 4, 3, 2, 1]}, index=[f"G{i}" for i in range(1, 7)])
    out = ps.ssgsea_score(expr, {"TOP": ["G1", "G2"], "BOTTOM": ["G5", "G6"]})
    assert out.loc["TOP", "s1"] > out.loc["BOTTOM", "s1"]


# ── score_pathways（整合入口）─────────────────────────────────────────────────


@pytest.fixture
def gs_yaml(tmp_path):
    return _write_yaml(tmp_path / "gs.yaml", "P: [GeneA, GeneB]\n")


def test_score_pathways_zscore_matches_direct(expr3, gs_yaml):
    direct = ps.zscore_aggregate(expr3, {"P": ["GeneA", "GeneB"]})
    via = ps.score_pathways(expr3, gene_sets_path=gs_yaml, method="zscore")
    pd.testing.assert_frame_equal(direct, via)


def test_score_pathways_ssgsea_dispatch(expr3, gs_yaml):
    out = ps.score_pathways(expr3, gene_sets_path=gs_yaml, method="ssgsea")
    assert out.shape == (1, 3)
    assert list(out.columns) == ["s1", "s2", "s3"]


def test_score_pathways_unknown_method_raises(expr3, gs_yaml):
    with pytest.raises(ValueError, match="未知評分方法"):
        ps.score_pathways(expr3, gene_sets_path=gs_yaml, method="bogus")


def test_score_pathways_writes_tsv(expr3, gs_yaml, tmp_path):
    out_dir = tmp_path / "out"
    scores = ps.score_pathways(
        expr3,
        gene_sets_path=gs_yaml,
        method="zscore",
        output_dir=out_dir,
        label="demo",
    )
    tsv = out_dir / "pathway_scores_zscore_demo.tsv"
    assert tsv.exists()
    back = pd.read_csv(tsv, sep="\t", index_col=0)
    np.testing.assert_allclose(back.loc["P"].values, scores.loc["P"].values)
