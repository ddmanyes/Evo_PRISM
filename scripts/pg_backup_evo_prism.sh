#!/bin/bash
# pg_backup_evo_prism.sh — daily pg_dump of evo_prism Postgres metadata to Drive.
#
# evo_prism metadata (analysis_history, sample_registry, tools, artifacts, metrics)
# lives in the sb-pg Docker container; DuckDB/Parquet files on KINGSTON drive are
# NOT backed up here (they are read-only analysis artefacts, replaceable from source).
# Keeps the last KEEP dumps (default 14).
#
# Env (optional):
#   ER_PG_CONTAINER   docker container name (default: sb-pg)
#   ER_PG_DATABASES   space-separated db list (default: "evo_prism")
#   ER_BACKUP_DIR     output dir (default: <PJ_save>/backups/evo-prism-pg)
#   ER_BACKUP_KEEP    how many dumps to keep (default: 14)
set -uo pipefail

CONTAINER="${ER_PG_CONTAINER:-sb-pg}"
DATABASES="${ER_PG_DATABASES:-evo_prism}"
KEEP="${ER_BACKUP_KEEP:-14}"
DEFAULT_DIR="/Users/zhanqiru/Library/CloudStorage/GoogleDrive-u9013039@gmail.com/我的雲端硬碟/PJ_save/backups/evo-prism-pg"
BACKUP_DIR="${ER_BACKUP_DIR:-$DEFAULT_DIR}"

DOCKER="${DOCKER_BIN:-/usr/local/bin/docker}"
[ -x "$DOCKER" ] || DOCKER="$(command -v docker || echo docker)"

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"

for DB in $DATABASES; do
  if ! "$DOCKER" exec "$CONTAINER" psql -U postgres -lqt 2>/dev/null | cut -d'|' -f1 | grep -qw "$DB"; then
    echo "[er-pg-backup] skip $DB (not present)"
    continue
  fi
  OUT="$BACKUP_DIR/${DB}-${STAMP}.sql.gz"
  if "$DOCKER" exec "$CONTAINER" pg_dump -U postgres "$DB" | gzip > "$OUT"; then
    echo "[er-pg-backup] wrote $OUT ($(du -h "$OUT" | cut -f1))"
  else
    echo "[er-pg-backup] FAILED dumping $DB" >&2
    rm -f "$OUT"
    continue
  fi
  # rotation: keep newest $KEEP
  ls -1t "$BACKUP_DIR/${DB}-"*.sql.gz 2>/dev/null | tail -n +"$((KEEP + 1))" | while read -r old; do
    echo "[er-pg-backup] prune $old"
    rm -f "$old"
  done
done
