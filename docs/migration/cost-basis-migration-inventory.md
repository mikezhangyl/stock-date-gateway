# Cost-Basis Migration Inventory

| Asset | Source Path | Decision | Target Path | Notes |
|-------|-------------|----------|-------------|-------|
| TushareMarketDataClient | `/Users/mikezhang/Coding/AI-Learning/Cost-Basis-Trading/backend/app/data/tushare_client.py` | 改造迁移 | `stock_data_gateway/providers/tushare/client.py` | 保留 lazy SDK loading、retry、rate limit、错误映射、脱敏和 retry event handler |
| test_tushare_client.py | `/Users/mikezhang/Coding/AI-Learning/Cost-Basis-Trading/backend/tests/test_tushare_client.py` | 改造迁移 | `tests/providers/test_tushare_client.py` | 作为迁移契约 |
| local-market-data-cache.md | `/Users/mikezhang/Coding/AI-Learning/Cost-Basis-Trading/docs/design/local-market-data-cache.md` | 设计复用 | `stock_data_gateway/cache/*` | 采用 semantic key、SQLite current/version 表、negative cache 和 checksum 思路 |
| a-share-chip-backfill-agent.md | `/Users/mikezhang/Coding/AI-Learning/Cost-Basis-Trading/docs/design/a-share-chip-backfill-agent.md` | 设计复用 | `stock_data_gateway/jobs/cyq_chips_backfill.py` | 第一版只做本地单进程 backfill，不迁移 PostgreSQL/ClickHouse control plane |
| tushare-data-contract.md | `/Users/mikezhang/Coding/AI-Learning/Cost-Basis-Trading/docs/references/tushare-data-contract.md` | 设计复用 | `stock_data_gateway/policies/tushare.py` | 用于 endpoint fields、query rules 和错误模型 |
| `/api/scans` | `/Users/mikezhang/Coding/AI-Learning/Cost-Basis-Trading/backend/app/api/routes.py` | 明确不迁移 | N/A | Cost-Basis 策略扫描业务 |
| `/api/backtests` | `/Users/mikezhang/Coding/AI-Learning/Cost-Basis-Trading/backend/app/api/routes.py` | 明确不迁移 | N/A | Cost-Basis 回测业务 |
| DeepSeek / AI research agent | Cost-Basis backend/docs | 明确不迁移 | N/A | 本项目不做 AI 判断 |
| Frontend UI | Cost-Basis frontend | 明确不迁移 | N/A | 本项目第一阶段只提供本地 API/CLI |
