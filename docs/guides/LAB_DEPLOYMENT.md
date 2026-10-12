# Lab and multiuser deployment

[README](../../README.md) · [English](LAB_DEPLOYMENT.md) · [繁體中文](LAB_DEPLOYMENT_zh.md)

For shared data, remote MCP access and asynchronous multiuser analysis. For a personal computer use the [single-user guide](SINGLE_USER.md).

Root docker-compose.yml describes the existing lab deployment. Before starting, adapt the build context, bind mounts, host bind address, embedding/LLM endpoints and owner token. Keep authentication enabled for remote access and issue individual member keys.

1. [Multiuser readiness and rollback](MULTIUSER_DEPLOYMENT_READINESS.md)
2. [Members, permissions, queue and ingestion](LAB_MEMBER_ANALYSIS.md)
3. [HTTP transport](MCP_HTTP_GUIDE.md)
4. [Advanced installation](INSTALLATION.md)
5. [Optional SSD mounts](OPTIONAL_SSD_MOUNT.md)
6. [Scheduled backup and maintenance](SCHEDULED_TASKS.md)

Database, workers, GPU watchers and model endpoints have separate deployment requirements. A single-user MCP smoke test does not establish multiuser concurrency, remote security or GPU-analysis acceptance. Configure optional Clef separately.

[Decision advisor](DECISION_ADVISOR.md)
