# FNI Flow And Event Data Capability Pack Change Request - 2026-05-26

Archive status: implemented and accepted

Archived on: 2026-05-27

Acceptance evidence:

- Gateway lint: `uv run ruff check .` -> passed
- Gateway full suite: `uv run pytest` -> `102 passed`, coverage `81.35%`
- Gateway diff hygiene: `git diff --check` -> passed
- FNI runtime conformance for the four new provider-neutral endpoints: `4/4 passed`
- FNI default gateway conformance after marking endpoints available: `17/17 passed`
- FNI runnable probes completed:
  - `outputs/flow_event_probes/2026-05-27-northbound/northbound_capital_report.md`
  - `outputs/flow_event_probes/2026-05-27-main-capital-flow/main_capital_flow_report.md`
  - `outputs/flow_event_probes/2026-05-27-etf-flow/etf_flow_report.md`
  - `outputs/flow_event_probes/2026-05-27-dragon-tiger/dragon_tiger_report.md`

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

FNI has accepted the first Can-Do market-data pack: sector concepts, ETF spot,
limit-up/down, and Tushare news briefs. The next planned datasets are flow and
event-context data.

Gateway should continue owning external data-source integration. FNI should
consume stable provider-neutral gateway endpoints and should not add direct
EastMoney/AkShare scraping logic.

This request is still Can-Do first. The goal is to return useful normalized rows
with explicit source/degradation metadata. Perfect coverage, historical backfill,
dashboards, and advanced scheduling are not required in this slice.

## Current Baseline

Already accepted:

- `POST /api/v1/market-data/chips/cyq`
  - FNI live conformance passed on 2026-05-26.
  - No gateway work is requested for the basic CYQ route in this pack.
  - Long-running CYQ backfill/scheduling can remain an operations backlog item.

Still incomplete:

- `GET /api/v1/market-data/eastmoney/northbound-capital`
  - route exists but returns empty rows.
- `GET /api/v1/market-data/eastmoney/main-capital-flow`
  - route exists but returns empty rows.
- ETF flow is not exposed through a normalized route yet.
- 龙虎榜 is not exposed through a normalized route yet.

## Required Provider-Neutral Endpoints

### 1. Northbound Capital

Expose:

```text
GET /api/v1/market-data/capital/northbound
```

Purpose:

- Optional macro liquidity context for FNI market structure reports.

Required query params:

```text
trade_date=2026-05-22
```

Required row fields:

```text
trade_date
net_buy_amount
source
```

Useful optional fields:

```text
buy_amount
sell_amount
provider
```

Implementation sources:

- Prefer whichever is more stable in current gateway: EastMoney public endpoint
  or AkShare wrapper.
- Existing provider-specific route can remain, but FNI wants the
  provider-neutral route above for future consumption.

### 2. Main Capital Flow

Expose:

```text
GET /api/v1/market-data/capital/main-flow
```

Purpose:

- Optional money-flow layer for stock and narrative context.

Suggested query params:

```text
trade_date=2026-05-22
limit=50
```

Required row fields:

```text
trade_date
symbol
main_net_inflow
source
```

Useful optional fields:

```text
name
pct_change
amount
provider
```

### 3. ETF Flow

Expose:

```text
GET /api/v1/market-data/etf/flow
```

Purpose:

- ETF rotation and liquidity context.
- Complements the accepted ETF spot ranking endpoint.

Suggested query params:

```text
trade_date=2026-05-22
limit=50
```

Required row fields:

```text
trade_date
symbol
net_inflow
source
```

Useful optional fields:

```text
name
pct_change
amount
provider
```

### 4. 龙虎榜

Expose:

```text
GET /api/v1/market-data/market/dragon-tiger
```

Purpose:

- Event-driven market context.
- Helps identify stocks with unusual trading attention.

Suggested query params:

```text
trade_date=2026-05-22
limit=50
```

Required row fields:

```text
trade_date
symbol
reason
source
```

Useful optional fields:

```text
name
buy_amount
sell_amount
net_buy_amount
provider
```

Likely implementation source:

- AkShare `stock_lhb_detail_em` or equivalent EastMoney-backed endpoint.

## Error Behavior

- Do not return empty success when an upstream endpoint is blocked, missing, or
  unmapped.
- If the endpoint is implemented but the queried date has no rows, return a
  successful empty response only when the gateway can distinguish true no-data
  from provider failure.
- Provider failures should return structured error/degradation metadata.
- Keep `/api/health` lightweight.

## Acceptance Checks Requested From Gateway

Gateway quality:

```bash
uv run ruff check .
uv run pytest
```

Runtime smoke:

```bash
curl 'http://127.0.0.1:8700/api/v1/market-data/capital/northbound?trade_date=2026-05-22'
curl 'http://127.0.0.1:8700/api/v1/market-data/capital/main-flow?trade_date=2026-05-22&limit=5'
curl 'http://127.0.0.1:8700/api/v1/market-data/etf/flow?trade_date=2026-05-22&limit=5'
curl 'http://127.0.0.1:8700/api/v1/market-data/market/dragon-tiger?trade_date=2026-05-22&limit=5'
```

Expected:

- Each route returns HTTP 200 with at least one normalized row for a known data
  date, or a clear structured provider error if the source is unavailable.
- Rows contain required fields.
- Responses include provider/source metadata compatible with existing normalized
  gateway envelopes.

## FNI-Side Acceptance After Gateway Implements This

FNI will:

1. Run gateway conformance against the four new provider-neutral endpoints.
2. Add runnable probe scripts for northbound, main capital flow, ETF flow, and
   龙虎榜.
3. Add JSON/HTML output where the data becomes part of a formal reader-facing
   report.
4. Keep scheduling, long-history backfill, and advanced robustness in backlog.

## Non-Goals

- No trading strategy.
- No AI prediction.
- No browser scraping.
- No proxy or anti-detect system.
- No full historical flow database in this request.
- No CYQ long-running backfill changes in this request.
