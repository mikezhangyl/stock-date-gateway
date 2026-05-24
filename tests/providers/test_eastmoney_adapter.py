from __future__ import annotations

import json
from io import BytesIO

import pytest

from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode
from stock_data_gateway.providers.eastmoney.adapter import EastmoneyProvider


class FakeHttpResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def __enter__(self) -> "FakeHttpResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_eastmoney_market_quotes_maps_push2_payload() -> None:
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request.full_url, timeout))
        return FakeHttpResponse(
            {
                "data": {
                    "diff": [
                        {
                            "f12": "600519",
                            "f14": "贵州茅台",
                            "f2": 1688.0,
                            "f3": 1.2,
                            "f4": 20.1,
                            "f5": 1000,
                            "f6": 1688000,
                        }
                    ]
                }
            }
        )

    provider = EastmoneyProvider(urlopen_fn=fake_urlopen)
    response = provider.fetch("market_quotes", {"stock_codes": "600519"})

    rows = response.rows()
    assert rows[0]["stock_code"] == "600519"
    assert rows[0]["latest_price"] == 1688.0
    assert rows[0]["source"] == "eastmoney"
    assert "secids=1.600519" in requests[0][0]


def test_eastmoney_planned_endpoints_return_structured_empty_rows() -> None:
    provider = EastmoneyProvider(urlopen_fn=lambda request, timeout: BytesIO(b"{}"))

    response = provider.fetch("northbound_capital", {"trade_date": "2026-05-22"})

    assert response.rows() == []


def test_eastmoney_market_quote_network_error_is_gateway_error() -> None:
    def failing_urlopen(request, timeout):
        raise OSError("network down")

    provider = EastmoneyProvider(urlopen_fn=failing_urlopen)

    with pytest.raises(GatewayError) as raised:
        provider.fetch("market_quotes", {"stock_codes": ["000001.SZ"]})

    assert raised.value.code == GatewayErrorCode.PROVIDER_UNAVAILABLE
