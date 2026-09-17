#!/usr/bin/env python
"""
定期驗證所有 data_manifests/ 的原始資料完整性（launchd 每週執行）。

背景見 CLAUDE.md §6「原始 pipeline 輸出不可修改」。2026-09-16 外部工具重寫
barcodes.tsv.gz 造成數小時分析建立在假訊號上，且完全無聲。這支是靜態偵測層，
補上「使用時偵測」（affine_rna_counting.py 的 composition_sanity_check）之前的
時間差 —— 越早發現，重建與重跑的代價越小。

在【主機】執行（非容器），故寫 logs/ 無 virtiofs 覆寫問題。
任一 manifest 出現變動即 exit 1，方便 launchd 或人工查覺。
"""
import json, subprocess, sys
from datetime import datetime
from pathlib import Path

EP = Path(__file__).resolve().parent.parent
MF_DIR = EP / "data_manifests"
LOG = EP / "logs" / "manifest_verify.log"
TOOL = EP / "scripts" / "verify_raw_integrity.py"


def main() -> int:
    ts = datetime.now().isoformat(timespec="seconds")
    lines = [f"\n{'='*70}\n[{ts}] 原始資料完整性驗證"]
    manifests = sorted(MF_DIR.glob("*.json"))
    if not manifests:
        lines.append("  ✗ data_manifests/ 內沒有任何 manifest")
        _write(lines)
        return 2

    bad = []
    for mf in manifests:
        try:
            root = json.loads(mf.read_text())["root"]
        except Exception as e:
            lines.append(f"  ✗ {mf.name}: manifest 無法解析 — {e}")
            bad.append(mf.name)
            continue
        if not Path(root).exists():
            lines.append(f"  ⊘ {mf.name}: 資料根目錄不存在（卷宗未掛載？）{root}")
            continue
        r = subprocess.run([sys.executable, str(TOOL), root, "--manifest", str(mf)],
                           capture_output=True, text=True)
        head = (r.stdout or "").strip().splitlines()
        if r.returncode == 0:
            lines.append(f"  ✓ {mf.name}")
        else:
            bad.append(mf.name)
            lines.append(f"  🔴 {mf.name}  (exit {r.returncode})")
            lines += [f"       {x}" for x in head if x.strip()]

    lines.append(f"  —— {len(manifests) - len(bad)}/{len(manifests)} 通過")
    if bad:
        lines.append(f"  🔴 有變動：{', '.join(bad)}")
        lines.append("     原始 pipeline 輸出不應被修改。先查是誰改的，再決定重建或更新 manifest。")
    _write(lines)
    print("\n".join(lines))
    return 1 if bad else 0


def _write(lines):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:            # 用 append，不覆寫
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    sys.exit(main())
