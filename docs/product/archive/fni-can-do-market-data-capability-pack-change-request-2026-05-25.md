# FNI Can-Do Market Data Capability Pack Change Request - 2026-05-25

Archive status: implemented and accepted

Archived on: 2026-05-25

Gateway implementation state: local working tree, not yet committed at archive
time.

Acceptance evidence:

- Gateway lint: `uv run ruff check .` -> passed
- Gateway full suite: `uv run pytest` -> `96 passed`, coverage `81.65%`
- FNI contract/probe tests: `uv run pytest tests/test_can_do_probe_scripts.py tests/test_market_data_gateway_contract.py -q` -> `12 passed`
- FNI lint: `uv run ruff check .` -> passed
- FNI compile check: `uv run python -m compileall -q src tests scripts` -> passed
- Runtime FNI gateway conformance for the four new endpoints: `4/4 passed`
- Runtime FNI probes completed:
  - `outputs/can_do_probes/2026-05-25-sector/sector_scan_report.md`
  - `outputs/can_do_probes/2026-05-25-etf-spot/etf_spot_report.md`
  - `outputs/can_do_probes/2026-05-25-limit-up-down/limit_up_down_report.md`
  - `outputs/can_do_probes/2026-05-25-news-briefs/news_briefs_smoke_report.md`

Upstream: `fund-narrative-intelligence` (`FNI`)

Consumer workspace:

```text
/Users/mikezhang/Coding/AI-Learning/fund-narrative-intelligence
```

Consumer contract reference:

```text
/Users/mikezhang/Coding/AI-Learning/fund-narrative-intelligence/config/market_data_gateway_contract.yaml
```

Related archived requests:

```text
docs/product/archive/fni-upstream-change-request-2026-05-25.md
docs/product/archive/fni-large-scan-async-job-change-request-2026-05-25.md
docs/product/archive/fni-breadth-scale-job-ops-change-request-2026-05-25.md
docs/product/archive/fni-breadth-window-cancelled-job-retry-change-request-2026-05-25.md
```

## Plain-Language Intent

FNI is shifting the next market-data phase from backend hardening to functional
"Can Do" expansion.

Gateway should own new external data-source integrations. FNI should consume a
stable gateway API and should not add new direct integrations for Tushare,
AkShare, EastMoney, or news sites unless a small compatibility shim is needed.

This request is intentionally not asking for production-perfect throughput,
dashboards, proxy systems, or browser scraping. The goal is to expose a few
useful datasets through normalized gateway endpoints so FNI can build scanners
and reports on top.

## Required Capabilities

### 1. Provider-Neutral Sector Concepts

Expose a provider-neutral endpoint:

```text
GET /api/v1/market-data/sectors/concepts
```

Purpose:

- FNI sector rotation scanner.
- Rank concepts/sectors by daily strength.
- Hide whether data came from AkShare, EastMoney fallback, cache, or another
  provider.

Suggested query params:

```text
trade_date=2026-05-22
limit=20
```

Required row fields:

```text
sector_name
pct_change
source
```

Useful optional fields:

```text
trade_date
amount
turnover_rate
leading_stock
provider
```

Gateway may implement this as a normalized alias over the existing
`/api/v1/market-data/akshare/sector-concepts` route, but FNI wants to move to
the provider-neutral route.

### 2. ETF Spot Ranking

Expose:

```text
GET /api/v1/market-data/etf/spot
```

Purpose:

- FNI ETF rotation scanner.
- Get a runnable ETF ranking without hardcoding every ETF symbol in FNI.

Suggested query params:

```text
limit=20
```

Required row fields:

```text
symbol
name
pct_change
source
```

Useful optional fields:

```text
trade_date
close
amount
volume
provider
```

Implementation can use AkShare, EastMoney public data, Tushare fund metadata
plus daily bars, or a fallback combination. For this phase, "has rows and
stable normalized fields" is more important than perfect coverage.

### 3. Provider-Neutral Limit-Up/Down Statistics

Expose:

