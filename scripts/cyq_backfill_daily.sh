#!/usr/bin/env bash
set -euo pipefail

root="${GATEWAY_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "${root}"

if [[ -f ".env.local" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env.local"
  set +a
fi

default_start_date() {
  date -v-7d +%Y%m%d 2>/dev/null || date -d "7 days ago" +%Y%m%d
}

: "${CYQ_TS_CODES:?CYQ_TS_CODES is required, for example: CYQ_TS_CODES=600519.SH,000001.SZ}"

start_date="${CYQ_START_DATE:-$(default_start_date)}"
end_date="${CYQ_END_DATE:-$(date +%Y%m%d)}"

IFS=',' read -r -a ts_codes <<< "${CYQ_TS_CODES}"
for raw_code in "${ts_codes[@]}"; do
  ts_code="$(echo "${raw_code}" | xargs)"
  if [[ -z "${ts_code}" ]]; then
    continue
  fi
  echo "cyq backfill: ${ts_code} ${start_date}-${end_date}"
  uv run market-gateway-backfill \
    --ts-code "${ts_code}" \
    --start-date "${start_date}" \
    --end-date "${end_date}"
done
