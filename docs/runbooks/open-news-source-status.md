# Open News Source Status

Gateway open news/index sources are metadata-only context sources. They cannot
promote evidence to `trusted_fact` without official corroboration.

## GDELT DOC API

- Source id: `gdelt_doc`
- Route: `/api/v1/market-data/source-events/open-news-index`
- Unified source kind: `open_news_index`
- Trust tier: `context_only`
- Retention: `metadata_only`
- Stability: `experimental`

Local verification on 2026-06-04 reached the public endpoint, but the upstream
returned a rate-limit text response instead of JSON:

`Please limit requests to one every 5 seconds...`

Gateway therefore treats GDELT as an experimental source with bounded limits,
short upstream timeouts, cache-backed source events, and structured degraded
responses for non-JSON, timeout, empty, or malformed upstream responses.

## Constraints

- No paid news provider dependency.
- No article body scraping.
- No browser automation.
- No sentiment model.
- No FNI direct source calls.
