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
optional AkShare sector/ETF/limit-up-down routes through provider-neutral FNI
surfaces:

```text
GET  /api/v1/market-data/sectors/concepts
GET  /api/v1/market-data/sectors/constituents
POST /api/v1/market-data/stocks/sector-memberships
GET  /api/v1/market-data/funds/profile
GET  /api/v1/market-data/funds/holdings
GET  /api/v1/market-data/capital/northbound
GET  /api/v1/market-data/capital/main-flow
GET  /api/v1/market-data/etf/basic
GET  /api/v1/market-data/etf/spot
GET  /api/v1/market-data/etf/flow
GET  /api/v1/market-data/index/constituents
GET  /api/v1/market-data/margin/summary
GET  /api/v1/market-data/margin/detail
GET  /api/v1/market-data/market/limit-up-down
GET  /api/v1/market-data/market/dragon-tiger
GET  /api/v1/market-data/fundamentals/earnings-calendar
GET  /api/v1/market-data/source-events/official-filings
GET  /api/v1/market-data/source-events/official-disclosures
GET  /api/v1/market-data/source-events/news-context
GET  /api/v1/market-data/source-events/social-heat
POST /api/v1/market-data/news/briefs
POST /api/v1/market-data/source-events/news-permission-smoke
```

The stock sector membership route materializes a local SQLite reverse index from
sector constituents so repeated FNI holding reports can look up
`symbol -> concept memberships` without rescanning sector names. Pass
`sector_universe_limit: 0` to scan only gateway seed boards; timeout and upstream
failures return HTTP 200 degraded metadata with board-level diagnostics.

The fund profile and holdings routes cache normalized rows in SQLite, try
Tushare first, and fall back to EastMoney/Tiantian public fund positions. When
cache and upstream providers are unavailable, they return HTTP 200 degraded
metadata with provider-attempt diagnostics instead of mock holdings or long
socket waits.

The news route uses Tushare `news` only and returns
`PROVIDER_PERMISSION_REQUIRED` when the configured token lacks that permission.

Narrative source-event routes add a lightweight source lakehouse slice for FNI:
SEC EDGAR and CN disclosure metadata are labeled `trusted_fact`, public news is
`context_only`, and Stocktwits/community output is `heat_signal_only` and
disabled by default. Source events persist normalized metadata, fetch runs,
quality snapshots, evidence snippets, entity mentions, and Bronze blob
manifests in SQLite; a Docker Compose profile for Postgres + MinIO local
development is documented in `docs/runbooks/source-lakehouse-runtime.md`.

Run the gateway-backed FNI acceptance suite:

```bash
uv run market-gateway-fni-acceptance \
  --fni-root /Users/mikezhang/Coding/AI-Learning/fund-narrative-intelligence \
  --mode all
```

See `docs/runbooks/fni-gateway-acceptance.md` for the operational runbook.
See `docs/runbooks/background-service-and-backfill.md` for LaunchAgent and CYQ
backfill setup.
See `docs/runbooks/source-lakehouse-runtime.md` for the local source lakehouse
runtime profile.
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
