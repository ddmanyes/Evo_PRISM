#!/usr/bin/env python3
import os
import csv
from collections import defaultdict

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
RESULTS_DIR = os.path.join(ROOT, 'results_kallisto')
MAPPING = os.path.join(RESULTS_DIR, 'gene_symbol_to_ensembl.tsv')
IN_COUNTS = os.path.join(RESULTS_DIR, 'gene_counts_ensembl.tsv')
IN_TPM = os.path.join(RESULTS_DIR, 'gene_tpm_ensembl.tsv')
OUT_COUNTS = os.path.join(RESULTS_DIR, 'gene_counts_mapped_symbol.tsv')
OUT_TPM = os.path.join(RESULTS_DIR, 'gene_tpm_mapped_symbol.tsv')


def load_mapping(path):
    ensembl2sym = {}
    if not os.path.exists(path):
        return ensembl2sym
    with open(path, 'r') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            sym = row.get('gene_symbol')
            en = row.get('ensembl_gene_id')
            if not sym or not en:
                continue
            # if multiple symbols map to same Ensembl, keep first (mapping was generated earlier)
            if en not in ensembl2sym:
                ensembl2sym[en] = sym
    return ensembl2sym


def map_file(in_path, out_path, ensembl2sym):
    if not os.path.exists(in_path):
        print('Input not found:', in_path)
        return
    with open(in_path, 'r') as f:
        reader = csv.reader(f, delimiter='\t')
        header = next(reader)
        samples = header[1:]

        agg = defaultdict(lambda: [0.0] * len(samples))

        for row in reader:
            if not row:
                continue
            en = row[0]
            vals = [float(x) if x != '' else 0.0 for x in row[1:]]
            sym = ensembl2sym.get(en, en)
            cur = agg[sym]
            for i, v in enumerate(vals):
                cur[i] += v

    # write
    with open(out_path, 'w') as fo:
        fo.write('gene\t' + '\t'.join(samples) + '\n')
        for g, vals in sorted(agg.items()):
            # format integers without .0 when appropriate
            out_vals = []
            for v in vals:
                if abs(v - round(v)) < 1e-8:
                    out_vals.append(str(int(round(v))))
                else:
                    out_vals.append(str(v))
            fo.write(g + '\t' + '\t'.join(out_vals) + '\n')


def main():
    ensembl2sym = load_mapping(MAPPING)
    if not ensembl2sym:
        print('Warning: mapping file not found or empty:', MAPPING)

    map_file(IN_COUNTS, OUT_COUNTS, ensembl2sym)
    map_file(IN_TPM, OUT_TPM, ensembl2sym)
    print('Wrote:', OUT_COUNTS)
    print('Wrote:', OUT_TPM)


if __name__ == '__main__':
    main()
