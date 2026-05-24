# Local Market Data Gateway 项目提示词

你现在要创建并实现一个独立项目：`local-market-data-gateway`。

这个项目不是某一个数据源的简单代理，也不是只服务 Tushare 的缓存工具。它是一个面向多个本地项目复用的本地市场数据网关，目标是在所有外部数据源前面建立统一的本地读取、缓存、回填、限流、日志和稳定性验证层。

## 架构师审核结论

结论：计划可以启动，但第一阶段必须按“迁移优先、缓存语义优先、接口兼容优先”的顺序执行。不要先做抽象完美的数据平台，也不要只做一个简单 Tushare 代理。

批准的第一阶段架构决策：

- 以 Cost-Basis-Trading 已有 Tushare client、测试和 cache 设计作为迁移基线，先保住已验证行为
- `POST /tushare` 是第一阶段唯一对调用方承诺稳定的 facade；其他统一 provider API 可以实现，但不作为稳定接入面
- normalized request hash 只能作为请求审计和 facade cache 辅助键，不能作为数据覆盖判断的唯一依据
- cache 的事实来源是 endpoint policy 产生的 semantic coverage key，例如 `provider + endpoint + instrument_id + date_key + semantic_params_hash`
- 第一阶段默认单机、单进程、SQLite WAL、一个 writer；不承诺多 worker、多机或局域网并发一致性
- payload store 先通过接口抽象；MVP 可以优先 SQLite JSON payload，Parquet/file-backed payload 作为大数据量优化，不阻塞首个闭环
- fake provider / mock client 测试必须先跑通，真实 Tushare live validation 必须显式 opt-in
- `cyq_chips` 是第一阶段重点，但应在 `trade_cal`、`daily`、`stock_basic` 的基础 cache 路径跑通后再做完整断点补拉

架构风险和对应控制：

- 风险：把 request cache 当 dataset cache。控制：所有 endpoint 必须先定义 coverage key、date_key、semantic params 和 payload shape
- 风险：过早引入 Parquet、PostgreSQL、ClickHouse 或分布式 worker。控制：第一阶段只保留接口和目录边界，不引入重依赖
- 风险：Tushare 迁移后丢失已有 retry/限流/脱敏行为。控制：先迁移 Cost-Basis 测试，再迁移实现
- 风险：不同 `fields` 请求导致重复抓取。控制：存 canonical payload，响应层做 projection；只有字段真实改变 provider 语义时才进入 semantic params
- 风险：并发 miss 击穿 provider。控制：SQLite lease / single-flight，且默认运行一个 uvicorn worker
- 风险：空数据被错误永久缓存。控制：`PROVISIONAL_NO_DATA` 必须有 TTL，`PERMANENT_NO_DATA` 只能用于可证明永久缺失

## 迁移基线：必须基于已有 Cost-Basis-Trading 工作

这个项目不是从零实现。必须先阅读并复用下面这个已有项目里的真实代码、测试和设计文档：

```text
/Users/mikezhang/Coding/AI-Learning/Cost-Basis-Trading
```

已有工作包括：

- `/Users/mikezhang/Coding/AI-Learning/Cost-Basis-Trading/backend/app/data/tushare_client.py`
  - 已有 `TushareMarketDataClient`
  - 已有 `TUSHARE_TOKEN` 环境变量读取
  - 已有 Tushare Python SDK lazy loading: `ts.pro_api(self.token)`
  - 已有 `trade_cal` 交易日解析
  - 已有 `stock_basic` 股票名称解析
  - 已有 `cyq_chips` 筹码分布抓取
  - 已有 `daily` 日线价格抓取
  - 已有 `_call_tushare(...)` 统一调用边界
  - 已有 retry、rate limit pacing、错误映射、错误脱敏和 retry event handler
- `/Users/mikezhang/Coding/AI-Learning/Cost-Basis-Trading/backend/tests/test_tushare_client.py`
  - 已有 Tushare client 单元测试
  - 覆盖 `cyq_chips` 按交易日查询
  - 覆盖 rate limit pacing
  - 覆盖 transient retry
  - 覆盖权限错误不重试
  - 覆盖中文限流错误识别
  - 覆盖错误信息脱敏
- `/Users/mikezhang/Coding/AI-Learning/Cost-Basis-Trading/backend/app/core/config.py`
  - 已有本地环境文件加载逻辑
- `/Users/mikezhang/Coding/AI-Learning/Cost-Basis-Trading/backend/app/api/routes.py`
  - 已有 FastAPI API 结构
  - 当前暴露的是业务 API：`/api/health`、`/api/scans`、`/api/backtests`、`/api/research-runs`
  - 注意：这些不是目标 gateway API，只能作为 FastAPI 组织方式参考
