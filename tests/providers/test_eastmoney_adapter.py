from __future__ import annotations

import json

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


def test_eastmoney_northbound_capital_maps_datacenter_payload() -> None:
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request.full_url)
        return FakeHttpResponse(
            {
                "data": {
                    "diff": [
                        {
                            "TRADE_DATE": "2026-05-22",
                            "NET_BUY_AMT": 12.5,
                            "BUY_AMT": 100.0,
                            "SELL_AMT": 87.5,
                        }
                    ]
                }
            }
        )

    provider = EastmoneyProvider(urlopen_fn=fake_urlopen)

    response = provider.fetch("northbound_capital", {"trade_date": "2026-05-22"})

    assert response.rows() == [
        {
            "trade_date": "2026-05-22",
            "net_buy_amount": 12.5,
            "source": "eastmoney",
            "buy_amount": 100.0,
            "sell_amount": 87.5,
            "provider": "eastmoney",
        }
    ]
    assert "RPT_MUTUAL_DEAL_HISTORY" in requests[0]


def test_eastmoney_main_capital_flow_maps_push2_payload() -> None:
    provider = EastmoneyProvider(
        urlopen_fn=lambda request, timeout: FakeHttpResponse(
            {
                "data": {
                    "diff": [
                        {
                            "f12": "600519",
                            "f14": "贵州茅台",
                            "f62": 123.0,
                            "f3": 1.2,
                            "f6": 999.0,
                        }
                    ]
                }
            }
        )
    )

    response = provider.fetch("main_capital_flow", {"trade_date": "2026-05-22", "limit": "1"})

    assert response.rows() == [
        {
            "trade_date": "2026-05-22",
            "symbol": "600519",
            "main_net_inflow": 123.0,
            "source": "eastmoney",
            "name": "贵州茅台",
            "pct_change": 1.2,
            "amount": 999.0,
            "provider": "eastmoney",
        }
    ]


def test_eastmoney_fund_profile_maps_tiantian_payload() -> None:
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request.full_url)
        return FakeHttpResponse(
            {
                "Expansion": "2026-05-22",
                "Datas": {
                    "fundStocks": [
                        {
                            "GPDM": "600519",
                            "GPJC": "贵州茅台",
                            "JZBL": "9.8",
                            "PCTNVCHG": "0.1",
                            "INDEXNAME": "白酒",
                        }
                    ]
                },
            }
        )

    provider = EastmoneyProvider(urlopen_fn=fake_urlopen)

    response = provider.fetch("fund_profile", {"fund_code": "161725"})

    rows = response.rows()
    assert rows[0]["fund_code"] == "161725"
    assert rows[0]["fund_name"] == "EastMoney Fund 161725"
    assert rows[0]["fund_type"] == "fund"
    assert rows[0]["currency"] == "CNY"
    assert rows[0]["as_of_date"] == "2026-05-22"
    assert rows[0]["provider"] == "eastmoney"
    assert rows[0]["source"] == "eastmoney"
    assert rows[0]["data_quality"] == "fresh"
    assert "FCODE=161725" in requests[0]


def test_eastmoney_fund_holdings_maps_tiantian_payload() -> None:
    provider = EastmoneyProvider(
        urlopen_fn=lambda request, timeout: FakeHttpResponse(
            {
                "Expansion": "2026-05-22",
                "Datas": {
                    "fundStocks": [
                        {
                            "GPDM": "600519",
                            "GPJC": "贵州茅台",
                            "JZBL": "9.8",
                            "PCTNVCHG": "0.1",
                            "INDEXNAME": "白酒",
                        },
                        {
                            "GPDM": "000858",
                            "GPJC": "五粮液",
                            "JZBL": "8.1",
                            "PCTNVCHG": "0",
                            "INDEXNAME": "白酒",
                        },
                    ]
                },
            }
        )
    )

    response = provider.fetch("fund_holdings", {"fund_code": "161725", "limit": "1"})

    rows = response.rows()
    assert rows == [
        {
            "fund_code": "161725",
            "as_of_date": "2026-05-22",
            "stock_code": "600519",
            "stock_name": "贵州茅台",
            "weight": 0.098,
            "source": "eastmoney",
            "rank": 1,
            "holding_change": 0.001,
            "industry": "白酒",
            "market": "SH",
            "ts_code": "600519.SH",
            "provider": "eastmoney",
            "source_url": "https://fundmobapi.eastmoney.com/FundMNewApi/FundMNInverstPosition?FCODE=161725",
            "retrieved_at": rows[0]["retrieved_at"],
        }
    ]


def test_eastmoney_market_quote_network_error_is_gateway_error() -> None:
    def failing_urlopen(request, timeout):
        raise OSError("network down")

    provider = EastmoneyProvider(urlopen_fn=failing_urlopen)

    with pytest.raises(GatewayError) as raised:
        provider.fetch("market_quotes", {"stock_codes": ["000001.SZ"]})

    assert raised.value.code == GatewayErrorCode.PROVIDER_UNAVAILABLE
