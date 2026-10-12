#!/usr/bin/env python
"""
原始 pipeline 輸出的完整性 manifest（建立 / 驗證）。

存在理由：2026-09-16 外部工具重寫了 square_002um 的 barcodes.tsv.gz，把矩陣每
一欄錯貼 bin。該損壞【不拋任何錯誤】—— bins/cell、UMI/cell、細胞數全落在合理
範圍，圖也畫得出來，數小時的下游分析都建立在假訊號上。檔案大小甚至沒變多少。

唯一能可靠抓到這類事的是內容雜湊。manifest 刻意存在 EP 的 git repo 內
（data_manifests/），使 manifest 本身的變動也留在版本歷史裡 —— 放在資料旁邊
等於跟被保護的對象放在同一個籃子。

分兩層避免每次都重算數十 GB：
  小檔（預設 <32MB，涵蓋 barcodes/features/scalefactors/tissue_positions）→ SHA256
  大檔（matrix.mtx.gz / BAM 等）→ 只記 size，內容變動靠小檔與 size 偵測
"""
import argparse, hashlib, json, sys
from pathlib import Path

SMALL_MB = 32
PATTERNS = ["barcodes.tsv.gz", "features.tsv.gz", "matrix.mtx.gz",
            "scalefactors_json.json", "tissue_positions.parquet", "*.h5"]


def sha256(p: Path, buf=1 << 20) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(buf):
            h.update(chunk)
    return h.hexdigest()


def scan(root: Path) -> dict:
    out = {}
    for pat in PATTERNS:
        for f in root.rglob(pat):
            if f.name.startswith("._"):          # macOS AppleDouble，非資料
                continue
            rel = str(f.relative_to(root))
            sz = f.stat().st_size
            rec = {"size": sz}
            if sz <= SMALL_MB * 1024 * 1024:
                rec["sha256"] = sha256(f)
            out[rel] = rec
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="要保護的資料根目錄")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--init", action="store_true", help="建立（或覆蓋）manifest")
    a = ap.parse_args()
    root = Path(a.root).resolve(); mf = Path(a.manifest)

    cur = scan(root)
    n_hashed = sum(1 for v in cur.values() if "sha256" in v)
    if a.init:
        mf.parent.mkdir(parents=True, exist_ok=True)
        mf.write_text(json.dumps({"root": str(root), "files": cur}, indent=2, sort_keys=True))
        print(f"manifest 已建立：{len(cur)} 檔（{n_hashed} 個含 SHA256）→ {mf}")
        return 0

    if not mf.exists():
        print(f"✗ 找不到 manifest：{mf}（先用 --init 建立）"); return 2
    old = json.loads(mf.read_text())["files"]
    added   = sorted(set(cur) - set(old))
    removed = sorted(set(old) - set(cur))
    changed = []
    for k in sorted(set(cur) & set(old)):
        c, o = cur[k], old[k]
        if c["size"] != o["size"]:
            changed.append((k, f"size {o['size']:,} → {c['size']:,}"))
        elif "sha256" in o and c.get("sha256") != o["sha256"]:
            changed.append((k, "SHA256 不符（大小相同，內容被換掉）"))
    print(f"檢查 {len(cur)} 檔（{n_hashed} 個比對 SHA256）  root={root}")
    for k in removed: print(f"  ✗ 已消失     {k}")
    for k, why in changed: print(f"  ✗ 已變動     {k}\n                  {why}")
    for k in added:   print(f"  + 新增       {k}")
    if changed or removed:
        print(f"\n🔴 {len(changed)} 檔變動、{len(removed)} 檔消失 —— 原始 pipeline 輸出不應被修改。")
        print("   請先確認是誰改的，再決定是重建還是更新 manifest。")
        return 1
    print("\n✓ 無變動" + (f"（{len(added)} 個新增檔，如屬預期請 --init 更新）" if added else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
