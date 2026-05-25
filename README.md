# Stock Data Gateway

Local Market Data Gateway for reusable market data access across local research projects.

The first stable facade is:

```text
POST http://127.0.0.1:8700/tushare
```

FNI-facing normalized routes are also available under:

```text
/api/v1/market-data/...
```

Large daily-bar scans can use the async job API:

```text
POST /api/v1/market-data/jobs/daily-bars
POST /api/v1/market-data/jobs/breadth-window
GET  /api/v1/market-data/jobs
GET  /api/v1/market-data/jobs/{job_id}
POST /api/v1/market-data/jobs/{job_id}/cancel
GET  /api/v1/market-data/jobs/{job_id}/rows
```

The service is intentionally local-first: SQLite cache, fake-provider tests by default, and live provider validation only when explicitly enabled.

## Development

```bash
uv sync --extra dev --extra provider
uv run pytest
scripts/dev_gateway.sh
```

Put provider secrets in `.env.local`; the gateway owns the real Tushare token and
does not trust or forward caller-supplied tokens.

Tushare pacing is configurable with `TUSHARE_RATE_LIMIT_PER_MINUTE`. The default
is `500`, matching the 5000-point Tushare tier.

Async scan job controls:

```text
GATEWAY_JOB_QUEUE_LIMIT=2
GATEWAY_JOB_MAX_SYMBOLS=5000
GATEWAY_JOB_MAX_BATCH_SIZE=100
GATEWAY_JOB_MAX_LOOKBACK_TRADING_DAYS=260
```

Retried async requests reuse compatible partial work when the previous semantic
job ended as `cancelled`, `failed`, or `interrupted`; completed rows remain
readable and are not duplicated.

## Fund Narrative Intelligence

`fund-narrative-intelligence` should integrate through the local HTTP facade, not
through Python imports from this project:

```bash
uv run uvicorn stock_data_gateway.main:app --host 127.0.0.1 --port 8700
```

Then run FNI with:

```bash
TUSHARE_API_URL=http://127.0.0.1:8700/tushare
```

The gateway supports the Tushare endpoints currently exercised by FNI market
quotes, valuation snapshots, and financial metrics: `daily`, `daily_basic`,
`stock_basic`, `income`, and `fina_indicator`. It also exposes normalized
Tushare daily/index/fund/calendar/metadata routes, EastMoney market quotes, and
optional AkShare sector/limit-up-down routes.

Run the gateway-backed FNI acceptance suite:

```bash
uv run market-gateway-fni-acceptance \
  --fni-root /Users/mikezhang/Coding/AI-Learning/fund-narrative-intelligence \
  --mode all
```

See `docs/runbooks/fni-gateway-acceptance.md` for the operational runbook.
See `docs/runbooks/background-service-and-backfill.md` for LaunchAgent and CYQ
backfill setup.
See `docs/product/upstream-consumption-and-change-guide.md` for the upstream
consumption contract and change request template.
See `docs/product/archive/` for implemented FNI change request records.
See `docs/product/endpoint-registry.md` for the current endpoint and field
registry.

## Cache Operations

```bash
uv run market-gateway-cache inspect
uv run market-gateway-cache audit --provider tushare
uv run market-gateway-cache clear --provider tushare --endpoint daily --yes
```

## Live Validation

Live Tushare checks require both a token and an explicit opt-in:

```bash
uv sync --extra dev --extra provider
RUN_LIVE_PROVIDER_TESTS=1 TUSHARE_TOKEN=... uv run market-gateway-validate --live
```

Offline/fake validation does not require a token:

```bash
uv run market-gateway-validate --fake
```
