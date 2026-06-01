# Endpoint Registry

This registry documents the current gateway-facing endpoint contract. It is the
reference for upstream services that need to request new fields, new endpoints,
or cache semantic changes.

## HTTP Surface

| Route | Method | Purpose |
| --- | --- | --- |
| `/api/health` | `GET` | Reports gateway and provider health. |
| `/tushare` | `POST` | Tushare-compatible facade backed by gateway cache and provider policies. |
| `/api/v1/market-data/tushare/daily` | `POST` | Normalized A-share daily bars. |
| `/api/v1/market-data/tushare/index-daily` | `POST` | Normalized index daily bars. |
| `/api/v1/market-data/tushare/fund-daily` | `POST` | Normalized fund/ETF daily bars. |
| `/api/v1/market-data/tushare/stock-basic` | `POST` | Normalized stock metadata. |
| `/api/v1/market-data/tushare/trade-cal` | `POST` | Normalized trading calendar. |
| `/api/v1/market-data/chips/cyq` | `POST` | Normalized CYQ chip distribution grouped by symbol/date. |
| `/api/v1/market-data/eastmoney/market-quotes` | `GET` | Normalized latest quote rows from EastMoney. |
| `/api/v1/market-data/eastmoney/northbound-capital` | `GET` | Normalized northbound capital rows from EastMoney datacenter. |
| `/api/v1/market-data/eastmoney/main-capital-flow` | `GET` | Normalized stock main-capital-flow ranking rows from EastMoney push2. |
| `/api/v1/market-data/akshare/sector-concepts` | `GET` | Normalized concept/sector rows from AkShare when the optional package is installed. |
| `/api/v1/market-data/akshare/limit-up-down` | `GET` | Normalized limit-up/limit-down counts from AkShare when available. |
| `/api/v1/market-data/sectors/concepts` | `GET` | Provider-neutral concept/sector ranking rows for FNI sector scanners. |
| `/api/v1/market-data/sectors/constituents` | `GET` | Provider-neutral concept/sector board constituent rows. |
| `/api/v1/market-data/stocks/sector-memberships` | `POST` | Provider-neutral stock-to-sector/concept membership rows backed by a local reverse index. |
| `/api/v1/market-data/funds/profile` | `GET` | Provider-neutral fund identity rows backed by local cache and Tushare/EastMoney fallback. |
| `/api/v1/market-data/funds/holdings` | `GET` | Provider-neutral latest fund stock holding rows backed by local cache and Tushare/EastMoney fallback. |
| `/api/v1/market-data/capital/northbound` | `GET` | Provider-neutral northbound capital flow row. |
| `/api/v1/market-data/capital/main-flow` | `GET` | Provider-neutral stock main-capital-flow ranking rows. |
| `/api/v1/market-data/etf/basic` | `GET` | Provider-neutral ETF identity and classification rows. |
| `/api/v1/market-data/etf/spot` | `GET` | Provider-neutral ETF spot ranking rows. |
| `/api/v1/market-data/etf/flow` | `GET` | Provider-neutral ETF capital-flow ranking rows. |
| `/api/v1/market-data/index/constituents` | `GET` | Provider-neutral benchmark constituent rows. |
| `/api/v1/market-data/margin/summary` | `GET` | Provider-neutral market margin summary row. |
| `/api/v1/market-data/margin/detail` | `GET` | Provider-neutral stock-level margin detail rows. |
| `/api/v1/market-data/market/limit-up-down` | `GET` | Provider-neutral market limit-up/limit-down count rows. |
| `/api/v1/market-data/market/dragon-tiger` | `GET` | Provider-neutral dragon-tiger list rows. |
| `/api/v1/market-data/fundamentals/earnings-calendar` | `GET` | Provider-neutral disclosure/event calendar rows. |
| `/api/v1/market-data/news/briefs` | `POST` | Tushare-backed structured news brief smoke endpoint. |
| `/api/v1/market-data/source-events/official-filings` | `GET` | Provider-neutral SEC EDGAR official filing metadata source events. |
| `/api/v1/market-data/source-events/official-disclosures` | `GET` | Provider-neutral CN official disclosure metadata source events. |
| `/api/v1/market-data/source-events/news-context` | `GET` | Provider-neutral public news context source events. |
| `/api/v1/market-data/source-events/social-heat` | `GET` | Provider-neutral community heat source events, disabled by default. |
| `/api/v1/market-data/source-events/news-permission-smoke` | `POST` | Tushare news permission and field-shape diagnostic report. |
| `/api/v1/market-data/jobs/daily-bars` | `POST` | Creates an async Tushare daily-bars scan job. |
| `/api/v1/market-data/jobs/breadth-window` | `POST` | Creates an async Tushare breadth-window cache-warming job. |
| `/api/v1/market-data/jobs` | `GET` | Lists async jobs with status, type, provider, endpoint, and timestamp filters. |
| `/api/v1/market-data/jobs/{job_id}` | `GET` | Returns async job status, progress, coverage, cache mode, and structured failures. |
| `/api/v1/market-data/jobs/{job_id}/cancel` | `POST` | Requests cancellation for an active async job without clearing cached rows. |
| `/api/v1/market-data/jobs/{job_id}/rows` | `GET` | Returns available normalized daily-bar rows for a job. |

