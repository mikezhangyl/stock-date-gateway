# Upstream Consumption and Change Guide

本文档面向 `stock-data-gateway` 的上游消费方，说明当前已经完成的能力、稳定消费合约、验收方式，以及以后新增数据能力时应该如何提出更新要求。

## 当前完成的能力

`stock-data-gateway` 现在是一个本地优先的市场数据网关：

- 提供本地 HTTP facade：`POST http://127.0.0.1:8700/tushare`
- 对上游保持 Tushare 官方请求/响应形状，便于已有 Tushare 调用迁移
- 网关自己持有真实 `TUSHARE_TOKEN`，不会信任或转发调用方传入的 token
- 使用 SQLite 做 read-through cache，避免上游服务重复打外部数据源
- 提供 FNI 期望的 normalized REST 路由：`/api/v1/market-data/...`
- 支持 `force_refresh` 和 refresh 失败时的 stale cache fallback
- 支持 Tushare facade 里的逗号分隔 `ts_code`，内部拆成逐标的缓存键
- 提供 EastMoney 行情和 AkShare 板块/涨跌停统计；AkShare 未安装时使用 EastMoney public data fallback
- 数据路由有 bounded fetch slot、每请求 symbol 上限和 request deadline，健康检查不依赖上游 provider
- 提供 SQLite-backed async job API，用于 daily-bars、breadth-window、job list、cancel、progress、coverage 和重启后状态查询
- 支持 fake validation、live provider validation、FNI gateway acceptance
- 提供 cache inspect/audit/clear 运维命令
- 已验证 `fund-narrative-intelligence` 可以通过 HTTP 消费本服务，不需要 Python import 或跨项目运行时依赖

已经提交的关键里程碑：

- `0472575 feat: bootstrap stock data gateway`
- `0909805 docs: clarify live provider setup`
- `bb29a92 feat: support fni tushare gateway calls`
- `925e974 feat: add fni acceptance and cache ops`
- `12f0907 docs: add upstream consumption guide`
- `e4a0488 chore: finalize gateway consumption cleanup`
- `ca9c055 feat: add daily bars async jobs`

## 稳定 HTTP 合约

健康检查：

```text
GET /api/health
```

Tushare facade：

```text
POST /tushare
Content-Type: application/json
```

请求形状：

```json
{
  "api_name": "daily",
  "token": "caller-token-is-ignored",
  "params": {
    "ts_code": "000001.SZ",
    "start_date": "20240102",
    "end_date": "20240102"
  },
  "fields": "ts_code,trade_date,close"
}
```

成功响应形状：

```json
{
  "code": 0,
  "msg": "",
  "data": {
    "fields": ["ts_code", "trade_date", "close"],
    "items": [["000001.SZ", "20240102", 10.5]]
  },
  "meta": {
    "provider": "tushare",
    "endpoint": "daily",
    "source": "external",
    "cache_hit": false,
    "fetched_external": true,
    "row_count": 1,
    "status": "ok"
  }
}
```

错误响应仍保持 Tushare-like envelope：

```json
{
  "code": 500,
  "msg": "Provider response missing fields: close",
  "data": {
    "fields": [],
    "items": []
  },
  "meta": {
    "status": "error",
    "error_code": "SCHEMA_CHANGED"
  }
}
```

上游服务应该只依赖 `code/msg/data/meta`，不要依赖 gateway 内部 Python 类型。

Normalized REST：

```text
POST /api/v1/market-data/tushare/daily
Content-Type: application/json
```

请求形状：

```json
{
  "symbols": ["000001.SZ"],
  "start_date": "2024-01-02",
  "end_date": "2024-01-02",
  "include_turnover": true,
  "force_refresh": false,
  "allow_stale": true
}
```

成功响应形状：

