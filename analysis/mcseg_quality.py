"""MCseg 分割品質視覺化（讀既有遮罩，不即時重跑分割）。

資料契約（沿用 scripts/msseg/seg_quality.py）：
    - 分割遮罩為**整數標籤陣列**：0 = 背景，1..N = 各細胞；``mask.max()`` = 細胞數
    - 每個 ROI 一對 ``.npy``：``{roi}_nuc.npy``（NUC 基準）與 ``{roi}_mcseg.npy``（完整流程）
    - 預設目錄 ``results/mcseg_qc/``；H&E ROI 影像（RGB uint8）可選

本模組只做**視覺化與量化**，不依賴 cellpose / GPU。即時重跑分割仍走
``scripts/msseg/seg_quality.py``（依賴 MSseg 原專案）。

主要函數：
    cell_metrics()             — 單一遮罩的細胞數 / 面積統計
    cell_size_distribution()   — 各細胞面積（像素數）陣列
    mask_overlay_plot()        — H&E（或純遮罩）上疊分割邊界
    comparison_plot()          — NUC vs MCseg 並排對比
    size_distribution_plot()   — 多遮罩細胞面積分布直方圖
    generate_mcseg_qc_report() — 掃 qc_dir 內所有 ROI，彙整報告 + 寫 analysis_history
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import matplotlib
import matplotlib.pyplot as plt

matplotlib.use("Agg")

import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import BIO_DB_ROOT, DUCKDB_PATH  # noqa: E402
from config.db_utils import safe_write  # noqa: E402
from analysis.viz_utils import file_to_b64_md as _file_to_b64_md  # noqa: E402
from analysis.path_utils import results_dir  # noqa: E402

logger = logging.getLogger(__name__)

# 輸入：既有分割遮罩目錄（讀）。輸出走 results_dir(sample_id, "mcseg_qc")（按樣本分子目錄）。
MCSEG_QC_DIR = BIO_DB_ROOT / "results" / "mcseg_qc"

_SAMPLE_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")


def _validate_sample_id(sample_id: str) -> None:
    if not _SAMPLE_ID_RE.match(sample_id):
        raise ValueError(f"無效的 sample_id：{sample_id!r}（只允許英數字、底線、連字號）")


# ── 量化 ──────────────────────────────────────────────────────────────────────


def cell_metrics(mask: np.ndarray) -> dict[str, float]:
    """單一標籤遮罩的細胞數與面積統計（像素為單位）。"""
    labels = mask[mask > 0]
    n_cells = int(mask.max())
    if labels.size == 0 or n_cells == 0:
        return {"n_cells": 0, "mean_area": 0.0, "median_area": 0.0, "foreground_frac": 0.0}
    areas = np.bincount(labels.ravel())[1:]  # 跳過背景 0
    areas = areas[areas > 0]
    return {
        "n_cells": n_cells,
        "mean_area": float(np.mean(areas)),
        "median_area": float(np.median(areas)),
        "foreground_frac": float((mask > 0).sum() / mask.size),
    }


def cell_size_distribution(mask: np.ndarray) -> np.ndarray:
    """回傳各細胞面積（像素數）的一維陣列，背景不計。"""
    labels = mask[mask > 0]
    if labels.size == 0:
        return np.array([], dtype=int)
    areas = np.bincount(labels.ravel())[1:]
    return areas[areas > 0]


def _boundaries(mask: np.ndarray) -> np.ndarray:
    """標籤相異的相鄰像素 → 邊界（純 numpy，免 skimage）。"""
    b = np.zeros(mask.shape, dtype=bool)
    diff_v = mask[:-1, :] != mask[1:, :]
    b[:-1, :] |= diff_v
    b[1:, :] |= diff_v
    diff_h = mask[:, :-1] != mask[:, 1:]
    b[:, :-1] |= diff_h
    b[:, 1:] |= diff_h
    return b


# ── 繪圖 ──────────────────────────────────────────────────────────────────────


def _save(fig, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return output_path


def mask_overlay_plot(
    mask: np.ndarray,
    output_path: Path,
    image: Optional[np.ndarray] = None,
    title: str = "segmentation",
) -> Path:
    """在 H&E ROI（或純遮罩著色）上疊分割邊界，儲存並回傳路徑。"""
    fig, ax = plt.subplots(figsize=(6, 6))
    if image is not None:
        ax.imshow(image)
    else:
        # 純遮罩：隨機標籤上色（背景黑）
        ax.imshow(np.where(mask > 0, mask % 20 + 1, 0), cmap="tab20")
    bnd = _boundaries(mask)
    overlay = np.zeros((*mask.shape, 4))
    overlay[bnd] = (1.0, 0.0, 0.0, 1.0)  # 紅色邊界
    ax.imshow(overlay)
    ax.set_title(f"{title}  (cells={int(mask.max())})", fontsize=11)
    ax.axis("off")
    return _save(fig, output_path)


def comparison_plot(
    nuc_mask: np.ndarray,
    mcseg_mask: np.ndarray,
    output_path: Path,
    image: Optional[np.ndarray] = None,
    roi_name: str = "ROI",
) -> Path:
    """NUC 基準 vs MCseg 完整流程並排對比。"""
    fig, axes = plt.subplots(1, 2, figsize=(12, 6))
    for ax, mask, label in (
        (axes[0], nuc_mask, "NUC"),
        (axes[1], mcseg_mask, "MCseg"),
    ):
        if image is not None:
            ax.imshow(image)
        else:
            ax.imshow(np.where(mask > 0, mask % 20 + 1, 0), cmap="tab20")
        bnd = _boundaries(mask)
        overlay = np.zeros((*mask.shape, 4))
        overlay[bnd] = (1.0, 0.0, 0.0, 1.0)
        ax.imshow(overlay)
        ax.set_title(f"{label}  (cells={int(mask.max())})", fontsize=11)
        ax.axis("off")
    fig.suptitle(f"{roi_name}: NUC vs MCseg", fontsize=13)
    fig.tight_layout()
    return _save(fig, output_path)


def size_distribution_plot(
    masks: dict[str, np.ndarray],
    output_path: Path,
) -> Path:
    """多遮罩細胞面積分布直方圖（重疊比較）。"""
    fig, ax = plt.subplots(figsize=(8, 5))
    for label, mask in masks.items():
        areas = cell_size_distribution(mask)
        if areas.size:
            ax.hist(areas, bins=30, alpha=0.5, label=f"{label} (n={areas.size})")
    ax.set_xlabel("cell area (px)", fontsize=11)
    ax.set_ylabel("count", fontsize=11)
    ax.set_title("Cell size distribution", fontsize=12)
    ax.legend(fontsize=9)
    ax.grid(True, linestyle="--", alpha=0.4)
    return _save(fig, output_path)


# ── ROI 探索 ──────────────────────────────────────────────────────────────────


def discover_roi_pairs(qc_dir: Path) -> list[tuple[str, Path, Path]]:
    """掃 qc_dir 找成對的 ``{roi}_nuc.npy`` / ``{roi}_mcseg.npy``。

    回傳 [(roi_name, nuc_path, mcseg_path), ...]，只收兩者皆存在的 ROI。
    """
    pairs: list[tuple[str, Path, Path]] = []
    for nuc in sorted(qc_dir.glob("*_nuc.npy")):
        roi = nuc.name[: -len("_nuc.npy")]
        mcseg = qc_dir / f"{roi}_mcseg.npy"
        if mcseg.exists():
            pairs.append((roi, nuc, mcseg))
    return pairs


# ── 報告生成 ──────────────────────────────────────────────────────────────────


def generate_mcseg_qc_report(
    sample_id: str,
    qc_dir: Optional[Path] = None,
    requested_by: str = "agent",
) -> tuple[str, str]:
    """掃 qc_dir 內所有 ROI，彙整 NUC vs MCseg 對比 + 面積分布報告。

    回傳 (analysis_id, report_path)。無 ROI 對時拋 FileNotFoundError。
    """
    _validate_sample_id(sample_id)
    qc_dir = Path(qc_dir) if qc_dir else MCSEG_QC_DIR

    # 前置驗證在寫 running 記錄之前：無數據的請求不該污染 analysis_history。
    if not qc_dir.exists():
        raise FileNotFoundError(f"MCseg QC 目錄不存在：{qc_dir}")
    pairs = discover_roi_pairs(qc_dir)
    if not pairs:
        raise FileNotFoundError(f"{qc_dir} 下找不到成對的 *_nuc.npy / *_mcseg.npy")

    analysis_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    params_json = json.dumps({"qc_dir": str(qc_dir)})

    import duckdb

    with duckdb.connect(str(DUCKDB_PATH)) as con:
        safe_write(
            con,
            """INSERT INTO analysis_history
                   (analysis_id, sample_id, analysis_type, parameters, status,
                    requested_by, started_at)
               VALUES (?, ?, 'mcseg_qc', ?, 'running', ?, ?)""",
            [analysis_id, sample_id, params_json, requested_by, started_at],
        )

    try:
        out_dir = results_dir(sample_id, "mcseg_qc")
        ts = started_at.strftime("%Y%m%d_%H%M%S")

        sections: list[str] = []
        artifacts: list[tuple[Path, str, str]] = []
        all_mcseg: dict[str, np.ndarray] = {}

        for roi, nuc_path, mcseg_path in pairs:
            nuc = np.load(nuc_path)
            mcseg = np.load(mcseg_path)
            cmp_out = out_dir / f"cmp_{sample_id}_{roi}_{ts}.png"
            comparison_plot(nuc, mcseg, cmp_out, roi_name=roi)
            artifacts.append((cmp_out, f"{roi} NUC vs MCseg 對比", "mcseg_compare"))
            all_mcseg[roi] = mcseg

            nm, mm = cell_metrics(nuc), cell_metrics(mcseg)
            sections.append(
                f"### ROI：{roi}\n\n"
                f"| 方法 | 細胞數 | 平均面積 | 中位面積 | 前景占比 |\n"
                f"|------|-------:|--------:|--------:|--------:|\n"
                f"| NUC | {nm['n_cells']} | {nm['mean_area']:.1f} | "
                f"{nm['median_area']:.1f} | {nm['foreground_frac']:.3f} |\n"
                f"| MCseg | {mm['n_cells']} | {mm['mean_area']:.1f} | "
                f"{mm['median_area']:.1f} | {mm['foreground_frac']:.3f} |\n"
                f"{_file_to_b64_md(cmp_out, f'{roi} comparison')}"
            )

        dist_out = out_dir / f"sizedist_{sample_id}_{ts}.png"
        size_distribution_plot(all_mcseg, dist_out)
        artifacts.append((dist_out, "MCseg 細胞面積分布", "mcseg_sizedist"))

        total_cells = sum(int(np.load(p).max()) for _, _, p in pairs)
        report_text = (
            f"# MCseg 分割品質報告\n\n"
            f"**生成時間**：{started_at.isoformat()}\n"
            f"**樣本**：{sample_id}\n"
            f"**ROI 數**：{len(pairs)}\n"
            f"**MCseg 總細胞數**：{total_cells}\n\n---\n\n"
            f"## 1. 各 ROI：NUC vs MCseg\n\n"
            + "\n---\n\n".join(sections)
            + f"\n\n---\n\n## 2. 細胞面積分布\n{_file_to_b64_md(dist_out, 'size distribution')}\n"
            f"\n---\n\n*由 BioAgent analysis/mcseg_quality.py 自動生成*\n"
        )
        report_path = out_dir / f"mcseg_qc_{sample_id}_{ts}.md"
        report_path.write_text(report_text, encoding="utf-8")

        summary = (f"MCseg {sample_id}：{len(pairs)} ROI，MCseg 共 {total_cells} 細胞。")[:50]

        completed_at = datetime.now(timezone.utc)
        with duckdb.connect(str(DUCKDB_PATH)) as con:
            safe_write(
                con,
                """UPDATE analysis_history
                      SET status='completed', result_path=?, completed_at=?, summary=?
                    WHERE analysis_id=?""",
                [str(report_path), completed_at, summary, analysis_id],
            )
            # HELIX §7.3：任何呼叫路徑都回填 tool_id（best-effort）
            try:
                from analysis.tool_registry import backfill_tool_id

                backfill_tool_id(con, "bio_run_mcseg_qc", analysis_id)
            except Exception as _exc:
                logger.warning("mcseg_qc: backfill_tool_id 失敗（非致命）: %s", _exc)
            try:
                from analysis.artifact_registry import register_artifact

                for path, desc, subtype in artifacts:
                    if path.exists():
                        register_artifact(
                            con, analysis_id, path, "figure", desc, artifact_subtype=subtype
                        )
                register_artifact(
                    con,
                    analysis_id,
                    report_path,
                    "report",
                    "MCseg QC 報告",
                    artifact_subtype="mcseg_report",
                )
            except Exception as _exc:
                logger.warning("mcseg_quality: register_artifact 失敗（非致命）: %s", _exc)

    except Exception:
        logger.exception("mcseg_qc 分析失敗  analysis_id=%s", analysis_id)
        with duckdb.connect(str(DUCKDB_PATH)) as con:
            safe_write(
                con,
                "UPDATE analysis_history SET status='failed', completed_at=? WHERE analysis_id=?",
                [datetime.now(timezone.utc), analysis_id],
            )
        raise

    logger.info("mcseg_qc 完成  analysis_id=%s", analysis_id)
    return analysis_id, str(report_path)
