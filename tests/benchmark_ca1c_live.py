"""
CA1-C Live Benchmark — 真實 Evo_PRISM 系統實測
================================================

本腳本是 docs/paper/analysis/benchmark_cache_rrf.py（純模擬）的對照組。
所有操作均走真實系統：
  - bge-m3-Q8_0 embedding（llama-server port 8081）
  - DuckDB VSS HNSW 向量索引（gold/hermes_cache.duckdb）
  - analysis/l1_cache.py write_to_l1_cache / semantic_search

流程：
  Phase 0: 健康檢查（embedding server + DB）
  Phase 1: 以 analysis_history 的真實紀錄填入 L1 cache（seed）
  Phase 2: 對 450 多樣化查詢執行 semantic_search()，測量真實延遲
  Phase 3: 統計並輸出 JSON + Markdown 報告

輸出：
  results/benchmark_ca1c_live_results.json
"""

from __future__ import annotations

import json
import statistics
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import requests

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from config.settings import (
    DUCKDB_PATH,
    EMBEDDING_DIM,
    L1_CACHE_PATH,
    L1_COSINE_THRESHOLD,
    L1_TTL_DAYS,
    LLAMACPP_BASE_URL,
)

FIXTURES_FILE = ROOT / "tests" / "fixtures" / "diversified_queries_450.json"
OUTPUT_PATH   = ROOT / "results" / "benchmark_ca1c_live_results.json"
N_REPEAT      = 3   # 每筆查詢重測次數，取中位數


# ── Phase 0: 健康檢查 ────────────────────────────────────────────────────────

def check_health() -> None:
    base = LLAMACPP_BASE_URL.split("/v1")[0].rstrip("/")
    url = f"{base}/health"
    try:
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        print(f"[OK] Embedding server: {r.json()}")
    except Exception as e:
        print(f"[FAIL] Embedding server not reachable: {e}")
        print(f"  Start: llama-server.exe -m bge-m3-Q8_0.gguf --embedding --port 8081")
        raise SystemExit(1)

    if not L1_CACHE_PATH.exists():
        print(f"[FAIL] L1 cache not found: {L1_CACHE_PATH}")
        raise SystemExit(1)

    con = duckdb.connect(str(L1_CACHE_PATH), read_only=True)
    col = con.execute(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name='memory_recent' AND column_name='embedding'"
    ).fetchone()
    con.close()

    if col is None:
        print("[FAIL] memory_recent table missing — run 03_init_l1_cache.py")
        raise SystemExit(1)

    actual_dim = col[0]   # e.g. "FLOAT[1024]"
    expected   = f"FLOAT[{EMBEDDING_DIM}]"
    if actual_dim != expected:
        print(f"[FAIL] Schema mismatch: DB has {actual_dim}, config expects {expected}")
        print("  Run: python scripts/03_init_l1_cache.py --reset")
        raise SystemExit(1)

    print(f"[OK] L1 cache schema: {actual_dim} | path: {L1_CACHE_PATH}")
    print(f"[OK] Threshold: {L1_COSINE_THRESHOLD} | TTL: {L1_TTL_DAYS} days")


# ── embedding 工具 ────────────────────────────────────────────────────────────

