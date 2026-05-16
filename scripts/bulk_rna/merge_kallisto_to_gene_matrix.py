#!/usr/bin/env python3
"""合併所有樣本的 Kallisto abundance.tsv → gene_counts.tsv + gene_tpm.tsv（以基因符號為主鍵）。"""
from __future__ import annotations

import csv
import logging
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config.settings import BIO_DB_ROOT

logger = logging.getLogger(__name__)

RESULTS_DIR = BIO_DB_ROOT / "bulk_rna_data" / "Kallisto_v1" / "results_kallisto"
OUT_COUNTS  = RESULTS_DIR / "gene_counts.tsv"
OUT_TPM     = RESULTS_DIR / "gene_tpm.tsv"


def parse_abundance(path: Path) -> tuple[dict[str, float], dict[str, float]]:
    """從單一 abundance.tsv 解析 gene→counts 與 gene→tpm。"""
    gene_counts: dict[str, float] = defaultdict(float)
    gene_tpm:    dict[str, float] = defaultdict(float)
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            target = row.get("target_id") or row.get("target", "")
            if not target:
                continue
            parts = target.split("|")
            if len(parts) >= 6 and parts[5].strip():
                gene = parts[5].strip()
            elif len(parts) >= 2 and parts[1].startswith("ENSMUSG"):
                gene = parts[1].strip()
            else:
                gene = parts[0].split("-")[0]

            try:
                counts = float(row.get("est_counts", 0))
            except (ValueError, TypeError):
                counts = 0.0
            try:
                tpm = float(row.get("tpm", 0))
            except (ValueError, TypeError):
                tpm = 0.0

            gene_counts[gene] += counts
            gene_tpm[gene] += tpm

    return gene_counts, gene_tpm


def _format_count(v: float) -> str:
    return str(int(v)) if v == int(v) else str(v)


def main() -> None:
    files = sorted(RESULTS_DIR.glob("*/abundance.tsv"))
    if not files:
        logger.error("No abundance.tsv files found under %s", RESULTS_DIR)
        sys.exit(1)

    samples: list[str] = []
    gene_set: set[str] = set()
    per_sample_counts: dict[str, dict[str, float]] = {}
    per_sample_tpm:    dict[str, dict[str, float]] = {}

    for path in files:
        sample = path.parent.name
        samples.append(sample)
        counts, tpm = parse_abundance(path)
        per_sample_counts[sample] = counts
        per_sample_tpm[sample] = tpm
        gene_set.update(counts)
        gene_set.update(tpm)

    genes = sorted(gene_set)

    with open(OUT_COUNTS, "w", encoding="utf-8") as fo:
        fo.write("gene\t" + "\t".join(samples) + "\n")
        for g in genes:
            row = [_format_count(per_sample_counts[s].get(g, 0.0)) for s in samples]
            fo.write(g + "\t" + "\t".join(row) + "\n")

    with open(OUT_TPM, "w", encoding="utf-8") as fo:
        fo.write("gene\t" + "\t".join(samples) + "\n")
        for g in genes:
            row = [str(per_sample_tpm[s].get(g, 0.0)) for s in samples]
            fo.write(g + "\t" + "\t".join(row) + "\n")

    logger.info("Wrote %s", OUT_COUNTS)
    logger.info("Wrote %s", OUT_TPM)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    main()
