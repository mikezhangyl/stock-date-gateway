from __future__ import annotations

import json

import pytest

from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode
from stock_data_gateway.providers.sec_edgar.adapter import SecEdgarProvider


class FakeHttpResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def __enter__(self) -> "FakeHttpResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_sec_edgar_official_filings_maps_recent_submissions() -> None:
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request.full_url, request.headers, timeout))
        return FakeHttpResponse(
            {
                "cik": "320193",
                "name": "Apple Inc.",
                "tickers": ["AAPL"],
                "filings": {
                    "recent": {
                        "accessionNumber": ["0000320193-26-000001"],
                        "filingDate": ["2026-05-29"],
                        "reportDate": ["2026-03-31"],
                        "form": ["10-Q"],
                        "primaryDocument": ["aapl-20260529.htm"],
                        "primaryDocDescription": ["10-Q quarterly report"],
                    }
                },
            }
        )

    provider = SecEdgarProvider(urlopen_fn=fake_urlopen, timeout_seconds=3.5)

    response = provider.fetch("official_filings", {"cik": "320193", "limit": 1})

    rows = response.rows()
    assert rows[0]["cik"] == "0000320193"
    assert rows[0]["entity_name"] == "Apple Inc."
    assert rows[0]["company_name"] == "Apple Inc."
    assert rows[0]["ticker"] == "AAPL"
    assert rows[0]["form"] == "10-Q"
    assert rows[0]["filing_date"] == "2026-05-29"
    assert rows[0]["accession_number"] == "0000320193-26-000001"
    assert rows[0]["metadata_only"] is True
    assert rows[0]["blob_uri"] == "bronze/sec_edgar/submissions/CIK0000320193.json"
    assert "CIK0000320193.json" in requests[0][0]
    assert requests[0][2] == 3.5


def test_sec_edgar_official_filings_filters_to_m20_forms() -> None:
    def fake_urlopen(request, timeout):
        return FakeHttpResponse(
            {
                "cik": "320193",
                "name": "Apple Inc.",
                "tickers": ["AAPL"],
                "filings": {
                    "recent": {
                        "accessionNumber": [
                            "0000320193-26-000001",
                            "0000320193-26-000002",
                            "0000320193-26-000003",
                        ],
                        "filingDate": ["2026-05-29", "2026-05-30", "2026-05-31"],
                        "reportDate": ["2026-03-31", "2026-03-31", "2026-03-31"],
                        "form": ["10-Q", "S-8", "8-K"],
                        "primaryDocument": ["aapl-10q.htm", "aapl-s8.htm", "aapl-8k.htm"],
                        "primaryDocDescription": ["10-Q quarterly report", "S-8 registration", "8-K current report"],
                    }
                },
            }
        )

    provider = SecEdgarProvider(urlopen_fn=fake_urlopen)

    response = provider.fetch("official_filings", {"cik": "320193", "limit": 10})

    assert [row["form"] for row in response.rows()] == ["10-Q", "8-K"]


def test_sec_edgar_network_error_is_gateway_error() -> None:
    def failing_urlopen(request, timeout):
        raise OSError("network down")

    provider = SecEdgarProvider(urlopen_fn=failing_urlopen)

    with pytest.raises(GatewayError) as raised:
        provider.fetch("official_filings", {"cik": "320193"})

    assert raised.value.code == GatewayErrorCode.PROVIDER_UNAVAILABLE
