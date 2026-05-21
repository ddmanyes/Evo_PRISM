# Hermes Bio-Memory — Implementation Plan

> **Goal:** Build a three-tier hierarchical bioinformatics memory backend based on the hw4 proposal,
> using real data from I:\ as the prototype.
> Engine: DuckDB + VSS | Format: Parquet | Interface: MCP Server (Python)
>
> **Development philosophy:** Start with basic analysis code that works on real data.
> Iteratively improve and expand as analysis progresses.

---

## Directory Structure

```
I:\bio_DB\
├── plan.md                          ← this file
├── bio_memory.duckdb                ← main DuckDB entry point (views + registry)
│
├── silver\                          ← L2: processed Parquet files (output of scripts/)
│   ├── spatial_counts_*.parquet     ← 8µm bin resolution (primary)
│   ├── spatial_meta_*.parquet
│   └── bulk_counts_kallisto_v1.parquet
│
├── gold\                            ← L1: semantic cache (built later)
│   └── hermes_cache.duckdb
│
├── scripts\                         ← one-shot conversion tools (run once per dataset)
│   ├── 00_init_db.py               ← create schema, sample_registry table
│   ├── 01_spatial_to_parquet.py    ← Visium HD MTX + spatial coords → Parquet
│   └── 02_kallisto_to_parquet.py   ← abundance.tsv files → Parquet
│
├── analysis\                        ← ongoing analysis code (continuously expanded)
│   ├── spatial_eda.py              ← spatial transcriptomics exploration
│   └── bulk_eda.py                 ← bulk RNA-seq exploration
│
├── server\                          ← MCP server (Phase 5, built later)
│   └── bio_memory_server.py
│
└── references\                      ← technical references (papers + tool docs)
    ├── duckdb.md                   ← DuckDB SIGMOD 2019
    ├── duckdb_vss.md               ← DuckDB VSS extension (HNSW)
    ├── llmlingua.md                ← LLMLingua EMNLP 2023
    ├── deepseek_ocr.md             ← DeepSeek-OCR 2025
    ├── memgpt.md                   ← MemGPT 2023
    ├── lakeharbor_icde2024.md      ← LakeHarbor ICDE 2024
    ├── agent_first_data_systems.md ← Agent-First Data Systems 2025
    ├── mcp_protocol.md             ← MCP Protocol (Anthropic)
    ├── anndata_scanpy.md           ← anndata / scanpy bioinformatics tools
    ├── bertscore.md                ← BERTScore evaluation metric
    └── pdfs\                       ← original PDFs
```

**Key design rule:**
- `scripts/` = run once to ingest / convert data into `silver/`
- `analysis/` = reusable functions used repeatedly during analysis
- `server/` = MCP interface layer, added only after L1/L2 are stable

---

## Layer Mapping (Theory → Reality)

| Layer | Paper Name | Role | Location |
|-------|-----------|------|----------|
| L3 Bronze | Immutable Raw Lake | Source of truth; never modified | `I:\Bioinfo_Projects\` / `I:\BulkRNA\` (existing) |
| L2 Silver | DuckDB Feature Store | Processed count matrices (Parquet) | `I:\bio_DB\silver\` |
| L1 Gold | Multi-Res Semantic Cache | Query embeddings + compressed reports | `I:\bio_DB\gold\hermes_cache.duckdb` |
| Entry | MCP Server | Hermes / Claude tool interface | `I:\bio_DB\server\bio_memory_server.py` |

---

## Phase 1 — Environment & Schema

- [ ] Install Python dependencies: `duckdb`, `anndata`, `pandas`, `pyarrow`, `scipy`
- [ ] Create directory skeleton: `silver/`, `gold/`, `scripts/`, `analysis/`, `server/`
- [ ] Write `scripts/00_init_db.py`:
  - Initialize `bio_memory.duckdb`
  - Verify VSS extension loads (`INSTALL vss; LOAD vss;`)
  - Create `sample_registry` table
- [ ] Populate `sample_registry` with all known samples from MASTER_LIST.md

**sample_registry schema:**
```sql
CREATE TABLE sample_registry (
    sample_id    VARCHAR PRIMARY KEY,
    project      VARCHAR,      -- 'MQ250428', 'Kallisto_v1'
    data_type    VARCHAR,      -- 'visium_hd', 'bulk_rnaseq', 'scrna'
    l3_path      VARCHAR,      -- absolute path to raw data on I:\
    l2_ready     BOOLEAN DEFAULT FALSE,
    notes        VARCHAR,
    last_updated TIMESTAMP
);
```

---

## Phase 2 — L2 Silver: Feature Store

### 2-A  Spatial Transcriptomics (Visium HD)

Primary prototype: **MQ250428-D1-D2** (most complete sample)

L3 source:
```
I:\Bioinfo_Projects\01_Spatial_Transcriptomics\20251125_TGIA_VisiumHD\
  20250612_MQ250428\MQ250428-D1-D2\outs\
    binned_outputs\square_008um\  ← PRIMARY: 8µm bins (~140K bins, analysis resolution)
    binned_outputs\square_016um\  ← SECONDARY: 16µm bins (~36K bins, overview)
    spatial\                      ← tissue_positions.parquet (barcode → pixel coords)
    filtered_feature_bc_matrix\   ← 2µm full resolution (~2M bins, too large for L2)