- `/Users/mikezhang/Coding/AI-Learning/Cost-Basis-Trading/docs/design/local-market-data-cache.md`
  - 已有本地 market data cache 设计
  - 包含 semantic key、SQLite schema、payload version、negative cache、stale/refresh 策略
- `/Users/mikezhang/Coding/AI-Learning/Cost-Basis-Trading/docs/design/a-share-chip-backfill-agent.md`
  - 已有 A 股 `cyq_chips` 长期回填 agent 设计
  - 包含 planner、worker、job lease、rate limit、PostgreSQL control plane、ClickHouse analytic storage 等后续方案
- `/Users/mikezhang/Coding/AI-Learning/Cost-Basis-Trading/docs/references/tushare-data-contract.md`
  - 已有 Tushare 数据契约
  - 明确 `cyq_chips`、`daily`、`trade_cal`、`stock_basic` 的字段、错误模型和查询规则
- `/Users/mikezhang/Coding/AI-Learning/Cost-Basis-Trading/docs/SECURITY.md`
  - 已有 token 与错误信息安全约束
- `/Users/mikezhang/Coding/AI-Learning/Cost-Basis-Trading/docs/RELIABILITY.md`
  - 已有 Tushare 可用性、权限、更新时间和限流风险说明

实现时必须优先迁移和改造这些已有能力，而不是重新写一套 Tushare 客户端。

## 复用边界

必须复用或迁移：

- `TushareMarketDataClient` 的 Tushare SDK 调用边界
- `cyq_chips` 按交易日拆分请求的实现思想
- `daily`、`trade_cal`、`stock_basic` 的现有访问能力
- retry、rate limit、错误映射、错误脱敏和 retry event logging
- `ChipDistributionPoint`、`DailyPriceBar` 等已验证的数据模型，必要时改造成 provider-neutral model
- `backend/tests/test_tushare_client.py` 中的测试用例，作为新项目的回归测试起点
- `local-market-data-cache.md` 中的 cache key、payload version、negative cache、SQLite-first 思路
- `a-share-chip-backfill-agent.md` 中的长期回填 agent 思路，但第一版不要直接上复杂分布式架构

不要直接迁移：

- Cost-Basis 的策略扫描业务逻辑
- `/api/scans`、`/api/backtests`、`/api/research-runs` 的业务语义
- 前端 UI
- DeepSeek/AI research agent
- 任何策略、打分、交易建议或回测逻辑

新项目要保留的是“数据源访问和本地缓存基础设施”，不是 Cost-Basis 的业务产品。

## 迁移盘点要求

正式实现前，必须先审计 Cost-Basis-Trading 中相关代码、测试和设计文档，形成迁移清单。不要跳过这一步直接创建全新实现。

迁移清单至少要把已有资产分成四类：

- 直接迁移：可以基本原样搬到新项目的代码、测试或配置
- 改造迁移：需要改造成 provider-neutral 结构后再进入新项目的能力
- 设计复用：只复用设计思想、约束或测试场景，不直接复制代码
- 明确不迁移：属于 Cost-Basis 业务产品的扫描、回测、AI research、前端或策略逻辑

第一批测试应以 Cost-Basis 已验证行为作为迁移契约。至少覆盖 token 脱敏、中文限流错误识别、transient retry、权限错误不重试、rate limit pacing、`cyq_chips` 按交易日请求和已缓存交易日跳过。

迁移清单建议落到新项目文档中，例如：

```text
docs/migration/cost-basis-migration-inventory.md
```

迁移清单格式建议：

```text
| Asset | Source Path | Decision | Target Path | Notes |
|-------|-------------|----------|-------------|-------|
| TushareMarketDataClient | Cost-Basis.../tushare_client.py | 改造迁移 | app/providers/tushare/client.py | 保留 retry/rate limit/error redaction |
| test_tushare_client.py | Cost-Basis.../tests/test_tushare_client.py | 改造迁移 | tests/providers/test_tushare_client.py | 作为迁移契约 |
| /api/scans | Cost-Basis.../routes.py | 明确不迁移 | N/A | 业务扫描接口 |
```

## 背景

我有多个本地投资研究项目会调用外部市场数据源，包括但不限于：

- Tushare
- AkShare
- EastMoney
- 未来可能接入的其他公开或付费数据源

这些项目不应该各自直接频繁访问外部数据源。所有外部数据访问都应优先经过这个本地数据网关：

```text
local project -> local-market-data-gateway -> local cache / external provider
```

网关内部负责判断：

- 本地 cache 是否命中
- 数据是否完整
- 是否可以返回 stale cache
- 是否需要外部补拉
- 是否需要异步回填
- 是否需要限流、重试、降级或返回明确错误
- 外部结果是否需要写回本地