```json
{
  "data": {
    "rows": [
      {
        "symbol": "000001.SZ",
        "trade_date": "2024-01-02",
        "close": 10.5,
        "volume": 1000.0,
        "turnover_rate": 1.2,
        "source": "tushare"
      }
    ]
  },
  "meta": {
    "provider": "tushare",
    "endpoint": "daily",
    "cache": {
      "hit": false,
      "mode": "upstream"
    },
    "generated_at": "2026-05-25T00:00:00+00:00",
    "row_count": 1,
    "status": "ok"
  }
}
```

## 当前支持的 Endpoint

当前 read-through cache 的主 provider 是 Tushare。EastMoney 与 AkShare 已有最小真实 adapter；AkShare 是 optional dependency，未安装时 normalized route 会使用 EastMoney public data fallback，避免返回空成功。

| Endpoint | 当前用途 | 缓存语义 |
| --- | --- | --- |
| `trade_cal` | 交易日解析、`cyq_chips` range 展开 | 按 `exchange + cal_date` |
| `stock_basic` | 股票名称、行业、上市信息 | 按 `ts_code + latest` |
| `daily` | 行情、FNI market/valuation 基础数据 | 按 `ts_code + trade_date`；无日期请求按最近窗口查询并缓存为 `latest` |
| `daily_basic` | PE/PB/市值/换手率等估值字段 | 按 `ts_code + trade_date`；无日期请求按最近窗口查询并缓存为 `latest` |
| `income` | 收入、归母净利等财务指标输入 | 按 `ts_code + latest` |
| `fina_indicator` | ROE、毛利率、成长率等财务指标输入 | 按 `ts_code + latest` |
| `cyq_chips` | 筹码分布回填 | 按 `ts_code + trade_date`；range 请求先解析交易日再逐日抓取 |
| `index_daily` | 指数日线，已注册策略 | 按 `ts_code + trade_date` |
| `fund_daily` | 基金日线，已注册策略 | 按 `ts_code + trade_date` |

Normalized HTTP routes:

| Route | Provider | 状态 |
| --- | --- | --- |
| `/api/v1/market-data/tushare/daily` | `tushare` | 已实现，read-through cache |
| `/api/v1/market-data/tushare/index-daily` | `tushare` | 已实现，read-through cache |
| `/api/v1/market-data/tushare/fund-daily` | `tushare` | 已实现，read-through cache |
| `/api/v1/market-data/tushare/stock-basic` | `tushare` | 已实现，read-through cache |
| `/api/v1/market-data/tushare/trade-cal` | `tushare` | 已实现，read-through cache |
| `/api/v1/market-data/chips/cyq` | `local_gateway/tushare` | 已实现，按 symbol/date 分组 |
| `/api/v1/market-data/eastmoney/market-quotes` | `eastmoney` | 已实现，直接调用 public quote API |
| `/api/v1/market-data/eastmoney/northbound-capital` | `eastmoney` | route 已保留，返回空 rows，等待字段映射 |
| `/api/v1/market-data/eastmoney/main-capital-flow` | `eastmoney` | route 已保留，返回空 rows，等待字段映射 |
| `/api/v1/market-data/akshare/sector-concepts` | `akshare` | 已实现，依赖 optional `akshare` 包 |
| `/api/v1/market-data/akshare/limit-up-down` | `akshare` | 已实现，依赖 optional `akshare` 包 |
| `/api/v1/market-data/stocks/sector-memberships` | `local_gateway/akshare` | 已实现，SQLite 物化反查索引 |
| `/api/v1/market-data/funds/profile` | `local_gateway/tushare/eastmoney` | 已实现，SQLite cache + Tushare/EastMoney fallback |
| `/api/v1/market-data/funds/holdings` | `local_gateway/tushare/eastmoney` | 已实现，SQLite cache + Tushare/EastMoney fallback |
| `/api/v1/market-data/source-events/official-filings` | `local_gateway/sec_edgar` | 已实现，SEC EDGAR metadata source events |
| `/api/v1/market-data/source-events/official-disclosures` | `local_gateway/akshare` | 已实现，CN official disclosure metadata source events |
| `/api/v1/market-data/source-events/news-context` | `local_gateway/tushare` | 已实现，public news context source events |
| `/api/v1/market-data/source-events/social-heat` | `local_gateway/stocktwits` | 已实现，默认禁用，显式启用后返回 heat signal |
| `/api/v1/market-data/source-events/news-permission-smoke` | `local_gateway/tushare` | 已实现，Tushare news 权限/字段 smoke |
| `/api/v1/market-data/jobs/daily-bars` | `tushare` | 已实现，异步 large scan job |
| `/api/v1/market-data/jobs/breadth-window` | `tushare` | 已实现，异步 breadth window cache-warming job |
| `/api/v1/market-data/jobs` | `tushare` | 已实现，job list/filter |
| `/api/v1/market-data/jobs/{job_id}` | `tushare` | 已实现，job status/progress/coverage |
| `/api/v1/market-data/jobs/{job_id}/cancel` | `tushare` | 已实现，幂等 cancel，保留 partial rows/cache |
| `/api/v1/market-data/jobs/{job_id}/rows` | `tushare` | 已实现，job rows 分页读取 |

