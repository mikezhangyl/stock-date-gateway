from __future__ import annotations

from typing import Optional

from stock_data_gateway.cache.service import ReadThroughQueryService
from stock_data_gateway.cache.sqlite_store import SQLiteCacheStore
from stock_data_gateway.domain.provider import ProviderResponse
from stock_data_gateway.policies.models import EndpointPolicy
from stock_data_gateway.policies.registry import PolicyRegistry
from stock_data_gateway.providers.base import ExternalDataProvider


class FakeAkshareProvider(ExternalDataProvider):
    provider_name = "akshare"

    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, endpoint: str, params: dict, fields: Optional[str] = None) -> ProviderResponse:
        self.calls += 1
        return ProviderResponse.from_rows(
            provider=self.provider_name,
            endpoint=endpoint,
            rows=[{"symbol": params["symbol"], "date": params["date"], "close": 8.8}],
        )

    def health_check(self):
        from stock_data_gateway.domain.provider import ProviderHealth

        return ProviderHealth(provider=self.provider_name, ok=True, message=None)


def test_cache_core_supports_non_tushare_provider_without_tushare_changes(tmp_path) -> None:
    policies = PolicyRegistry()
    policies.register(
        EndpointPolicy(
            provider="akshare",
            endpoint="stock_zh_a_hist",
            instrument_param="symbol",
            date_param="date",
            default_fields=["symbol", "date", "close"],
            date_key_role="trade_date",
            schema_version=1,
        )
    )
    provider = FakeAkshareProvider()
    store = SQLiteCacheStore(tmp_path / "market_data.sqlite3")
    store.initialize()
    gateway = ReadThroughQueryService({"akshare": provider}, policies, store)

    first = gateway.query("akshare", "stock_zh_a_hist", {"symbol": "000001", "date": "20240102"})
    second = gateway.query("akshare", "stock_zh_a_hist", {"symbol": "000001", "date": "20240102"})

    assert first.data.items == [["000001", "20240102", 8.8]]
    assert second.meta["source"] == "cache"
    assert provider.calls == 1