`POST /tushare` accepts:

```json
{
  "api_name": "daily",
  "params": {},
  "fields": "comma,separated,fields",
  "token": "ignored-by-gateway"
}
```

The caller token is ignored. The gateway token is loaded from `.env`, `.env.local`,
or process environment.

Normalized routes return:

```json
{
  "data": {
    "rows": []
  },
  "meta": {
    "provider": "tushare",
    "endpoint": "daily",
    "cache": {
      "hit": false,
      "mode": "upstream"
    },
    "generated_at": "2026-05-25T00:00:00+00:00",
    "row_count": 0,
    "status": "ok"
  }
}
```

Allowed cache modes are `cache`, `upstream`, `stale_cache`, and `mixed`.

## Tushare Policy Registry

| Endpoint | Instrument Param | Date Param | Range Params | Date Key Role | Schema Version | Default Fields |
| --- | --- | --- | --- | --- | --- | --- |
| `daily` | `ts_code` | `trade_date` | `start_date`, `end_date` | `trade_date` | `2` | `ts_code`, `trade_date`, `open`, `high`, `low`, `close`, `pre_close`, `change`, `pct_chg`, `vol`, `amount` |
| `daily_basic` | `ts_code` | `trade_date` | `start_date`, `end_date` | `trade_date` | `2` | `ts_code`, `trade_date`, `close`, `turnover_rate`, `turnover_rate_f`, `volume_ratio`, `pe`, `pe_ttm`, `pb`, `ps`, `ps_ttm`, `dv_ratio`, `dv_ttm`, `total_share`, `float_share`, `free_share`, `total_mv`, `circ_mv` |
| `index_daily` | `ts_code` | `trade_date` | `start_date`, `end_date` | `trade_date` | `2` | `ts_code`, `trade_date`, `open`, `high`, `low`, `close`, `pre_close`, `change`, `pct_chg`, `vol`, `amount` |
| `fund_daily` | `ts_code` | `trade_date` | `start_date`, `end_date` | `trade_date` | `2` | `ts_code`, `trade_date`, `open`, `high`, `low`, `close`, `pre_close`, `change`, `pct_chg`, `vol`, `amount` |
| `trade_cal` | `exchange` | `cal_date` | `start_date`, `end_date` | `calendar_date` | `2` | `exchange`, `cal_date`, `is_open`, `pretrade_date` |
| `stock_basic` | `ts_code` | none | none | `snapshot_date` | `2` | `ts_code`, `symbol`, `name`, `area`, `industry`, `market`, `exchange`, `list_status`, `list_date` |
| `income` | `ts_code` | none | none | `snapshot_date` | `1` | `ts_code`, `ann_date`, `f_ann_date`, `end_date`, `report_type`, `comp_type`, `total_revenue`, `revenue`, `operate_profit`, `total_profit`, `n_income`, `n_income_attr_p` |
| `fina_indicator` | `ts_code` | none | none | `snapshot_date` | `1` | `ts_code`, `ann_date`, `end_date`, `eps`, `dt_eps`, `total_revenue_ps`, `q_roe`, `roe`, `grossprofit_margin`, `debt_to_assets`, `tr_yoy`, `netprofit_yoy`, `dt_netprofit_yoy` |
| `cyq_chips` | `ts_code` | `trade_date` | `start_date`, `end_date` | `trade_date` | `1` | `ts_code`, `trade_date`, `price`, `percent` |

