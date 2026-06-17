-- Evo_PRISM PostgreSQL Schema
-- Run this once against a fresh evo_prism database:
--   psql postgresql://er_rw:password@localhost:5432/evo_prism -f scripts/pg_schema.sql
--
-- Requirements:
--   - Postgres >= 13 (gen_random_uuid() built-in via pgcrypto or pg13+)
--   - pgvector extension (for future L1 cache migration)
--
-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid() on PG < 13

-- ---------------------------------------------------------------------------
-- schema_migrations  (version tracking)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    description TEXT
);

-- ---------------------------------------------------------------------------
-- sample_registry
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sample_registry (
    sample_id      TEXT PRIMARY KEY,
    project        TEXT,
    data_type      TEXT,      -- visium_hd | visium | scrna | bulk_rnaseq | ...
    platform       TEXT,      -- 10x_visium_hd | cellranger | kallisto | ...
    species        TEXT,      -- mouse | human | rat
    tissue         TEXT,
    l3_path        TEXT,
    l2_ready       BOOLEAN NOT NULL DEFAULT FALSE,
    analysis_done  BOOLEAN NOT NULL DEFAULT FALSE,
    added_by       TEXT,
    notes          TEXT,
    last_updated   TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- v2 metadata
    condition      TEXT,      -- control | tumor | treated | ...
    time_point     TEXT,      -- 0h | 24h | day3 | ...
    batch          TEXT,      -- batch_1 | batch_2 | ...
    donor_id       TEXT,
    tags           TEXT[],
    alias          TEXT       -- legacy sample_id after rename
);

-- ---------------------------------------------------------------------------
-- tools  (versioned tool registry)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tools (
    tool_id        UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    tool_name      TEXT        NOT NULL,
    version        TEXT        NOT NULL,
    content_hash   VARCHAR(16) NOT NULL,
    module_path    TEXT        NOT NULL,
    function_name  TEXT        NOT NULL,
    description    TEXT,
    parameters     JSONB,
    status         TEXT        NOT NULL DEFAULT 'active',  -- active | deprecated | candidate
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    deprecated_at  TIMESTAMPTZ,
    env_hash       VARCHAR(16),
    UNIQUE (tool_name, content_hash)
);

-- ---------------------------------------------------------------------------
-- tool_dependencies  (directed dep graph — soft FK, no REFERENCES)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tool_dependencies (
    tool_id     UUID NOT NULL,
    depends_on  UUID NOT NULL,
    PRIMARY KEY (tool_id, depends_on)
);

