# 方法圖版本與結果取用

2026-10-12 校訂。9 月的分析方法圖是流程示意與歷史結果範例，不代表所有方法已在個人電腦驗收。圖中的耗時也是示意，並非效能保證。

## 結果取用

公開文件不提供研究結果下載。舊圖的「開啟真實結果」連結改為本說明，避免指向作者電腦上的 /Volumes 路徑。保留的檔名僅用來辨識歷史範例。

使用自己的 EP：先用 bio_history_lookup 查詢有權存取的分析，再用 bio_read_report 讀報告。只有明確要求圖片或資料時，才透過 bio_deliver_results 取用已登記的成果；仍受部署、身分與存取權限限制。

## Bulk EDA 輸入更新

bio_run_bulk_eda 支援 sample_id、選用 counts_path 與 coldata_path。指定的檔案必須位於該樣本登記的 L3 目錄內；相對路徑以該目錄解析。省略 counts_path 仍沿用舊 Kallisto 預設，並不自動搜尋任意矩陣。

```json
{"sample_id":"my_sample","counts_path":"counts.tsv","coldata_path":"coldata.tsv"}
```

舊 PNG/SVG 的 gene_counts.tsv 是範例名稱，不是新介面強制檔名。Bulk EDA 本身執行 QC、相關性、PCA 與報告；領域標題中的 DEG、熱圖及富集是另行呼叫的後續方法。

## 架構圖分工

- [現況架構](ARCHITECTURE_zh.md)：已存在的元件、條件與限制。
- SB 10/08 四流程：目標設計，尚未全面驗收。
- 9 月圖組與原始 PNG：歷史快照，保留來源，不作即時部署證明。

永久成果／ENGRAM 與可過期 L1 快取分開；影響查詢不是強制重算；Clef 為可選 Choice shadow，不具執行或發布權。