## Special Behavior

`daily`, `daily_basic`, `index_daily`, and `fund_daily` support latest-style
requests. If a caller sends `ts_code` without `trade_date`, `start_date`, or
`end_date`, the provider client expands the upstream Tushare request to a recent
window and the gateway stores the semantic cache key as `latest`.

`cyq_chips` range requests are expanded through `trade_cal`, so only open
trading days are fetched.

Comma-separated Tushare `ts_code` values are split into per-symbol semantic cache
queries before calling the provider. This preserves Tushare facade compatibility
without storing multi-symbol cache rows under one ambiguous instrument key.

Normalized Tushare POST bodies accept `force_refresh: true` to bypass current
cache records and fetch a new version. If `allow_stale: true`, a refresh failure
returns the previous cache record with cache mode `stale_cache`.

EastMoney and AkShare adapters are implemented as optional provider adapters.
EastMoney quote fetches use the public push2 quote API. AkShare routes use the
optional `akshare` package when available and otherwise fall back to EastMoney
public board/quote data so conformance endpoints do not return empty success
payloads.

The provider-neutral Can-Do routes expose stable FNI-facing names over current
provider implementations:

```text
GET /api/v1/market-data/sectors/concepts?trade_date=2026-05-22&limit=20
GET /api/v1/market-data/sectors/constituents?sector_name=机器人&trade_date=2026-05-22&limit=50
POST /api/v1/market-data/stocks/sector-memberships
GET /api/v1/market-data/funds/profile?fund_code=161725
GET /api/v1/market-data/funds/holdings?fund_code=161725&limit=10
GET /api/v1/market-data/capital/northbound?trade_date=2026-05-22
GET /api/v1/market-data/capital/main-flow?trade_date=2026-05-22&limit=50
GET /api/v1/market-data/etf/basic?market=cn&limit=50
GET /api/v1/market-data/etf/spot?limit=20
GET /api/v1/market-data/etf/flow?trade_date=2026-05-22&limit=50
GET /api/v1/market-data/index/constituents?index_symbol=000300.SH&trade_date=2026-05-22&limit=50
GET /api/v1/market-data/margin/summary?trade_date=2026-05-22
GET /api/v1/market-data/margin/detail?trade_date=2026-05-22&limit=50
GET /api/v1/market-data/market/limit-up-down?trade_date=2026-05-22
GET /api/v1/market-data/market/dragon-tiger?trade_date=2026-05-22&limit=50
GET /api/v1/market-data/fundamentals/earnings-calendar?start_date=2026-05-22&end_date=2026-06-05&limit=50
GET /api/v1/market-data/source-events/official-filings?cik=0000320193&limit=20
GET /api/v1/market-data/source-events/official-disclosures?symbol=000001&start_date=2026-05-01&end_date=2026-06-01
GET /api/v1/market-data/source-events/news-context?src=sina&start_datetime=2026-06-01%2009:00:00&end_datetime=2026-06-01%2010:00:00
GET /api/v1/market-data/source-events/social-heat?symbol=AAPL&enabled=true
POST /api/v1/market-data/news/briefs
POST /api/v1/market-data/source-events/news-permission-smoke
```

Sector/concept and limit-up/down routes currently use the AkShare adapter with
EastMoney fallback. ETF spot uses AkShare `fund_etf_spot_em` with EastMoney
fallback. News briefs only use Tushare `news`; permission failures return a
structured `PROVIDER_PERMISSION_REQUIRED` error instead of empty success, and
the gateway does not scrape news sites as fallback.