调用方不应该关心 cache 命中逻辑，也不应该直接实现外部数据源限流逻辑。

## 核心目标

构建一个生产导向的本地市场数据服务，优先解决以下问题：

- 降低外部 API 调用频率
- 避免重复抓取相同数据
- 支持长期历史数据回放
- 支持离线测试
- 支持多项目共享同一份本地市场数据
- 隔离不同外部数据源的不稳定性
- 统一日志、限流、重试、错误模型和健康检查
- 为后续市场宽度、板块轮动、ETF flow、新闻层和叙事引擎提供可靠数据基础

这个项目当前不是交易系统，不做预测，不实现策略引擎，不做 AI 判断。

## 第一阶段能力边界

第一阶段交付的是一个本地市场数据网关能力，而不是一个完整数据平台。完成后，本地研究项目应能把 Tushare HTTP 调用切到 `http://127.0.0.1:8700/tushare`，并由网关负责本地 cache、外部补拉、retry/rate limit、错误映射、日志和 token 管理。

主要 actor：

- 调用方本地项目：只提交数据请求，不持有外部 provider token，不决定 cache 策略
- gateway API：暴露兼容 facade 和必要的健康检查接口
- provider adapter：封装 Tushare、AkShare、EastMoney 等外部数据源差异
- cache/runtime layer：负责 coverage 判断、read-through cache、negative cache、offline mode 和审计日志
- operator：通过环境变量、日志和 validation CLI 观察服务状态

第一阶段固定约束：

- 只保证本地 `127.0.0.1` 使用，不承诺局域网多用户访问
- `POST /tushare` 是稳定兼容接口，`/providers/{provider}/query` 可以作为内部或实验接口
- 外部 token 只由 gateway 持有，调用方传入 token 仅用于兼容，不参与日志和 cache key
- 默认不允许调用方强制绕过 cache 直接打外部 provider；refresh 行为由 endpoint policy 控制
- 不引入 PostgreSQL、ClickHouse、分布式 worker 或复杂 job control plane 作为第一阶段必需依赖
- 不迁移 Cost-Basis 的策略扫描、回测、AI research agent 或前端业务能力

## 非绑定单一数据源原则

这个网关必须被设计成多数据源网关，而不是 Tushare 专用服务。

Tushare 只是第一批优先适配的数据源，因为当前已有一部分长期运行的 Tushare 抓取 agent 和筹码分布数据抓取能力可以迁移复用。

注意：第一阶段可以先把 Tushare 迁移出来跑通，但抽象层、cache key、日志字段、错误模型和 API 路由必须从第一天起支持多 provider。不要把项目命名、目录结构、表结构或核心类设计成 `tushare-only`。

架构上必须支持以下 provider 形态：

```python
class ExternalDataProvider:
    provider_name: str

    def fetch(self, endpoint: str, params: dict, fields: str | None = None) -> ProviderResponse:
        ...

    def health_check(self) -> ProviderHealth:
        ...
```

第一阶段可以先实现 Tushare provider，但代码结构不能把 cache key、API 路由、存储表、日志字段、错误模型写死为 Tushare 专属。

建议统一数据访问键：

```text
provider + endpoint + normalized_params_hash + fields_hash + version
```

示例：

```text
tushare + daily + hash(ts_code,start_date,end_date) + hash(fields) + v1
akshare + stock_zh_a_hist + hash(symbol,period,start_date,end_date,adjust) + hash(fields) + v1
eastmoney + etf_flow + hash(symbol,date_range) + hash(fields) + v1
```

注意：上述 key 适合表示一次规范化请求，但不能替代 endpoint-level 的数据覆盖模型。为了支持 partial hit、区间补拉、offline replay 和跨请求复用，必须为每个 endpoint 建立 cache policy / endpoint registry。

每个 endpoint 至少声明：

- provider endpoint 名称
- 参数 schema 和规范化规则
- 业务主键、分区键和日期字段
- 日期区间拆分方式
- 默认 TTL / freshness 策略
- 是否允许 stale cache
- 是否允许 negative cache，以及 `PROVISIONAL_NO_DATA` 的过期策略
- schema version / payload version
- Parquet 或 raw payload 的存储布局
- 外部请求 pacing、retry 和 timeout 策略

示例：

```text
daily:
  coverage_key: provider + endpoint + ts_code + trade_date
  date_field: trade_date
  range_params: start_date/end_date
  storage_grain: one row per ts_code + trade_date

cyq_chips:
  coverage_key: provider + endpoint + ts_code + trade_date
  date_field: trade_date
  split_strategy: one external request per trade_date
  no_data_policy: recent empty result -> PROVISIONAL_NO_DATA

stock_basic:
  coverage_key: provider + endpoint + snapshot_date/schema_version
  freshness: low-frequency snapshot
```

