# L3 層數據添加指南

**用途**：為 bio_DB 添加新的 Visium HD 或其他空間轉錄體數據  
**原則**：L3 = 不可變原始數據湖  
**最後更新**：2026-05-15

---

## 📋 快速流程

```
新數據到達
    ↓
1. 執行 00_register_sample.py (自動檢測 + 註冊)
    ↓
2. 在 L3 中組織數據 (samples/<sample_id>/raw/)
    ↓
3. 運行 01_spatial_to_parquet.py (轉換 → L2)
    ↓
4. 建立 DuckDB 分析紀錄
    ↓
5. 准備好查詢和 Agent 調用
```

---

## 🎯 Step 1: 註冊新樣本 (自動化)

### 流程

**新建 `scripts/01_register_sample.py`**：

```python
#!/usr/bin/env python3
"""
Automatically detect and register new Visium HD samples in L3
"""
import os
import json
from pathlib import Path
from datetime import datetime
import duckdb

L3_ROOT = Path(__file__).parent.parent / "crc_visium_data"
DB_PATH = Path(__file__).parent.parent / "bio_memory.duckdb"


def scan_l3_for_new_samples():
    """
    Scan L3 directory for new Visium HD samples
    Expected structure:
        crc_visium_data/
        ├─ official_v4/          (existing)
        ├─ sample_20250520/      (new)
        ├─ sample_20250525/      (new)
        └─ ...
    """
    con = duckdb.connect(str(DB_PATH))
    
    # Get existing samples
    existing = set(
        row[0] for row in con.execute(
            "SELECT sample_id FROM sample_registry"
        ).fetchall()
    )
    con.close()
    
    new_samples = []
    
    for sample_dir in sorted(L3_ROOT.iterdir()):
        if not sample_dir.is_dir():
            continue
        
        sample_id = sample_dir.name
        if sample_id in existing:
            print(f"✓ Already registered: {sample_id}")
            continue
        
        # Detect data type
        data_type = detect_data_type(sample_dir)
        if not data_type:
            print(f"⚠ Unknown data type: {sample_id}")
            continue
        
        new_samples.append({
            "sample_id": sample_id,
            "project": sample_id.split("_")[0],  # e.g., "MQ250428"
            "data_type": data_type,
            "l3_path": str(sample_dir),
            "detected_at": datetime.now().isoformat()
        })
    
    return new_samples


def detect_data_type(sample_dir: Path):
    """Detect data type by looking for key files"""
    has_visium_hd = (
        (sample_dir / "binned_outputs").exists() and
        (sample_dir / "segmented_outputs").exists()
    )
    has_bulk_rnaseq = (
        (sample_dir / "quant.sf").exists() or
        (sample_dir / "abundance.h5").exists()
    )
    has_scrna = (
        (sample_dir / "barcodes.tsv.gz").exists() and
        (sample_dir / "matrix.mtx.gz").exists()
    )
    
    if has_visium_hd:
        return "visium_hd"
    elif has_bulk_rnaseq:
        return "bulk_rnaseq"
    elif has_scrna:
        return "scrna"
    return None


def register_in_db(samples):
    """Insert new samples into sample_registry"""
    con = duckdb.connect(str(DB_PATH))
    
    for sample in samples:
        con.execute(
            """
            INSERT INTO sample_registry 
            (sample_id, project, data_type, l3_path, added_by)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                sample["sample_id"],
                sample["project"],
                sample["data_type"],
                sample["l3_path"],
                "auto_scan"
            ]
        )
    
    con.commit()
    con.close()
    print(f"✓ Registered {len(samples)} new samples")


if __name__ == "__main__":
    new_samples = scan_l3_for_new_samples()
    if new_samples:
        print(f"\n📊 Found {len(new_samples)} new samples:")
        for s in new_samples:
            print(f"  • {s['sample_id']} ({s['data_type']})")
        register_in_db(new_samples)
    else:
        print("✓ No new samples detected")
```

### 使用方法

```bash
cd /Volumes/NO NAME/bio_DB
python scripts/01_register_sample.py
```

---

## 📂 Step 2: 組織 L3 原始數據

### 推薦目錄結構

