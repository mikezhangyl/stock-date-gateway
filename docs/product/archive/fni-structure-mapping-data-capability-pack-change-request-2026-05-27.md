# FNI Structure Mapping Data Capability Pack Change Request - 2026-05-27

Archive status: implemented and accepted

Archived on: 2026-05-27

Acceptance evidence:

- Gateway lint: `uv run ruff check .` -> passed
- Gateway full suite: `uv run pytest` -> `110 passed`, coverage `81.29%`
- Gateway diff hygiene: `git diff --check` -> passed
- FNI runtime conformance for the six new provider-neutral endpoints: `6/6 passed`
- FNI runnable probes completed:
  - `outputs/structure_mapping_probes/2026-05-27-sector-constituents/sector_constituents_report.md`
  - `outputs/structure_mapping_probes/2026-05-27-etf-basic/etf_basic_report.md`
  - `outputs/structure_mapping_probes/2026-05-27-index-constituents/index_constituents_report.md`
  - `outputs/structure_mapping_probes/2026-05-27-margin-summary/margin_summary_report.md`
  - `outputs/structure_mapping_probes/2026-05-27-margin-detail/margin_detail_report.md`
  - `outputs/structure_mapping_probes/2026-05-27-earnings-calendar/earnings_calendar_report.md`

Observed FNI probe sources:

- Sector constituents: EastMoney fallback
- ETF basic: AkShare
- Index constituents: AkShare
- Margin summary/detail: AkShare
- Earnings calendar: AkShare

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

FNI has accepted the first market-structure pack and the flow/event pack through
provider-neutral gateway routes. The new flow/event routes are good enough for
Can-Do expansion: repeated FNI smoke checks can fetch rows, but public
EastMoney/AkShare-backed routes should still be treated as observable auxiliary
inputs rather than hard production dependencies.

The next useful breakthrough is not another quote endpoint. FNI needs structure
mapping data: board constituents, ETF metadata, index constituents, margin
finance context, and an earnings/event calendar. These datasets make future
reports more explainable because FNI can connect a stock, ETF, index, sector,
and event without guessing.

Gateway should continue owning external source integration and cache/fallback
decisions. FNI should consume stable provider-neutral gateway endpoints and
should not add direct Tushare, AkShare, EastMoney, or news-site integrations for
this pack.

This request is still Can-Do first. Return normalized rows with clear
source/provider/degradation metadata. Perfect historical coverage, dashboards,
scheduled backfills, and production SLOs are not required in this slice.

## Current FNI Stability Read

FNI ran a light repeated live check after the latest flow/event optimization:

- `capital/northbound`: repeatable rows after one transient degraded response.
- `capital/main-flow`: repeated `200 OK`, rows present.
- `etf/flow`: repeated `200 OK`, rows present.
- `market/dragon-tiger`: repeated `200 OK`, rows present.
- Older `eastmoney/market-quotes` still showed fragility for a sample symbol.

Conclusion: continue expanding new data, but keep reliability tier explicit.
Public web backed data remains Can-Do / auxiliary until longer validation proves
otherwise.

## Required Provider-Neutral Endpoints

### 1. Sector Constituents

Expose:

```text
GET /api/v1/market-data/sectors/constituents
```

Purpose:

- Map a concept/sector board to its member stocks.
- Support sector rotation explainability and future holding-to-sector exposure.

Suggested query params:

```text
sector_name=机器人
trade_date=2026-05-22
limit=50
```

Required row fields:

```text
sector_name
symbol
name
source
```

Useful optional fields:

```text
trade_date
pct_change
weight
provider
```

Likely sources:

- AkShare concept-board constituent functions when available.
- EastMoney public concept-board constituent data as fallback.

### 2. ETF Basic Metadata

Expose:

```text
GET /api/v1/market-data/etf/basic
```

Purpose:

- Return ETF identity and classification data.
- Keep ETF ranking reports explainable by showing broad category, issuer, and
  optional tracking index.

Suggested query params:

```text
market=cn
limit=50
```

Required row fields:

```text
symbol
name
source
```

Useful optional fields:

```text
category
fund_type
issuer
tracking_index
list_date
provider
```

Likely sources:

- Tushare fund metadata if available under the gateway's token/permission.
- AkShare/EastMoney ETF spot or fund metadata as fallback.

Implementation note:

- Prefer putting classification fields on this endpoint first. A separate ETF
  category endpoint is not required unless the gateway implementation naturally
  needs it.

### 3. Index Constituents