内部存储应优先保存 canonical/superset 数据，再按请求的 `fields` 做响应 projection。不要因为不同调用方请求字段不同，就重复抓取和存储同一批底层数据。只有当外部 provider 的字段选择真实影响权限、成本或返回语义时，才把 `fields_hash` 作为外部 fetch cache 的强维度。

## 建议项目结构

新项目可以采用下面的结构。具体文件名可以按实际框架调整，但模块边界不要混淆：

```text
local-market-data-gateway/
  app/
    main.py
    core/
      config.py
      logging.py
      errors.py
    api/
      health.py
      tushare_facade.py
      provider_query.py
    providers/
      base.py
      tushare/
        client.py
        adapter.py
        errors.py
      akshare/
        adapter.py
      eastmoney/
        adapter.py
    policies/
      registry.py
      tushare.py
    cache/
      index.py
      coverage.py
      payload_store.py
      negative_cache.py
      read_through.py
    jobs/
      cyq_chips_backfill.py
    cli/
      validate.py
      backfill.py
  tests/
    providers/
    policies/
    cache/
    api/
    cli/
  docs/
    migration/
    design/
```

结构要求：

- `api/` 只负责 HTTP shape、request parsing 和 response formatting，不直接知道 Tushare SDK 细节
- `providers/` 只负责外部数据源调用和 provider-specific error mapping，不直接决定全局 cache 策略
- `policies/` 声明 endpoint 的参数、coverage、TTL、schema version 和 storage grain
- `cache/` 负责 read-through、coverage 判断、payload 持久化、negative cache 和 stale 选择
- `jobs/` 只放回填或长任务逻辑，不放同步 API facade 的核心路径
- `cli/` 用于 live validation、backfill 和调试，不作为调用方项目的稳定依赖

## 核心执行路径

同步 API 请求必须走同一条 read-through pipeline，避免每个 endpoint 自己实现 cache 和 provider 逻辑。

```text
HTTP facade
  -> parse and validate request
  -> resolve provider + endpoint policy
  -> normalize params and requested fields
  -> build coverage plan
  -> read cache current entries
  -> classify keys: hit / stale / provisional_no_data / permanent_no_data / miss / refresh_required
  -> acquire fetch lease for miss/refresh keys
  -> call provider only for leased missing keys
  -> normalize provider payload to canonical rows
  -> write payload versions + current entries in SQLite transaction
  -> release lease
  -> merge cached and fetched payloads
  -> project requested fields
  -> format provider-compatible response
  -> write request audit log
```

Cache entry lifecycle:

```text
missing
  -> fetched_rows
  -> current_hit
  -> stale_hit
  -> refresh_required
  -> fetched_rows | provider_failed_return_stale | provider_failed_error

missing
  -> provider_empty
  -> provisional_no_data | permanent_no_data

current_hit
  -> provider_correction_detected
  -> new_version_current + old_version_superseded
```

Request outcome taxonomy:

- `cache_hit`: all requested coverage keys satisfied by current cache entries
- `partial_hit`: some keys hit, missing or refresh-required keys fetched externally
- `external_fetch`: no usable cache entries existed before provider call
- `stale_cache`: provider unavailable or refresh failed, endpoint policy allows stale return
- `negative_cache_hit`: endpoint policy permits returning no-data marker
- `offline_miss`: offline mode blocks provider call and cache cannot satisfy request
- `provider_error`: external provider failed and no usable stale cache exists
- `schema_changed`: provider response does not match endpoint policy contract

## API 设计原则

为了降低现有项目接入成本，网关应该优先提供“外部接口兼容”的 facade。

例如 Tushare：

外部调用原本可能是：

```text
POST https://api.tushare.pro
```

本地调用应可以变成：

```text
POST http://127.0.0.1:8700/tushare
```

请求体保持 Tushare Pro HTTP 风格：

```json
{
  "api_name": "daily",
  "token": "optional-or-ignored-by-local-gateway",
  "params": {
    "ts_code": "000001.SZ",
    "start_date": "20240101",
    "end_date": "20240131"
  },
  "fields": "ts_code,trade_date,open,high,low,close,vol,amount"
}
```

响应体保持 Tushare 风格：

```json
{
  "code": 0,
  "msg": "",
  "data": {
    "fields": ["ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"],
    "items": [
      ["000001.SZ", "20240131", 10.1, 10.5, 10.0, 10.3, 1000000, 10300000]
    ]
  }
}
```

可以额外返回 `meta`，但业务调用方不应该依赖 `meta` 做核心逻辑：

```json
{
  "meta": {
    "provider": "tushare",
    "endpoint": "daily",
    "source": "cache",
    "cache_hit": true,
    "stale": false,
    "fetched_external": false,
    "latency_ms": 12
  }
}
```

