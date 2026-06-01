# Source Lakehouse Runtime

This runbook covers the local development runtime for FNI narrative source
events. Gateway owns source ingestion, raw/source storage policy, source quality
metadata, and provider-neutral source-event routes. FNI should consume the
gateway HTTP contract and keep only report artifacts.

## Start Local Runtime

```bash
docker compose -f docker-compose.source-lakehouse.yml --profile source-lakehouse up -d
```

Services:

| Service | Purpose | Default local URL |
| --- | --- | --- |
| `source-postgres` | Relational control plane for future source registry/fetch-run/event tables | `127.0.0.1:8742` |
| `source-minio` | S3-compatible Bronze raw object store | `http://127.0.0.1:9000` |
| `source-minio-init` | Creates the `source-bronze` bucket | one-shot |

The current gateway implementation also creates a lightweight SQLite source
lakehouse in `market_data.sqlite3` for local can-do operation:

```text
source_registry
source_fetch_runs
source_documents
source_events
evidence_spans
entity_mentions
resolved_entities
source_quality_snapshots
source_blob_manifests
```

## Source Event Routes

```text
GET  /api/v1/market-data/source-events/official-filings?cik=0000320193&limit=20
GET  /api/v1/market-data/source-events/official-disclosures?symbol=000001&start_date=2026-05-01&end_date=2026-06-01
GET  /api/v1/market-data/source-events/news-context?src=sina&start_datetime=2026-06-01%2009:00:00&end_datetime=2026-06-01%2010:00:00
GET  /api/v1/market-data/source-events/social-heat?symbol=AAPL&enabled=true
POST /api/v1/market-data/source-events/news-permission-smoke
```

Trust semantics:

- SEC EDGAR and CN official disclosure metadata are `trusted_fact`.
- Tushare/public news context is `context_only`.
- Stocktwits/community rows are `heat_signal_only` and are disabled unless
  explicitly enabled with `enabled=true` or
  `GATEWAY_SOURCE_EVENTS_ENABLE_SOCIAL_HEAT=true`.

Raw storage policy:

- SEC EDGAR filing metadata can emit Bronze blob manifest paths.
- News and social context keep metadata/snippets only by default.
- Paid/news/social full text must not be retained unless a future source policy
  explicitly permits it.

## Smoke

```bash
curl -sS 'http://127.0.0.1:8700/api/v1/market-data/source-events/official-filings?cik=0000320193&limit=5'

curl -sS -X POST http://127.0.0.1:8700/api/v1/market-data/source-events/news-permission-smoke \
  -H 'Content-Type: application/json' \
  -d '{"src_values":["sina"],"start_datetime":"2026-06-01 09:00:00","end_datetime":"2026-06-01 10:00:00","limit_per_src":1}'
```

Validation:

```bash
uv run ruff check .
uv run pytest
```