```

> **Note on resolution:** Use **8µm bins** (`square_008um`) as the primary L2 target.
> The `filtered_feature_bc_matrix/` is 2µm (full resolution, ~2M bins per sample) — too large
> for direct L2 storage. Only load 2µm on demand from L3. See `references/anndata_scanpy.md`.

- [ ] Write `scripts/01_spatial_to_parquet.py`
- [ ] Output: `silver/spatial_counts_MQ250428-D1-D2_8um.parquet`
- [ ] Output: `silver/spatial_meta_MQ250428-D1-D2.parquet`
- [ ] Verify DuckDB can query by gene name and spatial coordinates
- [ ] After D1-D2 confirmed: repeat for MQ250422-A1-M2

**spatial_counts schema:**
```sql
sample_id VARCHAR, barcode VARCHAR, gene_name VARCHAR,
count INTEGER, x_um FLOAT, y_um FLOAT, bin_size_um INTEGER
```

**spatial_meta schema:**
```sql
sample_id VARCHAR, barcode VARCHAR,
n_genes INTEGER, n_counts INTEGER,
x_um FLOAT, y_um FLOAT, in_tissue BOOLEAN,
cluster_id INTEGER, cell_type VARCHAR
```

### 2-B  Bulk RNA-seq (Kallisto)

L3 source: `I:\BulkRNA\Kallisto_v1\results_kallisto\`

- [ ] Write `scripts/02_kallisto_to_parquet.py`
- [ ] Aggregate all `abundance.tsv` files; map transcript → gene via t2g table
- [ ] Output: `silver/bulk_counts_kallisto_v1.parquet`
- [ ] Verify DuckDB can filter by gene + condition

**bulk_counts schema:**
```sql
sample_id VARCHAR, gene_name VARCHAR,
est_counts FLOAT, tpm FLOAT,
condition VARCHAR, replicate INTEGER
```

---

## Phase 3 — Analysis Layer

Write reusable functions in `analysis/`; expand continuously during real analysis work.

### spatial_eda.py (starter functions)
- `load_sample(sample_id)` — query L2 Parquet via DuckDB, return DataFrame
- `plot_spatial(df, gene)` — scatter plot of gene expression on tissue
- `top_genes(sample_id, n)` — most highly expressed genes
- `compare_bins(sample_id, bin_sizes=[8,16])` — compare resolution tiers

### bulk_eda.py (starter functions)
- `load_bulk(condition=None)` — query bulk_counts from L2
- `diff_expr(cond_a, cond_b)` — simple fold-change table
- `plot_pca(df)` — PCA across samples

---

## Phase 4 — L1 Gold: Semantic Cache

*Build after Phase 2 and 3 are stable.*

- [ ] Create `gold/hermes_cache.duckdb` with three memory tables
- [ ] HNSW index on `embedding FLOAT[1536]` with cosine metric (see `references/duckdb_vss.md`)
- [ ] `scripts/cache_write.py` — embed + insert report into memory_recent
- [ ] `scripts/cache_query.py` — cosine similarity >= 0.88 search across all tiers
- [ ] TTL eviction: move expired recent → midterm (LLMLingua compression)
- [ ] Install LLMLingua: `pip install llmlingua` (~3 GB model download on first use)

**memory tables:**
```sql
memory_recent    (TTL 7 days,  full report text)
memory_midterm   (TTL 90 days, LLMLingua-compressed 1/20)
memory_longterm  (permanent,   DeepSeek-OCR visual summary)
```

**Pending decision:** OpenAI `text-embedding-3-small` (cloud, 1536-dim, $0.02/1M tokens)
vs. local `nomic-embed-text` (free, 768-dim, needs ~2 GB RAM).
If using local: `pip install sentence-transformers` + model download required.

> See `references/llmlingua.md` for compression details.
> See `references/duckdb_vss.md` for HNSW index setup.

---

## Phase 5 — MCP Server

*Build after L1 is stable.*

File: `server/bio_memory_server.py`
Install: `pip install mcp`
Register: add to `.claude/settings.json` under `mcpServers` (see `references/mcp_protocol.md`)

| MCP Tool | Input | Output |
|----------|-------|--------|
| `bio_memory_query` | question, sample_id | L1 hit text or L2 DuckDB result |
| `bio_memory_write` | sample_id, analysis_type, report_text | cache insert confirmation |
| `bio_register_sample` | sample_id, data_type, l3_path | registry upsert |

Fallback logic: L1 (cosine >= 0.88) → L2 DuckDB query → (warn: L3 requires manual pipeline)

> See `references/mcp_protocol.md` for server skeleton and registration steps.

---

## Phase 6 — Validation

Using MQ250428-D1-D2 as ground truth:

- [ ] 20 semantically overlapping spatial clustering queries
- [ ] Token consumption: native FTS5 vs. L1 cache hit
- [ ] Latency: L1 (<1s) / L2 (~30s) / L3 (~4h)
- [ ] BERTScore F1 between compressed and full-text answers (target >= 0.92)

Install: `pip install bert-score`
```python
from bert_score import score
P, R, F1 = score(compressed_outputs, full_outputs, lang="en")
# Target: F1.mean() >= 0.92
```

> See `references/bertscore.md` for methodology details.

---

## Implementation Order

```
Phase 1 (env + schema)
    └── Phase 2-A (spatial L2)  ─┐
    └── Phase 2-B (bulk L2)     ─┤─ Phase 3 (analysis layer, grows continuously)
                                  │
                                  └── Phase 4 (L1 cache)
                                          └── Phase 5 (MCP server)
                                                  └── Phase 6 (validation)