未来其他 provider 可以采用各自兼容接口，也可以额外提供统一内部接口：

```text
POST /providers/{provider}/query
```

但第一优先级是降低迁移成本，让已有项目通过替换 base URL 即可接入本地网关。

实现 Tushare facade 时，不要绕开 Cost-Basis 里已有的 `TushareMarketDataClient` 能力。正确做法是：

```text
POST /tushare
  -> parse Tushare HTTP-style request
  -> normalize provider + endpoint + params + fields
  -> read-through cache
  -> on miss call migrated TushareMarketDataClient / provider adapter
  -> persist response
  -> return Tushare-compatible response shape
```

Cost-Basis 现在已有的 `/api/scans` 是业务扫描接口，不是 gateway facade。不要让新项目依赖 `/api/scans` 来给其他项目供数。

Tushare facade 兼容细节：

- 成功响应必须保持 Tushare Pro HTTP shape：`code`、`msg`、`data.fields`、`data.items`
- `data.fields` 的顺序必须与调用方请求的 `fields` 一致；如果未传 `fields`，使用 endpoint policy 的默认字段顺序
- gateway 可以追加 `meta`，但调用方业务逻辑不得依赖 `meta`
- 调用方请求体里的 `token` 字段只用于兼容，不写入日志、不进入 cache key、不覆盖 gateway 自己的 `TUSHARE_TOKEN`
- provider 业务错误优先映射成 Tushare-compatible JSON error；明显 malformed request 可以返回 HTTP 400
- `NO_PERMISSION`、`RATE_LIMITED`、`SCHEMA_CHANGED`、`PROVIDER_UNAVAILABLE` 等错误必须同时进入结构化日志
- response projection 必须基于已存储的 canonical 数据生成，不得为了字段顺序或字段子集重复访问外部 provider

## Cache 策略

cache 命中、补拉和回写策略完全由网关决定。

调用方只表达“我要什么数据”，不表达“从 cache 拿还是从外部拿”。

网关至少支持：

- full hit: 直接返回本地完整数据
- partial hit: 本地已有部分数据，只补拉缺失区间
- miss: 外部拉取并写入本地
- stale hit: 外部不可用时可按策略返回旧数据
- negative cache: 明确记录某些日期或参数组合没有数据
- replay: 支持从本地历史数据重放
- offline mode: 禁止外部调用，只读本地

negative cache 需要区分：

- `PROVISIONAL_NO_DATA`: 可能是当天尚未发布、临时为空
- `PERMANENT_NO_DATA`: 该标的、日期或 endpoint 永久无数据

## 并发、一致性与写入安全

即使第一阶段只跑在本机，也要避免多个本地项目同时触发同一批外部请求。

最低要求：

- 对同一个 coverage key 或 normalized request key 做 single-flight / lightweight lease，避免并发 miss 重复打外部 provider
- cache index、negative cache 和 request audit 写入必须放在 SQLite transaction 中
- payload 文件写入必须采用临时文件加原子 rename；不要让半写入 Parquet/raw payload 被后续请求读取
- 外部 fetch 成功但 payload 持久化失败时，不得把 cache index 标记为 hit-ready
- stale cache 返回、external fetch、negative cache 写入和 refresh skip 都必须进入审计日志
- offline mode 下不得创建外部 fetch lease，不得执行 provider health check 或任何外部请求
- retry 只包 provider 调用，不应该重复执行已经成功落盘的写入步骤

## 存储建议

第一阶段优先简单、可靠、可调试：

- SQLite: 存储 cache index、请求元数据、negative cache、任务状态
- Parquet: 存储大批量表格数据
- local filesystem: 存储原始响应和调试快照

架构师建议的落地顺序：

1. Phase 1A 先采用 SQLite-first：`current_entries`、`entry_versions`、`fetch_leases`、`request_audit`、`provider_events`、`cache_conflicts`
2. Phase 1A payload 可以先存 SQLite JSON，尤其是 `daily`、`trade_cal`、`stock_basic` 和按日拆分后的 `cyq_chips`
3. 同时保留 `PayloadStore` 接口，支持后续把大 payload 切到 Parquet 或 filesystem
4. Phase 1B 再启用 file-backed payload：SQLite 存 pointer/checksum/schema version，Parquet 或 raw JSONL 存实际大表
5. 不要在第一版同步 API 路径里引入异步 outbox，除非实际 payload 写入已经成为瓶颈

建议 SQLite 核心表：

```text
market_cache_current_entries
market_cache_entry_versions
market_cache_fetch_leases
market_cache_request_audit
market_cache_provider_events
market_cache_conflicts
```

表职责：

