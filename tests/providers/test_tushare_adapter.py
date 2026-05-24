from __future__ import annotations

import pytest

from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode
from stock_data_gateway.providers.tushare.adapter import TushareProvider
from tests.fakes import FakeFrame


class FakeClient:
    def __init__(self, frame: FakeFrame) -> None:
        self.frame = frame
        self.calls = []

    def fetch_dataframe(self, endpoint, params, fields=None):
        self.calls.append((endpoint, params, fields))
        return self.frame


def test_tushare_adapter_fetches_dataframe_and_projects_fields() -> None:
    client = FakeClient(FakeFrame([{"ts_code": "000001.SZ", "trade_date": "20240102", "close": 10.5}]))
    provider = TushareProvider(client=client)

    response = provider.fetch("daily", {"ts_code": "000001.SZ", "trade_date": "20240102"}, fields="trade_date,close")

    assert response.fields == ["trade_date", "close"]
    assert response.items == [["20240102", 10.5]]
    assert client.calls[0][0] == "daily"


def test_tushare_adapter_normalizes_nan_values_before_json_response() -> None:
    client = FakeClient(FakeFrame([{"ts_code": "000001.SZ", "pe_ttm": float("nan")}]))
    provider = TushareProvider(client=client)

    response = provider.fetch("daily_basic", {"ts_code": "000001.SZ"}, fields="ts_code,pe_ttm")

    assert response.items == [["000001.SZ", None]]


def test_tushare_adapter_reports_schema_changed_for_missing_requested_field() -> None:
    provider = TushareProvider(client=FakeClient(FakeFrame([{"ts_code": "000001.SZ"}])))

    with pytest.raises(GatewayError) as raised:
        provider.fetch("daily", {"ts_code": "000001.SZ"}, fields="ts_code,close")

    assert raised.value.code == GatewayErrorCode.SCHEMA_CHANGED


def test_tushare_adapter_health_reports_client_factory_failure() -> None:
    def factory():
        raise GatewayError(GatewayErrorCode.MISSING_TOKEN, "missing")

    provider = TushareProvider(client_factory=factory)

    health = provider.health_check()

    assert health.ok is False
    assert health.message == "MISSING_TOKEN"
