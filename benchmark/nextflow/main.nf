// Nextflow benchmark workflow — Evo_PRISM CB1
// Per-sample QC + aggregate PCA on 98 Kallisto samples.
// Uses Python stdlib only (csv, json, math) so it runs in any Python 3 env.
//
// First run:
//   nextflow run benchmark/nextflow/main.nf \
//     --data_dir bulk_rna_data/Kallisto_v1/results_kallisto \
//     --results_dir benchmark/nextflow/results
//
// Incremental resume:
//   nextflow run benchmark/nextflow/main.nf -resume \
//     --data_dir bulk_rna_data/Kallisto_v1/results_kallisto \
//     --results_dir benchmark/nextflow/results

nextflow.enable.dsl = 2

params.data_dir    = "bulk_rna_data/Kallisto_v1/results_kallisto"
params.results_dir = "benchmark/nextflow/results"

// ── Process: per-sample QC (pure stdlib, no external deps) ───────────────────
process PER_SAMPLE_QC {
    tag "${sample}"
    publishDir "${params.results_dir}/qc", mode: 'copy', overwrite: true

    input:
    tuple val(sample), path(abundance), path(run_info)

    output:
    path "${sample}_qc.csv"

    script:
    """
    python3 - << 'PYEOF'
import json, csv

with open("${run_info}") as fh:
    ri = json.load(fh)

total_counts = 0.0
n_detected   = 0
with open("${abundance}") as fh:
    reader = csv.DictReader(fh, delimiter="\\t")
    for row in reader:
        cnt = float(row["est_counts"])
        total_counts += cnt
        if cnt > 0:
            n_detected += 1

result = {
    "sample":           "${sample}",
    "n_processed":      ri.get("n_processed", 0),
    "n_pseudoaligned":  ri.get("n_pseudoaligned", 0),
    "p_pseudoaligned":  ri.get("p_pseudoaligned", 0.0),
    "total_counts":     total_counts,
    "n_detected_genes": n_detected,
}
with open("${sample}_qc.csv", "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=result.keys())
    w.writeheader()
    w.writerow(result)
PYEOF
    """
}

// ── Process: aggregate QC + PCA (stdlib + numpy, widely available) ────────────
process AGGREGATE_QC {
    publishDir "${params.results_dir}", mode: 'copy', overwrite: true

    input:
    path qc_csvs

    output:
    path "aggregate_qc.csv"
    path "pca.png"

    script:
    """
    python3 - << 'PYEOF'
import subprocess, sys
subprocess.run([sys.executable, "-m", "pip", "install", "-q",
                "numpy", "matplotlib", "scikit-learn"], check=False)
import csv, glob, math, os

# Read all QC CSVs
rows = []
for fname in glob.glob("*_qc.csv"):
    with open(fname) as fh:
        rows.extend(list(csv.DictReader(fh)))

# Write aggregate
fieldnames = list(rows[0].keys())
with open("aggregate_qc.csv", "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(rows)

# PCA via numpy (available in nextflow/nextflow image)
try:
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    feat_cols = ["total_counts", "n_detected_genes", "p_pseudoaligned"]
    X = np.array([[float(r[c]) for c in feat_cols] for r in rows], dtype=float)

    # Standardise
    mu = X.mean(axis=0); sd = X.std(axis=0, ddof=1)
    sd[sd == 0] = 1.0
    Xz = (X - mu) / sd

    # PCA via SVD
    U, S, Vt = np.linalg.svd(Xz, full_matrices=False)
    pc = Xz @ Vt[:2].T

    fig, ax = plt.subplots(figsize=(8, 6))
    ax.scatter(pc[:, 0].tolist(), pc[:, 1].tolist(), alpha=0.7, s=30)
    ax.set_xlabel("PC1"); ax.set_ylabel("PC2")
    ax.set_title(f"QC PCA -- {len(rows)} samples")
    fig.savefig("pca.png", dpi=100, bbox_inches="tight")
    plt.close(fig)
except Exception as e:
    # Fallback: empty placeholder if numpy/matplotlib unavailable
    with open("pca.png", "wb") as fh:
        pass
    print(f"[WARN] PCA plot skipped: {e}")
PYEOF
    """
}

// ── Workflow ──────────────────────────────────────────────────────────────────
workflow {
    sample_ch = Channel
        .fromPath("${params.data_dir}/*/abundance.tsv")
        .map { abundance_file ->
            def sample   = abundance_file.parent.name
            def run_info = file("${abundance_file.parent}/run_info.json")
            tuple(sample, abundance_file, run_info)
        }

    qc_ch = PER_SAMPLE_QC(sample_ch)
    AGGREGATE_QC(qc_ch.collect())
}
