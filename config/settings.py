"""
Centralized configuration for BioAgent.
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
EMBEDDING_PROVIDER   = os.getenv("EMBEDDING_PROVIDER", "llamacpp")  # "llamacpp" | "google" | "openai"
EMBEDDING_MODEL      = os.getenv("EMBEDDING_MODEL", "bge-m3-Q8_0")
EMBEDDING_DIM        = int(os.getenv("EMBEDDING_DIM", "1024"))
LLAMACPP_BASE_URL    = os.getenv("LLAMACPP_BASE_URL", "http://localhost:8081/v1")
LLAMACPP_MODEL_PATH  = os.path.expanduser("~/llama.cpp/models/bge-m3-Q8_0.gguf")

# Matryoshka dual-layer index (9D): coarse index uses first MATRYOSHKA_DIM dims
# bge-m3 supports Matryoshka — truncating to 256 dims retains ~95% recall
MATRYOSHKA_DIM       = int(os.getenv("MATRYOSHKA_DIM", "256"))
MATRYOSHKA_ENABLED   = os.getenv("MATRYOSHKA_ENABLED", "false").lower() == "true"
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ── 推理後端 ───────────────────────────────────────────────
# "local"  → llama.cpp port 8080（本機 Gemma 4，免費）
# "claude" → Anthropic Claude API（需 ANTHROPIC_API_KEY）
# "google" → Google Gemini API（需 GOOGLE_API_KEY）
INFERENCE_BACKEND = os.getenv("INFERENCE_BACKEND", "local")
CLAUDE_MODEL      = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
GOOGLE_API_KEY    = os.getenv("GOOGLE_API_KEY", "")
GOOGLE_MODEL      = os.getenv("GOOGLE_MODEL", "gemini-2.0-flash")
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY", "")

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

# ── HELIX 設定 ─────────────────────────────────────────────
# 工具 revision_count 達到此值即列入熱區（hot zone）
HELIX_HOT_THRESHOLD         = int(os.getenv("HELIX_HOT_THRESHOLD", "3"))
# 穩定化迭代超過此天數未關閉則視為失效（auto_revert 觸發閾值）
HELIX_STALE_ITERATION_DAYS  = int(os.getenv("HELIX_STALE_ITERATION_DAYS", "30"))
# diagnosis_img 遺忘曲線：超過此天數後 downsample to 0.5x
HELIX_SNAPSHOT_DECAY_DAYS_1 = int(os.getenv("HELIX_SNAPSHOT_DECAY_DAYS_1", "180"))
# 超過此天數後 downsample to 0.25x
HELIX_SNAPSHOT_DECAY_DAYS_2 = int(os.getenv("HELIX_SNAPSHOT_DECAY_DAYS_2", "365"))

# ── Artifact 路徑工具 ──────────────────────────────────────
def resolve_artifact_path(rel_path: str) -> Path:
    """Convert a BIO_DB_ROOT-relative artifact path to an absolute Path.

    analysis_artifacts.file_path stores relative paths since migration v12.
    Call this whenever reading file_path from the DB.

    Example:
        resolve_artifact_path("results/s1/pca.png")
        → Path("/Volumes/NO NAME/bio_DB/results/s1/pca.png")
    """
    p = Path(rel_path)
    if p.is_absolute():
        return p
    return BIO_DB_ROOT / p


# ── 開發設定 ───────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
DRY_RUN   = os.getenv("DRY_RUN", "false").lower() == "true"
