# M21 live topic source-events smoke

Date: 2026-06-08

Scope: `MIK-296` improves the existing unified Gateway source-event query for the first FNI live review topics only:

- `AI infrastructure`
- `solar/storage`

Gateway remains the owner of upstream access. FNI should call Gateway only.

## Smoke commands

Run with a local Gateway server:

```bash
curl -sS 'http://127.0.0.1:8700/api/v1/market-data/narrative/source-events?keyword=AI%20infrastructure&limit=10&upstream_timeout_seconds=3'
curl -sS 'http://127.0.0.1:8700/api/v1/market-data/narrative/source-events?keyword=solar%2Fstorage&limit=10&upstream_timeout_seconds=3'
```

The selected topic profile expands the default source kinds to:

```json
["official_filings", "official_sources", "news_context", "open_news_index", "industry_media"]
```

It does not add paid providers, full article extraction, FNI rendering, or social heat by default.

## Expected useful-row shape

When Gateway finds usable rows, each row should expose stable fields FNI can render:

```json
{
  "source_event_id": "gdelt:example",
  "source_type": "news",
  "source_provider": "gdelt",
  "source_url": "https://example.com/ai-infrastructure",
  "title": "Hyperscalers expand AI infrastructure data centers",
  "trust_tier": "context_only",
  "source_quality": "open_news_index_experimental",
  "narrative_hints": ["AI infrastructure", "data center"],
  "provider_metadata": {
    "source_kind": "open_news_index",
    "topic_profile": {
      "topic_id": "ai_infrastructure",
      "canonical_topic": "AI infrastructure"
    },
    "governance": {
      "owner_service": "stock-data-gateway"
    }
  }
}
```

## Expected degraded/no-data shape

When Gateway can fetch raw rows but none are usable for the selected topic, the route should stay HTTP 200 and return structured diagnostics:

```json
{
  "data": {"rows": []},
  "meta": {
    "status": "degraded",
    "owner_service": "stock-data-gateway",
    "warning": {
      "code": "NO_SELECTED_TOPIC_SOURCE_EVENTS",
      "topic_id": "solar_storage"
    },
    "topic_diagnostics": {
      "raw_row_count": 1,
      "usable_row_count": 0,
      "source_kind_statuses": {
        "open_news_index": {
          "status": "ok",
          "raw_row_count": 1,
          "usable_row_count": 0
        }
      }
    }
  }
}
```

This degraded response is consumable by FNI and should be shown as a source coverage gap, not hidden.
