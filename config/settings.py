"""
Centralized configuration for Hermes Bio-Memory.
All file paths and constants are defined here — no hardcoding in scripts.
"""
from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()

# ── 根目錄 ────────────────────────────────────────────────
BIO_DB_ROOT = Path(os.getenv("BIO_DB_ROOT", Path(__file__).parent.parent))

# ── 資料層路徑 ─────────────────────────────────────────────
L3_ROOT      = Path(os.getenv("L3_DATA_ROOT",  BIO_DB_ROOT / "crc_visium_data"))
L2_ROOT      = BIO_DB_ROOT / "silver"
L1_ROOT      = BIO_DB_ROOT / "gold"
RESULTS_ROOT = BIO_DB_ROOT / "results_ana"
DATA_ROOT    = BIO_DB_ROOT / "data_ana"

# ── DuckDB ─────────────────────────────────────────────────
DUCKDB_PATH    = Path(os.getenv("DUCKDB_PATH",   BIO_DB_ROOT / "bio_memory.duckdb"))
L1_CACHE_PATH  = Path(os.getenv("L1_CACHE_PATH", L1_ROOT / "hermes_cache.duckdb"))

# ── Embedding ──────────────────────────────────────────────
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "google")  # "google" | "openai" | "local"
GOOGLE_API_KEY     = os.getenv("GOOGLE_API_KEY", "")
OPENAI_API_KEY     = os.getenv("OPENAI_API_KEY", "")
EMBEDDING_MODEL    = os.getenv("EMBEDDING_MODEL", "gemini-embedding-001")
EMBEDDING_DIM      = int(os.getenv("EMBEDDING_DIM", "1536"))

# ── L1 快取參數 ────────────────────────────────────────────
L1_COSINE_THRESHOLD = 0.88
L1_TTL_DAYS         = 7
L1_SUMMARY_MAX_CHARS = 50

# ── Visium HD 解析度 ────────────────────────────────────────
VISIUM_RESOLUTIONS = ["002um", "008um", "016um"]
DEFAULT_RESOLUTION = "008um"

# ── Telegram（Phase 0）─────────────────────────────────────
TELEGRAM_BOT_TOKEN       = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ALLOWED_USER_IDS = [
    int(uid) for uid in os.getenv("TELEGRAM_ALLOWED_USER_IDS", "").split(",") if uid
]

# ── 開發設定 ───────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
DRY_RUN   = os.getenv("DRY_RUN", "false").lower() == "true"