字段策略在 `stock_data_gateway/policies/tushare.py` 中集中注册。新增字段时通常需要同步 bump 对应 `schema_version`，避免旧缓存窄字段污染新请求。

## Cache 语义

缓存 key 由以下部分构成：

- `provider`
- `endpoint`
- `instrument_id`
- `date_key`
- `date_key_role`
- `semantic_params_hash`

`semantic_params` 至少包含 `schema_version`。如果 endpoint 的默认字段、数据解释方式、单位、复权语义、provider 参数语义发生变化，应提升 `schema_version`。

缓存记录分为：

- `market_cache_current_entries`：当前可用版本
- `market_cache_entry_versions`：历史版本
- `market_cache_request_audit`：请求审计
- `market_cache_provider_events`：provider 失败或重试事件
- `market_cache_conflicts`：同一语义 key 的 payload checksum 冲突

运维命令：

```bash
uv run market-gateway-cache inspect --provider tushare --endpoint daily
uv run market-gateway-cache audit --provider tushare
uv run market-gateway-cache clear --provider tushare --endpoint daily --yes
```

`clear` 只清缓存记录，不清审计历史。

普通查询默认命中 cache。Normalized Tushare POST body 可设置：

- `force_refresh: true`：忽略当前记录，尝试抓取新版本
- `allow_stale: true`：refresh 失败时返回旧 cache，`meta.cache.mode=stale_cache`

## FNI 当前消费方式

FNI 不应该 import 本项目代码。正确方式是通过环境变量指向本地 HTTP facade：

```bash
TUSHARE_API_URL=http://127.0.0.1:8700/tushare
```

一键验收：

```bash
uv run market-gateway-fni-acceptance \
  --fni-root /Users/mikezhang/Coding/AI-Learning/fund-narrative-intelligence \
  --mode all
```

该命令会：

- 自动启动 gateway，或使用已有健康 gateway
- 跑 FNI `validate_tushare_primary_acceptance.py`
- 跑 FNI `validate_real_enriched_acceptance.py`
- 检查 `tushare-valuation` 和 `tushare-financial-metrics` 的 `source_url` 必须是 `http://127.0.0.1:8700/tushare`

最近一次通过的验收输出：

- `outputs/gateway_tushare_primary_20260525_015429`
- `outputs/gateway_real_enriched_20260525_015443`

FNI 的 normalized REST gateway 初始需求已经归档在
`docs/product/archive/fni-upstream-change-request-2026-05-25.md`。当前版本已经实现该
文档中非 planned 路由，并为 planned 路由保留稳定空响应入口。

FNI 的 large scan async job 需求已经归档在
`docs/product/archive/fni-large-scan-async-job-change-request-2026-05-25.md`。该阶段把
500-symbol daily scan 从同步大请求升级为 async job，并已被 FNI 验收。

