"""
Patch: HELIX register_tool() calls omitted in AA1 commit (4a8ebef).

HELIX §7.1 規定：任何對 analysis/ 下工具函數的修改，完成後必須執行 register_tool()。
AA1 commit 修改了 scan_candidates() 與 tool_health_report() 但未呼叫，
導致 tool_change_log 空白、revision_count 不累積、Benchmark 2 演化曲線無效。

此腳本為一次性補救，設計為冪等（同 hash 重複呼叫不產生新 revision）。
"""

import sys
from pathlib import Path

# 保證從專案根目錄匯入
sys.path.insert(0, str(Path(__file__).parent.parent))

import duckdb
from config.settings import DUCKDB_PATH
from analysis.tool_registry import register_tool, tool_health_report
from analysis.code_promoter import scan_candidates


def main() -> None:
    print(f"DB: {DUCKDB_PATH}")
    with duckdb.connect(str(DUCKDB_PATH)) as con:
        # 1. scan_candidates — AA1 改用 HELIX Eq.(1) f_promote 取代 reuse_count 啟發式
        tid1 = register_tool(
            con,
            tool_name="bio_scan_promotion_candidates",
            fn=scan_candidates,
            version="1.1.0",
            description=(
                "掃描 promotion_candidates，以 HELIX Eq.(1) f_promote 公式"
                "（α·ReuseCount + β·UserApproval − γ·Complexity）計算升格分數，"
                "回傳 ≥ θ_promote 的候選清單。"
            ),
        )
        print(f"  bio_scan_promotion_candidates  tool_id={tid1}")

        # 2. tool_health_report — AA1 新增 tool_health_scores + HealthScore 警示
        tid2 = register_tool(
            con,
            tool_name="bio_tool_health",
            fn=tool_health_report,
            version="1.1.0",
            description=(
                "HELIX 工具庫健康報告：active/deprecated 計數、熱區偵測、"
                "穩定化迭代管理、Eq.(2) HealthScore 計算與 θ_warning 警示。"
            ),
        )
        print(f"  bio_tool_health                tool_id={tid2}")

    print("patch_aa1_tool_registration: 完成。")


if __name__ == "__main__":
    main()
