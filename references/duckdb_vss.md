> **Source:** DuckDB VSS Extension — Official documentation and GitHub
> https://github.com/duckdb/duckdb_vss
> https://duckdb.org/docs/extensions/vss

---

# DuckDB VSS Extension — Vector Similarity Search

**Maintainer:** DuckDB Labs
**License:** MIT
**Status:** Official DuckDB extension (installable via `INSTALL vss`)

---

## Overview

The DuckDB VSS extension adds **Approximate Nearest Neighbor (ANN) search** capabilities to DuckDB using the **HNSW (Hierarchical Navigable Small World)** algorithm. It enables vector similarity search directly on DuckDB tables without an external vector database.

This is the core engine for L1 Gold semantic cache in the Hermes Bio-Memory plan.

---

## Installation & Setup

```sql
INSTALL vss;
LOAD vss;
```

Or in Python:
```python
import duckdb
con = duckdb.connect("hermes_cache.duckdb")
con.execute("INSTALL vss; LOAD vss;")
```

---

## Creating a Vector Column and HNSW Index

```sql
-- Create table with embedding column
CREATE TABLE memory_recent (
    id        UUID DEFAULT gen_random_uuid(),
    text      VARCHAR,
    embedding FLOAT[1536]    -- dimension must be fixed at create time
);

-- Build HNSW index
CREATE INDEX embedding_idx
ON memory_recent
USING HNSW (embedding)
WITH (metric = 'cosine');   -- options: 'cosine', 'l2sq', 'ip' (inner product)
```

Supported metrics:
| Metric | Use case |
|--------|----------|
| `cosine` | Normalized embeddings (recommended for text) |
| `l2sq` | Euclidean distance squared |
| `ip` | Inner product (for dot-product similarity) |

---

## Querying: Similarity Search

```sql
-- Find top-5 most similar to a query embedding
SELECT id, text,
       array_cosine_similarity(embedding, ?::FLOAT[1536]) AS score
FROM memory_recent
ORDER BY score DESC
LIMIT 5;
```

Or using the `<=>` operator (cosine distance, lower = more similar):
```sql
SELECT id, text,
       embedding <=> ?::FLOAT[1536] AS distance
FROM memory_recent
ORDER BY distance ASC
LIMIT 5;
```

Filter by threshold (Hermes plan uses cosine similarity >= 0.88):
```sql
SELECT id, text,
       array_cosine_similarity(embedding, ?::FLOAT[1536]) AS score
FROM memory_recent
WHERE array_cosine_similarity(embedding, ?::FLOAT[1536]) >= 0.88
ORDER BY score DESC
LIMIT 10;
```

---

## HNSW Algorithm Notes

HNSW (Malkov & Yashunin, 2018) builds a multi-layer graph where each node connects to its nearest neighbors. Query traversal starts at the top (sparse) layer and descends to the bottom (dense) layer.

Key parameters (set at index creation):
```sql
CREATE INDEX idx ON table USING HNSW (embedding)
WITH (
    metric      = 'cosine',
    ef_construction = 128,   -- higher = better quality index, slower build
    M           = 16         -- connections per node; higher = better recall, more memory
);
```

**Recall vs. speed tradeoff:**
- `ef_construction=128, M=16` → balanced (default)
- `ef_construction=256, M=32` → high recall, slower insert

---

## Persistence

The HNSW index is **persisted on disk** when using a file-based DuckDB connection:
```python
con = duckdb.connect("hermes_cache.duckdb")  # index saved here
```

**Important:** VSS indexes are NOT persisted in WAL mode if the connection closes uncleanly. Always use `con.close()` explicitly.

---

## Relevance to Hermes Bio-Memory

| Hermes Component | DuckDB VSS Role |
|-----------------|-----------------|
| L1 Gold: memory_recent | HNSW index on `embedding FLOAT[1536]` with cosine metric |
| L1 Gold: memory_midterm | Same, separate table |
| L1 Gold: memory_longterm | Same, separate table |
| Query routing | `array_cosine_similarity() >= 0.88` threshold for cache hit |

---

## References

- Malkov, Y. A., & Yashunin, D. A. (2018). Efficient and robust approximate nearest neighbor search using hierarchical navigable small world graphs. *IEEE TPAMI*, 42(4), 824–836.
- DuckDB VSS GitHub: https://github.com/duckdb/duckdb_vss
- DuckDB VSS Docs: https://duckdb.org/docs/extensions/vss