Flow/event capability routes require `trade_date`. Northbound capital uses
EastMoney `RPT_MUTUAL_DEAL_HISTORY` and returns `trade_date`,
`net_buy_amount`, `source`, plus optional buy/sell/provider fields. Main capital
flow uses EastMoney push2 ranking fields and returns `trade_date`, `symbol`,
`main_net_inflow`, `source`, plus optional name/change/amount/provider fields.
ETF flow uses AkShare `fund_etf_fund_flow_rank_em` when available and EastMoney
push2 ETF flow ranking as fallback. Dragon-tiger uses AkShare
`stock_lhb_detail_em` when available and EastMoney `RPT_DAILYBILLBOARD_DETAILS`
as fallback. For these flow/event routes, upstream failure, missing adapters,
schema misses, and indistinguishable empty responses are surfaced as degraded
normalized responses with `meta.warning`; they are not returned as silent empty
success payloads.

Structure-mapping routes are Can-Do / auxiliary data surfaces. Sector
constituents use AkShare `stock_board_concept_cons_em` with EastMoney board
constituent fallback. ETF basic uses AkShare `fund_etf_category_ths` with
EastMoney ETF identity fallback. Index constituents use AkShare
`index_stock_cons_weight_csindex`. Margin summary/detail use AkShare margin
exchange functions and normalize Shenzhen summary amounts to yuan. Earnings
calendar uses AkShare `stock_notice_report` over the requested date range and
returns disclosure/event rows; a successful empty response means no rows were
returned for the requested window.

Stock sector memberships expose a reverse lookup over the sector constituent
surface. The request body requires `symbols` and `trade_date`, and accepts
`sector_types`, `limit_per_symbol`, `force_refresh`, and
`sector_universe_limit`, `timeout_seconds`, and `upstream_timeout_seconds`.
When `sector_universe_limit = 0`, the route scans only the gateway seed boards
and does not call the broader sector universe endpoint. The current Can-Do
implementation supports `concept` memberships and reports unsupported types
such as `industry` in `meta.coverage.unsupported_sector_types` plus
`meta.warning`; it does not silently drop them. Rows include `symbol`,
`sector_name`, `sector_type`, `source`, and optional `name`, `sector_code`,
`trade_date`, `pct_change`, `weight`, `provider`, and `membership_source`.

The reverse index is persisted in SQLite tables
`stock_sector_membership_rows` and `stock_sector_membership_coverage` in the
same `market_data.sqlite3` file as the read-through cache. The first request
materializes rows from AkShare/EastMoney-backed sector constituents; repeated
requests for known rows or known misses return `cache.mode = cache`. If the
board universe or constituent scan fails before any membership rows can be
materialized, the normalized response is returned with `meta.status =
degraded` and a structured `meta.warning` instead of silent empty success.
Symbols that scan successfully but have no memberships are omitted from
`data.rows` and listed under `meta.coverage.missing_symbols`. Coverage metadata
also includes per-board diagnostics in `meta.coverage.board_results`, with
reasons such as `upstream_failed`, `timeout`, `empty_board`, and
`symbol_uncovered`. Timeout and provider failures are HTTP 200 degraded
responses; only invalid request parameters should be HTTP 400.

Fund profile and fund holdings expose the gateway-owned source layer needed by
FNI's fund-code driven report flow. Both routes require `fund_code`; holdings
also accepts `limit`, `force_refresh`, `timeout_seconds`, and
`upstream_timeout_seconds`. The gateway reads local SQLite tables
`fund_profile_rows`, `fund_holding_rows`, and `fund_holding_coverage` first,
then tries Tushare (`fund_basic` / `fund_portfolio`) and falls back to the
EastMoney/Tiantian public fund position endpoint. Successful rows are normalized
with `fund_code`, `fund_name`/`stock_code`, `weight`, `as_of_date`, `source`,
and provider metadata. If cache and all upstream providers fail, the route
returns HTTP 200 with `meta.status=degraded`, `meta.warning`, and
`meta.coverage.provider_attempts`; invalid parameters remain HTTP 400.

