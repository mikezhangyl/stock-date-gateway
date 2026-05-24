# FNI Gateway Acceptance Runbook

This runbook validates that `fund-narrative-intelligence` uses this gateway over
HTTP instead of importing or sharing Python modules.

## Prerequisites

Install gateway dependencies:

```bash
uv sync --extra dev --extra provider
```

Put provider secrets in `.env.local` in this repository:

```bash
TUSHARE_TOKEN=...
```

Do not put the gateway token in FNI. FNI only needs to know the local facade URL.

## One-Command Acceptance

From `stock-data-gateway`:

```bash
uv run market-gateway-fni-acceptance \
  --fni-root /Users/mikezhang/Coding/AI-Learning/fund-narrative-intelligence \
  --mode all
```

The command starts the gateway on `http://127.0.0.1:8700` when it is not already
healthy, sets `TUSHARE_API_URL=http://127.0.0.1:8700/tushare` for the FNI
subprocess, runs:

- `scripts/validate_tushare_primary_acceptance.py`
- `scripts/validate_real_enriched_acceptance.py`

Success criteria:

- `ok` is `true`.
- The `tushare-primary` summary reports `tushare-valuation` and
  `tushare-financial-metrics`.
- Both Tushare layers report `source_url` as `http://127.0.0.1:8700/tushare`.
- Row counts are greater than zero.

## Existing Gateway

If a gateway is already running:

```bash
uv run uvicorn stock_data_gateway.main:app --host 127.0.0.1 --port 8700
uv run market-gateway-fni-acceptance \
  --fni-root /Users/mikezhang/Coding/AI-Learning/fund-narrative-intelligence \
  --mode tushare-primary \
  --no-start-gateway
```

## Direct FNI Usage

When running FNI manually:

```bash
TUSHARE_API_URL=http://127.0.0.1:8700/tushare \
uv run python scripts/validate_tushare_primary_acceptance.py \
  --output-dir outputs/tushare_primary_gateway_manual
```

## Cache Operations

Inspect current cache entries:

```bash
uv run market-gateway-cache inspect
```

Summarize request audit rows:

```bash
uv run market-gateway-cache audit --provider tushare
```

Clear one endpoint:

```bash
uv run market-gateway-cache clear \
  --provider tushare \
  --endpoint daily \
  --yes
```

Clear one semantic key:

```bash
uv run market-gateway-cache clear \
  --provider tushare \
  --endpoint daily \
  --instrument-id 000001.SZ \
  --date-key latest \
  --yes
```

`clear` requires `--yes` because it removes cached rows. Audit rows are retained
so operational history remains available.

## Troubleshooting

If acceptance fails before FNI starts, check gateway health:

```bash
curl http://127.0.0.1:8700/api/health
```

If FNI starts but Tushare layers do not point at the local gateway, check that
`TUSHARE_API_URL` is not overridden in FNI `.local.env`.

If cached rows have an old schema, clear the affected endpoint or rely on policy
`schema_version` changes to create fresh semantic keys.