- `market_cache_current_entries`: 每个 semantic key 当前可读版本
- `market_cache_entry_versions`: 不可变 payload 版本，支持 provider correction
- `market_cache_fetch_leases`: 防止并发 miss 重复打外部 provider
- `market_cache_request_audit`: 每次 facade/provider query 的请求审计
- `market_cache_provider_events`: retry、rate-limit sleep、provider error、schema changed 等事件
- `market_cache_conflicts`: 同 semantic key 返回不同 checksum 时的冲突记录

后续数据量变大后再升级：

- PostgreSQL: 任务控制、job lease、provider 状态
- ClickHouse: 高频、大规模分析型数据
- DuckDB: 本地分析查询

不要一开始就过度引入复杂基础设施。

## 第一阶段必须支持的数据源与 endpoint

优先实现 Tushare provider：

- `daily`
- `daily_basic`
- `index_daily`
- `fund_daily`
- `stock_basic`
- `trade_cal`
- `cyq_chips`

其中 `cyq_chips` 是重点迁移对象，需要复用已有长期抓取 agent 的思想：

- 按交易日拆分请求
- 避免一次请求过大
- 支持断点续跑
- 支持失败重试
- 支持本地已完成区间跳过
- 记录每次抓取的 endpoint、参数、耗时、行数、失败原因

`cyq_chips` 第一版具体迁移要求：

- 先复用 Cost-Basis 现有的“先查 `trade_cal`，再按每个 `trade_date` 调 `cyq_chips`”逻辑
- cache 粒度优先采用 `provider=tushare + endpoint=cyq_chips + ts_code + trade_date + fields/schema_version`
- 已缓存交易日不得再次访问外部 Tushare，除非 refresh 策略明确要求
- 对最近交易日空数据使用 `PROVISIONAL_NO_DATA`
- 不得 forward-fill 或编造筹码分布数据
- 必须保留每次外部调用的 retry/rate-limit 事件
- 必须把 Cost-Basis 现有 `test_tushare_client.py` 里的相关测试迁移到新项目

第二阶段再接入：

- AkShare A 股历史行情
- AkShare ETF 数据
- AkShare 板块/概念数据
- AkShare 涨跌停统计
- EastMoney ETF flow
- EastMoney 主力资金流

## Provider 要求

每个 provider 必须有独立模块，不允许把多个数据源逻辑混在同一个类里。

每个 provider 至少支持：

- token 或配置加载
- 请求 pacing
- retry with backoff
- timeout
- health check
- endpoint-level stability record
- secret redaction
- structured logging
- standardized error mapping

统一错误模型建议：

```text
MISSING_TOKEN
NO_PERMISSION
EMPTY_DATA
PARTIAL_DATA
RATE_LIMITED
NETWORK_ERROR
INVALID_SYMBOL
SCHEMA_CHANGED
PROVIDER_UNAVAILABLE
UNKNOWN_ERROR
```

## 日志要求

每次请求必须记录：

- provider
- endpoint
- normalized params hash
- request time
- response time
- source: cache / external / stale_cache
- cache_hit
- row_count
- status
- retry_count
- failure_reason

日志中不得泄露 token、cookie、authorization header 或其他密钥。

## 安全要求

- 外部 provider token 由网关持有，不由调用方项目持有
- 调用方传来的 token 可以忽略或仅用于兼容，不应写入日志
- 不要把 secrets 写进代码、测试 fixture、cache 文件或报告
- 本地服务初期可以只监听 `127.0.0.1`
- 如果未来开放局域网访问，需要加入本地认证机制

## 运行配置

第一阶段应通过环境变量完成配置，默认值必须适合本地开发和离线测试。

建议配置：

```text
GATEWAY_HOST=127.0.0.1
GATEWAY_PORT=8700
MARKET_DATA_HOME=./data/market-data
GATEWAY_OFFLINE_MODE=false
GATEWAY_LOG_LEVEL=INFO
TUSHARE_TOKEN=...
TUSHARE_TIMEOUT_SECONDS=30
TUSHARE_MIN_REQUEST_INTERVAL_SECONDS=0.25
TUSHARE_MAX_RETRIES=3
```

配置要求：

- `.env` 可以用于本地开发，但 `.env`、真实 token、cookie、authorization header 不得提交
- 测试必须能在没有真实 `TUSHARE_TOKEN` 的情况下运行，通过 fake provider 或 mock client 覆盖主要行为
- live validation CLI 必须显式 opt-in 才能访问真实外部 provider，例如要求设置 `RUN_LIVE_PROVIDER_TESTS=1`
- cache 路径、日志路径和 raw payload 路径必须可以配置，避免不同项目互相污染测试数据

## 与现有项目的接入方式

当前 `fund-narrative-intelligence` 项目里，Tushare 调用应该改为可配置 base URL：

```text
TUSHARE_API_URL=https://api.tushare.pro
```

