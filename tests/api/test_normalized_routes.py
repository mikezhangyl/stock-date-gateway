from __future__ import annotations

from fastapi.testclient import TestClient

from stock_data_gateway.cache.service import ReadThroughQueryService
from stock_data_gateway.cache.sqlite_store import SQLiteCacheStore
from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode
from stock_data_gateway.domain.provider import ProviderHealth, ProviderResponse
from stock_data_gateway.main import create_app
from stock_data_gateway.policies.registry import PolicyRegistry
from stock_data_gateway.policies.tushare import register_tushare_policies
from stock_data_gateway.providers.base import ExternalDataProvider
from tests.fakes import FakeProvider


class FakeEastmoneyProvider(ExternalDataProvider):
    provider_name = "eastmoney"

    def fetch(self, endpoint: str, params: dict, fields: str | None = None) -> ProviderResponse:
        return ProviderResponse.from_rows(
            provider=self.provider_name,
            endpoint=endpoint,
            rows=[
                {
                    "stock_code": "600519",
                    "stock_name": "贵州茅台",
                    "latest_price": 1688.0,
                    "change_percent": 1.2,
                    "retrieved_at": "2026-05-25T00:00:00+00:00",
                    "source": "eastmoney",
                }
            ],
        )

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, ok=True)


class FakeAkshareProvider(ExternalDataProvider):
    provider_name = "akshare"

    def fetch(self, endpoint: str, params: dict, fields: str | None = None) -> ProviderResponse:
        if endpoint == "sector_concepts":
            rows = [{"sector_name": "白酒", "pct_change": 2.3, "source": "akshare"}]
        else:
            rows = [{"trade_date": "2026-05-22", "limit_up_count": 60, "limit_down_count": 4, "source": "akshare"}]
        return ProviderResponse.from_rows(provider=self.provider_name, endpoint=endpoint, rows=rows)

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, ok=True)


class FailingProvider(ExternalDataProvider):
    provider_name = "failing"

    def fetch(self, endpoint: str, params: dict, fields: str | None = None) -> ProviderResponse:
        raise GatewayError(GatewayErrorCode.PROVIDER_UNAVAILABLE, "planned outage")

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, ok=False)


def make_client(tmp_path, provider_overrides: dict[str, ExternalDataProvider] | None = None) -> TestClient:
    policies = PolicyRegistry()
    register_tushare_policies(policies, provider_name="tushare")
    store = SQLiteCacheStore(tmp_path / "market_data.sqlite3")
    store.initialize()
    providers: dict[str, ExternalDataProvider] = {
        "tushare": FakeProvider(),
        "eastmoney": FakeEastmoneyProvider(),
        "akshare": FakeAkshareProvider(),
    }
    providers.update(provider_overrides or {})
    gateway = ReadThroughQueryService(
        providers,
        policies,
        store,
    )
    return TestClient(create_app(gateway))


