> **Source PDF:** `references/pdfs/duckdb_sigmod2019.pdf`
> Full paper extracted from PDF. All 4 sections, Table 1, and 14 references included.

---

# DuckDB: an Embeddable Analytical Database

**Mark Raasveldt** — m.raasveldt@cwi.nl — CWI, Amsterdam
**Hannes Mühleisen** — hannes@cwi.nl — CWI, Amsterdam

*SIGMOD '19, June 30–July 5, 2019, Amsterdam, Netherlands*
https://doi.org/10.1145/3299869.3320212

---

## Abstract

The great popularity of SQLite shows that there is a need for unobtrusive in-process data management solutions. However, there is no such system yet geared towards analytical workloads. We demonstrate DuckDB, a novel data management system designed to execute analytical SQL queries while embedded in another process. In our demonstration, we pit DuckDB against other data management solutions to showcase its performance in the embedded analytics scenario. DuckDB is available as Open Source software under a permissive license.

---

## 1 Introduction

Data management systems have evolved into large monolithic database servers running as stand-alone processes. While powerful, stand-alone systems require considerable effort to set up properly and data access is constricted by their client protocols. There exists a completely separate use case: databases *embedded* into other processes as a linked library running within a "host" process. The most well-known representative is SQLite — the most widely deployed SQL database engine with more than a trillion databases in active use — but SQLite strongly focuses on OLTP workloads and its performance on analytical (OLAP) workloads is very poor.

**Figure 1: Systems Landscape** — A 2×2 matrix (Embedded/Stand-Alone vs. OLTP/OLAP). SQLite occupies Embedded/OLTP; PostgreSQL, IBM DB2, Teradata occupy Stand-Alone OLTP/OLAP; the Embedded/OLAP quadrant is marked "?" — the gap DuckDB fills.

Requirements for embedded analytical databases identified from the authors' prior work on MonetDBLite:

- **High efficiency for OLAP workloads** without completely sacrificing OLTP performance
- **High degree of stability** — a crash takes the host process down with it; queries must abort cleanly on resource exhaustion
- **Efficient data transfer** — same address space enables zero-copy sharing between DB and application
- **Practical embeddability and portability** — no external library dependencies at compile- or runtime; no signal handling, `exit()` calls, or global state modification

DuckDB is a new purpose-built embeddable RDBMS available under the MIT license: https://github.com/cwida/duckdb

---

## 2 Design and Implementation

DuckDB follows the textbook component separation: Parser → Logical Planner → Optimizer → Physical Planner → Execution Engine, with orthogonal Transaction and Storage Managers.

**Table 1: DuckDB Component Overview**

| Component | Implementation | Reference |
|-----------|---------------|-----------|
| API | C/C++ / SQLite compatibility layer | |
| SQL Parser | libpg_query (Postgres-derived) | [2] |
| Optimizer | Cost-Based (dynamic programming) | [7, 9] |
| Execution Engine | Vectorized ("Vector Volcano") | [1] |
| Concurrency Control | Serializable MVCC (HyPer variant) | [10] |
| Storage | DataBlocks (columnar, compressed) | [5] |

**Parser:** Derived from Postgres' libpg_query. Parse tree immediately transformed into DuckDB-native C++ structures.

**Logical Planner:** Binder resolves schema objects (tables, columns, types); plan generator produces logical operators (scan, filter, project, join). Statistics on stored data are propagated through expression trees and used for overflow prevention and optimization.

**Optimizer:** Join order optimization via dynamic programming [7] with greedy fallback for complex graphs [11]. Arbitrary subquery flattening [9]. Rewrite rules for common subexpression elimination and constant folding. Cardinality estimation via samples + HyperLogLog.

**Execution Engine:** Vectorized interpreted engine [1] — chosen over JIT (e.g. LLVM) for portability. Fixed-size vectors of 1024 values. Fixed-length types as native arrays; strings as pointer arrays into a string heap. NULLs use a separate bit vector (only allocated when NULLs exist). Selection vectors avoid data shifting during filtering. Execution uses the "Vector Volcano" pull model — chunks flow from scan operators up to the root.

**Concurrency Control:** HyPer's serializable MVCC [10] — updates in-place with previous states in a separate undo buffer. Supports concurrent OLTP modifications alongside OLAP queries.

**Storage:** Read-optimized DataBlocks layout. Tables horizontally partitioned into column chunks, compressed into physical blocks using lightweight compression. Blocks carry min/max indexes and lightweight per-column indexes for predicate pushdown.

---

## 3 Demonstration Scenario

Four benchmark computers run SQLite, MonetDBLite, HyPer, and DuckDB in parallel on TPC-H queries. A physical dial controls dataset size; a shared screen shows live metrics (QpS, memory pressure). As dataset size increases, SQLite suffers from its row-based model, MonetDBLite from excessive intermediate materialization, HyPer from its socket client protocol overhead — while DuckDB continues functioning.

Two scenarios: (1) pre-configured query with audience-controlled dial; (2) audience-proposed query to prevent cherry-picking.

---

## 4 Current State and Next Steps

Runs all TPC-H queries; all but two TPC-DS queries at time of writing. Immediate roadmap: complete DataBlocks storage, subquery folding, buffer manager, intra-query parallelism with work-stealing scheduler. Future: self-checking via checksums on persistent and intermediate data (viable in vectorized engines since chunks fit in CPU cache).

---

## References

[1] Boncz, Zukowski, Nes. 2005. MonetDB/X100: Hyper-Pipelining Query Execution. *CIDR 2005*.
[2] Fittl. 2019. libpg_query. https://github.com/fittl/libpg_query
[5] Lang et al. 2016. Data Blocks: Hybrid OLTP and OLAP on Compressed Storage. *SIGMOD 2016*. https://doi.org/10.1145/2882903.2882925
[7] Moerkotte & Neumann. 2008. Dynamic programming strikes back. *SIGMOD 2008*. https://doi.org/10.1145/1376616.1376672
[9] Neumann & Kemper. 2015. Unnesting Arbitrary Queries. *BTW 2015*.
[10] Neumann, Mühlbauer, Kemper. 2015. Fast Serializable Multi-Version Concurrency Control. *SIGMOD 2015*. https://doi.org/10.1145/2723372.2749436
[12] Raasveldt & Mühleisen. 2017. Don't Hold My Data Hostage. *PVLDB* 10, 10. https://doi.org/10.14778/3115404.3115408
[13] Raasveldt & Mühleisen. 2018. MonetDBLite: An Embedded Analytical Database. arXiv:1805.08520
[14] Wickham et al. 2018. dplyr: A Grammar of Data Manipulation. https://CRAN.R-project.org/package=dplyr
