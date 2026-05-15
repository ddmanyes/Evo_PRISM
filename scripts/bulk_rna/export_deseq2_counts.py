#!/usr/bin/env python3
"""Produce integer raw counts matrix and a colData template for DESeq2/edgeR.

Usage: python3 export_deseq2_counts.py [input_counts.tsv]
If input is omitted, uses results_kallisto/gene_counts_mapped_symbol.tsv if exists,
otherwise results_kallisto/gene_counts_ensembl.tsv or results_kallisto/gene_counts.tsv.
"""
import os
import sys
import csv
from math import isclose

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
RESULTS_DIR = os.path.join(ROOT, 'results_kallisto')
DEFAULTS = [
    os.path.join(RESULTS_DIR, 'gene_counts_mapped_symbol.tsv'),
    os.path.join(RESULTS_DIR, 'gene_counts_ensembl.tsv'),
    os.path.join(RESULTS_DIR, 'gene_counts.tsv'),
]

OUT_COUNTS = os.path.join(RESULTS_DIR, 'deseq2_counts.tsv')
OUT_COUNTS_CSV = os.path.join(RESULTS_DIR, 'deseq2_counts.csv')
OUT_COLDATA = os.path.join(RESULTS_DIR, 'deseq2_coldata.tsv')


def choose_input():
    if len(sys.argv) > 1:
        path = sys.argv[1]
        if os.path.exists(path):
            return path
        else:
            print('Provided input not found:', path)
            sys.exit(1)
    for p in DEFAULTS:
        if os.path.exists(p):
            return p
    print('No default counts file found. Expected one of:', ','.join(DEFAULTS))
    sys.exit(1)


def read_counts(path):
    with open(path, 'r') as f:
        reader = csv.reader(f, delimiter='\t')
        header = next(reader)
        samples = header[1:]
        genes = []
        mat = []
        for row in reader:
            if not row:
                continue
            genes.append(row[0])
            vals = [float(x) if x != '' else 0.0 for x in row[1:]]
            mat.append(vals)
    return genes, samples, mat


def to_integer_matrix(genes, samples, mat):
    # DESeq2/edgeR require integer counts. If values are floats, round to nearest int.
    int_mat = []
    for row in mat:
        int_row = []
        for v in row:
            # if already integer-like, cast; else round
            if isclose(v, round(v), rel_tol=1e-9, abs_tol=1e-9):
                int_row.append(int(round(v)))
            else:
                int_row.append(int(round(v)))
        int_mat.append(int_row)
    return int_mat


def write_tsv(path, genes, samples, mat):
    with open(path, 'w') as fo:
        fo.write('gene\t' + '\t'.join(samples) + '\n')
        for g, row in zip(genes, mat):
            fo.write(g + '\t' + '\t'.join(str(x) for x in row) + '\n')


def write_csv(path, genes, samples, mat):
    import csv
    with open(path, 'w', newline='') as fo:
        w = csv.writer(fo)
        w.writerow(['gene'] + samples)
        for g, row in zip(genes, mat):
            w.writerow([g] + row)


def infer_coldata(samples):
    # Simple heuristic: split sample name by '_' and take the first token as group
    rows = []
    for s in samples:
        group = s.split('_')[0] if '_' in s else s
        rows.append((s, group))
    return rows


def write_coldata(path, rows):
    with open(path, 'w') as fo:
        fo.write('sample\tgroup\n')
        for s, g in rows:
            fo.write(f"{s}\t{g}\n")


def main():
    inp = choose_input()
    genes, samples, mat = read_counts(inp)
    int_mat = to_integer_matrix(genes, samples, mat)
    write_tsv(OUT_COUNTS, genes, samples, int_mat)
    write_csv(OUT_COUNTS_CSV, genes, samples, int_mat)
    coldata = infer_coldata(samples)
    write_coldata(OUT_COLDATA, coldata)
    print('Wrote counts TSV:', OUT_COUNTS)
    print('Wrote counts CSV:', OUT_COUNTS_CSV)
    print('Wrote colData template:', OUT_COLDATA)


if __name__ == '__main__':
    main()
