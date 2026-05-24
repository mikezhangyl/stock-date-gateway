# FNI Upstream Change Request - 2026-05-25

## Title

Support `fund-narrative-intelligence` V0 gateway-first market-data validation.

## Upstream

`fund-narrative-intelligence` (`FNI`)

Workspace:

```text
/Users/mikezhang/Coding/AI-Learning/fund-narrative-intelligence
```

Consumer-side contract file:

```text
config/market_data_gateway_contract.yaml
```

## Business workflow

FNI is building V0 stock narrative signal infrastructure. Current scope is data
source validation, not strategy or prediction.

Gateway data is used by:

- live data-source validation
- controlled market-data stress probes
- breadth scan prototypes
- sector rotation scan prototypes
- future cost-basis/chip analysis

Missing data policy:

- Tushare market-data endpoints should be available through gateway cache or upstream read-through.
- Sector/concept data may degrade to partial initially, but failures must be structured and diagnosable.
- `cyq_chips` should be gateway-owned and should not be re-fetched directly by FNI.

## Provider / endpoint

FNI supports two gateway consumption modes.

### Mode A - Tushare facade compatibility

Already documented by this project:

```text
POST /tushare
```

FNI can point existing Tushare callers at this route with:

```bash
TUSHARE_API_URL=http://127.0.0.1:8700/tushare
```

Required endpoint coverage for Mode A:

- `trade_cal`
- `stock_basic`
- `daily`
- `daily_basic`
- `index_daily`
- `fund_daily`
- `income`
- `fina_indicator`
- `cyq_chips`

Important compatibility requirement:

- FNI may call `daily`, `daily_basic`, `index_daily`, and `fund_daily` with comma-separated `ts_code` values.
- If gateway internally caches per symbol/date, it should split or normalize this safely instead of treating the comma-separated symbol list as one instrument.

### Mode B - normalized REST gateway

FNI has code ready to use this mode when:

```bash
MARKET_DATA_GATEWAY_URL=http://127.0.0.1:8700
```

The desired normalized routes are defined in FNI:

```text
config/market_data_gateway_contract.yaml
```

Near-term routes requested:

| Route | Provider | Dataset | Priority |
| --- | --- | --- | --- |
| `POST /api/v1/market-data/tushare/daily` | Tushare | A-share daily bars | P0 |
| `POST /api/v1/market-data/tushare/index-daily` | Tushare | index bars | P0 |
| `POST /api/v1/market-data/tushare/fund-daily` | Tushare | ETF daily bars | P0 |
| `POST /api/v1/market-data/tushare/stock-basic` | Tushare | stock metadata | P0 |
| `POST /api/v1/market-data/tushare/trade-cal` | Tushare | trade calendar | P0 |
| `GET /api/v1/market-data/eastmoney/market-quotes` | EastMoney | latest quotes | P1 |
| `GET /api/v1/market-data/akshare/sector-concepts` | AkShare/EastMoney | sector concepts | P1 |
| `GET /api/v1/market-data/akshare/limit-up-down` | AkShare/EastMoney | limit up/down stats | P1 |
| `POST /api/v1/market-data/chips/cyq` | gateway-owned/Tushare cache | chip distribution | P1 |

Planned routes, not required for first gateway acceptance:

- `GET /api/v1/market-data/eastmoney/northbound-capital`
- `GET /api/v1/market-data/eastmoney/main-capital-flow`

## Request params

Normalized Tushare routes should accept JSON bodies.

Example daily request:

```json
{
  "symbols": ["600519.SH"],
  "start_date": "2026-05-22",
  "end_date": "2026-05-22",
  "include_turnover": true
}
```

Example trade calendar request:

```json
{
  "exchange": "SSE",
  "start_date": "2026-05-22",
  "end_date": "2026-05-22"
}
```

Example quote request:

```text
GET /api/v1/market-data/eastmoney/market-quotes?stock_codes=600519
```

## Required fields

Normalized route responses should use this envelope:

```json
{
  "data": {
    "rows": []
  },
  "meta": {
    "provider": "tushare",
    "endpoint": "daily",
    "cache": {
      "hit": true,
      "mode": "cache"
    },
    "generated_at": "2026-05-25T00:00:00+08:00"
  }
}
```

Required row fields by route:

- `tushare/daily`: `symbol`, `trade_date`, `close`, `volume`
- `tushare/index-daily`: `symbol`, `trade_date`, `close`
- `tushare/fund-daily`: `symbol`, `trade_date`, `close`
- `tushare/stock-basic`: `ts_code`, `symbol`, `name`
- `tushare/trade-cal`: `exchange`, `cal_date`, `is_open`
- `eastmoney/market-quotes`: `stock_code`, `latest_price`, `retrieved_at`
- `akshare/sector-concepts`: `sector_name`, `pct_change`, `source`
- `akshare/limit-up-down`: `trade_date`, `limit_up_count`, `limit_down_count`
- `chips/cyq`: `symbol`, `trade_date`, `cost_distribution`, `source`

