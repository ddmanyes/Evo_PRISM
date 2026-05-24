"""
PM3 — Cross-Domain Transfer Test (EvolveMem cross-benchmark transfer 啟發).

驗證 ENGRAM 的 3-way RRF 語意快取配置（於 Bulk RNA-seq 場景優化）是否可以
zero-shot 遷移至 Spatial Visium HD 場景，並量化 positive / catastrophic transfer。

實驗設計：
  Step 1 — Bulk Baseline：讀取 CB1 benchmark 結果（axis_A/B），取得 Bulk RRF 配置
           在 98 Kallisto 樣本上的快取效能指標（cache_hit_rate、avg_latency_ms、
           effective_hit_rate）作為 Source Domain 基準。

  Step 2 — Spatial Zero-Shot：以相同 RRF 參數（w1/w2/w3=1/1.5/0.5, θ=0.88, k=60）
           zero-shot 應用於 Spatial Visium HD 樣本（從 sample_registry 讀取），
           統計 analysis_history 中 Spatial EDA 的快取命中率與成功率。

  Step 3 — Transfer Metrics：計算
           Δ_precision = spatial_hit_rate − bulk_hit_rate
           Δ_latency   = spatial_avg_latency − bulk_avg_latency
           若 Δ_precision > 0           → positive transfer
           若 Δ_precision < −CATASTROPHIC_THRESHOLD → catastrophic transfer

執行方式：
  python benchmark/run_cross_domain_transfer.py
  python benchmark/run_cross_domain_transfer.py --dry-run   # 僅顯示計畫，不寫 DB
  python benchmark/run_cross_domain_transfer.py --out-json benchmark/results/cross_domain_transfer_results.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

# Allow running from project root directly.
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import DUCKDB_PATH, BIO_DB_ROOT

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Experiment constants ──────────────────────────────────────────────────────

CB1_RESULTS_PATH = BIO_DB_ROOT / "benchmark" / "results" / "cb1_benchmark_results.json"
DEFAULT_OUT_PATH = BIO_DB_ROOT / "benchmark" / "results" / "cross_domain_transfer_results.json"

# RRF config (Bulk-optimized)
RRF_CONFIG = {"w1": 1.0, "w2": 1.5, "w3": 0.5, "theta": 0.88, "k": 60}

# Transfer classification threshold
CATASTROPHIC_THRESHOLD = -0.10  # Δ_precision worse than −10 pp → catastrophic


# ── Step 1: Load Bulk Domain baseline ────────────────────────────────────────


def load_bulk_baseline() -> dict:
    """Read CB1 results JSON for Bulk domain metrics (axis_A + axis_B)."""
    if not CB1_RESULTS_PATH.exists():
        logger.warning(
            "CB1 results not found at %s. Run `python benchmark/run_benchmark.py --axis all` first.",
            CB1_RESULTS_PATH,
        )
        return {}

    with open(CB1_RESULTS_PATH, encoding="utf-8") as f:
        cb1 = json.load(f)

    axis_a = cb1.get("axis_A", {}).get("evo_prism", {})
    axis_b = cb1.get("axis_B", {}).get("evo_prism", {})

    bulk_hit_rate = axis_b.get("cache_hit_rate", None)
    bulk_avg_latency = axis_a.get("avg_latency_ms", None)

    # per_category from PM2-B
    per_cat = cb1.get("per_category", {})
    cache_hit_avg_ms = per_cat.get("cache_hit", {}).get("avg_latency_ms", None)
    cache_miss_avg_ms = per_cat.get("cache_miss", {}).get("avg_latency_ms", None)

    result = {
        "domain": "bulk_rnaseq",
        "n_samples": 98,
        "rrf_config": RRF_CONFIG,
        "cache_hit_rate": bulk_hit_rate,
        "avg_latency_ms": bulk_avg_latency,
        "cache_hit_avg_ms": cache_hit_avg_ms,
        "cache_miss_avg_ms": cache_miss_avg_ms,
        "source": str(CB1_RESULTS_PATH),
    }
    logger.info(
        "Bulk baseline: hit_rate=%s, avg_latency=%s ms",
        f"{bulk_hit_rate:.3f}" if bulk_hit_rate is not None else "N/A",
        f"{bulk_avg_latency:.1f}" if bulk_avg_latency is not None else "N/A",
    )
    return result


# ── Step 2: Spatial Zero-Shot ─────────────────────────────────────────────────


def load_spatial_metrics() -> dict:
    """Query analysis_history for Spatial Visium HD EDA metrics.

    Reads cache hit / miss / latency from historical analysis records.
    Returns empty dict with is_simulated=True if data is unavailable.
    """
    try:
        import duckdb

        with duckdb.connect(str(DUCKDB_PATH), read_only=True) as con:
            # Check if spatial samples exist
            spatial_samples = con.execute(
                """
                SELECT sample_id FROM sample_registry
                WHERE data_type = 'visium_hd'
                """
            ).fetchall()

            if not spatial_samples:
                logger.warning("No visium_hd samples found in sample_registry.")
                return {"domain": "visium_hd", "is_simulated": True, "n_samples": 0}

            sample_ids = [r[0] for r in spatial_samples]
            n_spatial = len(sample_ids)
            placeholders = ", ".join("?" * n_spatial)

            # EDA analysis records for spatial samples
            rows = con.execute(
                f"""
                SELECT
                    ah.sample_id,
                    ah.analysis_type,
                    ah.status,
                    ah.l1_cache_id,
                    EXTRACT(EPOCH FROM (ah.completed_at - ah.started_at)) * 1000 AS latency_ms
                FROM analysis_history ah
                WHERE ah.sample_id IN ({placeholders})
                  AND ah.analysis_type IN ('spatial_eda', 'bio_run_spatial_eda')
                  AND ah.status IN ('completed', 'failed')
                ORDER BY ah.completed_at DESC
                """,
                sample_ids,
            ).fetchall()

    except Exception as exc:
        logger.warning("Could not query analysis_history: %s", exc)
        return {"domain": "visium_hd", "is_simulated": True, "n_samples": 0, "error": str(exc)}

    if not rows:
        logger.warning(
            "No spatial EDA records in analysis_history for %d visium_hd samples.", n_spatial
        )
        return {
            "domain": "visium_hd",
            "is_simulated": True,
            "n_samples": n_spatial,
            "note": "No EDA runs recorded yet — run spatial EDA first.",
        }

    total = len(rows)
    completed = sum(1 for r in rows if r[2] == "completed")
    cache_hits = sum(1 for r in rows if r[3] is not None)  # l1_cache_id set = cache hit
    latencies = [r[4] for r in rows if r[4] is not None and r[4] > 0]
    avg_latency = sum(latencies) / len(latencies) if latencies else None

    result = {
        "domain": "visium_hd",
        "is_simulated": False,
        "n_samples": n_spatial,
        "n_eda_records": total,
        "success_rate": round(completed / total, 4) if total else None,
        "cache_hit_rate": round(cache_hits / total, 4) if total else None,
        "avg_latency_ms": round(avg_latency, 2) if avg_latency is not None else None,
        "rrf_config": RRF_CONFIG,
    }
    logger.info(
        "Spatial metrics: n=%d, hit_rate=%s, avg_latency=%s ms, success=%s",
        n_spatial,
        f"{result['cache_hit_rate']:.3f}" if result["cache_hit_rate"] is not None else "N/A",
        f"{result['avg_latency_ms']:.1f}" if result["avg_latency_ms"] is not None else "N/A",
        f"{result['success_rate']:.3f}" if result["success_rate"] is not None else "N/A",
    )
    return result


# ── Step 3: Transfer Metrics ──────────────────────────────────────────────────


def compute_transfer_metrics(bulk: dict, spatial: dict) -> dict:
    """Compute positive / catastrophic transfer indicators.

    Δ_precision = spatial_hit_rate − bulk_hit_rate
    Δ_latency   = spatial_avg_latency − bulk_avg_latency  (ms)

    Transfer label:
      positive     : Δ_precision > 0
      neutral      : |Δ_precision| < 0.05
      degraded     : −CATASTROPHIC_THRESHOLD ≤ Δ_precision < 0
      catastrophic : Δ_precision < CATASTROPHIC_THRESHOLD
    """
    b_hit = bulk.get("cache_hit_rate")
    s_hit = spatial.get("cache_hit_rate")
    b_lat = bulk.get("avg_latency_ms")
    s_lat = spatial.get("avg_latency_ms")

    is_simulated = spatial.get("is_simulated", False)

    delta_precision: float | None = None
    delta_latency: float | None = None
    transfer_label = "unknown"

    if b_hit is not None and s_hit is not None:
        delta_precision = round(s_hit - b_hit, 4)
        if delta_precision > 0.0:
            transfer_label = "positive"
        elif abs(delta_precision) < 0.05:
            transfer_label = "neutral"
        elif delta_precision >= CATASTROPHIC_THRESHOLD:
            transfer_label = "degraded"
        else:
            transfer_label = "catastrophic"

    if b_lat is not None and s_lat is not None:
        delta_latency = round(s_lat - b_lat, 2)

    metrics = {
        "delta_precision": delta_precision,
        "delta_latency_ms": delta_latency,
        "transfer_label": transfer_label,
        "catastrophic_threshold": CATASTROPHIC_THRESHOLD,
        "is_simulated": is_simulated,
        "interpretation": _interpret(transfer_label, delta_precision, delta_latency, is_simulated),
    }

    logger.info(
        "Transfer: label=%s  Δ_precision=%s  Δ_latency=%s ms",
        transfer_label,
        f"{delta_precision:+.4f}" if delta_precision is not None else "N/A",
        f"{delta_latency:+.1f}" if delta_latency is not None else "N/A",
    )
    return metrics


def _interpret(
    label: str,
    delta_p: float | None,
    delta_l: float | None,
    simulated: bool,
) -> str:
    if simulated:
        return (
            "空間 EDA 尚無歷史記錄；遷移評分無法計算。"
            "請先對 Spatial Visium HD 樣本執行 bio_run_spatial_eda，再重新執行本腳本。"
        )
    if label == "positive":
        return (
            f"正向遷移（positive transfer）：Bulk 優化的 RRF 配置在空間場景"
            f"快取命中率提升 {delta_p:+.1%}，ENGRAM 配置具跨域通用性。"
        )
    if label == "neutral":
        return (
            "中性遷移（neutral）：Bulk → Spatial 快取命中率差異 < 5 pp，"
            "RRF 配置跨域穩定，不需針對 Spatial 場景重調。"
        )
    if label == "degraded":
        return (
            f"輕度退化（degraded transfer）：Bulk → Spatial 快取命中率下降 {delta_p:+.1%}，"
            "建議對 Spatial 場景微調 RRF 權重（w1/w2）或降低 θ 門檻。"
        )
    if label == "catastrophic":
        return (
            f"災難性遷移（catastrophic transfer）：Bulk → Spatial 命中率暴跌 {delta_p:+.1%}，"
            "需針對 Spatial 場景重新優化 ENGRAM 配置（獨立調參或 domain-adaptive fine-tuning）。"
        )
    return "遷移評分無法計算（數據不足）。"


# ── Main ──────────────────────────────────────────────────────────────────────


def run(dry_run: bool = False, out_path: Path = DEFAULT_OUT_PATH) -> dict:
    t0 = time.perf_counter()

    print("=" * 60)
    print("PM3 Cross-Domain Transfer Test")
    print(f"  Source domain  : Bulk RNA-seq (CB1, 98 Kallisto samples)")
    print(f"  Target domain  : Spatial Visium HD (SDS-D0/D1/D2)")
    print(f"  RRF config     : w1={RRF_CONFIG['w1']} w2={RRF_CONFIG['w2']}"
          f" w3={RRF_CONFIG['w3']}  θ={RRF_CONFIG['theta']}  k={RRF_CONFIG['k']}")
    print("=" * 60)

    print("\n[Step 1] Loading Bulk baseline (CB1 results)…")
    bulk = load_bulk_baseline()

    print("\n[Step 2] Loading Spatial zero-shot metrics (analysis_history)…")
    spatial = load_spatial_metrics()

    print("\n[Step 3] Computing transfer metrics…")
    transfer = compute_transfer_metrics(bulk, spatial)

    elapsed_ms = (time.perf_counter() - t0) * 1000

    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "rrf_config": RRF_CONFIG,
        "bulk_domain": bulk,
        "spatial_domain": spatial,
        "transfer_metrics": transfer,
        "elapsed_ms": round(elapsed_ms, 2),
    }

    # ── Report ──
    print("\n" + "─" * 60)
    print("Transfer Metrics Summary")
    print(f"  Δ_precision  : {transfer['delta_precision']}")
    print(f"  Δ_latency_ms : {transfer['delta_latency_ms']}")
    print(f"  Transfer label: {transfer['transfer_label'].upper()}")
    print(f"\n  {transfer['interpretation']}")
    print("─" * 60)

    if not dry_run:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n✓ Results saved → {out_path}")
    else:
        print("\n[dry-run] Results not saved.")

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PM3 Cross-Domain Transfer Benchmark")
    parser.add_argument(
        "--dry-run", action="store_true", help="Detect only — do not write output JSON"
    )
    parser.add_argument(
        "--out-json",
        type=Path,
        default=DEFAULT_OUT_PATH,
        help="Output JSON path (default: benchmark/results/cross_domain_transfer_results.json)",
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run, out_path=args.out_json)