```text
GET /api/v1/market-data/market/limit-up-down
```

Purpose:

- FNI daily market-temperature report.
- Avoid binding the consumer to AkShare route names.

Required query params:

```text
trade_date=2026-05-22
```

Required row fields:

```text
trade_date
limit_up_count
limit_down_count
source
```

Useful optional fields:

```text
limit_up_symbols
limit_down_symbols
provider
```

Gateway may implement this as a normalized alias over the existing
`/api/v1/market-data/akshare/limit-up-down` route.

### 4. Structured News Briefs Through Tushare First

Expose:

```text
POST /api/v1/market-data/news/briefs
```

Purpose:

- FNI V2 news/narrative layer.
- Use structured licensed/API-style news data before considering direct
  news-site crawling.

Preferred upstream source:

```text
Tushare news API
```

Reference:

```text
https://tushare.pro/news
https://www.tushare.pro/document/41?doc_id=143
```

Suggested request body:

```json
{
  "source_provider": "tushare",
  "src": "sina",
  "start_datetime": "2026-05-22 09:00:00",
  "end_datetime": "2026-05-22 15:30:00",
  "limit": 20
}
```

Required row fields:

```text
datetime
title
content
source
```

Useful optional fields:

```text
channels
src
provider
```

Important behavior:

- If the local Tushare token lacks `news` permission, return a structured error
  such as `PROVIDER_PERMISSION_REQUIRED`.
- Do not scrape news websites as fallback in this phase.
- Do not return empty success if permission is missing.

## Existing Routes To Keep Working

Do not break the already accepted gateway routes:

```text
/api/v1/market-data/tushare/daily
/api/v1/market-data/tushare/index-daily
/api/v1/market-data/tushare/fund-daily
/api/v1/market-data/jobs/daily-bars
/api/v1/market-data/jobs/breadth-window
/api/v1/market-data/jobs
/api/v1/market-data/jobs/{job_id}
/api/v1/market-data/jobs/{job_id}/rows
/api/v1/market-data/jobs/{job_id}/cancel
```

Index OHLCV and ETF daily data can continue using the existing Tushare-backed
routes for now. This request mainly adds provider-neutral convenience surfaces
and Tushare news smoke support.

## Acceptance Checks Requested From Gateway

Please add or update gateway-side acceptance so these commands can pass:

```bash
uv run ruff check .
uv run pytest
```

Runtime smoke checks should cover:

```bash
curl 'http://127.0.0.1:8700/api/v1/market-data/sectors/concepts?limit=5'
curl 'http://127.0.0.1:8700/api/v1/market-data/etf/spot?limit=5'
curl 'http://127.0.0.1:8700/api/v1/market-data/market/limit-up-down?trade_date=2026-05-22'
curl -X POST 'http://127.0.0.1:8700/api/v1/market-data/news/briefs' \
  -H 'Content-Type: application/json' \
  -d '{"source_provider":"tushare","src":"sina","start_datetime":"2026-05-22 09:00:00","end_datetime":"2026-05-22 15:30:00","limit":20}'
```

Expected behavior:

- Sector concepts returns at least 1 row with required fields.
- ETF spot returns at least 1 row with required fields.
- Limit-up/down returns at least 1 row with required fields.
- News returns rows when permission exists, or a structured permission error
  when permission is absent.
- `/api/health` remains lightweight and HTTP 200.
- No browser automation, CAPTCHA bypass, proxy rotation, or news-site scraping.

## FNI-Side Acceptance After Gateway Implements This

After gateway reports completion, FNI will:

1. Run gateway conformance for the new planned contract endpoints.
2. Add/adjust FNI source-layer methods to consume provider-neutral gateway
   routes.
3. Add runnable scanner/report commands for sector, ETF, limit-up/down, and
   news smoke.
4. Keep deeper throughput and cold-cache tuning in TODO/backlog.

## Non-Goals

- No trading strategy.
- No AI prediction.
- No social-media scraping.
- No browser farm.
- No anti-detect or proxy system.
- No full production observability pass in this request.
