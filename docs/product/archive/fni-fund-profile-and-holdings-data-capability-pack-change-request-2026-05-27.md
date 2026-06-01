# FNI Fund Profile And Holdings Data Capability Pack Change Request - 2026-05-27

Archive status: implemented on 2026-05-27.

Implementation evidence:

- Gateway routes:
  - `GET /api/v1/market-data/funds/profile`
  - `GET /api/v1/market-data/funds/holdings`
- Local persistence: SQLite fund tables `fund_profile_rows`,
  `fund_holding_rows`, and `fund_holding_coverage`
- Provider fallback: cache first, Tushare `fund_basic` / `fund_portfolio`,
  then EastMoney/Tiantian public fund positions
- Validation: fake-provider API tests cover Tushare success, EastMoney fallback,
  repeated-request cache hits, degraded failures, and parameter validation.

Upstream consumer: `fund-narrative-intelligence` (`FNI`)

Consumer workspace:

```text
/Users/mikezhang/Coding/AI-Learning/fund-narrative-intelligence
```

Consumer contract reference:

```text
/Users/mikezhang/Coding/AI-Learning/fund-narrative-intelligence/config/market_data_gateway_contract.yaml
```

## Plain-Language Intent

FNI's main product flow starts with a fund code:

```text
fund code -> fund identity -> fund top holdings -> narrative exposure report
```

Today FNI already has direct fund-holding providers:

- EastMoney/Tiantian public endpoint:
  `https://fundmobapi.eastmoney.com/FundMNewApi/FundMNInverstPosition`
- Tushare token endpoint: `fund_portfolio`

Those direct providers live in:

```text
/Users/mikezhang/Coding/AI-Learning/fund-narrative-intelligence/src/providers/eastmoney.py
/Users/mikezhang/Coding/AI-Learning/fund-narrative-intelligence/src/providers/tushare_holdings.py
```

The next gateway step is to move this external data acquisition behind the
local gateway, without changing the product meaning. FNI should be able to ask
the gateway for fund profile and fund holdings, and the gateway should own:

- cache-hit decisions
- upstream provider fallback
- retry/deadline behavior
- structured degraded metadata
- local persistence for future replay

This is not a strategy, prediction, scraping, or browser-automation request.
Do not add Playwright, browser farms, proxy rotation, CAPTCHA bypass, or social
media/news crawling for this pack.

## Required Provider-Neutral Endpoints

### 1. Fund Profile

Expose:

```text
GET /api/v1/market-data/funds/profile
```

Purpose:

- Return fund identity rows for FNI reports.
- Give FNI a gateway-owned source layer for fund metadata.
- Keep response normalized and provider-neutral.

Suggested query:

```text
fund_code=161725
```

Required row fields:

```text
fund_code
fund_name
fund_type
currency
source
```

Useful optional row fields:

```text
as_of_date
provider
source_url
retrieved_at
data_quality
```

### 2. Fund Holdings

Expose:

```text
GET /api/v1/market-data/funds/holdings
```

Purpose:

- Return the latest top holdings for a fund.
- Preserve the FNI report semantics of top holdings with stock code, stock name,
  holding weight, and as-of date.
- Allow future FNI reports to stop calling EastMoney/Tushare directly for fund
  holdings.

Suggested query:

```text
fund_code=161725&limit=10
```

Required row fields:

```text
fund_code
as_of_date
stock_code
stock_name
weight
source
```

Useful optional row fields:

```text
rank
holding_change
industry
market
ts_code
provider
source_url
retrieved_at
```

## Response Envelope

Use the existing normalized gateway envelope:

```json
{
  "data": {
    "rows": []
  },
  "meta": {
    "provider": "local_gateway",
    "endpoint": "fund_holdings",
    "cache": {
      "hit": false,
      "mode": "upstream"
    },
    "generated_at": "2026-05-27T00:00:00+00:00",
    "row_count": 0,
    "status": "degraded",
    "warning": {
      "code": "PROVIDER_UNAVAILABLE",
      "message": "Unable to fetch fund holdings."
    }
  }
}
```

Recommended metadata additions:

```text
coverage.requested_fund_code
coverage.returned_holding_count
coverage.as_of_date
coverage.provider_attempts
```

Provider attempts should identify whether EastMoney, Tushare, cache, or fallback
was used.

## Provider Behavior

Preferred Can-Do behavior:

1. Try a local cache first.
2. Fetch from Tushare `fund_portfolio` when token and permission allow.
3. Fallback to EastMoney/Tiantian public holdings endpoint.
4. If both fail, return a structured degraded payload.

Important:

- Do not silently return mock holdings from the gateway.
- FNI may keep mock fallback for demo mode, but the gateway should return real
  provider data, cached data, stale cache with metadata, or structured degraded
  metadata.
- Do not return empty success if all upstream providers failed.
- Do not let public upstream calls hang until the FNI client socket timeout.

## Local Cache Requirements

Persist normalized rows in the gateway SQLite store or existing cache layer.

Suggested keys:

```text
fund_profile: fund_code + provider
fund_holdings: fund_code + as_of_date + provider
```

Suggested tables:

```text
fund_profile_rows
fund_holding_rows
fund_holding_coverage
```

The gateway may choose a different internal layout, but repeated requests should
be able to hit cache and avoid unnecessary upstream calls.

## FNI Acceptance Criteria

After implementation, FNI should be able to add gateway consumers without
changing the product-level fund payload semantics.

Minimum gateway smoke:

```bash
curl -sS -w '\nHTTP %{http_code} TOTAL %{time_total}s\n' \
  'http://127.0.0.1:8700/api/v1/market-data/funds/profile?fund_code=161725'

curl -sS -w '\nHTTP %{http_code} TOTAL %{time_total}s\n' \
  'http://127.0.0.1:8700/api/v1/market-data/funds/holdings?fund_code=161725&limit=10'
```

Expected:

- HTTP 200 for successful or structured degraded responses.
- Successful `funds/holdings` returns at least one row for a known fund when
  upstream provider access is available.
- Degraded response includes `meta.status=degraded` and a useful `warning`.
- Request completes within a bounded deadline; no socket-level hangs.

Gateway verification:

```bash
uv run ruff check .
uv run pytest
```

FNI-side planned contract endpoint IDs are already registered as:

```text
gateway_fund_profile
gateway_fund_holdings
```

They are marked `maturity: planned`, so FNI conformance will skip them by
default until gateway implements the endpoints.

## Non-Goals

- No browser automation.
- No proxy rotation.
- No CAPTCHA bypass.
- No social/news scraping.
- No trading strategy.
- No AI prediction.
- No change to FNI's narrative scoring logic in this gateway pack.
