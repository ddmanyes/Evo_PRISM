> **Source:** anndata and scanpy — official documentation
> anndata: https://anndata.readthedocs.io/
> scanpy: https://scanpy.readthedocs.io/
> Paper: Virshup et al. 2021 (anndata), Wolf et al. 2018 (scanpy)

---

# anndata & scanpy — Bioinformatics Data Structures

---

## anndata

**Paper:** Virshup et al. (2021). *The scverse project provides a computational ecosystem for single-cell omics data analysis.* Nature Biotechnology.
**Install:** `pip install anndata`

### AnnData Object Structure

```
AnnData object
    obs:    DataFrame (n_cells × n_cell_metadata)   ← barcode-level metadata
    var:    DataFrame (n_genes × n_gene_metadata)   ← gene-level metadata
    X:      sparse matrix (n_cells × n_genes)       ← count matrix (primary)
    obsm:   dict of arrays                          ← cell embeddings (UMAP, PCA, spatial)
    obsp:   dict of sparse matrices                 ← connectivities, distances
    uns:    dict                                    ← unstructured metadata
    layers: dict of matrices                        ← alternative count matrices
```

### Reading Visium HD Data

```python
import anndata as ad
import scipy.io as sio
import pandas as pd
import numpy as np

# Option 1: Read directly from 10x output directory
import scanpy as sc
adata = sc.read_visium(
    path="I:/Bioinfo_Projects/.../MQ250428-D1-D2/outs/",
    count_file="filtered_feature_bc_matrix.h5"
)

# Option 2: Read MTX format (for large datasets)
from scipy.io import mmread
import gzip

barcodes = pd.read_csv("barcodes.tsv.gz", header=None, names=["barcode"])
features = pd.read_csv("features.tsv.gz", header=None,
                       names=["gene_id", "gene_name", "feature_type"], sep="\t")
matrix = mmread("matrix.mtx.gz").T.tocsr()  # cells × genes

adata = ad.AnnData(
    X=matrix,
    obs=barcodes.set_index("barcode"),
    var=features.set_index("gene_id")
)

# Option 3: Read binned_outputs .h5 directly (Visium HD specific)
adata_8um = sc.read_10x_h5(
    "I:/Bioinfo_Projects/.../binned_outputs/square_008um/filtered_feature_bc_matrix.h5"
)
```

### Reading Spatial Coordinates

```python
import json

# tissue_positions.parquet (newer SpaceRanger versions)
positions = pd.read_parquet(
    "I:/Bioinfo_Projects/.../outs/spatial/tissue_positions.parquet"
)
# columns: barcode, in_tissue, array_row, array_col, pxl_row_in_fullres, pxl_col_in_fullres

# For µm coordinates (Visium HD):
scalefactors = json.load(open("scalefactors_json.json"))
# pxl → µm: divide by scalefactors["microns_per_pixel"]
```

### Extracting to Parquet (for L2 Silver)

```python
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# Get gene expression as long-format DataFrame
genes_of_interest = adata.var_names  # or subset
expr_matrix = adata.X.toarray()      # (n_cells, n_genes) — careful with memory

df = pd.DataFrame(
    expr_matrix,
    index=adata.obs_names,
    columns=adata.var["gene_name"]
).reset_index()
df_long = df.melt(id_vars=["barcode"], var_name="gene_name", value_name="count")
df_long = df_long[df_long["count"] > 0]  # remove zeros (sparse)

# Join with spatial coords
df_long = df_long.merge(positions[["barcode", "x_um", "y_um"]], on="barcode")

pq.write_table(pa.Table.from_pandas(df_long), "silver/spatial_counts_sample.parquet")
```

---

## scanpy

**Paper:** Wolf et al. (2018). *SCANPY: large-scale single-cell gene expression data analysis.* Genome Biology.
**Install:** `pip install scanpy`

### Key Functions for Visium HD Analysis

```python
import scanpy as sc

# Quality control
sc.pp.filter_cells(adata, min_genes=50)
sc.pp.filter_genes(adata, min_cells=3)
adata.obs["n_counts"] = adata.X.sum(axis=1)

# Normalization
sc.pp.normalize_total(adata, target_sum=1e4)
sc.pp.log1p(adata)

# Dimensionality reduction
sc.pp.highly_variable_genes(adata, n_top_genes=2000)
sc.tl.pca(adata)
sc.pp.neighbors(adata)
sc.tl.umap(adata)

# Clustering
sc.tl.leiden(adata, resolution=0.5)

# Spatial visualization
sc.pl.spatial(adata, color="leiden", img_key="hires")

# Gene expression on tissue
sc.pl.spatial(adata, color="PTPRC", img_key="hires")  # CD45
```

### Saving / Loading h5ad

```python
adata.write_h5ad("processed_sample.h5ad")     # save
adata = sc.read_h5ad("processed_sample.h5ad") # load
```

---

## Relevance to Hermes Bio-Memory

| Plan Phase | anndata/scanpy Role |
|-----------|---------------------|
| Phase 2-A script | Read Visium HD MTX + spatial coords via anndata |
| Phase 2-A schema | `adata.obs` → spatial_meta.parquet; `adata.X` → spatial_counts.parquet |
| Phase 3 analysis | scanpy clustering (leiden), spatial plots |
| L3 Bronze source | .h5ad files already present in `app備份` and analysis directories |

---

## Visium HD Specific Notes

- `filtered_feature_bc_matrix/` → 2 µm bins (full resolution, ~2M bins, very large)
- `binned_outputs/square_008um/` → 8 µm bins (aggregated, ~140K bins, recommended for analysis)
- `binned_outputs/square_016um/` → 16 µm bins (~36K bins, for overview)
- Spatial coordinates: `tissue_positions.parquet` (pixel coords), divide by `microns_per_pixel` for µm

For Phase 2-A, use **8 µm bins** as primary L2 feature store target.

---

## References

- Virshup et al. 2021. The scverse project. *Nature Biotechnology*. https://doi.org/10.1038/s41587-023-01733-8
- Wolf et al. 2018. SCANPY. *Genome Biology* 19, 15. https://doi.org/10.1186/s13059-017-1382-0
- anndata docs: https://anndata.readthedocs.io/
- scanpy docs: https://scanpy.readthedocs.io/