```
crc_visium_data/
├─ official_v4/                          ← 現有樣本
│  ├─ binned_outputs/
│  ├─ segmented_outputs/
│  └─ spatial/
│
├─ MQ250520_sample_1/                    ← 新樣本 (Visium HD)
│  ├─ raw/
│  │  ├─ binned_outputs/                  ← 保持原始目錄結構
│  │  │  ├─ square_002um/
│  │  │  ├─ square_008um/
│  │  │  └─ square_016um/
│  │  ├─ segmented_outputs/
│  │  │  ├─ cell_segmentations.geojson
│  │  │  └─ filtered_feature_cell_matrix.h5
│  │  ├─ spatial/
│  │  │  └─ tissue_positions.parquet
│  │  ├─ metrics/
│  │  │  ├─ Visium_HD_*.metrics_summary.csv
│  │  │  └─ Visium_HD_*.probe_set.csv
│  │  └─ MANIFEST.json                    ← ⭐ 新增：元數據
│  │
│  └─ README.md                           ← 樣本說明
│
└─ Kallisto_v1_sample_2/                 ← 新樣本 (Bulk RNA)
   ├─ raw/
   │  ├─ quant.sf
   │  ├─ abundance.h5
   │  └─ MANIFEST.json
   └─ README.md
```

### MANIFEST.json 格式 (樣本元數據)

在每個新樣本的 `raw/` 目錄下創建 `MANIFEST.json`：

```json
{
  "sample_id": "MQ250520_sample_1",
  "project": "MQ250520",
  "data_type": "visium_hd",
  "species": "mouse",
  "tissue_type": "colon_cancer",
  
  "sequencing_info": {
    "platform": "Visium HD",
    "resolution": "2µm",
    "chemistry_version": "v2",
    "read_count": 450000000
  },
  
  "files": {
    "feature_matrix_002um": "binned_outputs/square_002um/filtered_feature_bc_matrix.h5",
    "feature_matrix_008um": "binned_outputs/square_008um/filtered_feature_bc_matrix.h5",
    "cell_segmentation": "segmented_outputs/cell_segmentations.geojson",
    "tissue_image": "Visium_HD_MQ250520_tissue_image.btf"
  },
  
  "qa_metrics": {
    "total_spots": 5000000,
    "median_reads_per_spot": 90000,
    "median_genes_per_spot": 5000
  },
  
  "timestamps": {
    "sequencing_date": "2025-05-20",
    "data_received": "2025-05-22",
    "l3_ingested": "2025-05-23"
  }
}
```

---

## 🔄 Step 3: 轉換 L3 → L2 (Parquet)

### 執行轉換

```bash
python scripts/02_spatial_to_parquet.py --sample_id MQ250520_sample_1
```

### 轉換工具 (`scripts/02_spatial_to_parquet.py`)

```python
#!/usr/bin/env python3
"""
Convert L3 Visium HD matrices (H5/MTX) → L2 Parquet (JIDN optimized)
"""
import argparse
from pathlib import Path
import pandas as pd
import anndata
import duckdb

L3_ROOT = Path(__file__).parent.parent / "crc_visium_data"
L2_ROOT = Path(__file__).parent.parent / "silver"
DB_PATH = Path(__file__).parent.parent / "bio_memory.duckdb"


def convert_visium_to_parquet(sample_id: str, resolution: str = "008um"):
    """
    Convert Visium HD H5 → Parquet
    Args:
        sample_id: e.g., "MQ250520_sample_1"
        resolution: "002um", "008um", or "016um"
    """
    # Load H5AD
    h5_path = (
        L3_ROOT / sample_id / "raw" / "binned_outputs"
        / f"square_{resolution}" / "filtered_feature_bc_matrix.h5"
    )
    
    print(f"Loading {h5_path}...")
    adata = anndata.read_h5ad(h5_path)
    
    # Extract counts
    counts_df = pd.DataFrame.sparse.from_spmatrix(
        adata.X,
        index=adata.obs_names,
        columns=adata.var_names
    ).sparse.to_coo()
    
    # Save as Parquet (JIDN compression)
    output_dir = L2_ROOT / sample_id / f"resolution_{resolution}"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    parquet_path = output_dir / "feature_bc_matrix.parquet"
    counts_df.to_parquet(
        parquet_path,
        compression="snappy",
        index=True
    )
    
    print(f"✓ Saved: {parquet_path}")
    
    # Save metadata separately
    obs_path = output_dir / "barcodes.parquet"
    adata.obs.to_parquet(obs_path)
    
    var_path = output_dir / "features.parquet"
    adata.var.to_parquet(var_path)
    
    # Update database
    con = duckdb.connect(str(DB_PATH))
    con.execute(
        """
        UPDATE sample_registry
        SET l2_ready = TRUE, last_updated = now()
        WHERE sample_id = ?
        """,
        [sample_id]
    )
    con.commit()
    con.close()
    
    print(f"✓ Database updated")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample_id", required=True)
    parser.add_argument("--resolution", default="008um", 
                        choices=["002um", "008um", "016um"])
    args = parser.parse_args()
    
    convert_visium_to_parquet(args.sample_id, args.resolution)
```

