from __future__ import annotations

import json

import pytest

from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode
from stock_data_gateway.providers.gdelt.adapter import GdeltProvider


class FakeHttpResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> "FakeHttpResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def test_gdelt_doc_articles_maps_metadata_rows() -> None:
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request.full_url, request.headers, timeout))
        return FakeHttpResponse(
            json.dumps(
                {
                    "articles": [
                        {
                            "title": "Apple expands AI infrastructure",
                            "url": "https://example.com/apple-ai",
                            "domain": "example.com",
                            "seendate": "20260604120000",
                            "language": "English",
                            "sourceCountry": "United States",
                        }
                    ]
                }
            ).encode("utf-8")
        )

    provider = GdeltProvider(urlopen_fn=fake_urlopen, timeout_seconds=4.0)

    response = provider.fetch(
        "doc_articles",
        {
            "query": "Apple AI",
            "start_datetime": "2026-06-04T00:00:00Z",
            "end_datetime": "2026-06-04T23:59:59Z",
            "limit": 1,
        },
    )

    rows = response.rows()
    assert rows[0]["title"] == "Apple expands AI infrastructure"
    assert rows[0]["source_url"] == "https://example.com/apple-ai"
    assert rows[0]["source_domain"] == "example.com"
    assert rows[0]["published_at"] == "20260604120000"
    assert rows[0]["language"] == "English"
    assert rows[0]["metadata_only"] is True
    assert "query=Apple+AI" in requests[0][0]
    assert "startdatetime=20260604000000" in requests[0][0]
    assert requests[0][2] == 4.0


def test_gdelt_empty_articles_is_gateway_error() -> None:
    provider = GdeltProvider(urlopen_fn=lambda request, timeout: FakeHttpResponse(b'{"articles": []}'))

    with pytest.raises(GatewayError) as raised:
        provider.fetch("doc_articles", {"query": "Apple", "limit": 1})

    assert raised.value.code == GatewayErrorCode.EMPTY_DATA


def test_gdelt_non_json_response_is_gateway_error() -> None:
    provider = GdeltProvider(urlopen_fn=lambda request, timeout: FakeHttpResponse(b"rate limited"))

    with pytest.raises(GatewayError) as raised:
        provider.fetch("doc_articles", {"query": "Apple", "limit": 1})

    assert raised.value.code == GatewayErrorCode.PROVIDER_UNAVAILABLE


def test_gdelt_malformed_articles_are_skipped() -> None:
    provider = GdeltProvider(
        urlopen_fn=lambda request, timeout: FakeHttpResponse(
            json.dumps({"articles": [{"title": "missing url"}, {"url": "https://example.com/missing-title"}]}).encode(
                "utf-8"
            )
        )
    )

    with pytest.raises(GatewayError) as raised:
        provider.fetch("doc_articles", {"query": "Apple", "limit": 2})

    assert raised.value.code == GatewayErrorCode.EMPTY_DATA
