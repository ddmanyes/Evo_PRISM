"""
Phase 2A — L3 Visium HD (8µm h5ad) → L2 Parquet

Output layout per sample:
  silver/{sample_id}/
      obs_metadata.parquet   barcode + spatial coordinates
      var_metadata.parquet   gene_name + gene_id + genome
      expression/
          part-0000.parquet  long-format (barcode, gene_name, count), non-zero only
          part-0001.parquet  ...

DuckDB query example (gene spatial map):
  SELECT o.barcode, o.spatial_x, o.spatial_y, e.count
  FROM 'silver/CRC/obs_metadata.parquet' o
  JOIN 'silver/CRC/expression/*.parquet' e USING (barcode)
  WHERE e.gene_name = 'PTPRC'
"""

import argparse
import json
import sys
import time
from pathlib import Path

import anndata as ad
import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DUCKDB_PATH, L2_ROOT, L3_ROOT

# ── Visium HD 8µm h5ad relative path inside a sample's outs/ ──────────────
_H5AD_SUBPATH = "binned_outputs/binned_outputs/square_008um/filtered_feature_bc_matrix_agg.h5ad"

CHUNK_SIZE = 5_000  # barcodes per Parquet part (~10 MB/part after compression)


def find_h5ad(l3_path: Path) -> Path:
    """Resolve the 8µm h5ad inside a Visium HD outs directory."""
    candidate = l3_path / _H5AD_SUBPATH
    if candidate.exists():
        return candidate
    # Fallback: search recursively (covers non-standard layouts)
    hits = list(l3_path.rglob("filtered_feature_bc_matrix_agg.h5ad"))
    if not hits:
        raise FileNotFoundError(f"No filtered_feature_bc_matrix_agg.h5ad under {l3_path}")
    return sorted(hits)[0]


def convert(sample_id: str, l3_path: Path, out_dir: Path) -> dict:
    """
    Convert one Visium HD sample to L2 Parquet.
    Returns metadata dict for analysis_history.
    """
    h5ad_path = find_h5ad(l3_path)
    print(f"[{sample_id}] Reading: {h5ad_path}")
    print(f"[{sample_id}] Output:  {out_dir}")

    out_dir.mkdir(parents=True, exist_ok=True)
    expr_dir = out_dir / "expression"
    expr_dir.mkdir(exist_ok=True)

    adata = ad.read_h5ad(str(h5ad_path), backed="r")
    n_obs, n_var = adata.shape
    print(f"[{sample_id}] Shape: {n_obs:,} bins × {n_var:,} genes")

    # ── 1. obs metadata ─────────────────────────────────────────────────────
    obs_df = adata.obs.copy().reset_index(names="barcode")
    if "spatial" in adata.obsm:
        obs_df["spatial_x"] = adata.obsm["spatial"][:, 0]
        obs_df["spatial_y"] = adata.obsm["spatial"][:, 1]
    obs_path = out_dir / "obs_metadata.parquet"
    obs_df.to_parquet(obs_path, index=False, compression="zstd")
    print(f"[{sample_id}] obs_metadata → {obs_path.stat().st_size / 1024:.0f} KB")

    # ── 2. var metadata ─────────────────────────────────────────────────────
    var_df = adata.var.copy().reset_index(names="gene_name")
    var_path = out_dir / "var_metadata.parquet"
    var_df.to_parquet(var_path, index=False, compression="zstd")
    print(
        f"[{sample_id}] var_metadata → {var_path.stat().st_size / 1024:.0f} KB  ({n_var:,} genes)"
    )

    # ── 3. expression matrix (long format, nonzero only, chunked) ───────────
    barcodes = adata.obs_names.tolist()
    genes = adata.var_names.tolist()
    t0 = time.time()
    total_nonzero = 0
    part = 0

    for start in range(0, n_obs, CHUNK_SIZE):
        end = min(start + CHUNK_SIZE, n_obs)
        chunk = adata.X[start:end, :].toarray()  # (chunk, n_var) dense

        rows, cols = np.nonzero(chunk)
        if len(rows) == 0:
            continue

        df = pd.DataFrame(
            {
                "barcode": pd.array([barcodes[start + r] for r in rows], dtype="string"),
                "gene_name": pd.array([genes[c] for c in cols], dtype="string"),
                "count": chunk[rows, cols].astype(np.float32),
            }
        )
        part_path = expr_dir / f"part-{part:04d}.parquet"
        df.to_parquet(part_path, index=False, compression="zstd")
        total_nonzero += len(rows)
        part += 1

        pct = 100 * end // n_obs
        elapsed = time.time() - t0
        print(
            f"\r  [{pct:3d}%] {end:,}/{n_obs:,} barcodes  {total_nonzero:,} nonzero  {elapsed:.0f}s",
            end="",
            flush=True,
        )

    print()  # newline after progress
    adata.file.close()

    total_size_mb = sum(f.stat().st_size for f in expr_dir.glob("*.parquet")) / 1024**2
    print(
        f"[{sample_id}] expression → {part} parts, {total_nonzero:,} nonzero entries, {total_size_mb:.1f} MB"
    )

    return {
        "n_obs": n_obs,
        "n_var": n_var,
        "n_nonzero": total_nonzero,
        "n_parts": part,
        "out_dir": str(out_dir),
    }


