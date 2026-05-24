# Upstream Consumption and Change Guide

本文档面向 `stock-data-gateway` 的上游消费方，说明当前已经完成的能力、稳定消费合约、验收方式，以及以后新增数据能力时应该如何提出更新要求。

## 当前完成的能力

`stock-data-gateway` 现在是一个本地优先的市场数据网关：

- 提供本地 HTTP facade：`POST http://127.0.0.1:8700/tushare`
- 对上游保持 Tushare 官方请求/响应形状，便于已有 Tushare 调用迁移
- 网关自己持有真实 `TUSHARE_TOKEN`，不会信任或转发调用方传入的 token
- 使用 SQLite 做 read-through cache，避免上游服务重复打外部数据源
- 支持 fake validation、live provider validation、FNI gateway acceptance
- 提供 cache inspect/audit/clear 运维命令
- 已验证 `fund-narrative-intelligence` 可以通过 HTTP 消费本服务，不需要 Python import 或跨项目运行时依赖

已经提交的关键里程碑：

- `0472575 feat: bootstrap stock data gateway`
- `0909805 docs: clarify live provider setup`
- `bb29a92 feat: support fni tushare gateway calls`
- `925e974 feat: add fni acceptance and cache ops`

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

## 当前支持的 Tushare Endpoint

当前真实 provider 是 Tushare。AkShare 和 EastMoney adapter 目前仍是 placeholder。

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

- `outputs/gateway_tushare_primary_20260525_011907`
- `outputs/gateway_real_enriched_20260525_011918`

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

