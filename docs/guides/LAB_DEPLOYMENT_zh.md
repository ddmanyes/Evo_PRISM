# 實驗室與多人部署

[README](../../README_zh.md) · [English](LAB_DEPLOYMENT.md) · [繁體中文](LAB_DEPLOYMENT_zh.md)

適用於多人共同存取、遠端 MCP、非同步分析佇列與共用研究資料。個人本機使用請改看 [single-user guide](SINGLE_USER_zh.md).

根目錄 docker-compose.yml 是既有實驗室部署。啟動前必須調整 build context、bind mounts、host bind 位址、embedding／LLM 端點與 owner token；不可直接把既有路徑當成可攜預設。對外服務保持驗證開啟，成員使用各自的 key。

1. [Multiuser readiness and rollback](MULTIUSER_DEPLOYMENT_READINESS.md)
2. [Members, permissions, queue and ingestion](LAB_MEMBER_ANALYSIS.md)
3. [HTTP transport](MCP_HTTP_GUIDE.md)
4. [Advanced installation](INSTALLATION_zh.md)
5. [Optional SSD mounts](OPTIONAL_SSD_MOUNT.md)
6. [Scheduled backup and maintenance](SCHEDULED_TASKS.md)

正式服務使用的資料庫、worker、GPU watcher 與模型端點各有部署條件；單機 MCP smoke test 不等於多人並行、遠端安全或 GPU 分析驗收。Clef 另依決策指南選擇配置。

[Decision advisor](DECISION_ADVISOR.md)
