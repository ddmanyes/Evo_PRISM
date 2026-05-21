#!/usr/bin/env python3
"""產出整數 raw counts 矩陣與 colData 模板，供 DESeq2/edgeR 直接使用。

使用方式：
    python3 export_deseq2_counts.py [input_counts.tsv]

若未指定輸入，依序嘗試：
    gene_counts_mapped_symbol.tsv → gene_counts_ensembl.tsv → gene_counts.tsv
"""

from __future__ import annotations

import csv
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config.settings import BIO_DB_ROOT

logger = logging.getLogger(__name__)

RESULTS_DIR = BIO_DB_ROOT / "bulk_rna_data" / "Kallisto_v1" / "results_kallisto"
DEFAULTS = [
    RESULTS_DIR / "gene_counts_mapped_symbol.tsv",
    RESULTS_DIR / "gene_counts_ensembl.tsv",
    RESULTS_DIR / "gene_counts.tsv",
]
OUT_COUNTS = RESULTS_DIR / "deseq2_counts.tsv"
OUT_COUNTS_CSV = RESULTS_DIR / "deseq2_counts.csv"
OUT_COLDATA = RESULTS_DIR / "deseq2_coldata.tsv"


def choose_input() -> Path:
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        if path.exists():
            return path
        logger.error("Provided input not found: %s", path)
        sys.exit(1)
    for p in DEFAULTS:
        if p.exists():
            return p
    logger.error(
        "No default counts file found. Expected one of: %s",
        ", ".join(str(p) for p in DEFAULTS),
    )
    sys.exit(1)


def read_counts(path: Path) -> tuple[list[str], list[str], list[list[float]]]:
    with open(path, encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        header = next(reader)
        samples = header[1:]
        genes: list[str] = []
        mat: list[list[float]] = []
        for row in reader:
            if not row:
                continue
            genes.append(row[0])
            mat.append([float(x) if x else 0.0 for x in row[1:]])
    return genes, samples, mat


def to_integer_matrix(mat: list[list[float]]) -> list[list[int]]:
    """DESeq2/edgeR 需要整數 counts，對 Kallisto 的浮點估計值四捨五入。"""
    return [[int(round(v)) for v in row] for row in mat]


def write_tsv(path: Path, genes: list[str], samples: list[str], mat: list[list[int]]) -> None:
    with open(path, "w", encoding="utf-8") as fo:
        fo.write("gene\t" + "\t".join(samples) + "\n")
        for g, row in zip(genes, mat):
            fo.write(g + "\t" + "\t".join(str(x) for x in row) + "\n")


def write_csv(path: Path, genes: list[str], samples: list[str], mat: list[list[int]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fo:
        w = csv.writer(fo)
        w.writerow(["gene"] + samples)
        for g, row in zip(genes, mat):
            w.writerow([g] + row)


def infer_coldata(samples: list[str]) -> list[tuple[str, str]]:
    """以樣本名稱首段（`_` 分隔）推斷 group，僅作為初始模板，請人工確認。"""
    return [(s, s.split("_")[0] if "_" in s else s) for s in samples]


def write_coldata(path: Path, rows: list[tuple[str, str]]) -> None:
    with open(path, "w", encoding="utf-8") as fo:
        fo.write("sample\tgroup\n")
        for s, g in rows:
            fo.write(f"{s}\t{g}\n")


def main() -> None:
    inp = choose_input()
    logger.info("Using input: %s", inp)
    genes, samples, mat = read_counts(inp)
    int_mat = to_integer_matrix(mat)
    write_tsv(OUT_COUNTS, genes, samples, int_mat)
    logger.info("Wrote counts TSV: %s", OUT_COUNTS)
    write_csv(OUT_COUNTS_CSV, genes, samples, int_mat)
    logger.info("Wrote counts CSV: %s", OUT_COUNTS_CSV)
    coldata = infer_coldata(samples)
    write_coldata(OUT_COLDATA, coldata)
    logger.info("Wrote colData template: %s  (please verify group assignments)", OUT_COLDATA)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    main()
