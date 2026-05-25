# Prompt: Implement FNI Breadth-Scale Gateway Job Operations

你现在在项目：

```text
/Users/mikezhang/Coding/AI-Learning/stock-data-gateway
```

目标不是重新设计数据平台，也不是做交易策略。目标是基于现有 `stock-data-gateway`，继续补齐 FNI 下一阶段验证需要的 gateway job operations。

## 先读这些文档

请先阅读：

```text
docs/product/upstream-consumption-and-change-guide.md
docs/product/fni-breadth-scale-job-ops-change-request-2026-05-25.md
docs/product/archive/fni-large-scan-async-job-change-request-2026-05-25.md
```

FNI 侧的验收证据在：

```text
/Users/mikezhang/Coding/AI-Learning/fund-narrative-intelligence/outputs/data_capabilities/gateway_async_rerun_summary_2026-05-25.md
/Users/mikezhang/Coding/AI-Learning/fund-narrative-intelligence/outputs/data_capabilities/gateway_breadth_scale_probe_summary_2026-05-25.md
/Users/mikezhang/Coding/AI-Learning/fund-narrative-intelligence/outputs/data_capabilities/gateway_daily_bars_500_coverage_analysis_2026-05-25.md
```

## 当前已知状态

已经完成并通过：

- `/api/health`
- normalized REST routes
- Tushare facade
- AkShare/EastMoney fallback
- daily-bars async job
- FNI 500-symbol, 5-day integrated stress：`3088` rows，`0` failures

仍然阻塞：

- breadth scan 需要更宽 lookback window，冷缓存时 job 很慢且难管理。
- 现在没有 job list、cancel、持久化、ETA、throughput、coverage explanation。
- FNI 不应该继续靠调大 timeout 或手动生成很多 100-symbol jobs 来解决。

## 本轮要做的事

优先实现下面这些能力。按小步提交、测试驱动来做。

### 1. Job List Endpoint

新增：

```text
GET /api/v1/market-data/jobs
```

至少支持：

- 按 status 过滤
- 按 job type 过滤
- 返回 running/completed/cancelled/failed jobs

每个 job 至少返回：

- `job_id`
- `job_type`
- `status`
- `requested_symbols`
- `completed_symbols`
- `failed_symbols`
- `rows_available`
- `created_at`
- `started_at`
- `updated_at`
- `cache.mode`
- 是否 active/running

### 2. Job Cancel Endpoint

新增：

```text
POST /api/v1/market-data/jobs/{job_id}/cancel
```

要求：

- cancel 是幂等的。
- running job 被标记为 `cancelled`。
- worker 应在当前 symbol 或当前小批次结束后停止。
- 已经抓到的 rows 仍可通过 rows endpoint 读取。
- cancel 不清 cache。

### 3. Job State Persistence

现在 job state 在内存里。需要持久化到 SQLite 或现有 cache store 中。

最低要求：

- completed job 的 status 和 summary 在 gateway 重启后仍可查询。
- running job 如果 gateway 重启，恢复为结构化的 `interrupted`、`failed` 或可 resume 状态。
- 已抓取 rows 仍可通过 cache 或 job rows endpoint 找回。

不要一次做复杂分布式队列。先做本地可靠持久化。

### 4. Progress Metadata

扩展 job status，增加：

- `cache_hit_symbols`
- `upstream_fetch_symbols`
- `stale_cache_symbols`
- `current_symbol`
- `current_batch_index`
- `batch_count`
- `symbols_per_minute` 或最近吞吐
- `last_progress_at`
- `last_error`

目标是让 FNI 能区分：

- 正常慢
- 卡住
- 上游限流
- cache 命中
- 外部补拉

### 5. Coverage Explanation

daily-bars job 完成后，要能解释 missing symbol-date pairs。

建议在 job status 或 rows meta 中返回：

```json
{
  "coverage": {
    "expected_pairs": 2500,
    "returned_pairs": 2488,
    "missing_pairs": 12,
    "missing_reasons": [
      {
        "symbol": "000004.SZ",
        "trade_date": "2026-05-22",
        "reason": "no_provider_row"
      }
    ]
  }
}
```

reason 可以先用简单枚举：

- `no_provider_row`
- `suspended_or_no_trade`
- `provider_error`
- `schema_error`
- `not_requested`
- `unknown`

先做到结构稳定，不要求一次性完美判断所有停牌原因。

### 6. Breadth Window Job

新增一个更贴近 FNI 的 job：

```text
POST /api/v1/market-data/jobs/breadth-window
```

请求示例：

```json
{
  "provider": "tushare",
  "symbols": ["000001.SZ", "000002.SZ"],
  "end_date": "2026-05-22",
  "lookback_trading_days": 20,
  "include_turnover": true,
  "mode": "read_through_cache",
  "allow_stale": true
}
```

gateway 负责：

- 解析交易日历
- 得到 start/end/trade_dates
- 复用 daily-bars job 的抓取逻辑
- 统一 job lifecycle
- 输出 rows 和 coverage summary

FNI 不应该自己拼很多 daily-bars job 来完成一个 breadth scan。

## 非目标

不要实现：

- proxy rotation
- browser automation
- CAPTCHA 绕过
- anti-detect
- tick-level websocket
- trading strategy
- AI prediction

本轮只做本地数据网关的可靠 job operations。

## 验收标准

gateway 项目内至少通过：

```bash
uv run ruff check .
uv run pytest
```

并补充 API/manager tests，覆盖：

- create/list daily-bars job
- cancel running job
- cancel 幂等
- rows endpoint 在 cancelled job 下仍可读 partial rows
- completed job status persistence
- interrupted/restart 状态处理
- coverage summary shape
- breadth-window job create/status/rows

FNI 验收目标：

1. 500-symbol, 20-trading-day breadth-window job 能快速创建。
2. `GET /api/v1/market-data/jobs` 能看到 running job。
3. job status 能显示 progress、cache/upstream counts、last_progress_at。
4. cancel 后 partial rows 可读。
5. 同语义 job recreate 能 idempotent 或 resume。
6. completed rows 有 coverage summary。
7. `/api/health` 在 job 运行期间保持 HTTP `200`。

## 实现原则

- 优先复用现有 `DailyBarsJobManager`、cache service、policy registry，不要重造 provider 层。
- 如果需要抽象 job store，就让 daily-bars 和 breadth-window 共用。
- API response shape 保持和现有 normalized envelope 一致。
- 错误要结构化，不要空成功。
- 所有新接口都要有测试。
- 改完后更新 `docs/product/upstream-consumption-and-change-guide.md`。

完成后请把实现 commit、测试结果、FNI acceptance 指令写回本项目文档，并把已完成的 active request 移到 `docs/product/archive/`，归档头部写明 status、日期、commit、验收证据。
