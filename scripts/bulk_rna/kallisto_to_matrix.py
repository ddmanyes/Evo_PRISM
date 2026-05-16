#!/usr/bin/env python3
"""從 Kallisto abundance.tsv + reference FASTA 建立 gene count 矩陣（舊版，需 FASTA）。

較新版請用 merge_kallisto_to_gene_matrix.py（不需 reference FASTA）。
"""
from __future__ import annotations

import gzip
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config.settings import BIO_DB_ROOT

logger = logging.getLogger(__name__)

KALLISTO_RESULTS_DIR = BIO_DB_ROOT / "bulk_rna_data" / "Kallisto_v1" / "results_kallisto"
REF_FASTA            = BIO_DB_ROOT / "references" / "genome" / "transcripts.fasta.gz"
OUTPUT_MATRIX        = KALLISTO_RESULTS_DIR / "kallisto_gene_counts.csv"


def load_transcript_to_gene_map(fasta_path: Path) -> dict[str, str] | None:
    """從 FASTA 解析 transcript→gene 對照（header 格式：>transcript_id|gene_id|...）。"""
    t2g: dict[str, str] = {}
    logger.info("Loading transcript mapping from %s", fasta_path)
    try:
        open_func = gzip.open if str(fasta_path).endswith(".gz") else open
        with open_func(fasta_path, "rt") as f:
            for line in f:
                if line.startswith(">"):
                    parts = line[1:].strip().split("|")
                    if len(parts) >= 2:
                        t2g[parts[0]] = parts[1]
        logger.info("Mapped %d transcripts to genes", len(t2g))
        if not t2g:
            logger.warning("No transcript-to-gene mappings found — check FASTA header format")
        return t2g
    except Exception:
        logger.exception("Error reading FASTA file %s", fasta_path)
        return None


def aggregate_kallisto_counts(
    kallisto_dir: Path,
    t2g_map: dict[str, str],
    output_file: Path,
) -> None:
    """將所有樣本的 abundance.tsv 彙整為 gene count 矩陣並寫出 CSV。"""
    logger.info("Scanning for Kallisto results in %s", kallisto_dir)
    abundance_files = sorted(kallisto_dir.rglob("abundance.tsv"))
    if not abundance_files:
        logger.error("No abundance.tsv files found under %s", kallisto_dir)
        return

    logger.info("Found %d samples", len(abundance_files))
    gene_counts: dict[str, pd.Series] = {}

    for f in abundance_files:
        sample_id = f.parent.name
        logger.info("Processing %s", sample_id)
        df = pd.read_csv(f, sep="\t")
        df["gene_id"] = df["target_id"].map(t2g_map)
        unmapped = int(df["gene_id"].isna().sum())
        if unmapped:
            logger.warning("%s: %d transcripts could not be mapped", sample_id, unmapped)
        gene_counts[sample_id] = df.groupby("gene_id")["est_counts"].sum()

    matrix = pd.DataFrame(gene_counts).fillna(0).round().astype(int)
    matrix.to_csv(output_file, encoding="utf-8")
    logger.info("Saved gene count matrix to %s  shape=%s", output_file, matrix.shape)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    if not REF_FASTA.exists():
        logger.error("Reference FASTA not found: %s", REF_FASTA)
        sys.exit(1)
    if not KALLISTO_RESULTS_DIR.exists():
        logger.error("Kallisto results directory not found: %s", KALLISTO_RESULTS_DIR)
        sys.exit(1)
    t2g = load_transcript_to_gene_map(REF_FASTA)
    if t2g is not None:
        aggregate_kallisto_counts(KALLISTO_RESULTS_DIR, t2g_map=t2g, output_file=OUTPUT_MATRIX)