```

---

## Key File Paths

| Item | Path |
|------|------|
| Primary prototype | `I:\...\20250612_MQ250428\MQ250428-D1-D2\outs\` |
| Secondary prototype | `I:\...\20250612_MQ250428\MQ250428-A1-D1\outs\` |
| Bulk RNA source | `I:\BulkRNA\Kallisto_v1\results_kallisto\` |
| Main DuckDB | `I:\bio_DB\bio_memory.duckdb` |
| L1 cache DB | `I:\bio_DB\gold\hermes_cache.duckdb` |
| L2 Parquet store | `I:\bio_DB\silver\` |
| MCP server | `I:\bio_DB\server\bio_memory_server.py` |

---

## Reference Index

| Reference | Covers | Phase |
|-----------|--------|-------|
| `references/duckdb.md` | DuckDB engine design (SIGMOD 2019) | 1, 2 |
| `references/duckdb_vss.md` | HNSW vector search in DuckDB | 4 |
| `references/llmlingua.md` | Prompt compression (EMNLP 2023) | 4 |
| `references/deepseek_ocr.md` | Visual compression for long-term memory | 4 |
| `references/memgpt.md` | Hierarchical memory model (2023) | 4 |
| `references/lakeharbor_icde2024.md` | Structure-aware data lake (ICDE 2024) | 2 |
| `references/agent_first_data_systems.md` | Agent-first DB design (2025) | All |
| `references/mcp_protocol.md` | MCP server skeleton + registration | 5 |
| `references/anndata_scanpy.md` | Reading .h5ad and Visium HD data | 2 |
| `references/bertscore.md` | Evaluation metric (Zhang et al. 2020) | 6 |

---

## Open Questions

1. **Embedding model**: OpenAI `text-embedding-3-small` (1536-dim, cloud) vs. local `nomic-embed-text` (768-dim, free) — decide before Phase 4
2. **LLMLingua disk**: ~3 GB model download on first use — confirm I:\ has space before Phase 4
3. **MQ250422-A1-D1 missing files**: web_summary, metrics_summary, molecule_info.h5 absent (pre-existing SpaceRanger issue) — use D1-D2 as primary prototype
4. **NDPI registration**: Both MQ250428 samples still need manual image alignment — spatial plots will lack high-res histology overlay until complete
5. **2µm vs 8µm for L2**: L2 uses 8µm bins; 2µm only loaded on-demand from L3 (too large for persistent Parquet)