def test_normalized_tushare_daily_returns_fni_contract_shape(tmp_path) -> None:
    client = make_client(tmp_path)

    response = client.post(
        "/api/v1/market-data/tushare/daily",
        json={
            "symbols": ["000001.SZ"],
            "start_date": "2024-01-02",
            "end_date": "2024-01-02",
            "include_turnover": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["rows"] == [
        {
            "symbol": "000001.SZ",
            "trade_date": "2024-01-02",
            "open": 10.0,
            "high": 11.0,
            "low": 9.5,
            "close": 10.5,
            "pre_close": 10.0,
            "volume": 1000.0,
            "amount": 10500.0,
            "turnover_rate": 1.2,
            "source": "tushare",
        }
    ]
    assert body["meta"]["provider"] == "tushare"
    assert body["meta"]["endpoint"] == "daily"
    assert body["meta"]["cache"]["mode"] == "upstream"
    assert body["meta"]["cache"]["hit"] is False


def test_normalized_index_and_fund_routes_return_bars(tmp_path) -> None:
    client = make_client(tmp_path)

    index_response = client.post(
        "/api/v1/market-data/tushare/index-daily",
        json={"symbols": ["000001.SH"], "start_date": "2024-01-02", "end_date": "2024-01-02"},
    )
    fund_response = client.post(
        "/api/v1/market-data/tushare/fund-daily",
        json={"symbols": ["510300.SH"], "start_date": "2024-01-02", "end_date": "2024-01-02"},
    )

    assert index_response.status_code == 200
    assert index_response.json()["data"]["rows"][0]["symbol"] == "000001.SH"
    assert fund_response.status_code == 200
    assert fund_response.json()["data"]["rows"][0]["symbol"] == "510300.SH"


def test_normalized_stock_basic_and_trade_cal_routes(tmp_path) -> None:
    client = make_client(tmp_path)

    stock_basic = client.post("/api/v1/market-data/tushare/stock-basic", json={"list_status": "L", "limit": 1})
    trade_cal = client.post(
        "/api/v1/market-data/tushare/trade-cal",
        json={"exchange": "SSE", "start_date": "2024-01-02", "end_date": "2024-01-02"},
    )

    assert stock_basic.status_code == 200
    assert stock_basic.json()["data"]["rows"][0]["ts_code"] == "000001.SZ"
    assert trade_cal.status_code == 200
    assert trade_cal.json()["data"]["rows"][0] == {
        "exchange": "SSE",
        "cal_date": "2024-01-02",
        "is_open": True,
        "pretrade_date": "2023-12-29",
        "source": "tushare",
    }


def test_normalized_provider_routes_return_rows(tmp_path) -> None:
    client = make_client(tmp_path)

    quote = client.get("/api/v1/market-data/eastmoney/market-quotes?stock_codes=600519")
    sector = client.get("/api/v1/market-data/akshare/sector-concepts?limit=1")
    limit_stats = client.get("/api/v1/market-data/akshare/limit-up-down?trade_date=2026-05-22")

    assert quote.status_code == 200
    assert quote.json()["data"]["rows"][0]["stock_code"] == "600519"
    assert sector.status_code == 200
    assert sector.json()["data"]["rows"][0]["sector_name"] == "白酒"
    assert limit_stats.status_code == 200
    assert limit_stats.json()["data"]["rows"][0]["limit_up_count"] == 60


def test_normalized_routes_validate_required_inputs(tmp_path) -> None:
    client = make_client(tmp_path)

    missing_symbols = client.post(
        "/api/v1/market-data/tushare/daily",
        json={"start_date": "2024-01-02", "end_date": "2024-01-02"},
    )
    invalid_limit = client.post("/api/v1/market-data/tushare/stock-basic", json={"limit": "bad"})
    missing_stock_codes = client.get("/api/v1/market-data/eastmoney/market-quotes")

    assert missing_symbols.status_code == 400
    assert missing_symbols.json()["error"]["code"] == GatewayErrorCode.INVALID_REQUEST.value
    assert invalid_limit.status_code == 400
    assert missing_stock_codes.status_code == 400


def test_unstable_provider_route_returns_degraded_empty_payload(tmp_path) -> None:
    client = make_client(tmp_path, {"eastmoney": FailingProvider()})

    response = client.get("/api/v1/market-data/eastmoney/market-quotes?stock_codes=600519")

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["rows"] == []
    assert body["meta"]["status"] == "degraded"
    assert body["meta"]["cache"]["mode"] == "stale_cache"


def test_cyq_route_groups_distribution_by_symbol_and_date(tmp_path) -> None:
    client = make_client(tmp_path)

    response = client.post(
        "/api/v1/market-data/chips/cyq",
        json={"symbols": ["000001.SZ"], "trade_date": "2024-01-02"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["rows"] == [
        {
            "symbol": "000001.SZ",
            "trade_date": "2024-01-02",
            "cost_distribution": [{"price": 10.0, "percent": 0.25}],
            "source": "tushare",
        }
    ]
