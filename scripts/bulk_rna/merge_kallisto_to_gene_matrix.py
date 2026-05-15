#!/usr/bin/env python3
import os
import glob
import csv
from collections import defaultdict, OrderedDict

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
RESULTS_DIR = os.path.join(ROOT, 'results_kallisto')
OUT_COUNTS = os.path.join(RESULTS_DIR, 'gene_counts.tsv')
OUT_TPM = os.path.join(RESULTS_DIR, 'gene_tpm.tsv')


def parse_abundance(path):
    """Return dict gene -> (counts_sum, tpm_sum) from an abundance.tsv"""
    gene_counts = defaultdict(float)
    gene_tpm = defaultdict(float)
    with open(path, 'r') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            target = row.get('target_id') or row.get('target')
            if not target:
                continue
            # target_id fields are pipe-separated; gene symbol appears around token index 5
            parts = target.split('|')
            gene = None
            if len(parts) >= 6 and parts[5].strip():
                gene = parts[5].strip()
            elif len(parts) >= 2 and parts[1].startswith('ENSMUSG'):
                gene = parts[1].strip()
            else:
                gene = parts[0].split('-')[0]

            try:
                counts = float(row.get('est_counts', 0))
            except:
                counts = 0.0
            try:
                tpm = float(row.get('tpm', 0))
            except:
                tpm = 0.0

            gene_counts[gene] += counts
            gene_tpm[gene] += tpm

    return gene_counts, gene_tpm


def main():
    # find all abundance.tsv files under results_kallisto/*/abundance.tsv
    pattern = os.path.join(RESULTS_DIR, '*', 'abundance.tsv')
    files = sorted(glob.glob(pattern))
    if not files:
        print('No abundance.tsv files found under', RESULTS_DIR)
        return

    samples = []
    gene_set = set()
    per_sample_counts = OrderedDict()
    per_sample_tpm = OrderedDict()

    for path in files:
        sample = os.path.basename(os.path.dirname(path))
        samples.append(sample)
        counts, tpm = parse_abundance(path)
        per_sample_counts[sample] = counts
        per_sample_tpm[sample] = tpm
        gene_set.update(counts.keys())
        gene_set.update(tpm.keys())

    genes = sorted(gene_set)

    # write counts matrix
    with open(OUT_COUNTS, 'w') as fo:
        fo.write('gene\t' + '\t'.join(samples) + '\n')
        for g in genes:
            row = [str(int(per_sample_counts[s].get(g, 0))) if per_sample_counts[s].get(g, 0).is_integer() else str(per_sample_counts[s].get(g, 0)) for s in samples]
            fo.write(g + '\t' + '\t'.join(row) + '\n')

    # write tpm matrix
    with open(OUT_TPM, 'w') as fo:
        fo.write('gene\t' + '\t'.join(samples) + '\n')
        for g in genes:
            row = [str(per_sample_tpm[s].get(g, 0.0)) for s in samples]
            fo.write(g + '\t' + '\t'.join(row) + '\n')

    print('Wrote:', OUT_COUNTS)
    print('Wrote:', OUT_TPM)


if __name__ == '__main__':
    main()