-- ---------------------------------------------------------------------------
-- tool_change_log  (HELIX promotion / demotion audit trail)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tool_change_log (
    log_id         UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    tool_name      TEXT        NOT NULL,
    from_status    TEXT,
    to_status      TEXT        NOT NULL,
    reason         TEXT,
    changed_by     TEXT        NOT NULL DEFAULT 'helix',
    changed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- tool_stabilization_log  (HELIX stabilization iteration records)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tool_stabilization_log (
    log_id             UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    tool_name          TEXT        NOT NULL,
    trigger_reason     TEXT,
    complexity_before  REAL,
    complexity_after   REAL,
    outcome            TEXT,       -- stabilized | ongoing | reverted
    action_taken       TEXT,
    opened_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at          TIMESTAMPTZ
);

-- ---------------------------------------------------------------------------
-- analysis_history
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS analysis_history (
    analysis_id       UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    sample_id         TEXT        REFERENCES sample_registry(sample_id),
    analysis_type     TEXT,       -- qc | spatial_gene | clustering | diff_expr | ...
    parameters        JSONB,
    status            TEXT,       -- running | completed | failed | stale
    result_path       TEXT,
    l1_cache_id       UUID,
    requested_by      TEXT,
    started_at        TIMESTAMPTZ,
    completed_at      TIMESTAMPTZ,
    summary           TEXT,       -- ≤50 chars, token-efficient search
    tool_id           UUID,       -- soft FK to tools(tool_id)
    -- v2 metadata
    analysis_version  TEXT,
    tool_version      TEXT,
    tags              TEXT[],
    user_approval     INTEGER,    -- NULL=unrated | 0=negative | 1=confirmed
    failure_diagnosis TEXT,       -- JSON: {type, detail, diagnosed_at}
    parent_analysis_id UUID,      -- lineage: points to superseded run
    parameter_hash    VARCHAR(16),
    summary_metrics   JSONB
);

CREATE INDEX IF NOT EXISTS idx_ah_sample_type
    ON analysis_history (sample_id, analysis_type);
CREATE INDEX IF NOT EXISTS idx_ah_status_started
    ON analysis_history (status, started_at);
CREATE INDEX IF NOT EXISTS idx_ah_completed
    ON analysis_history (completed_at DESC NULLS LAST);

-- ---------------------------------------------------------------------------
-- analysis_artifacts
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS analysis_artifacts (
    artifact_id      UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    analysis_id      UUID        NOT NULL REFERENCES analysis_history(analysis_id),
    artifact_type    TEXT        NOT NULL,  -- figure | table | report | embedding | ...
    artifact_subtype TEXT,
    label            TEXT        NOT NULL,
    file_path        TEXT,
    file_size_kb     INTEGER,
    mime_type        TEXT,
    embedding        vector(1024),
    embedding_256    vector(256),
    input_data_hash  TEXT,
    code_hash        TEXT,
    env_hash         TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_artifacts_analysis
    ON analysis_artifacts (analysis_id);

-- ---------------------------------------------------------------------------
-- analysis_artifact_blobs  (inline data ≤ 500 KB)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS analysis_artifact_blobs (
    artifact_id  UUID PRIMARY KEY REFERENCES analysis_artifacts(artifact_id),
    inline_data  TEXT NOT NULL CHECK (octet_length(inline_data) <= 512000)
);

-- ---------------------------------------------------------------------------
-- artifact_relations  (provenance / lineage edges)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS artifact_relations (
    source_id    UUID NOT NULL REFERENCES analysis_artifacts(artifact_id),
    target_id    UUID NOT NULL REFERENCES analysis_artifacts(artifact_id),
    relation     TEXT NOT NULL,   -- derived_from | compared_with | ...
    PRIMARY KEY (source_id, target_id, relation)
);

-- ---------------------------------------------------------------------------
-- mcp_tool_metrics  (MCP call performance fact table)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mcp_tool_metrics (
    metric_id    UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    tool_name    TEXT        NOT NULL,
    tool_id      UUID,               -- soft FK to tools(tool_id)
    duration_ms  INTEGER     NOT NULL,
    status       TEXT        NOT NULL,  -- ok | user_error | system_error | rate_limited
    error_class  TEXT,
    requested_by TEXT        NOT NULL DEFAULT 'mcp_client',
    recorded_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_mcp_metrics_tool_time
    ON mcp_tool_metrics (tool_name, recorded_at);

-- ---------------------------------------------------------------------------
-- v_tool_perf_30d  (30-day performance view)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_tool_perf_30d AS
SELECT
    tool_name,
    COUNT(*)                                                              AS n_calls,
    ROUND(AVG(duration_ms)::numeric, 2)                                  AS avg_duration_ms,
    ROUND(percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms)::numeric, 2)
                                                                          AS p95_duration_ms,
    ROUND(SUM(CASE WHEN status != 'ok' THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2)
                                                                          AS error_rate,
    SUM(CASE WHEN status = 'rate_limited' THEN 1 ELSE 0 END)             AS n_rate_limited
FROM mcp_tool_metrics
WHERE recorded_at >= now() - INTERVAL '30 days'
GROUP BY tool_name;

-- ---------------------------------------------------------------------------
-- analysis_index  (compact browsing view)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW analysis_index AS
SELECT
    sample_id,
    analysis_type,
    COUNT(*)                                              AS run_count,
    MAX(completed_at)::DATE                               AS last_run_date,
    MIN(started_at)::DATE                                 AS first_run_date,
    STRING_AGG(DISTINCT requested_by, ', ')               AS run_by_members,
    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS success_count,
    SUM(CASE WHEN status = 'failed'    THEN 1 ELSE 0 END) AS fail_count
FROM analysis_history
GROUP BY sample_id, analysis_type
ORDER BY last_run_date DESC;

-- ---------------------------------------------------------------------------
-- engram_search_metrics  (semantic search telemetry)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS engram_search_metrics (
    metric_id    UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    query_text   TEXT        NOT NULL,
    result_count INTEGER,
    duration_ms  INTEGER,
    recorded_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