切换到本地网关时：

```text
TUSHARE_API_URL=http://127.0.0.1:8700/tushare
```

只要本地网关保持 Tushare HTTP response shape，调用方项目不需要知道数据来自 cache 还是外部 Tushare。

## 不要做的事

当前阶段不要实现：

- 交易系统
- 策略引擎
- AI 预测
- LLM 决策
- 浏览器自动化
- 代理池
- 反爬绕过
- 实时 websocket 行情系统
- tick 级别撮合或回测引擎

这个项目是数据基础设施，不是策略产品。

## 测试与验证策略

第一阶段必须以可离线运行的自动化测试为主，真实外部 provider 验证为显式 opt-in。

必须覆盖的测试类型：

- provider client 单元测试：迁移 Cost-Basis 现有 `test_tushare_client.py` 行为，覆盖 retry、rate limit、权限错误、中文限流错误和错误脱敏
- endpoint policy 测试：验证参数规范化、coverage key、默认字段、schema version、negative cache 策略
- cache 测试：第一次 miss 调 provider，第二次同请求必须 hit cache，不再访问外部 provider
- projection 测试：同一底层数据在不同 `fields` 请求下复用 canonical payload，只改变响应字段和顺序
- partial hit 测试：已缓存区间不得重复抓取，只补缺失交易日或缺失分区
- offline mode 测试：只读本地 cache，缺失时返回明确错误，不访问外部 provider
- negative cache 测试：`PROVISIONAL_NO_DATA` 可过期刷新，`PERMANENT_NO_DATA` 不被重复外部请求击穿
- 并发测试：多个同 key 请求同时 miss 时，fake provider 只被调用一次
- facade 测试：`POST /tushare` 请求和响应保持 Tushare-compatible shape
- secret safety 测试：token、cookie、authorization header 不出现在日志、错误、cache index、raw payload 路径或测试报告中

live validation CLI 应验证：

- 真实 Tushare token 存在时，能拉取一个小范围 `trade_cal`、`stock_basic`、`daily` 和 `cyq_chips`
- 同一请求第二次命中 cache，并报告外部调用次数为 0
- provider 临时失败或 fake failure 时，可返回 stale cache 或明确错误
- schema 字段与 `tushare-data-contract.md` 中的契约一致，变化时报告 `SCHEMA_CHANGED`

## 待确认决策

以下决策可以在第一版实现时采用保守默认值，但必须在代码或迁移清单中显式记录：

- 每个 endpoint 的 canonical field superset：先以 Cost-Basis 数据模型和 `tushare-data-contract.md` 为准
- stale cache TTL：先对历史交易日倾向长期有效，对最近交易日使用较短 TTL
- `PROVISIONAL_NO_DATA` 过期时间：先结合交易日历和最近交易日设置，不永久缓存当天空结果
- raw payload 是否长期保留：第一阶段保留用于调试，但要避免写入 token 和敏感 header
- `/providers/{provider}/query` 是否公开给调用方：第一阶段可作为内部实验接口，不作为稳定兼容承诺
- refresh 触发方式：第一阶段优先由 endpoint policy 和 validation/backfill CLI 控制，不让普通调用方随意绕过 cache

## 实施批次

### Milestone 0: 项目初始化与迁移盘点

产物：

- 初始化 Python/FastAPI 项目结构
- 建立 `docs/migration/cost-basis-migration-inventory.md`
- 审计 Cost-Basis-Trading 中 Tushare client、测试、cache 设计、security/reliability 文档
- 明确直接迁移、改造迁移、设计复用、明确不迁移

退出条件：

- 迁移清单完成
- 不迁移的 Cost-Basis 业务路由和前端能力被明确列入排除项
- 测试框架可以运行一个空测试或 smoke test

### Milestone 1: Tushare Client 迁移契约

产物：

- 迁移或改造 `TushareMarketDataClient`
- 保留 lazy SDK loading、`TUSHARE_TOKEN` 加载、retry、rate limit pacing、错误映射、错误脱敏、retry event handler
- 迁移 Cost-Basis `test_tushare_client.py` 的关键测试

退出条件：

- 无真实 token 情况下单元测试通过
- `cyq_chips` 仍然按交易日请求，不使用大范围 `start_date/end_date` 直接拉
- 权限错误不重试，rate limit / network error 可重试
- retry/provider event 不泄露 token 或 secret-like 字段

### Milestone 2: Endpoint Policy 与 Cache Core

产物：

- `ExternalDataProvider`、`ProviderResponse`、`ProviderHealth` 抽象
- `trade_cal`、`stock_basic`、`daily`、`cyq_chips` endpoint policy
- semantic coverage key、参数规范化、field projection 规则
- SQLite schema: current entries、entry versions、fetch leases、request audit、provider events、conflicts
- fake provider 驱动的 read-through cache