FNI 的 breadth-scale job ops 需求已经归档在
`docs/product/archive/fni-breadth-scale-job-ops-change-request-2026-05-25.md`。该阶段把
MA20/breadth-style cache warming 升级为可 list、cancel、重启后可查、可解释 coverage
的本地 job operations。

Async daily-bars job:

```bash
curl -X POST http://127.0.0.1:8700/api/v1/market-data/jobs/daily-bars \
  -H 'Content-Type: application/json' \
  -d '{
    "provider": "tushare",
    "symbols": ["600519.SH", "000001.SZ"],
    "start_date": "2026-05-18",
    "end_date": "2026-05-22",
    "include_turnover": true,
    "batch_size": 100
  }'
```

Then poll:

```bash
curl 'http://127.0.0.1:8700/api/v1/market-data/jobs?job_type=daily-bars'
curl http://127.0.0.1:8700/api/v1/market-data/jobs/{job_id}
curl 'http://127.0.0.1:8700/api/v1/market-data/jobs/{job_id}/rows?offset=0&limit=10000'
curl -X POST http://127.0.0.1:8700/api/v1/market-data/jobs/{job_id}/cancel
```

Breadth-window job:

```bash
curl -X POST http://127.0.0.1:8700/api/v1/market-data/jobs/breadth-window \
  -H 'Content-Type: application/json' \
  -d '{
    "provider": "tushare",
    "symbols": ["600519.SH", "000001.SZ"],
    "end_date": "2026-05-22",
    "lookback_trading_days": 20,
    "include_turnover": true,
    "batch_size": 100
  }'
```

Queue and batch controls:

```text
GATEWAY_JOB_QUEUE_LIMIT=2
GATEWAY_JOB_MAX_SYMBOLS=5000
GATEWAY_JOB_MAX_BATCH_SIZE=100
GATEWAY_JOB_MAX_LOOKBACK_TRADING_DAYS=260
```

Job status includes:

- `job_type`, `status`, symbol/row counts, `created_at`, `started_at`, `updated_at`
- `cache_hit_symbols`, `upstream_fetch_symbols`, `stale_cache_symbols`
- `current_symbol`, `current_batch_index`, `batch_count`, `symbols_per_minute`
- `last_progress_at`, `last_error`
- `coverage.expected_pairs`, `coverage.returned_pairs`, `coverage.missing_pairs`, `coverage.missing_reasons`

If the gateway restarts while a job is `accepted` or `running`, the restored job
status becomes `interrupted`; completed/cancelled/failed job summaries and rows
remain queryable from SQLite.

If a consumer repeats the same semantic `daily-bars` or `breadth-window` request
after the previous job ended as `cancelled`, `failed`, or `interrupted`, the
gateway reactivates that compatible partial job instead of returning the old
terminal status. Already fetched rows remain readable and are not duplicated;
the resumed worker skips completed symbols and continues the remaining symbols.

FNI 的 Can-Do market-data capability pack 已经归档在
`docs/product/archive/fni-can-do-market-data-capability-pack-change-request-2026-05-25.md`。
该阶段新增 provider-neutral sector concepts、ETF spot、provider-neutral
limit-up/down 和 Tushare news briefs，供 FNI 后续 scanner/report 直接消费。

FNI stock sector membership capability 已新增：

```text
POST /api/v1/market-data/stocks/sector-memberships
```

请求要求 `symbols` 和 `trade_date`，可选 `sector_types`、
`limit_per_symbol`、`force_refresh`、`sector_universe_limit`、
`timeout_seconds`、`upstream_timeout_seconds`。当前 Can-Do 支持 `concept` 反查：
gateway 通过 sector constituent provider 建立
`sector -> constituents -> symbol memberships` 反向索引，并把 rows 与
no-coverage markers 持久化到本地 SQLite。重复请求优先返回
`meta.cache.mode=cache`；没有 membership 的 symbol 会列入
`meta.coverage.missing_symbols`，上游扫描失败会返回 `meta.status=degraded`
和结构化 `meta.warning`，不会静默返回空成功。`sector_universe_limit=0`
只扫描 gateway seed boards，不会调用 broader sector universe；coverage 里
的 `board_results` 会区分 `upstream_failed`、`timeout`、`empty_board` 和
`symbol_uncovered`。

