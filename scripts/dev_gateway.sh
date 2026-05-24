#!/usr/bin/env bash
set -euo pipefail

host="${GATEWAY_HOST:-127.0.0.1}"
port="${GATEWAY_PORT:-8700}"

if [[ ! -f ".env.local" && -z "${TUSHARE_TOKEN:-}" ]]; then
  echo "warning: TUSHARE_TOKEN is not configured; /api/health will report the Tushare provider as unavailable" >&2
fi

exec uv run uvicorn stock_data_gateway.main:app --host "${host}" --port "${port}"