## Field units and null semantics

- Dates should be stable ISO dates (`YYYY-MM-DD`) on normalized routes.
- Numeric fields should be numbers or `null`, not `"--"` in normalized routes.
- Empty rows are allowed for no-data cases, but the gateway should distinguish no-data from provider failure in `meta.status` or error envelope.
- `is_open` should be boolean on normalized routes.

## Cache semantic key

FNI expects gateway to own cache-hit decisions.

Expected cache semantics:

- Daily bars: `instrument_id=symbol`, `date_key=trade_date`
- Daily basic: same symbol/date semantics; if joined into daily response, cache default fields broadly enough to avoid narrow-field cache pollution.
- Trade calendar: `instrument_id=exchange`, `date_key=cal_date`
- Stock metadata: persistent snapshot; should include schema version in semantic params.
- Sector concepts: short TTL or daily snapshot; can be stale-cache served if upstream blocks.
- `cyq_chips`: gateway-owned persistent cache by `symbol + trade_date`

If default fields, units, adjustment semantics, or provider params change, bump the endpoint `schema_version`.

## Freshness / refresh expectations

For first acceptance:

- No force-refresh HTTP API is required.
- Read-through cache is enough.
- Reports should expose `cache.hit`, `cache.mode`, `provider`, `endpoint`, and row count.

Future need:

- scheduled backfill for `cyq_chips`
- optional stale-cache serving for unstable AkShare/EastMoney sector endpoints
- optional refresh CLI remains acceptable; HTTP refresh is not required yet

## Fallback policy

FNI behavior:

- If `MARKET_DATA_GATEWAY_URL` is configured, FNI tries normalized gateway first.
- If normalized gateway fails, FNI falls back to direct existing providers.
- If `TUSHARE_API_URL=http://127.0.0.1:8700/tushare`, existing Tushare-like calls go through the facade and should not require FNI token handling.

Gateway behavior requested:

- Do not trust caller-provided token.
- Use gateway-owned provider credentials.
- Preserve structured failure reasons.
- Prefer returning a stable error envelope over connection-level failures.

## Acceptance checks

FNI can validate the already documented `/tushare` facade with:

```bash
python scripts/validate_market_data_gateway_contract.py \
  --base-url http://127.0.0.1:8700 \
  --mode tushare-facade
```

For the normalized routes, FNI should run:

```bash
MARKET_DATA_GATEWAY_URL=http://127.0.0.1:8700 \
python scripts/validate_market_data_gateway_contract.py \
  --base-url http://127.0.0.1:8700
```

Then:

```bash
MARKET_DATA_GATEWAY_URL=http://127.0.0.1:8700 \
python scripts/report_market_data_runtime.py --format markdown
```

Expected:

- `Gateway Configured: True`
- provider-level gateway flags are true for configured providers

Then:

```bash
MARKET_DATA_GATEWAY_URL=http://127.0.0.1:8700 \
python scripts/run_market_data_stress.py \
  --use-stock-metadata \
  --max-symbols 100 \
  --batch-size 50
```

Expected:

- historical and daily probes pass
- gateway cache metadata is present
- failures, if any, are structured

For facade compatibility:

```bash
TUSHARE_API_URL=http://127.0.0.1:8700/tushare \
python scripts/validate_tushare_primary_acceptance.py \
  --output-dir outputs/tushare_primary_gateway
```

Expected:

- Tushare provider `source_url` is `http://127.0.0.1:8700/tushare`
- no layer-level fallback away from Tushare for Tushare primary acceptance

## Sample expected response

Normalized daily response:

```json
{
  "data": {
    "rows": [
      {
        "symbol": "600519.SH",
        "trade_date": "2026-05-22",
        "open": 1510.0,
        "high": 1530.0,
        "low": 1500.0,
        "close": 1520.0,
        "pre_close": 1515.0,
        "volume": 123456.0,
        "turnover_rate": 0.53,
        "source": "tushare"
      }
    ]
  },
  "meta": {
    "provider": "tushare",
    "endpoint": "daily",
    "cache": {
      "hit": true,
      "mode": "cache"
    },
    "generated_at": "2026-05-25T00:00:00+08:00"
  }
}
```

## Operational notes

- FNI currently treats normalized AkShare sector/concept endpoint as unstable; gateway stale-cache behavior would materially improve V0 reliability.
- FNI does not need browser automation, proxy rotation, CAPTCHA bypass, or stealth infrastructure in V0.
- The first integration target is stable gateway acceptance, not full market strategy logic.
