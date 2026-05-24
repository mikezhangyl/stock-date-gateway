from __future__ import annotations

from stock_data_gateway.cache.service import ReadThroughQueryService
from stock_data_gateway.cache.sqlite_store import SQLiteCacheStore
from stock_data_gateway.policies.registry import PolicyRegistry
from stock_data_gateway.policies.tushare import register_tushare_policies
from tests.fakes import FakeProvider


def service(tmp_path, provider: FakeProvider) -> ReadThroughQueryService:
    policies = PolicyRegistry()
    register_tushare_policies(policies, provider_name="fake")
    store = SQLiteCacheStore(tmp_path / "market_data.sqlite3")
    store.initialize()
    return ReadThroughQueryService({"fake": provider}, policies, store)


def test_query_splits_comma_separated_tushare_symbols(tmp_path) -> None:
    provider = FakeProvider()
    gateway = service(tmp_path, provider)

    result = gateway.query(
        "fake",
        "daily",
        {"ts_code": "000001.SZ,600519.SH", "trade_date": "20240102"},
        fields="ts_code,trade_date,close",
    )

    assert result.meta["status"] == "ok"
    assert result.data.items == [
        ["000001.SZ", "20240102", 10.5],
        ["600519.SH", "20240102", 10.5],
    ]
    assert [call[1]["ts_code"] for call in provider.calls] == ["000001.SZ", "600519.SH"]


def test_force_refresh_replaces_cached_record(tmp_path) -> None:
    provider = FakeProvider()
    gateway = service(tmp_path, provider)

    first = gateway.query(
        "fake",
        "daily",
        {"ts_code": "000001.SZ", "trade_date": "20240102"},
        fields="ts_code,trade_date,close",
    )
    refreshed = gateway.query(
        "fake",
        "daily",
        {"ts_code": "000001.SZ", "trade_date": "20240102"},
        fields="ts_code,trade_date,close",
        force_refresh=True,
    )

    assert first.meta["source"] == "external"
    assert refreshed.meta["source"] == "external_refresh"
    assert len(provider.calls) == 2


def test_force_refresh_can_return_stale_cache_on_provider_failure(tmp_path) -> None:
    provider = FakeProvider()
    gateway = service(tmp_path, provider)
    gateway.query(
        "fake",
        "daily",
        {"ts_code": "000001.SZ", "trade_date": "20240102"},
        fields="ts_code,trade_date,close",
    )

    provider.fail = True
    result = gateway.query(
        "fake",
        "daily",
        {"ts_code": "000001.SZ", "trade_date": "20240102"},
        fields="ts_code,trade_date,close",
        force_refresh=True,
        allow_stale_on_error=True,
    )

    assert result.meta["status"] == "ok"
    assert result.meta["source"] == "stale_cache"
    assert result.meta["cache_hit"] is True
    assert result.data.items == [["000001.SZ", "20240102", 10.5]]