def _clean_resource_forks(directory: Path) -> int:
    """Remove macOS ._* AppleDouble files that confuse DuckDB glob."""
    removed = 0
    for f in directory.rglob("._*"):
        f.unlink()
        removed += 1
    return removed


def verify_with_duckdb(out_dir: Path, gene: str = "PTPRC") -> None:
    """Quick sanity check: DuckDB can join obs + expression and query a gene."""
    # Clean resource forks before glob (ExFAT creates ._* files)
    n_removed = _clean_resource_forks(out_dir)
    if n_removed:
        print(f"[DuckDB verify] cleaned {n_removed} macOS resource fork(s)")

    con = duckdb.connect()
    obs = str(out_dir / "obs_metadata.parquet")
    # Build explicit file list to avoid any stray files
    expr_files = sorted(f for f in (out_dir / "expression").glob("part-*.parquet"))
    expr_list = ", ".join(f"'{f}'" for f in expr_files)

    result = con.execute(
        f"""
        SELECT COUNT(*) AS n_bins, ROUND(AVG(e.count), 2) AS mean_count
        FROM read_parquet('{obs}') o
        JOIN read_parquet([{expr_list}]) e USING (barcode)
        WHERE e.gene_name = ?
        """,
        [gene],
    ).fetchone()
    print(f"[DuckDB verify] gene={gene!r}: {result[0]:,} bins with expression, mean={result[1]}")
    con.close()


def mark_l2_ready(sample_id: str, metadata: dict) -> None:
    """Update sample_registry and insert analysis_history row."""
    con = duckdb.connect(str(DUCKDB_PATH))
    con.execute(
        "UPDATE sample_registry SET l2_ready = TRUE, last_updated = now() WHERE sample_id = ?",
        [sample_id],
    )
    con.execute(
        """
        INSERT INTO analysis_history
            (sample_id, analysis_type, parameters, status, result_path,
             requested_by, started_at, completed_at, summary)
        VALUES (?, 'l2_convert', ?::JSON, 'completed', ?, 'script_02', now(), now(), ?)
        """,
        [
            sample_id,
            json.dumps(metadata),
            metadata["out_dir"],
            f"L2 Parquet: {metadata['n_obs']:,}bins x {metadata['n_var']:,}genes",
        ],
    )
    con.close()
    print(f"[{sample_id}] sample_registry.l2_ready = TRUE, analysis_history updated")


def main():
    parser = argparse.ArgumentParser(description="L3 Visium HD → L2 Parquet")
    parser.add_argument("--sample-id", default="crc_official_v4", help="sample_registry key")
    parser.add_argument(
        "--l3-path",
        default=str(L3_ROOT / "official_v4"),
        help="Path to sample outs directory (L3)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Skip actual conversion")
    parser.add_argument("--verify-gene", default="PTPRC", help="Gene to verify after conversion")
    args = parser.parse_args()

    l3_path = Path(args.l3_path)
    out_dir = L2_ROOT / args.sample_id

    if args.dry_run:
        h5ad = find_h5ad(l3_path)
        print(f"[dry-run] h5ad found: {h5ad}")
        print(f"[dry-run] would output to: {out_dir}")
        return

    metadata = convert(args.sample_id, l3_path, out_dir)
    verify_with_duckdb(out_dir, gene=args.verify_gene)
    mark_l2_ready(args.sample_id, metadata)
    print("\nPhase 2A complete.")


if __name__ == "__main__":
    main()
