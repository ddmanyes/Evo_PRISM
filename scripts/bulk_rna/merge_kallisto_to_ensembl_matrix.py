#!/usr/bin/env python3
import os
import glob
import csv
from collections import defaultdict, OrderedDict

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
RESULTS_DIR = os.path.join(ROOT, 'results_kallisto')
OUT_COUNTS = os.path.join(RESULTS_DIR, 'gene_counts_ensembl.tsv')
OUT_TPM = os.path.join(RESULTS_DIR, 'gene_tpm_ensembl.tsv')
MAPPING = os.path.join(RESULTS_DIR, 'gene_symbol_to_ensembl.tsv')


def parse_abundance_for_mapping(path, mapping, counts_dict, tpm_dict):
    with open(path, 'r') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            target = row.get('target_id') or row.get('target')
            if not target:
                continue
            parts = target.split('|')
            ensg = None
            if len(parts) >= 2 and parts[1].startswith('ENSMUSG'):
                ensg = parts[1].strip()
            gene_sym = None
            if len(parts) >= 6 and parts[5].strip():
                gene_sym = parts[5].strip()

            key = ensg if ensg else (gene_sym if gene_sym else parts[0])

            try:
                counts = float(row.get('est_counts', 0))
            except:
                counts = 0.0
            try:
                tpm = float(row.get('tpm', 0))
            except:
                tpm = 0.0

            counts_dict[key] += counts
            tpm_dict[key] += tpm

            if gene_sym and ensg:
                if gene_sym not in mapping:
                    mapping[gene_sym] = ensg


def main():
    pattern = os.path.join(RESULTS_DIR, '*', 'abundance.tsv')
    files = sorted(glob.glob(pattern))
    if not files:
        print('No abundance.tsv files found under', RESULTS_DIR)
        return

    samples = []
    per_sample_counts = OrderedDict()
    per_sample_tpm = OrderedDict()
    mapping = {}
    gene_set = set()

    for path in files:
        sample = os.path.basename(os.path.dirname(path))
        samples.append(sample)
        counts = defaultdict(float)
        tpms = defaultdict(float)
        parse_abundance_for_mapping(path, mapping, counts, tpms)
        per_sample_counts[sample] = counts
        per_sample_tpm[sample] = tpms
        gene_set.update(counts.keys())

    genes = sorted(gene_set)

    # write mapping
    with open(MAPPING, 'w') as fo:
        fo.write('gene_symbol\tensembl_gene_id\n')
        for sym, en in sorted(mapping.items()):
            fo.write(f"{sym}\t{en}\n")

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

    print('Wrote mapping:', MAPPING)
    print('Wrote:', OUT_COUNTS)
    print('Wrote:', OUT_TPM)


if __name__ == '__main__':
    from collections import defaultdict
    main()
