# Industry Media Source Status

Gateway industry-media sources are controlled RSS/feed pilots for narrative
context discovery. They are not official fact sources.

## PV-Tech RSS Pilot

- Source id: `pv_tech_news`
- Route: `/api/v1/market-data/source-events/industry-media`
- Unified source kind: `industry_media`
- Trust tier: `research_context`
- Retention: `metadata_only`
- Parser: `rss`
- Status: `pilot`

Local verification on 2026-06-04 confirmed `https://www.pv-tech.org/feed/`
returned HTTP 200 with RSS content-type. Gateway fetches only feed metadata and
stores summary/excerpt fields only when the registry allows them.

## Constraints

- No broad crawl.
- No dynamic browser.
- No CAPTCHA/login/paywall access.
- No FNI direct crawling.
- No `trusted_fact` promotion without official corroboration.