---

## 📊 Step 4: 更新數據庫記錄

新樣本的 DuckDB 記錄會自動建立。檢查狀態：

```python
import duckdb

con = duckdb.connect("/Volumes/NO NAME/bio_DB/bio_memory.duckdb")

# 查看所有樣本
print(con.execute("SELECT * FROM sample_registry").df())

# 查看轉換進度
print(con.execute("""
    SELECT sample_id, data_type, l2_ready, l3_path
    FROM sample_registry
""").df())

con.close()
```

---

## 🎛️ Step 5: 查詢與驗證

### 查詢新樣本

```python
import duckdb

con = duckdb.connect("/Volumes/NO NAME/bio_DB/bio_memory.duckdb")

# 查詢特定樣本的分析歷史
results = con.execute("""
    SELECT analysis_type, status, COUNT(*) as runs
    FROM analysis_history
    WHERE sample_id = 'MQ250520_sample_1'
    GROUP BY analysis_type, status
""").df()

print(results)
```

### 驗證 L2 Parquet

```bash
python -c "
import pandas as pd
parquet_file = '/Volumes/NO NAME/bio_DB/silver/MQ250520_sample_1/resolution_008um/feature_bc_matrix.parquet'
df = pd.read_parquet(parquet_file)
print(f'Shape: {df.shape}')
print(df.head())
"
```

---

## 📝 完整工作流範例

### 場景：添加新的 Visium HD 樣本 `MQ250525_P1`

```bash
# 1️⃣ 假設數據已複製到 L3
ls -la /Volumes/NO\ NAME/bio_DB/crc_visium_data/MQ250525_P1/

# 2️⃣ 建立 MANIFEST.json
cat > /Volumes/NO\ NAME/bio_DB/crc_visium_data/MQ250525_P1/raw/MANIFEST.json << 'EOF'
{
  "sample_id": "MQ250525_P1",
  "project": "MQ250525",
  "data_type": "visium_hd",
  "species": "mouse",
  "tissue_type": "pancreas"
}
EOF

# 3️⃣ 執行自動註冊
cd /Volumes/NO\ NAME/bio_DB
python scripts/01_register_sample.py

# 4️⃣ 轉換到 L2
python scripts/02_spatial_to_parquet.py --sample_id MQ250525_P1 --resolution 008um

# 5️⃣ 驗證
python -c "
import duckdb
con = duckdb.connect('bio_memory.duckdb')
print(con.execute('SELECT * FROM sample_registry WHERE sample_id=\"MQ250525_P1\"').df())
con.close()
"
```

---

## ⚠️ 注意事項

| 規則 | 說明 |
|------|------|
| **不修改 L3** | L3 是只讀不可變的。新數據只能新增目錄，不能修改 |
| **MANIFEST.json 必需** | 每個新樣本的 `raw/` 目錄必須有元數據文件 |
| **自動檢測** | 執行 `01_register_sample.py` 會自動掃描並註冊 L3 中的新目錄 |
| **L2 轉換** | 必須顯式運行 `02_spatial_to_parquet.py` 才能觸發轉換 |
| **數據庫同步** | 轉換完成後 `sample_registry.l2_ready = TRUE` |

---

## 🔗 相關文件

- [TEST_DATABASE_INDEX.md](TEST_DATABASE_INDEX.md) - 測試數據庫總覽
- [plan_zh.md](plan_zh.md) - 系統設計
- [scripts/00_init_db.py](scripts/00_init_db.py) - DuckDB 初始化
