"""分析輸出路徑工具：統一「按樣本分子目錄」慣例。

集中先前 spatial_eda / report_generator 各自的 `_results_dir`，並讓 bulk_eda /
mcseg_quality 從扁平 REPORTS_DIR 改為同一慣例：

    results/<sample_id>/<analysis_type>/

好處：多樣本輸出不混雜、易清理；含路徑遍歷防護（resolve 後須落在 BIO_DB_ROOT 內）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import BIO_DB_ROOT  # noqa: E402

_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def results_dir(sample_id: str, analysis_type: str) -> Path:
    """回傳 ``results/<sample_id>/<analysis_type>/``（自動建立，含遍歷防護）。"""
    if not _SEGMENT_RE.match(sample_id):
        raise ValueError(f"Invalid sample_id {sample_id!r}: only A-Z a-z 0-9 _ - are allowed")
    if not _SEGMENT_RE.match(analysis_type):
        raise ValueError(f"Invalid analysis_type {analysis_type!r}")
    d = (BIO_DB_ROOT / "results" / sample_id / analysis_type).resolve()
    if not d.is_relative_to(BIO_DB_ROOT.resolve()):
        raise ValueError(f"Path traversal detected for sample_id={sample_id!r}")
    d.mkdir(parents=True, exist_ok=True)
    return d
