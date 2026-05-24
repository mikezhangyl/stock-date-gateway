# Stock Data Gateway

Local Market Data Gateway for reusable market data access across local research projects.

The first stable facade is:

```text
POST http://127.0.0.1:8700/tushare
```

The service is intentionally local-first: SQLite cache, fake-provider tests by default, and live provider validation only when explicitly enabled.

## Development

```bash
uv sync --extra dev --extra provider
uv run pytest
uv run uvicorn stock_data_gateway.main:app --host 127.0.0.1 --port 8700
```

Put provider secrets in `.env.local`; the gateway owns the real Tushare token and
does not trust or forward caller-supplied tokens.

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
`stock_basic`, `income`, and `fina_indicator`.

Run the gateway-backed FNI acceptance suite:

```bash
uv run market-gateway-fni-acceptance \
  --fni-root /Users/mikezhang/Coding/AI-Learning/fund-narrative-intelligence \
  --mode all
```

See `docs/runbooks/fni-gateway-acceptance.md` for the operational runbook.

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