退出条件：

- miss -> provider fetch -> cache write -> second request hit cache
- 不同 `fields` 请求复用同一 canonical payload
- offline mode 缺失时返回明确错误，不能触发 provider
- 并发同 key miss 时 fake provider 只被调用一次

### Milestone 3: Tushare Facade API

产物：

- FastAPI `POST /tushare`
- health endpoint
- Tushare-compatible request/response shape
- 标准错误映射和结构化请求审计

退出条件：

- `POST /tushare` 支持 `trade_cal`、`stock_basic`、`daily`
- 成功响应包含 `code`、`msg`、`data.fields`、`data.items`
- 请求体 token 不进入 cache key、日志、错误或 audit payload
- malformed request 返回 HTTP 400；provider 业务错误返回 Tushare-compatible JSON error

### Milestone 4: `cyq_chips` 按交易日补拉

产物：

- `cyq_chips` coverage plan: `ts_code + trade_date`
- 先查 `trade_cal`，再按每个交易日判断 cache/miss
- 已缓存交易日跳过外部请求
- `PROVISIONAL_NO_DATA` 和 stale/refresh 策略
- 可选 backfill CLI 的最小版本

退出条件：

- 请求一个日期范围时只拉缺失交易日
- 最近交易日空结果不会被永久缓存
- 不 forward-fill、不编造筹码分布数据
- retry/rate-limit/provider events 可追踪到每个外部调用

### Milestone 5: Live Validation 与调用方接入

产物：

- live validation CLI
- `RUN_LIVE_PROVIDER_TESTS=1` 显式启用真实 Tushare 验证
- `fund-narrative-intelligence` 可通过 `TUSHARE_API_URL=http://127.0.0.1:8700/tushare` 接入

退出条件：

- 小范围真实 `trade_cal`、`stock_basic`、`daily`、`cyq_chips` 验证通过
- 同一 live 请求第二次报告外部调用次数为 0
- provider 临时失败时能按 policy 返回 stale cache 或明确错误
- README 或运行说明包含本地启动、offline mode、live validation 用法

### Milestone 6: 第二 provider 扩展准备

产物：

- AkShare 或 EastMoney 的空 adapter / fake adapter 验证
- 至少一个非 Tushare endpoint policy 通过 fake provider 测试

退出条件：

- 核心 cache/logging/error 模型没有 Tushare-only 命名
- 新 provider 不需要修改 Tushare facade 或 Tushare client
- 证明 provider-neutral 抽象足以承载第二数据源

## 推荐交付顺序

以 `Milestone 0` 到 `Milestone 6` 为准推进。不要跳过迁移盘点直接写新代码；不要在 `Milestone 2` 前实现复杂 facade；不要在 `Milestone 4` 前把 `cyq_chips` 做成长任务平台；不要在 `Milestone 6` 前接真实第二 provider。

最小可启动工作顺序：

1. Milestone 0: 项目初始化与迁移盘点
2. Milestone 1: Tushare client 迁移契约
3. Milestone 2: endpoint policy 与 cache core
4. Milestone 3: Tushare facade API
5. Milestone 4: `cyq_chips` 按交易日补拉
6. Milestone 5: live validation 与调用方接入
7. Milestone 6: 第二 provider 扩展准备

## 验收标准

第一阶段完成后，应满足：

- `POST /tushare` 能兼容 Tushare Pro HTTP 请求/响应
- `daily`、`daily_basic`、`stock_basic`、`trade_cal`、`cyq_chips` 至少可用
- `cyq_chips` 复用了 Cost-Basis 已验证的按交易日拆分请求逻辑
- Cost-Basis 现有 Tushare client 测试已经迁移或等价重写，并且通过
- 已完成 Cost-Basis 迁移清单，并明确哪些资产直接迁移、改造迁移、设计复用或不迁移
- `trade_cal`、`stock_basic`、`daily`、`cyq_chips` 已有 endpoint registry / cache policy 定义
- 同一请求第二次不再访问外部 Tushare
- 同一底层数据不会因为不同 `fields` 请求被无意义重复抓取或重复存储
- 外部 Tushare 临时失败时，可按策略返回本地已有数据或明确错误
- 所有请求都有结构化日志
- token 不出现在日志、报告或 cache 文件里
- 可以通过环境变量切换官方 Tushare 和本地网关
- 代码结构允许继续添加 AkShare、EastMoney 等 provider
- 新项目没有引入 Cost-Basis 的策略扫描、回测、AI research agent 或前端业务逻辑

最终目标是形成一个通用的本地市场数据基础设施，让所有研究项目优先使用本地数据网关，外部数据源只作为网关内部的补数来源。
