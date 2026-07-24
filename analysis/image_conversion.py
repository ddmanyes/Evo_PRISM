"""Proprietary whole-slide scanner image → Pyramidal BigTIFF conversion.

Converts scanner-proprietary formats (currently Hamamatsu .ndpi) into a
tiled, pyramidal BigTIFF that downstream imaging tools (mcseg, Loupe Browser)
can read. Prefers libvips (fast, low-memory streaming); falls back to
Bio-Formats bfconvert (slower, needs JVM heap).

Main function:
    convert_ndpi_to_tiff(sample_id, input_ndpi, ...) -> (analysis_id, output_tiff_path)
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import BFTOOLS_PATH  # noqa: E402
from analysis.validators import validate_sample_id  # noqa: E402
from analysis.tool_registry import register_tool_on_import  # noqa: E402
from analysis.run_context import analysis_run  # noqa: E402
from store.factory import get_store  # noqa: E402

logger = logging.getLogger(__name__)


def _check_vips() -> bool:
    """系統是否安裝 libvips（`vips` CLI 在 PATH 上）。"""
    return shutil.which("vips") is not None


def _check_bfconvert() -> bool:
    """專案 tools/bftools/ 下是否存在 Bio-Formats bfconvert 備援工具。"""
    return BFTOOLS_PATH.exists()


def _run_conversion(
    input_path: Path,
    output_path: Path,
    compression: str,
    quality: int,
    tile_size: int,
) -> tuple[float, str, str]:
    """執行金字塔 BigTIFF 轉換。優先 libvips，不可用則降級 bfconvert。

    Returns:
        (duration_seconds, tool_used, command_used)
    """
    start = time.time()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    if _check_vips():
        cmd = [
            "vips", "tiffsave", str(input_path), str(output_path),
            "--tile", "--tile-width", str(tile_size), "--tile-height", str(tile_size),
            "--pyramid", "--compression", compression, "--bigtiff",
        ]
        if compression == "jpeg":
            cmd += ["--Q", str(quality)]
        tool_used = "libvips"
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"libvips conversion failed: {result.stderr}")
    elif _check_bfconvert():
        try:
            os.chmod(BFTOOLS_PATH, 0o755)
        except OSError as e:
            logger.warning("_run_conversion: 無法設置 bfconvert 執行權限：%s", e)
        bf_comp = {"jpeg": "JPEG", "lzw": "LZW", "deflate": "zlib"}.get(compression, "Uncompressed")
        cmd = [
            str(BFTOOLS_PATH), "-bigtiff", "-compression", bf_comp,
            "-tilex", str(tile_size), "-tiley", str(tile_size),
            "-pyramid-resolutions", "6",
            str(input_path), str(output_path),
        ]
        tool_used = "bfconvert"
        env = {**os.environ, "BF_MEM": "6g"}
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if result.returncode != 0:
            raise RuntimeError(f"bfconvert conversion failed: {result.stderr}")
    else:
        raise RuntimeError(
            "找不到轉檔工具：系統未安裝 libvips（`vips`），"
            f"專案 tools/bftools/ 下也沒有 bfconvert（{BFTOOLS_PATH}）。"
            "請先安裝其中一個再重試。"
        )

    command_used = " ".join(cmd)
    duration = time.time() - start
    return duration, tool_used, command_used


@register_tool_on_import(
    tool_name="bio_convert_ndpi_to_tiff",
    version="1.0.0",
    description="將 Hamamatsu NDPI 全片掃描影像轉換為金字塔 BigTIFF，供 mcseg / Loupe 讀取",
)
def convert_ndpi_to_tiff(
    sample_id: str,
    input_ndpi: "Path | str",
    output_tiff: "Path | str | None" = None,
    compression: str = "jpeg",
    quality: int = 85,
    tile_size: int = 256,
    requested_by: str = "agent",
) -> tuple[str, str]:
    """將 .ndpi 全片掃描檔轉換為 tile 化、金字塔結構的 BigTIFF。

    輸出預設與輸入同資料夾、同檔名換副檔名（沿用既有 L3 就地轉檔慣例，讓
    bio_run_mcseg_fullslide 等下游工具能直接在原資料夾找到轉出的 TIFF，不需另外搬檔）。

    Returns:
        (analysis_id, output_tiff_path)
    """
    validate_sample_id(sample_id)
    input_path = Path(input_ndpi).resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"找不到輸入 NDPI 檔案：{input_path}")
    if input_path.suffix.lower() != ".ndpi":
        raise ValueError(f"輸入檔案副檔名應為 .ndpi，實際為：{input_path.suffix}")

    out_path = Path(output_tiff).resolve() if output_tiff else input_path.with_suffix(".tiff")

    _params = {
        "input_ndpi": str(input_path), "output_tiff": str(out_path),
        "compression": compression, "quality": quality, "tile_size": tile_size,
    }

    with analysis_run(
        sample_id, "image_format_conversion",
        params=_params,
        requested_by=requested_by,
        tool_name="bio_convert_ndpi_to_tiff",
    ) as run:
        duration, tool_used, command_used = _run_conversion(
            input_path, out_path, compression, quality, tile_size
        )

        out_size_gb = out_path.stat().st_size / (1024 ** 3)
        summary = (
            f"NDPI→Pyramidal BigTIFF via {tool_used}：{input_path.name} → "
            f"{out_path.name}（{out_size_gb:.1f}GB，{duration:.0f}s）"
        )[:200]

        # 注意：刻意不呼叫 run.artifact() 登記輸出的 TIFF——
        # register_artifact() 會把整個檔案讀進記憶體算 base64 決定要不要 inline，
        # 對 GB 級 TIFF 這樣做會爆記憶體；超過 512KB 門檻時它還會把檔案「搬」到
        # results/overflow/ 並刪除原檔，破壞 mcseg 等下游工具依賴的 L3 原地路徑慣例。
        run.complete(
            out_path, summary,
            summary_metrics={
                "tool_used": tool_used,
                "duration_seconds": round(duration, 2),
                "output_size_gb": round(out_size_gb, 2),
                "command": command_used,
            },
        )

        try:
            get_store().update_sample(
                sample_id,
                notes=(
                    f"NDPI converted to Pyramidal BigTIFF ({tool_used}); "
                    f"ready for registration. Last conversion duration: {duration:.1f}s"
                ),
            )
        except Exception:
            logger.warning(
                "convert_ndpi_to_tiff: sample_registry notes 更新失敗（非致命）", exc_info=True
            )

    logger.info(
        "convert_ndpi_to_tiff 完成  analysis_id=%s  tool=%s  duration=%.1fs",
        run.analysis_id, tool_used, duration,
    )
    return run.analysis_id, str(out_path)
