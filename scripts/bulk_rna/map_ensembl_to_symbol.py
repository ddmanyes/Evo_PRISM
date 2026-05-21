#!/usr/bin/env python3
"""將 gene_counts_ensembl.tsv 的 ENSMUSG ID 轉換為基因符號，輸出最終版計數矩陣。

需先執行 merge_kallisto_to_ensembl_matrix.py 產生 gene_symbol_to_ensembl.tsv。
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
MAPPING = RESULTS_DIR / "gene_symbol_to_ensembl.tsv"
IN_COUNTS = RESULTS_DIR / "gene_counts_ensembl.tsv"
IN_TPM = RESULTS_DIR / "gene_tpm_ensembl.tsv"
OUT_COUNTS = RESULTS_DIR / "gene_counts_mapped_symbol.tsv"
OUT_TPM = RESULTS_DIR / "gene_tpm_mapped_symbol.tsv"


def load_mapping(path: Path) -> dict[str, str]:
    """載入 ensembl_gene_id → gene_symbol 對照表。"""
    ensembl2sym: dict[str, str] = {}
    if not path.exists():
        return ensembl2sym
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            sym = row.get("gene_symbol", "")
            en = row.get("ensembl_gene_id", "")
            if sym and en and en not in ensembl2sym:
                ensembl2sym[en] = sym
    return ensembl2sym


def map_file(in_path: Path, out_path: Path, ensembl2sym: dict[str, str]) -> None:
    """讀取以 ENSMUSG ID 為主鍵的計數檔，轉換後寫出以基因符號為主鍵的新檔。"""
    if not in_path.exists():
        logger.error("Input not found: %s", in_path)
        return

    with open(in_path, encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        header = next(reader)
        samples = header[1:]
        n = len(samples)
        agg: dict[str, list[float]] = defaultdict(lambda: [0.0] * n)
        for row in reader:
            if not row:
                continue
            en = row[0]
            vals = [float(x) if x else 0.0 for x in row[1:]]
            sym = ensembl2sym.get(en, en)
            cur = agg[sym]
            for i, v in enumerate(vals):
                cur[i] += v

    with open(out_path, "w", encoding="utf-8") as fo:
        fo.write("gene\t" + "\t".join(samples) + "\n")
        for g, vals in sorted(agg.items()):
            out_vals = [str(int(round(v))) if abs(v - round(v)) < 1e-8 else str(v) for v in vals]
            fo.write(g + "\t" + "\t".join(out_vals) + "\n")

    logger.info("Wrote %s", out_path)


def main() -> None:
    ensembl2sym = load_mapping(MAPPING)
    if not ensembl2sym:
        logger.warning(
            "Mapping file not found or empty: %s — output will use Ensembl IDs as-is", MAPPING
        )
    map_file(IN_COUNTS, OUT_COUNTS, ensembl2sym)
    map_file(IN_TPM, OUT_TPM, ensembl2sym)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    main()
