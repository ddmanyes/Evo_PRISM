#!/usr/bin/env python3
"""合併所有樣本的 Kallisto abundance.tsv → gene_counts_ensembl.tsv + gene_tpm_ensembl.tsv（以 ENSMUSG ID 為主鍵）。

同時產生 gene_symbol_to_ensembl.tsv 對照表，供 map_ensembl_to_symbol.py 使用。
"""
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
OUT_COUNTS  = RESULTS_DIR / "gene_counts_ensembl.tsv"
OUT_TPM     = RESULTS_DIR / "gene_tpm_ensembl.tsv"
MAPPING     = RESULTS_DIR / "gene_symbol_to_ensembl.tsv"


def parse_abundance(
    path: Path,
    mapping: dict[str, str],
    counts_dict: dict[str, float],
    tpm_dict: dict[str, float],
) -> None:
    """從單一 abundance.tsv 填入 counts/tpm（以 ENSMUSG ID 為 key）並更新 symbol→ensembl 對照。"""
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            target = row.get("target_id") or row.get("target", "")
            if not target:
                continue
            parts = target.split("|")
            ensg     = parts[1].strip() if len(parts) >= 2 and parts[1].startswith("ENSMUSG") else None
            gene_sym = parts[5].strip() if len(parts) >= 6 and parts[5].strip() else None
            key = ensg or gene_sym or parts[0]

            try:
                counts = float(row.get("est_counts", 0))
            except (ValueError, TypeError):
                counts = 0.0
            try:
                tpm = float(row.get("tpm", 0))
            except (ValueError, TypeError):
                tpm = 0.0

            counts_dict[key] += counts
            tpm_dict[key] += tpm

            if gene_sym and ensg and gene_sym not in mapping:
                mapping[gene_sym] = ensg


def _format_count(v: float) -> str:
    return str(int(v)) if v == int(v) else str(v)


def main() -> None:
    files = sorted(RESULTS_DIR.glob("*/abundance.tsv"))
    if not files:
        logger.error("No abundance.tsv files found under %s", RESULTS_DIR)
        sys.exit(1)

    samples: list[str] = []
    per_sample_counts: dict[str, dict[str, float]] = {}
    per_sample_tpm:    dict[str, dict[str, float]] = {}
    mapping:           dict[str, str] = {}
    gene_set:          set[str] = set()

    for path in files:
        sample = path.parent.name
        samples.append(sample)
        counts: dict[str, float] = defaultdict(float)
        tpms:   dict[str, float] = defaultdict(float)
        parse_abundance(path, mapping, counts, tpms)
        per_sample_counts[sample] = counts
        per_sample_tpm[sample] = tpms
        gene_set.update(counts)

    genes = sorted(gene_set)

    with open(MAPPING, "w", encoding="utf-8") as fo:
        fo.write("gene_symbol\tensembl_gene_id\n")
        for sym, en in sorted(mapping.items()):
            fo.write(f"{sym}\t{en}\n")
    logger.info("Wrote mapping: %s", MAPPING)

    with open(OUT_COUNTS, "w", encoding="utf-8") as fo:
        fo.write("gene\t" + "\t".join(samples) + "\n")
        for g in genes:
            row = [_format_count(per_sample_counts[s].get(g, 0.0)) for s in samples]
            fo.write(g + "\t" + "\t".join(row) + "\n")
    logger.info("Wrote %s", OUT_COUNTS)

    with open(OUT_TPM, "w", encoding="utf-8") as fo:
        fo.write("gene\t" + "\t".join(samples) + "\n")
        for g in genes:
            row = [str(per_sample_tpm[s].get(g, 0.0)) for s in samples]
            fo.write(g + "\t" + "\t".join(row) + "\n")
    logger.info("Wrote %s", OUT_TPM)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    main()
