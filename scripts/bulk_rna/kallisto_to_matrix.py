import os
import pandas as pd
import glob
import gzip

def load_transcript_to_gene_map(fasta_path):
    """
    Parses a FASTA file to create a transcript-to-gene mapping.
    Handles headers formatted like: >transcript_id|gene_id|...
    """
    t2g = {}
    print(f"Loading transcript mapping from {fasta_path}...")
    
    try:
        # Handle both gzipped and plain FASTA
        open_func = gzip.open if fasta_path.endswith('.gz') else open
        mode = 'rt' if fasta_path.endswith('.gz') else 'r'
        
        with open_func(fasta_path, mode) as f:
            for line in f:
                if line.startswith('>'):
                    # The header is like: >ENSMUST00000193812.2|ENSMUSG00000102693.2|...
                    header_parts = line[1:].strip().split('|')
                    if len(header_parts) >= 2:
                        transcript_id = header_parts[0]
                        gene_id = header_parts[1]
                        t2g[transcript_id] = gene_id
                        
        print(f"  - Mapped {len(t2g)} transcripts to genes.")
        if len(t2g) == 0:
            print("    WARNING: No transcript-to-gene mappings were found. Check FASTA header format.")
        return t2g
    except Exception as e:
        print(f"Error reading FASTA file: {e}")
        return None

def aggregate_kallisto_counts(kallisto_dir, t2g_map, output_file):
    """
    Aggregates Kallisto abundance.tsv files into a gene count matrix.
    """
    print(f"Scanning for Kallisto results in {kallisto_dir}...")
    
    # Find all abundance.tsv files (assuming structure: results_kallisto/SAMPLE_ID/abundance.tsv)
    # We search recursively
    abundance_files = glob.glob(os.path.join(kallisto_dir, "**", "abundance.tsv"), recursive=True)
    
    if not abundance_files:
        print("No 'abundance.tsv' files found!")
        return
    
    print(f"  - Found {len(abundance_files)} samples.")
    
    gene_counts = {}
    
    for f in abundance_files:
        # Extract sample ID from parent directory name
        sample_id = os.path.basename(os.path.dirname(f))
        print(f"  - Processing {sample_id}...")
        
        # Read Kallisto output
        df = pd.read_csv(f, sep='\t')
        
        # Map transcripts to genes
        df['gene_id'] = df['target_id'].map(t2g_map)
        
        # Drop unmapped transcripts
        unmapped = df['gene_id'].isna().sum()
        if unmapped > 0:
            print(f"    Warning: {unmapped} transcripts could not be mapped to a gene.")
            
        # Sum counts by gene
        # We use 'est_counts' for raw counts (suitable for DESeq2)
        sample_counts = df.groupby('gene_id')['est_counts'].sum()
        
        gene_counts[sample_id] = sample_counts
        
    # Combine into a single DataFrame
    matrix = pd.DataFrame(gene_counts)
    
    # Fill NaNs with 0 (genes present in some samples but not others)
    matrix = matrix.fillna(0)
    
    # Convert to integer (counts should be integers for most downstream tools, though Kallisto gives floats)
    matrix = matrix.round().astype(int)
    
    # Save
    matrix.to_csv(output_file)
    print(f"Successfully saved gene count matrix to {output_file}")
    print(f"  - Matrix shape: {matrix.shape}")

if __name__ == "__main__":
    # --- Configuration ---
    # Adjust these paths as needed
    BASE_DIR = os.getcwd()
    KALLISTO_RESULTS_DIR = os.path.join(BASE_DIR, "../Kallisto_v1/results_kallisto") 
    REF_FASTA = os.path.join(BASE_DIR, "../Kallisto_v1/reference/transcripts.fasta.gz")
    OUTPUT_MATRIX = "input/kallisto_gene_counts.csv"
    
    # Check if files exist
    if not os.path.exists(KALLISTO_RESULTS_DIR):
        print(f"Error: Kallisto results directory not found: {KALLISTO_RESULTS_DIR}")
        # Fallback for testing/dev environment
        # KALLISTO_RESULTS_DIR = "results_kallisto" 
    
    if not os.path.exists(REF_FASTA):
        print(f"Error: Reference FASTA not found: {REF_FASTA}")
        print("Please ensure the reference file path is correct.")
        exit(1)
        
    # Run
    t2g = load_transcript_to_gene_map(REF_FASTA)
    if t2g:
        aggregate_kallisto_counts(KALLISTO_RESULTS_DIR, t2g_map=t2g, output_file=OUTPUT_MATRIX)