Expose:

```text
GET /api/v1/market-data/index/constituents
```

Purpose:

- Map common benchmarks such as CSI 300 / SSE Composite style indexes to member
  stocks.
- Support breadth segmentation and benchmark exposure reports.

Suggested query params:

```text
index_symbol=000300.SH
trade_date=2026-05-22
limit=50
```

Required row fields:

```text
index_symbol
symbol
name
source
```

Useful optional fields:

```text
trade_date
weight
industry
provider
```

Likely sources:

- Tushare index constituent or index weight data if available.
- AkShare public index constituent data as fallback.

### 4. Margin Summary

Expose:

```text
GET /api/v1/market-data/margin/summary
```

Purpose:

- Market-level leverage and risk-appetite context.

Required query params:

```text
trade_date=2026-05-22
```

Required row fields:

```text
trade_date
financing_balance
source
```

Useful optional fields:

```text
securities_lending_balance
financing_buy_amount
repayment_amount
provider
```

### 5. Margin Detail

Expose:

```text
GET /api/v1/market-data/margin/detail
```

Purpose:

- Stock-level financing and lending context.
- Helps identify unusual leverage pressure around specific names.

Suggested query params:

```text
trade_date=2026-05-22
limit=50
```

Required row fields:

```text
trade_date
symbol
financing_balance
source
```

Useful optional fields:

```text
name
financing_buy_amount
securities_lending_balance
provider
```

### 6. Earnings Calendar

Expose:

```text
GET /api/v1/market-data/fundamentals/earnings-calendar
```

Purpose:

- Basic event calendar for earnings announcements, performance forecasts, or
  disclosure events.
- This is report context only, not prediction logic.

Required query params:

```text
start_date=2026-05-22
end_date=2026-06-05
```

Suggested optional params:

```text
limit=50
```

Required row fields:

```text
symbol
name
ann_date
event_type
source
```

Useful optional fields:

```text
forecast_type
net_profit_min
net_profit_max
provider
```

Likely sources:

- Tushare disclosure, forecast, or equivalent financial event endpoints if
  available under current permissions.
- Public data fallback only if it can return normalized rows without brittle
  browser scraping.

## Error And Degradation Behavior

- Do not return empty success when the upstream source is blocked, unmapped, or
  failed.
- A successful empty response is acceptable only when the gateway can
  distinguish true no-data from provider failure.
- Provider failures should return structured error/degradation metadata
  compatible with existing normalized responses.
- Keep `/api/health` lightweight and avoid upstream calls there.
- Do not add browser automation, proxy rotation, CAPTCHA bypass, or anti-detect
  infrastructure for this pack.

## Acceptance Checks Requested From Gateway

Gateway quality:

```bash
uv run ruff check .
uv run pytest
```

Runtime smoke:

```bash
curl 'http://127.0.0.1:8700/api/v1/market-data/sectors/constituents?sector_name=机器人&trade_date=2026-05-22&limit=5'
curl 'http://127.0.0.1:8700/api/v1/market-data/etf/basic?market=cn&limit=5'
curl 'http://127.0.0.1:8700/api/v1/market-data/index/constituents?index_symbol=000300.SH&trade_date=2026-05-22&limit=5'
curl 'http://127.0.0.1:8700/api/v1/market-data/margin/summary?trade_date=2026-05-22'
curl 'http://127.0.0.1:8700/api/v1/market-data/margin/detail?trade_date=2026-05-22&limit=5'
curl 'http://127.0.0.1:8700/api/v1/market-data/fundamentals/earnings-calendar?start_date=2026-05-22&end_date=2026-06-05&limit=5'
```

Expected:

- Each route returns HTTP 200 with normalized rows for a known data date/window,
  or a clear structured provider error/degradation response if unavailable.
- Rows contain required fields.
- Responses include provider/source metadata compatible with existing
  normalized routes.
- No silent empty success payloads for provider failure.

## FNI Follow-Up After Gateway Implementation

After gateway implementation is complete, please report:

- Implemented routes.
- Source/provider actually used for each route.
- Any permission limitations.
- Quality results from `ruff` and `pytest`.
- Runtime smoke row counts and any degraded cases.

Then archive this request under:

```text
/Users/mikezhang/Coding/AI-Learning/stock-data-gateway/docs/product/archive/
```

FNI will then promote the corresponding planned endpoints in
`config/market_data_gateway_contract.yaml`, add provider methods/probes, and run
contract conformance from the consumer side.
