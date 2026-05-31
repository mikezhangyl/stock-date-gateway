# FNI Narrative Source + Lightweight Lakehouse Capability Change Request - 2026-06-01

Requester: Fund Narrative Intelligence

Target project: `stock-data-gateway`

## Boundary Correction

FNI and gateway previously agreed that external data-source access belongs in
`stock-data-gateway`. FNI should not implement new direct SEC EDGAR, CNINFO,
public-news, Stocktwits, Tushare, paid-news, or crawler adapters.

FNI's responsibility is:

- define consumer contracts and required normalized schemas;
- consume gateway endpoints;
- render reports, source quality labels, and diagnostics;
- validate gateway contract conformance;
- request new data-source capabilities through gateway change-request docs.

Gateway's responsibility is:

- upstream source adapters;
- credentials and provider permissions;
- request pacing, retry, cache, degradation semantics;
- raw/source storage;
- provider-specific schema normalization;
- source quality and trust metadata;
- provider-neutral REST endpoints.

## Product Need

Narrative Service needs fresh, evidence-backed source events so users can answer:

> 今天/最近市场上出现了哪些新叙事？这些叙事有什么证据？证据来自官方事实、授权新闻、公开上下文，还是社区热度？

The requested gateway capabilities should support a lightweight source
lakehouse: raw source material, normalized source events, evidence snippets, and
source quality metadata.

## Requested Gateway Capability Pack

### 1. Official Filing / Disclosure Source Events

Initial upstream candidates:

- SEC EDGAR submissions API.
- CNINFO announcements.
- Later: SSE/SZSE/HKEX disclosure surfaces.

FNI feasibility checks:

- SEC EDGAR `https://data.sec.gov/submissions/CIK0000320193.json` returned
  Apple metadata with 1000 recent filing rows; latest sampled filing date was
  `2026-05-29`.
- Existing CNINFO access returned two recent `000001` announcements over a
  30-day window, including a `2026-05-22` 平安银行 shareholder-meeting legal
  opinion PDF.

Desired normalized route examples:

```text
GET /api/v1/market-data/source-events/official-filings
GET /api/v1/market-data/source-events/official-disclosures
```

Minimum normalized row fields:

```text
source_event_id
source_id
source_type
provider
trust_tier
entity_type
entity_id
entity_name
market
event_type
event_time
published_at
fetched_at
title
summary
source_url
provider_item_id
raw_hash
blob_uri
license_scope
retention_policy
confidence
degradation_warnings
```

Trust rule:

- SEC EDGAR / CNINFO / exchange disclosures may be labeled `trusted_fact`.
- If only metadata is parsed and the PDF/body is not parsed, include
  `metadata_only=true`.

### 2. Public News Context Source Events

Initial upstream candidates:

- Tushare `news` when permission is confirmed.
- Google News RSS only if gateway accepts it as a context source.
- Sina Finance / STCN / public finance portals after source-policy review.

FNI feasibility checks:

- Google News RSS returned 100 results for an A-share semiconductor query.
- Existing Sina Finance roll parsing returned rows but also navigation/homepage
  noise, so parser/source-quality cleanup is required.

Desired normalized route:

```text
GET /api/v1/market-data/source-events/news-context
```

Required semantics:

- public-news rows are `context_only`, not `trusted_fact`;
- provider/source-domain quality label is included;
- parser health and skipped/noise count are reported;
- full text is not retained unless permission allows it.

### 3. Social / Community Heat Source Events

Initial upstream candidate:

- Stocktwits controlled symbol stream pilot.

FNI feasibility check:

- `https://api.stocktwits.com/api/2/streams/symbol/AAPL.json?limit=5`
  returned HTTP 200 and five messages.

Desired normalized route:

```text
GET /api/v1/market-data/source-events/social-heat
```

Required semantics:

- always label output `heat_signal_only`;
- never satisfy trusted-evidence requirements;
- include source terms/rate-limit metadata;
- do not store unnecessary user profile data;
- default disabled unless explicitly enabled.

### 4. Tushare News Permission Smoke

FNI needs gateway to verify current Tushare credentials for `news` access.

Requested smoke:

- small time window;
- candidate `src` values from official docs, such as `sina`, `wallstreetcn`,
  `10jqka`, `eastmoney`, `yicai`, `cls`, or current supported values;
- report rows returned, fields present, latency, permission failure reason, and
  throttling behavior.

Desired route or probe:

```text
POST /api/v1/market-data/source-events/news-permission-smoke
```

or an equivalent gateway validation report.

### 5. Lightweight Source Lakehouse Runtime

Because gateway owns external source ingestion, gateway should own the source
lakehouse runtime.

Requested local runtime:

- Docker Compose profile for Mac development.
- Postgres for relational control plane.
- MinIO or S3-compatible object store for raw Bronze blobs.
- Named volumes or documented project-local bind mounts.
- No host-machine bare database install requirement.
- No Kubernetes / production cloud deployment in this slice.

Layering:

```text
Bronze: immutable raw provider payloads/files, when license permits
Silver: normalized source documents/events/evidence/entity tables
Gold: provider-neutral source-event/read-model endpoints for consumers
```

Minimum storage responsibilities:

- `source_registry`
- `source_fetch_runs`
- `source_documents`
- `source_events`
- `evidence_spans`
- `entity_mentions`
- `resolved_entities`
- `source_quality_snapshots`
- blob manifest / content-addressed raw object paths

FNI should only store consumer artifacts and report outputs, not canonical raw
upstream source data.

## Provider-Neutral Envelope

All gateway responses should use the existing normalized envelope style:

```text
rows
meta.source
meta.provider
meta.status
meta.degradation_events
meta.coverage
meta.provider_attempts
```

Additional requested metadata:

```text
meta.source_quality
meta.trust_tier
meta.license_scope
meta.retention_policy
meta.raw_storage_policy
meta.parser_version
meta.cache_hit
```

## FNI Consumer Contract

After gateway lands routes, FNI will:

- update its gateway consumer contract;
- add lightweight provider-neutral client wrappers;
- add live probes and Chinese HTML capability reports;
- render source quality and trust labels in Narrative Radar / digest surfaces;
- avoid direct external source calls except existing legacy compatibility shims.

## Explicit Non-Goals

- No direct external source adapters in FNI.
- No browser automation, CAPTCHA bypass, stealth browser, or proxy evasion.
- No full-text storage of paid/news/social sources unless license explicitly
  allows it.
- No social/community source promoted to trusted fact without official or
  licensed corroboration.
- No vector DB or search engine requirement in the first gateway runtime slice.

## Related FNI Linear Corrections

The following FNI issues were marked moved/canceled as direct FNI implementation
items and should be treated as gateway capability requests instead:

- `MIK-235`: SEC EDGAR official filing source capability.
- `MIK-236`: CNINFO official disclosure event classifier.
- `MIK-237`: public news context source-quality.
- `MIK-238`: Stocktwits heat-signal controlled source.
- `MIK-246`: source storage schema/repository.
- `MIK-247`: raw zone/blob manifest.
- `MIK-249`: Docker local source lakehouse runtime.