def embed(text: str) -> list[float]:
    url = f"{LLAMACPP_BASE_URL.rstrip('/')}/embeddings"
    r = requests.post(
        url,
        json={"model": "bge-m3-Q8_0", "input": text},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["data"][0]["embedding"]


# ── Phase 1: 填入 L1 cache（seed） ───────────────────────────────────────────

def seed_cache(max_entries: int = 150) -> int:
    """
    從 analysis_history 取真實分析紀錄，embed summary 後寫入 L1 cache。
    僅 seed 一次；若 cache 已有資料則跳過。
    """
    # 連線確認目前行數
    con_check = duckdb.connect(str(L1_CACHE_PATH), read_only=True)
    existing = con_check.execute("SELECT COUNT(*) FROM memory_recent").fetchone()[0]  # type: ignore[index]
    con_check.close()

    if existing > 0:
        print(f"[SKIP] Cache already has {existing} entries — skipping seed phase")
        return existing

    print(f"\n=== Phase 1: Seeding L1 cache from analysis_history ===")

    main_con = duckdb.connect(str(DUCKDB_PATH), read_only=True)
    rows = main_con.execute(f"""
        SELECT analysis_id, sample_id, analysis_type, summary, parameters
        FROM analysis_history
        WHERE status = 'completed'
          AND summary IS NOT NULL
          AND summary != ''
        ORDER BY completed_at DESC
        LIMIT {max_entries}
    """).fetchall()
    main_con.close()

    print(f"  Found {len(rows)} completed analysis entries to seed")

    cache_con = duckdb.connect(str(L1_CACHE_PATH))
    try:
        cache_con.execute("LOAD vss")
        cache_con.execute("SET hnsw_enable_experimental_persistence = true")
    except Exception as e:
        print(f"  WARNING: VSS load: {e}")

    now       = datetime.now(timezone.utc)
    expires   = now + timedelta(days=L1_TTL_DAYS)
    written   = 0
    failed    = 0

    for i, (aid, sid, atype, summary, params) in enumerate(rows):
        # 用 summary 作為 query_text（代表「這類查詢的語意」）
        query_text = summary[:500]

        # 構建一個代表性 report_text
        report_text = f"[{atype}] {sid}: {summary}"
        if params:
            try:
                p = json.loads(params) if isinstance(params, str) else params
                report_text += f" | params: {json.dumps(p, ensure_ascii=False)[:200]}"
            except Exception:
                pass

        try:
            t0  = time.perf_counter()
            emb = embed(query_text)
            dt  = time.perf_counter() - t0

            cache_con.execute(
                """
                INSERT INTO memory_recent
                    (id, sample_id, query_text, report_text, summary,
                     embedding, analysis_id, created_at, expires_at, analysis_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    str(uuid.uuid4()),
                    sid or "unknown",
                    query_text,
                    report_text,
                    summary[:200],
                    emb,
                    str(aid) if aid else None,
                    now,
                    expires,
                    atype,
                ],
            )
            written += 1
            if (i + 1) % 10 == 0:
                print(f"  Seeded {written}/{len(rows)} ... (last embed {dt*1000:.1f} ms)")
        except Exception as e:
            failed += 1
            print(f"  WARN row {i}: {e}")

    cache_con.execute("CHECKPOINT")
    cache_con.close()
    print(f"  Seed complete: {written} written, {failed} failed")
    return written


# ── Phase 2: 執行 450 查詢 ────────────────────────────────────────────────────

def run_live_queries(queries: list[dict]) -> list[dict]:
    print(f"\n=== Phase 2: Running {len(queries)} live queries ===")
    print(f"  Threshold: cosine >= {L1_COSINE_THRESHOLD}")

    cache_con = duckdb.connect(str(L1_CACHE_PATH))
    try:
        cache_con.execute("LOAD vss")
        cache_con.execute("SET hnsw_enable_experimental_persistence = true")
    except Exception as e:
        print(f"  WARNING: VSS: {e}")

    row_count = cache_con.execute("SELECT COUNT(*) FROM memory_recent").fetchone()[0]  # type: ignore[index]
    print(f"  Cache entries: {row_count}")

    results = []
    hit_count     = 0
    miss_count    = 0
    error_count   = 0

    for i, q in enumerate(queries):
        qtext = q["query_text"]
        repeat_lats = []
        best_score  = 0.0
        is_hit      = False

        for _ in range(N_REPEAT):
            try:
                # embed the query
                t_emb0 = time.perf_counter()
                qvec   = embed(qtext)
                t_emb1 = time.perf_counter()
                embed_ms = (t_emb1 - t_emb0) * 1000

                # HNSW search
                t_srch0 = time.perf_counter()
                rows = cache_con.execute(
                    f"""
                    SELECT id, sample_id, summary, analysis_type,
                           array_cosine_similarity(embedding, ?::FLOAT[{EMBEDDING_DIM}]) AS score
                    FROM   memory_recent
                    WHERE  expires_at > now()
                    ORDER BY score DESC
                    LIMIT 1
                    """,
                    [qvec],
                ).fetchall()
                t_srch1 = time.perf_counter()
                search_ms = (t_srch1 - t_srch0) * 1000

                total_ms = embed_ms + search_ms
                repeat_lats.append(total_ms)

                if rows:
                    score = float(rows[0][4])
                    best_score = max(best_score, score)
                    if score >= L1_COSINE_THRESHOLD:
                        is_hit = True

            except Exception as e:
                error_count += 1
                print(f"  ERROR q{i}: {e}")
                repeat_lats.append(float("nan"))

        valid_lats = [x for x in repeat_lats if x == x]  # filter NaN
        median_lat = statistics.median(valid_lats) if valid_lats else float("nan")

        if is_hit:
            hit_count += 1
        else:
            miss_count += 1

        results.append({
            "query_id":          q["query_id"],
            "persona":           q.get("persona", ""),
            "llm_backend":       q.get("llm_backend", ""),
            "semantic_bucket":   q["semantic_bucket"],
            "ground_truth_hit":  q["ground_truth_hit"],
            "fingerprint_changed": q["fingerprint_changed"],
            "is_hit":            is_hit,
            "best_score":        round(best_score, 4),
            "latency_ms":        round(median_lat, 3) if median_lat == median_lat else None,
        })

        if (i + 1) % 50 == 0:
            running_hr = hit_count / (i + 1)
            print(f"  Progress {i+1}/{len(queries)} | hit_rate={running_hr:.1%} | errors={error_count}")

    cache_con.close()
    print(f"  Done: hits={hit_count}, misses={miss_count}, errors={error_count}")
    return results


# ── Phase 3: 統計與輸出 ───────────────────────────────────────────────────────

def compute_metrics(results: list[dict]) -> dict:
    n = len(results)
    hits   = [r for r in results if r["is_hit"]]
    misses = [r for r in results if not r["is_hit"]]

    hit_rate = len(hits) / n

    # Precision / Recall vs ground_truth_hit
    tp = sum(1 for r in results if r["is_hit"] and r["ground_truth_hit"])
    fp = sum(1 for r in results if r["is_hit"] and not r["ground_truth_hit"])
    fn = sum(1 for r in results if not r["is_hit"] and r["ground_truth_hit"])
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0

    # Pollution: hits where fingerprint_changed = True
    fp_changed_hits = sum(1 for r in results if r["is_hit"] and r["fingerprint_changed"])
    n_fp_changed    = sum(1 for r in results if r["fingerprint_changed"])
    pollution_rate  = fp_changed_hits / n_fp_changed if n_fp_changed > 0 else 0.0

    # Latency (hit vs miss)
    hit_lats  = [r["latency_ms"] for r in hits  if r["latency_ms"] is not None]
    miss_lats = [r["latency_ms"] for r in misses if r["latency_ms"] is not None]
    all_lats  = [r["latency_ms"] for r in results if r["latency_ms"] is not None]

    def med_iqr(lats: list[float]) -> tuple[float, float]:
        if not lats:
            return float("nan"), float("nan")
        s = sorted(lats)
        q1 = s[len(s) // 4]
        q3 = s[3 * len(s) // 4]
        return statistics.median(lats), q3 - q1

    hit_med,  hit_iqr  = med_iqr(hit_lats)
    miss_med, miss_iqr = med_iqr(miss_lats)
    all_med,  all_iqr  = med_iqr(all_lats)
    p95 = sorted(all_lats)[int(len(all_lats) * 0.95)] if all_lats else float("nan")

    # By persona / LLM
    personas = sorted(set(r["persona"] for r in results))
    llms     = sorted(set(r["llm_backend"] for r in results))
    by_persona = {
        p: sum(1 for r in results if r["persona"] == p and r["is_hit"]) /
           max(1, sum(1 for r in results if r["persona"] == p))
        for p in personas
    }
    by_llm = {
        l: sum(1 for r in results if r["llm_backend"] == l and r["is_hit"]) /
           max(1, sum(1 for r in results if r["llm_backend"] == l))
        for l in llms
    }

    # By semantic bucket
    bucket_hits  = [0] * 5
    bucket_total = [0] * 5
    for r in results:
        b = r["semantic_bucket"]
        bucket_total[b] += 1
        if r["is_hit"]:
            bucket_hits[b] += 1
    bucket_hit_rates = [
        bucket_hits[i] / bucket_total[i] if bucket_total[i] > 0 else 0.0
        for i in range(5)
    ]

    return {
        "n_queries":          n,
        "n_hits":             len(hits),
        "n_misses":           len(misses),
        "hit_rate":           round(hit_rate, 4),
        "pollution_rate":     round(pollution_rate, 4),
        "precision":          round(precision, 4),
        "recall":             round(recall, 4),
        "latency_hit_median_ms":  round(hit_med,  4) if hit_med  == hit_med  else None,
        "latency_hit_iqr_ms":     round(hit_iqr,  4) if hit_iqr  == hit_iqr  else None,
        "latency_miss_median_ms": round(miss_med, 3) if miss_med == miss_med else None,
        "latency_overall_median_ms": round(all_med, 3) if all_med == all_med else None,
        "latency_p95_ms":     round(p95, 3) if p95 == p95 else None,
        "by_persona":         {k: round(v, 4) for k, v in by_persona.items()},
        "by_llm_backend":     {k: round(v, 4) for k, v in by_llm.items()},
        "bucket_hit_rates":   [round(x, 4) for x in bucket_hit_rates],
    }


def print_report(metrics: dict) -> None:
    print("\n" + "=" * 70)
    print("CA1-C Live Benchmark Results (Real Evo_PRISM System)")
    print("=" * 70)
    print(f"\nN = {metrics['n_queries']} diversified queries | "
          f"Cache threshold = {L1_COSINE_THRESHOLD} | "
          f"Embedding = bge-m3-Q8_0 (1024-dim)\n")

    print("| Metric                        | Value           |")
    print("|:------------------------------|:----------------|")
    print(f"| Hit Rate                      | {metrics['hit_rate']:.1%}          |")
    print(f"| Pollution Rate                | {metrics['pollution_rate']:.1%}          |")
    print(f"| Precision (vs ground_truth)   | {metrics['precision']:.3f}          |")
    print(f"| Recall    (vs ground_truth)   | {metrics['recall']:.3f}          |")
    h_lat = metrics['latency_hit_median_ms']
    print(f"| Hit Latency (median)          | {f'{h_lat:.3f} ms' if h_lat else 'N/A'}       |")
    print(f"| Overall Latency (median)      | {metrics['latency_overall_median_ms']:.1f} ms        |")
    print(f"| P95 Latency                   | {metrics['latency_p95_ms']:.1f} ms        |")

    print("\n### By Persona")
    for p, hr in metrics["by_persona"].items():
        print(f"  {p:<30} hit_rate={hr:.1%}")

    print("\n### By LLM Backend")
    for l, hr in metrics["by_llm_backend"].items():
        print(f"  {l:<30} hit_rate={hr:.1%}")

    print("\n### By Semantic Bucket")
    labels = ["0-20% (zero)", "20-40% (low)", "40-60% (med)", "60-80% (high)", "80-100% (extreme)"]
    for i, (lbl, hr) in enumerate(zip(labels, metrics["bucket_hit_rates"])):
        print(f"  bucket {i} {lbl:<25} hit_rate={hr:.1%}")


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("Evo_PRISM CA1-C Live Benchmark")
    print(f"  Cache:      {L1_CACHE_PATH}")
    print(f"  Main DB:    {DUCKDB_PATH}")
    print(f"  Server:     {LLAMACPP_BASE_URL}")
    print(f"  Embed dim:  {EMBEDDING_DIM}")
    print()

    # Phase 0
    check_health()

    # Phase 1
    n_seeded = seed_cache(max_entries=150)
    if n_seeded == 0:
        print("[WARN] Cache still empty after seeding — check analysis_history")

    # Load diversified queries
    if not FIXTURES_FILE.exists():
        print(f"[FAIL] Queries file not found: {FIXTURES_FILE}")
        raise SystemExit(1)
    with open(FIXTURES_FILE, encoding="utf-8") as f:
        queries = json.load(f)
    print(f"\nLoaded {len(queries)} diversified queries from {FIXTURES_FILE.name}")

    # Phase 2
    t_start = time.time()
    per_query_results = run_live_queries(queries)
    elapsed = time.time() - t_start

    # Phase 3
    metrics = compute_metrics(per_query_results)
    print_report(metrics)

    # Save
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    output = {
        "benchmark":          "ca1c_live",
        "timestamp":          datetime.now(timezone.utc).isoformat(),
        "n_queries":          len(queries),
        "n_cache_seed":       n_seeded,
        "embedding_model":    "bge-m3-Q8_0",
        "embedding_dim":      EMBEDDING_DIM,
        "cosine_threshold":   L1_COSINE_THRESHOLD,
        "n_repeat":           N_REPEAT,
        "total_elapsed_sec":  round(elapsed, 1),
        "metrics":            metrics,
        "per_query":          per_query_results,
    }
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\nResults saved: {OUTPUT_PATH}")
    print(f"Total elapsed: {elapsed:.1f} s")


if __name__ == "__main__":
    main()
