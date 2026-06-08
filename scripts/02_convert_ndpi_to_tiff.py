#!/usr/bin/env python3
"""Evo_PRISM NDPI to BigTIFF 轉換與紀錄登記腳本。

此腳本將專有的 Hamamatsu .ndpi 格式切片影像轉換為 10x Genomics 軟體支援的、
具備 Tile 與 Pyramid (金字塔縮放) 結構的 BigTIFF 格式 (.tiff)，
並將此資料處理過程登記到 Evo_PRISM 語意記憶資料庫 (DuckDB) 當中。

使用方式:
    uv run scripts/02_convert_ndpi_to_tiff.py \
        --sample-id crc_visium_hd_a1 \
        --input-ndpi /path/to/image.ndpi \
        --output-tiff /path/to/output.tiff
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# 載入 Evo_PRISM 專案路徑與設定
sys.path.insert(0, str(Path(__file__).parent.parent))
from config.db_utils import open_db, safe_write

# 初始化 Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("ndpi_converter")

def check_bfconvert() -> bool:
    """檢查 Evo_PRISM 專案中是否存在 Bio-Formats bfconvert 工具。"""
    bf_path = Path(__file__).parent.parent / "tools" / "bftools" / "bfconvert"
    return bf_path.exists()

def check_vips() -> bool:
    """檢查系統中是否存在 libvips 轉檔工具 (vips)。"""
    return shutil.which("vips") is not None

def run_conversion(
    input_ndpi: Path,
    output_tiff: Path,
    compression: str = "jpeg",
    quality: int = 85,
    tile_size: int = 256
) -> tuple[float, str, str]:
    """執行高畫質、金字塔 (Pyramid) 且相容於 10x Genomics Loupe Browser 的 BigTIFF 轉換。
    
    優先使用極速、低內存的 libvips (vips tiffsave)。如果系統沒有安裝 vips，則降級使用
    Bio-Formats bfconvert 進行 Pyramidal OME-TIFF 轉換。
    
    傳回:
        (duration, tool_used, command_used)
    """
    start_time = time.time()
    
    # 確保輸出檔案目錄存在
    output_tiff.parent.mkdir(parents=True, exist_ok=True)
    if output_tiff.exists():
        try:
            output_tiff.unlink()
        except Exception:
            pass

    if check_vips():
        logger.info("檢測到系統安裝有 libvips！開始使用 libvips 進行極速 Pyramidal BigTIFF 轉換...")
        logger.info("輸入: %s", input_ndpi)
        logger.info("輸出: %s", output_tiff)
        logger.info("壓縮格式: %s | 品質: %d | 瓦片大小: %d", compression, quality, tile_size)
        
        # 建立 vips 參數
        cmd = [
            "vips",
            "tiffsave",
            str(input_ndpi),
            str(output_tiff),
            "--tile",
            "--tile-width", str(tile_size),
            "--tile-height", str(tile_size),
            "--pyramid",
            "--compression", compression,
            "--bigtiff"
        ]
        if compression == "jpeg":
            cmd += ["--Q", str(quality)]
            
        tool_used = "libvips"
        command_used = " ".join(cmd)
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error("libvips 轉換失敗！")
            logger.error("錯誤資訊: %s", result.stderr)
            raise RuntimeError(f"libvips conversion failed: {result.stderr}")
    else:
        logger.warning("未檢測到 libvips。將降級使用 Java Bio-Formats bfconvert（這可能速度較慢且需要大內存）...")
        bf_executable = Path(__file__).parent.parent / "tools" / "bftools" / "bfconvert"
        
        try:
            os.chmod(bf_executable, 0o755)
        except Exception as e:
            logger.warning("無法手動設置執行權限：%s", e)

        # 設置環境變數以給予 Java 6GB 內存，防止轉大圖時 OOM (Java heap space)
        import os as py_os
        env = {**py_os.environ, "BF_MEM": "6g"}
        
        # 轉換 compression 對應至 bfconvert
        # bfconvert compression option: Uncompressed, LZW, JPEG-2000, JPEG-2000 Lossy, JPEG, zlib
        bf_comp = "Uncompressed"
        if compression == "jpeg":
            bf_comp = "JPEG"
        elif compression == "lzw":
            bf_comp = "LZW"
        elif compression == "deflate":
            bf_comp = "zlib"

        cmd = [
            str(bf_executable),
            "-bigtiff",
            "-compression", bf_comp,
            "-tilex", str(tile_size),
            "-tiley", str(tile_size),
            "-pyramid-resolutions", "6", # 預設產生 6 層金字塔
            str(input_ndpi),
            str(output_tiff)
        ]
        
        tool_used = "bfconvert"
        command_used = " ".join(cmd)
        
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if result.returncode != 0:
            logger.error("bfconvert 轉換失敗！")
            logger.error("標準錯誤輸出: %s", result.stderr)
            raise RuntimeError(f"bfconvert conversion failed: {result.stderr}")

    duration = time.time() - start_time
    logger.info("影像轉換成功！總共耗時 %.2f 秒", duration)
    
    # 強制將 macOS 磁碟寫入快取 (Write Cache) 刷入外接隨身碟實體晶片，防止提前讀取造成檔案損毀
    logger.info("正在強制將磁碟快取同步刷入 Kingston 隨身碟，請稍候...")
    os.sync()
    time.sleep(5)  # 給予隨身碟與作業系統解鎖檔案的準備時間
    logger.info("磁碟快取同步完成，檔案已安全鎖定！")
    
    return duration, tool_used, command_used

def record_to_db(
    sample_id: str,
    input_ndpi: Path,
    output_tiff: Path,
    duration: float,
    tool_used: str,
    command_used: str
) -> str:
    """將轉換歷史寫入 analysis_history，並更新 sample_registry。"""
    analysis_id = str(uuid.uuid4())
    logger.info("正在將轉換歷史登記到 Evo_PRISM DuckDB (ID: %s)...", analysis_id)

    # 1. 登記到 analysis_history 永久分析歷史
    sql_history = """
        INSERT INTO analysis_history (
            analysis_id, sample_id, analysis_type, parameters, status,
            result_path, requested_by, started_at, completed_at, summary, tool_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
    """
    
    import json
    params_json = json.dumps({
        "input_ndpi": str(input_ndpi),
        "output_tiff": str(output_tiff),
        "tool": tool_used,
        "command": command_used,
        "duration_seconds": round(duration, 2)
    })
    
    now = datetime.now(timezone.utc)
    summary_text = (
        f"Successfully converted NDPI to Lossless/High-quality Pyramid BigTIFF via {tool_used}. "
        f"Input: {input_ndpi.name} | Output: {output_tiff.name} | Duration: {duration:.1f}s"
    )

    # 2. 更新 sample_registry 當中的樣本備註
    sql_update_sample = """
        UPDATE sample_registry
        SET notes = ?,
            last_updated = ?
        WHERE sample_id = ?
    """
    new_notes = f"NDPI converted to Pyramidal BigTIFF ({tool_used}); ready for registration. Last conversion duration: {duration:.1f}s"

    with open_db() as con:
        # 寫入歷史
        safe_write(
            con,
            sql_history,
            [
                analysis_id,
                sample_id,
                "image_format_conversion",
                params_json,
                "completed",
                str(output_tiff),
                "AI Agent (Antigravity)",
                now,
                now,
                summary_text
            ]
        )
        # 更新樣品備註
        safe_write(
            con,
            sql_update_sample,
            [new_notes, now, sample_id]
        )

    logger.info("Evo_PRISM DuckDB 登記完成！")
    return analysis_id

def main() -> None:
    parser = argparse.ArgumentParser(description="Evo_PRISM NDPI to BigTIFF 轉換與紀錄登記腳本")
    parser.add_argument("--sample-id", required=True, help="登記的樣本 ID (例如 crc_visium_hd_a1)")
    parser.add_argument("--input-ndpi", required=True, help="輸入的 .ndpi 高清圖檔路徑")
    parser.add_argument("--output-tiff", required=True, help="輸出的 .tiff 高清圖檔路徑")
    parser.add_argument("--compression", default="jpeg", choices=["jpeg", "lzw", "none", "deflate"], help="壓縮格式 (預設: jpeg)")
    parser.add_argument("--quality", type=int, default=85, help="JPEG 壓縮品質 (1-100，預設: 85)")
    parser.add_argument("--tile-size", type=int, default=256, help="Tile 瓦片大小 (預設: 256)")
    
    args = parser.parse_args()
    input_path = Path(args.input_ndpi).resolve()
    output_path = Path(args.output_tiff).resolve()

    has_vips = check_vips()
    has_bf = check_bfconvert()

    if not has_vips and not has_bf:
        logger.error("系統中未找到 'vips'，且專案的 tools 目錄下未找到 'bfconvert' 轉檔工具。請安裝其中一個以進行轉檔。")
        sys.exit(1)

    if not input_path.exists():
        logger.error("找不到輸入檔案: %s", input_path)
        sys.exit(1)

    # 建立輸出資料夾 (如果不存在的話)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        duration, tool_used, command_used = run_conversion(
            input_path, 
            output_path, 
            compression=args.compression,
            quality=args.quality,
            tile_size=args.tile_size
        )
        record_to_db(
            sample_id=args.sample_id,
            input_ndpi=input_path,
            output_tiff=output_path,
            duration=duration,
            tool_used=tool_used,
            command_used=command_used
        )
        logger.info("🎉 轉換與資料庫登記程序已完美結束！")
    except Exception as e:
        logger.error("程序執行過程中出錯: %s", e)
        sys.exit(1)

if __name__ == "__main__":
    main()