Narrative source-event routes use the same normalized envelope and add
source-specific metadata: `meta.source`, `meta.provider_attempts`,
`meta.degradation_events`, `meta.source_quality`, `meta.trust_tier`,
`meta.license_scope`, `meta.retention_policy`, `meta.raw_storage_policy`,
`meta.parser_version`, and `meta.cache_hit`. Rows include the source-event
contract fields requested by FNI: `source_event_id`, `source_id`,
`source_type`, `provider`, `trust_tier`, `entity_type`, `entity_id`,
`entity_name`, `market`, `event_type`, `event_time`, `published_at`,
`fetched_at`, `title`, `summary`, `source_url`, `provider_item_id`,
`raw_hash`, `blob_uri`, `license_scope`, `retention_policy`, `confidence`,
and `degradation_warnings` when available.

SEC EDGAR official filings and CN official disclosure metadata are labeled
`trusted_fact`. Public news context is labeled `context_only`; it keeps snippets
and metadata and does not retain full text by default. Stocktwits/social output
is labeled `heat_signal_only`, never satisfies trusted-evidence requirements,
and is disabled unless the caller passes `enabled=true` or the gateway operator
sets `GATEWAY_SOURCE_EVENTS_ENABLE_SOCIAL_HEAT=true`.

The lightweight source lakehouse slice persists normalized metadata in SQLite
tables `source_registry`, `source_fetch_runs`, `source_documents`,
`source_events`, `evidence_spans`, `entity_mentions`, `resolved_entities`,
`source_quality_snapshots`, and `source_blob_manifests`. A Docker Compose
profile for local Postgres and MinIO is documented in
`docs/runbooks/source-lakehouse-runtime.md`; this is a Mac/local development
runtime, not a Kubernetes or cloud deployment.

Data-fetching routes are protected by a bounded fetch slot, a per-request symbol
limit, and a request deadline. The health route stays lightweight and does not
call upstream providers.

Large daily-bar and breadth-window scans should use the async job routes. Job
creation is idempotent for the same semantic request and returns quickly with
`202 Accepted`. The in-process worker uses bounded internal batches and persists
job status, rows, progress, failures, and coverage metadata to SQLite for later
polling after process restart.

Active jobs can be cancelled. Cancellation is idempotent, preserves already
fetched rows, and stops the worker before the next symbol. If the gateway
restarts while a job is active, the job is recovered as `interrupted` with
structured status instead of disappearing from the job list.

Repeating the same semantic request after a job ended as `cancelled`, `failed`,
or `interrupted` reactivates compatible partial work instead of returning the
old terminal status. Previously fetched rows and cache entries are preserved,
and the resumed worker skips already completed symbols.

Job queue controls:

```text
GATEWAY_JOB_QUEUE_LIMIT=2
GATEWAY_JOB_MAX_SYMBOLS=5000
GATEWAY_JOB_MAX_BATCH_SIZE=100
GATEWAY_JOB_MAX_LOOKBACK_TRADING_DAYS=260
```

Provider dataframe missing values are normalized to JSON-safe `null` before the
facade response is serialized.

## Change Rules

Use these rules when changing the registry:

- Add fields to `default_fields` before upstream callers depend on projecting
  them.
- Bump `schema_version` when default fields, units, adjustment semantics, date
  interpretation, or provider query semantics change.
- Add fake-provider tests for every newly supported endpoint or field family.
- Add facade/cache projection tests for fields requested by an upstream service.
- Add live validation or an upstream acceptance check when the endpoint is
  business-critical.

## Upstream Request Checklist

When requesting a registry change, provide:

- Endpoint name and provider.
- Required params and optional params.
- Required fields, types, units, and null semantics.
- Cache key semantics: instrument, date key, range behavior, and schema version.
- Freshness expectations and fallback policy.
- Acceptance command and expected source/provider assertions.
