from __future__ import annotations

import json

from stock_data_gateway.providers.stocktwits.adapter import StocktwitsProvider


class FakeHttpResponse:
    def __init__(self, payload: dict, headers: dict[str, str] | None = None) -> None:
        self._payload = payload
        self.headers = headers or {}

    def __enter__(self) -> "FakeHttpResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_stocktwits_social_heat_keeps_only_message_level_fields() -> None:
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request.full_url)
        return FakeHttpResponse(
            {
                "messages": [
                    {
                        "id": 123,
                        "body": "AAPL momentum is strong",
                        "created_at": "2026-06-01T09:00:00Z",
                        "user": {"id": 99, "username": "not-retained"},
                    }
                ]
            },
            headers={"X-RateLimit-Remaining": "199"},
        )

    provider = StocktwitsProvider(urlopen_fn=fake_urlopen)

    response = provider.fetch("social_heat", {"symbol": "AAPL", "limit": 1})

    rows = response.rows()
    assert rows == [
        {
            "message_id": "123",
            "symbol": "AAPL",
            "body": "AAPL momentum is strong",
            "created_at": "2026-06-01T09:00:00Z",
            "source_url": "https://stocktwits.com/symbol/AAPL",
            "rate_limit_remaining": "199",
        }
    ]
    assert "limit=1" in requests[0]
