# Stock Data Gateway

Local Market Data Gateway for reusable market data access across local research projects.

The first stable facade is:

```text
POST http://127.0.0.1:8700/tushare
```

The service is intentionally local-first: SQLite cache, fake-provider tests by default, and live provider validation only when explicitly enabled.

## Development

```bash
uv sync --extra dev
uv run pytest
uv run uvicorn stock_data_gateway.main:app --host 127.0.0.1 --port 8700
```

## Live Validation

Live Tushare checks require both a token and an explicit opt-in:

```bash
RUN_LIVE_PROVIDER_TESTS=1 TUSHARE_TOKEN=... uv run market-gateway-validate --live
```

Offline/fake validation does not require a token:

```bash
uv run market-gateway-validate --fake
```
