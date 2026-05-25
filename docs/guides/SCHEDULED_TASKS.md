# Evo_PRISM — Scheduled Tasks

All background schedulers run automatically via macOS **launchd** (plist templates in `docs/launchd/`) or any cron-compatible scheduler on Linux.

## Task Table

| Script | Schedule | Function |
|:---|:---|:---|
| `scheduler/backup_db.py` | Daily 02:00 | `EXPORT DATABASE` → `~/bio_db_backups/` (7-day retention) |
| `scheduler/cleanup_l1_cache.py` | Daily 03:30 | Purge L1 semantic cache entries past their TTL |
| `scheduler/cleanup_figure_cache.py` | Daily 03:35 | Purge expired figure cache files (TTL 14 days) |
| `scheduler/rebuild_hnsw.py` | Sundays 03:00 | Rebuild HNSW index + ENGRAM BM25 FTS index |
| `scheduler/scan_new_samples.py` | Every 30 min | Scan and auto-register new Kallisto output samples |
| `scheduler/helix_expire_snapshots.py` | Sundays 04:00 | HELIX visual snapshot Ebbinghaus forgetting-curve downsampling |

## HELIX Snapshot Downsampling Schedule

`helix_expire_snapshots.py` progressively reduces the resolution of old diagnostic snapshots to simulate biological memory decay while minimising storage cost:

```text
Days 0–180 after iteration closed :  640×640  ~100 VLM tokens  full resolution
Days 180–365 after iteration closed:  320×320   ~25 VLM tokens  50% downsampled
Days > 365 after iteration closed  :  160×160    ~6 VLM tokens  25% downsampled
```

Thresholds are controlled by `settings.HELIX_SNAPSHOT_DECAY_DAYS_1` and `HELIX_SNAPSHOT_DECAY_DAYS_2`.

## Installing on macOS (launchd)

```bash
# Example: enable the daily backup scheduler
cp docs/launchd/launchd_backup.plist.example \
   ~/Library/LaunchAgents/com.hermes.backup.plist
launchctl load ~/Library/LaunchAgents/com.hermes.backup.plist
```

See `docs/launchd/` for plist templates for all six tasks.

## Running Manually

Each script can be invoked directly at any time:

```bash
.venv/bin/python scheduler/backup_db.py
.venv/bin/python scheduler/helix_expire_snapshots.py
```
