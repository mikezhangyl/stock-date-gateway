# FNI Breadth Scale Job Ops Change Request - 2026-05-25

Status: active request

Upstream: `fund-narrative-intelligence` (`FNI`)

Consumer workspace:

```text
/Users/mikezhang/Coding/AI-Learning/fund-narrative-intelligence
```

Supersedes follow-up work from:

```text
docs/product/archive/fni-large-scan-async-job-change-request-2026-05-25.md
```

## Summary

The daily-bars async job API solved the original 500-symbol, 5-day HTTP `504`
problem. FNI has now integrated that API and the warm/cache-path 500-symbol
stress passes.

The next blocker is operational scale for breadth-style scans. A MA20 breadth
scan needs a wider lookback window and quickly becomes a cold-cache warming
problem. FNI should not solve this by increasing client waits or by spawning
many independent batch jobs blindly. The gateway needs job operations that make
long-running cache warming observable, controllable, restart-safe, and
coverage-explainable.

## Current Evidence

FNI evidence files:

```text
outputs/data_capabilities/gateway_async_rerun_summary_2026-05-25.md
outputs/data_capabilities/gateway_breadth_scale_probe_summary_2026-05-25.md
outputs/data_capabilities/gateway_daily_bars_500_coverage_analysis_2026-05-25.md
outputs/breadth_scan/2026-05-25-gateway-sync-50-ma20/breadth_scan_report.md
outputs/data_capabilities/gateway_cold_cache_probe_2026-05-25.json
```

Passing baseline:

- 500-symbol, 5-day async integrated stress completed.
- `3088` total rows, `0` failures.
- Historical rows: `2488`.
- Daily rows: `498`.
- Sector rows: `102`.
- Gateway health remained HTTP `200`.

New scale probe findings:

- 500-symbol, 20-trading-day breadth scan did not complete within a useful
  interactive validation window.
- Its async job `daily-bars-04af10b93f553a5a` was still running at `106/500`
  symbols and `2098` rows after several minutes.
- 50-symbol, 20-trading-day synchronous breadth scan failed:
  - gateway request timed out
  - direct Tushare fallback timed out
  - AkShare fallback hit proxy/remote disconnect
- 1000-symbol, 5-day stress expansion was stopped early to avoid filling the
  gateway with many cold-cache jobs.
- A cold-cache 100-symbol, 5-day job eventually completed with `499` rows and
  `0` failures, confirming the issue is operational throughput/control rather
  than simple endpoint failure.

Coverage finding from accepted 500-symbol, 5-day job:

- expected symbol-date pairs: `2500`
- rows returned: `2488`
- missing pairs: `12`
- missing symbols: `000004.SZ`, `000518.SZ`, `000608.SZ`, `000638.SZ`
- likely explanation: no trading/suspension or provider no-data rows, but the
  gateway does not currently classify missing rows.

## Required Gateway Capabilities

### 1. Job Listing

Add an endpoint to list jobs:

```text
GET /api/v1/market-data/jobs
```

Minimum filters:

- provider
- endpoint or job type
- status
- created-after / updated-after

Minimum fields:

- `job_id`
- job type
- status
- requested symbols
- completed symbols
- failed symbols
- rows available
- created/started/updated timestamps
- cache mode summary
- active worker indicator

### 2. Job Cancellation

Add cancellation:

```text
POST /api/v1/market-data/jobs/{job_id}/cancel
```

Required behavior:

- terminal status becomes `cancelled`
- worker stops before starting the next symbol or batch
- already fetched rows remain retrievable
- cancellation is idempotent
- cancellation does not clear cache

### 3. Restart-Safe Job State

Persist job state outside process memory.

Minimum expectation:

- gateway restart does not lose completed job metadata
- running jobs recover as `interrupted`, `failed`, or resumable state
- rows already written to cache remain associated with the job

FNI can accept an `interrupted` status if it is structured and rows remain
readable.

### 4. Better Progress and Throughput Metadata

Job status should expose:

- cache hit symbol count
- upstream fetch symbol count
- stale-cache symbol count
- current symbol or current batch index
- symbols per minute or recent throughput
- estimated remaining symbols or ETA when possible
- last progress timestamp
- last error summary

This lets FNI distinguish healthy slow progress from a stuck worker.

### 5. Coverage Explanation

Rows endpoint or job status should explain missing symbol-date pairs.

Suggested shape:

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

Reason values can start simple:

- `no_provider_row`
- `suspended_or_no_trade`
- `provider_error`
- `schema_error`
- `not_requested`
- `unknown`

### 6. Breadth Window Cache-Warming Job

Daily-bars jobs are enough for basic data retrieval, but FNI breadth scans need
a higher-level cache-warming workflow:

```text
POST /api/v1/market-data/jobs/breadth-window
```

Minimum request:

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

The gateway should own:

- resolving trade calendar
- deduplicating and chunking symbols
- pacing upstream calls
- retry and stale-cache behavior
- coverage summary
- resumable row retrieval

FNI should not have to create dozens of independent 100-symbol jobs for a
single breadth scan.

## Acceptance Checks

FNI acceptance should prove:

1. A 500-symbol, 20-trading-day breadth-window job can be created quickly.
2. `GET /jobs` shows the job while it is running.
3. Job status progresses with cache/upstream counts and last-progress timestamp.
4. The job can be cancelled and returns partial rows.
5. Recreating the same semantic job is idempotent or resumes compatible work.
6. Completed rows include coverage summary with missing-pair reasons.
7. Gateway health remains HTTP `200` while jobs are running.

Secondary acceptance:

1. A 1000-symbol, 5-day daily-bars cold-cache job completes or returns
   `completed_with_failures` with structured failures.
2. Full A-share, 1-day job can be submitted without FNI manually spawning
   many independent batch jobs.

## Non-Goals

Do not implement:

- browser automation
- proxy rotation
- CAPTCHA bypass
- tick-level websocket infrastructure
- strategy or prediction logic

This is purely data-source reliability and job operations infrastructure.
