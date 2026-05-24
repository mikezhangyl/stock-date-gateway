#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${root}"

if [[ -f ".env.local" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env.local"
  set +a
fi

host="${GATEWAY_HOST:-127.0.0.1}"
port="${GATEWAY_PORT:-8700}"

if [[ ! -f ".env.local" && -z "${TUSHARE_TOKEN:-}" ]]; then
  echo "warning: TUSHARE_TOKEN is not configured; /api/health will report the Tushare provider as unavailable" >&2
fi

exec uv run uvicorn stock_data_gateway.main:app --host "${host}" --port "${port}"
