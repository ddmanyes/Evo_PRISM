"""分析輸出路徑工具：統一「按樣本分子目錄」慣例。

集中先前 spatial_eda / report_generator 各自的 `_results_dir`，並讓 bulk_eda /
mcseg_quality 從扁平 REPORTS_DIR 改為同一慣例：

    results/<sample_id>/<analysis_type>/

好處：多樣本輸出不混雜、易清理；含路徑遍歷防護（resolve 後須落在 BIO_DB_ROOT 內）。
"""

from __future__ import annotations

import re
import sys
import time
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


# results/ 是 Docker bind mount（host 端在 exFAT 外接碟上）。實測 2026-09-10：剛寫完的
# 檔案 stat() 立刻看得到、open() 卻要等約 0.1–0.5 秒才成功，中間一律 FileNotFoundError
# （fsync 無效，純 mount 端 dentry 快取延遲；容器本地 /tmp 沒有此現象）。
# 症狀是「圖畫好了、檔案在硬碟上，報告卻寫著『生成失敗』」——bulk_eda 的 QC/dist/PCA
# 三張圖從此靜默掉了好幾輪（見 fix note 2026-09-10）。凡是「剛寫完馬上回讀」的路徑
# 都要走這裡。
_READBACK_ATTEMPTS = 6
_READBACK_BACKOFF_SEC = 0.1


def read_back_bytes(path: "Path | str") -> bytes:
    """讀取剛寫入的檔案；遇 FileNotFoundError 短暫重試（bind mount 可見性延遲）。

    重試上限約 2.1 秒（0.1+0.2+…+0.5）。超過仍讀不到就照常拋 FileNotFoundError——
    那已經不是可見性延遲，而是檔案真的不在。
    """
    p = Path(path)
    for attempt in range(_READBACK_ATTEMPTS):
        try:
            return p.read_bytes()
        except FileNotFoundError:
            if attempt == _READBACK_ATTEMPTS - 1:
                raise
            time.sleep(_READBACK_BACKOFF_SEC * (attempt + 1))
    raise AssertionError("unreachable")