FNI fund profile / holdings capability 已新增：

```text
GET /api/v1/market-data/funds/profile?fund_code=161725
GET /api/v1/market-data/funds/holdings?fund_code=161725&limit=10
```

这两个 route 使用 provider-neutral normalized envelope。Gateway 先读本地
SQLite 表 `fund_profile_rows`、`fund_holding_rows` 和
`fund_holding_coverage`，cache miss 后优先请求 Tushare `fund_basic` /
`fund_portfolio`，再 fallback 到 EastMoney/Tiantian public fund position
endpoint。成功 rows 会保留 `source` / `provider` / `source_url` / `as_of_date`
语义；两边都失败时返回 HTTP 200 degraded payload，并在
`meta.coverage.provider_attempts` 里列出 cache、Tushare、EastMoney 的尝试结果。
Gateway 不返回 mock holdings；参数错误仍然是 HTTP 400。

FNI narrative source lakehouse capability 已新增：

```text
GET /api/v1/market-data/source-events/official-filings?cik=0000320193&limit=20
GET /api/v1/market-data/source-events/official-disclosures?symbol=000001&start_date=2026-05-01&end_date=2026-06-01
GET /api/v1/market-data/source-events/news-context?src=sina&start_datetime=2026-06-01%2009:00:00&end_datetime=2026-06-01%2010:00:00
GET /api/v1/market-data/source-events/social-heat?symbol=AAPL&enabled=true
POST /api/v1/market-data/source-events/news-permission-smoke
```

这些 route 统一返回 normalized envelope，并增加 `meta.source`、
`meta.provider_attempts`、`meta.degradation_events`、`meta.source_quality`、
`meta.trust_tier`、`meta.license_scope`、`meta.retention_policy`、
`meta.raw_storage_policy`、`meta.parser_version` 和 `meta.cache_hit`。
SEC EDGAR / CN disclosure metadata 标记为 `trusted_fact`；public news context
标记为 `context_only`；Stocktwits/community 数据标记为 `heat_signal_only`，
默认禁用，不能满足 trusted evidence 要求。

Gateway 当前在本地 SQLite 中创建 lightweight source lakehouse 表：
`source_registry`、`source_fetch_runs`、`source_documents`、`source_events`、
`evidence_spans`、`entity_mentions`、`resolved_entities`、
`source_quality_snapshots` 和 `source_blob_manifests`。本地开发如需
Postgres + MinIO runtime，可使用
`docker-compose.source-lakehouse.yml` 的 `source-lakehouse` profile；详见
`docs/runbooks/source-lakehouse-runtime.md`。FNI 仍只存 consumer artifacts 和
report outputs，不存 canonical raw upstream source data。

## Change Request 生命周期

上游 change request 使用文件生命周期管理：

- active request 放在 `docs/product/` 下。
- 已实现并通过验收的 request 移动到 `docs/product/archive/`。
- 归档文件顶部必须写明 `Archive status`、归档日期、实现提交或验收依据。
- 新需求不要继续追加到已归档文件；新建一个 active request 文件。
- 如果新需求继承旧需求的后续问题，在新文件里用 `Supersedes` 指向旧归档文件。

## 上游提出更新要求时应包含的信息

上游服务如果需要新增或修改数据能力，请按下面格式提需求。

### 1. 业务用途

说明这个数据将用于哪个上游流程：

- 页面/报告/模型/回测/扫描器名称
- 是否阻塞主流程
- 缺数据时允许 fallback、partial 还是必须 hard fail

示例：

```text
FNI financial_metrics 希望新增 balance sheet 的 debt/equity 字段，用于生成资产负债风险信号。缺失时允许 partial，但不能 fallback 到 mock。
```

