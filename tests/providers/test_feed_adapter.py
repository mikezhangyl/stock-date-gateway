from __future__ import annotations

import pytest

from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode
from stock_data_gateway.providers.feed.adapter import FeedProvider
from stock_data_gateway.source_registry import official_source_seed_rows


class FakeHttpResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> "FakeHttpResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def _source(**overrides):
    row = official_source_seed_rows()[0]
    return {**row, **overrides}


def test_feed_provider_parses_rss_items_into_source_events() -> None:
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request.full_url, request.headers, timeout))
        return FakeHttpResponse(
            b"""
            <rss version="2.0">
              <channel>
                <item>
                  <title>SEC charges issuer</title>
                  <link>https://www.sec.gov/news/press-release/2026-1</link>
                  <guid>sec-2026-1</guid>
                  <pubDate>Thu, 04 Jun 2026 12:00:00 GMT</pubDate>
                  <description>Official metadata summary.</description>
                </item>
              </channel>
            </rss>
            """
        )

    provider = FeedProvider(urlopen_fn=fake_urlopen)

    response = provider.fetch("feed_events", {"source": _source(), "limit": 1})

    rows = response.rows()
    assert rows[0]["source_id"] == "sec_press_releases"
    assert rows[0]["provider"] == "sec"
    assert rows[0]["title"] == "SEC charges issuer"
    assert rows[0]["source_url"] == "https://www.sec.gov/news/press-release/2026-1"
    assert rows[0]["provider_item_id"] == "sec-2026-1"
    assert rows[0]["trust_tier"] == "trusted_fact"
    assert rows[0]["metadata_only"] is True
    assert requests[0][0] == "https://www.sec.gov/news/pressreleases.rss"
    assert requests[0][2] == 8.0


def test_feed_provider_parses_atom_entries() -> None:
    def fake_urlopen(request, timeout):
        return FakeHttpResponse(
            b"""
            <feed xmlns="http://www.w3.org/2005/Atom">
              <entry>
                <id>tag:example.gov,2026:policy-1</id>
                <title>Policy update</title>
                <link href="https://example.gov/policy-1" />
                <updated>2026-06-04T12:00:00Z</updated>
                <summary>Allowed excerpt.</summary>
              </entry>
            </feed>
            """
        )

    provider = FeedProvider(urlopen_fn=fake_urlopen)

    response = provider.fetch("feed_events", {"source": _source(provider="example_agency"), "limit": 5})

    rows = response.rows()
    assert rows[0]["provider"] == "example_agency"
    assert rows[0]["title"] == "Policy update"
    assert rows[0]["source_url"] == "https://example.gov/policy-1"
    assert rows[0]["published_at"] == "2026-06-04T12:00:00Z"
    assert rows[0]["summary"] == "Allowed excerpt."


def test_feed_provider_parses_sitemap_urls() -> None:
    def fake_urlopen(request, timeout):
        return FakeHttpResponse(
            b"""
            <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <url>
                <loc>https://example.gov/notices/1</loc>
                <lastmod>2026-06-04</lastmod>
              </url>
            </urlset>
            """
        )

    provider = FeedProvider(urlopen_fn=fake_urlopen)

    response = provider.fetch(
        "feed_events",
        {"source": _source(parser_strategy="sitemap", provider="example_exchange"), "limit": 1},
    )

    rows = response.rows()
    assert rows[0]["provider"] == "example_exchange"
    assert rows[0]["title"] == "https://example.gov/notices/1"
    assert rows[0]["source_url"] == "https://example.gov/notices/1"
    assert rows[0]["published_at"] == "2026-06-04"


def test_feed_provider_invalid_xml_is_gateway_error() -> None:
    provider = FeedProvider(urlopen_fn=lambda request, timeout: FakeHttpResponse(b"<rss><broken>"))

    with pytest.raises(GatewayError) as raised:
        provider.fetch("feed_events", {"source": _source(), "limit": 1})

    assert raised.value.code == GatewayErrorCode.PROVIDER_UNAVAILABLE
