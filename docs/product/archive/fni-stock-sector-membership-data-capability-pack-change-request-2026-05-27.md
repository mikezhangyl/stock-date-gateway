# FNI Stock Sector Membership Data Capability Pack Change Request - 2026-05-27

Archive status: implemented on 2026-05-27.

Implementation evidence:

- Gateway route: `POST /api/v1/market-data/stocks/sector-memberships`
- Local persistence: SQLite reverse-index tables `stock_sector_membership_rows`
  and `stock_sector_membership_coverage`
- Validation: fake-provider API tests cover membership rows, explicit missing
  coverage, and repeated-request cache hits.

Upstream: `fund-narrative-intelligence` (`FNI`)

Consumer workspace:

```text
/Users/mikezhang/Coding/AI-Learning/fund-narrative-intelligence
```

Consumer contract reference:

```text
/Users/mikezhang/Coding/AI-Learning/fund-narrative-intelligence/config/market_data_gateway_contract.yaml
```

## Plain-Language Intent

FNI has now consumed the current gateway Can-Do surface into the daily market
structure report: breadth, index/ETF benchmark bars, sector/ETF heat,
limit-up/down, flow/event rows, CYQ chip samples, news, and structure mapping.

The next explainability step is blocked by a missing reverse membership dataset.
FNI can currently ask:

```text
sector/concept -> constituent stocks
```

But FNI cannot reliably ask:

```text
stock symbol -> sectors/concepts it belongs to
```

That reverse lookup is needed for fund-holding reports and stock-level narrative
explainability. FNI should not brute-force all concept boards inside report
runs. The gateway should own the board universe scan, cache, fallback behavior,
and reverse-index materialization.

This is a gateway data-capability request, not a strategy or prediction request.
Do not add browser scraping, proxy rotation, anti-detect infrastructure, or
social/news crawling for this pack.

## Required Provider-Neutral Endpoint

### Stock Sector Memberships

Expose:

```text
POST /api/v1/market-data/stocks/sector-memberships
```

Purpose:

- Map arbitrary A-share symbols to concept/sector memberships.
- Support fund holding sector exposure.
- Support stock-level narrative context without guessing from names.
- Avoid FNI-side brute-force scans over all sectors during report generation.

Suggested request body:

```json
{
  "symbols": ["600519.SH", "300024.SZ"],
  "trade_date": "2026-05-22",
  "sector_types": ["concept", "industry"],
  "limit_per_symbol": 50
}
```

Required row fields:

```text
symbol
sector_name
sector_type
source
```

Useful optional row fields:

```text
name
sector_code
trade_date
pct_change
weight
provider
membership_source
```

Response semantics:

- Return one row per symbol/sector membership.
- If a symbol is valid but has no memberships, return no row for that symbol and
  include a coverage reason in metadata.
- Do not return silent empty success when the upstream board universe or reverse
  index failed.
- Preserve normalized metadata: provider, endpoint, cache mode, generated_at,
  row_count, degradation warning when applicable.

Likely implementation:

- Reuse existing sector/concept constituent capability where possible.
- Build or refresh a gateway-owned reverse index:

```text
sector/concept board -> constituents -> symbol membership rows
```

- Persist the reverse index locally so repeated FNI reports do not rescan all
  public-web endpoints.
- Use EastMoney/AkShare fallback according to existing gateway adapter policy.

## Why FNI Cannot Solve This Locally

FNI already has `GET /api/v1/market-data/sectors/constituents`, but that endpoint
requires a known sector name. A fund or stock report starts with holdings, not a
known list of sectors. To infer membership locally, FNI would need a stable
sector universe and then scan every board. That creates unnecessary load, repeats
gateway work, and is risky because the provider-neutral sector ranking route is
currently unstable in live checks.

Gateway is the right layer because it can:

- schedule/cache the broad board scan,
- retry/fallback upstream endpoints,
- expose coverage diagnostics,
- serve future FNI reports from a stable normalized API.

## FNI Acceptance Criteria

FNI will consider this Can-Do accepted when:

- `POST /api/v1/market-data/stocks/sector-memberships` returns rows for at least
  one widely covered sample symbol.
- Response rows include `symbol`, `sector_name`, `sector_type`, and `source`.
- Missing/empty coverage is explicit in metadata or structured warning fields.
- Repeated calls prefer gateway cache or a materialized local reverse index.
- The endpoint does not depend on FNI passing a sector name.
- Gateway tests and ruff pass.
- FNI can add a runnable probe and then consume the endpoint in a future holding
  sector exposure report.

## Suggested FNI Smoke Request

```bash
curl -sS -X POST "http://127.0.0.1:8700/api/v1/market-data/stocks/sector-memberships" \
  -H "content-type: application/json" \
  -d '{"symbols":["600519.SH","300024.SZ"],"trade_date":"2026-05-22","limit_per_symbol":20}'
```

Expected shape:

```json
{
  "data": {
    "rows": [
      {
        "symbol": "300024.SZ",
        "name": "机器人",
        "sector_name": "机器人概念",
        "sector_type": "concept",
        "source": "eastmoney"
      }
    ]
  },
  "meta": {
    "provider": "gateway",
    "endpoint": "stock_sector_memberships",
    "cache": {"hit": true, "mode": "cache"},
    "generated_at": "2026-05-27T00:00:00+08:00"
  }
}
```

## Not In Scope

- No price prediction.
- No trading signals.
- No LLM strategy engine.
- No Snowball/Taoguba/X/Reddit scraping.
- No browser automation.
- No proxy or CAPTCHA work.
- No need to make full historical membership coverage perfect in this slice.