### 2. Provider 与 Endpoint

说明希望使用的外部数据源：

- provider：`tushare`、`akshare`、`eastmoney` 或新 provider
- endpoint/API 名称
- 官方文档链接或现有调用样例
- 是否需要付费权限

示例：

```text
provider=tushare
endpoint=balancesheet
params={"ts_code": "..."}
fields=ts_code,ann_date,end_date,total_assets,total_liab,total_hldr_eqy_exc_min_int
```

### 3. 参数与字段

必须列出：

- 必填参数
- 可选参数
- 字段列表
- 字段类型
- 单位
- 日期格式
- 是否可能返回 `NaN`、空字符串、`--`

如果上游只需要字段子集，也要写清楚完整默认字段和上游投影字段。网关通常会缓存默认字段，再按上游 `fields` 投影。

### 4. 缓存语义

必须明确：

- `instrument_id` 应该是什么
- `date_key` 应该是什么
- 是否是 range 请求
- 是否允许 `latest`
- 字段变化是否需要 bump `schema_version`
- 是否允许 negative cache 或 provisional no-data

示例：

```text
instrument_id=ts_code
date_key=end_date
date_key_role=report_period
range_start_param=start_date
range_end_param=end_date
schema_version=1
```

### 5. Freshness 与刷新

说明：

- 接受多旧的数据
- 是否需要强制刷新能力
- 是否需要定时 backfill
- 是否需要按交易日补齐

当前 gateway 主要是 read-through cache；强制刷新和 TTL 还不是稳定对外合约。

### 6. 验收标准

每个更新要求必须给出可自动验证的验收标准：

- fake provider 测试应该覆盖什么
- live validation 检查哪些 endpoint
- 上游 acceptance 如何证明没有 fallback
- row count 最低要求
- source_url/provider_name/data_quality 期望值

示例：

```text
Acceptance:
- `uv run pytest` 通过，coverage >= 80%
- `RUN_LIVE_PROVIDER_TESTS=1 uv run market-gateway-validate --live` 通过
- FNI `tushare-primary` 中 financial_metrics provider_name=tushare-financial-metrics
- source_url=http://127.0.0.1:8700/tushare
- metrics row_count > 0
```

## 常见更新类型

### 新增 Tushare endpoint

需要改动：

- `stock_data_gateway/policies/tushare.py`
- fake provider fixtures
- facade/cache tests
- live validation 或专门 acceptance
- README/runbook 如涉及上游消费方式变化

### 扩展已有 endpoint 字段

需要改动：

- 扩大 `default_fields`
- bump 对应 `schema_version`
- 增加投影测试，证明旧缓存不会污染新字段
- 视情况清理旧 endpoint cache

### 新增非 Tushare provider

需要改动：

- 新 provider adapter
- provider-neutral policy
- provider health check
- cache key 语义
- 上游选择 provider 的 routing 规则
- live validation 的权限与失败语义

### 新增上游 acceptance

需要改动：

- `stock_data_gateway/cli/fni_acceptance.py` 或新增上游专用 acceptance CLI
- artifact parser
- source_url/provider_name/data_quality 断言
- runbook

## 当前已知边界

- Gateway 当前是本地服务，不是公网多租户服务
- 真实 provider 目前主要是 Tushare
- 调用方 token 被忽略，真实 token 只从 gateway 环境读取
- 目前没有稳定的强制刷新 HTTP API
- Cache clear 是本地 CLI，不是 HTTP API
- `real-enriched` acceptance 主要验证 FNI 完整真实链路，不代表所有 Tushare endpoint 都被该验收覆盖

## 推荐变更请求模板

```text
Title:

Upstream:

Business workflow:

Provider / endpoint:

Request params:

Required fields:

Field units and null semantics:

Cache semantic key:

Freshness / refresh expectations:

Fallback policy:

Acceptance checks:

Sample expected response:

Operational notes:
```
