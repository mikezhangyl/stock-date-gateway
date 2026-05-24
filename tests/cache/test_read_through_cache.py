from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from stock_data_gateway.cache.service import ReadThroughQueryService
from stock_data_gateway.cache.sqlite_store import SQLiteCacheStore
from stock_data_gateway.policies.registry import PolicyRegistry
from stock_data_gateway.policies.tushare import register_tushare_policies
from tests.fakes import FakeProvider


def service(tmp_path, provider: FakeProvider, offline: bool = False) -> ReadThroughQueryService:
    policies = PolicyRegistry()
    register_tushare_policies(policies, provider_name="fake")
    store = SQLiteCacheStore(tmp_path / "market_data.sqlite3")
    store.initialize()
    return ReadThroughQueryService({"fake": provider}, policies, store, offline_mode=offline)


def test_read_through_cache_fetches_once_then_hits_cache(tmp_path) -> None:
    provider = FakeProvider()
    gateway = service(tmp_path, provider)

    first = gateway.query(
        "fake",
        "daily",
        {"ts_code": "000001.SZ", "start_date": "20240102", "end_date": "20240102"},
        fields="ts_code,trade_date,close",
    )
    second = gateway.query(
        "fake",
        "daily",
        {"ts_code": "000001.SZ", "start_date": "20240102", "end_date": "20240102"},
        fields="close,ts_code",
    )

    assert len(provider.calls) == 1
    assert first.data.fields == ["ts_code", "trade_date", "close"]
    assert second.data.fields == ["close", "ts_code"]
    assert second.meta["source"] == "cache"


def test_partial_hit_only_fetches_missing_dates(tmp_path) -> None:
    provider = FakeProvider()
    gateway = service(tmp_path, provider)

    gateway.query("fake", "daily", {"ts_code": "000001.SZ", "start_date": "20240102", "end_date": "20240102"})
    gateway.query("fake", "daily", {"ts_code": "000001.SZ", "start_date": "20240102", "end_date": "20240103"})

    assert [call[1].get("trade_date") for call in provider.calls] == ["20240102", "20240103"]


def test_offline_mode_returns_clear_error_without_provider_call(tmp_path) -> None:
    provider = FakeProvider()
    gateway = service(tmp_path, provider, offline=True)

    response = gateway.query(
        "fake",
        "daily",
        {"ts_code": "000001.SZ", "start_date": "20240102", "end_date": "20240102"},
    )

    assert response.meta["status"] == "error"
    assert response.meta["error_code"] == "OFFLINE_MISS"
    assert provider.calls == []


def test_concurrent_same_key_miss_calls_provider_once(tmp_path) -> None:
    provider = FakeProvider()
    gateway = service(tmp_path, provider)

    def run_query():
        return gateway.query(
            "fake",
            "daily",
            {"ts_code": "000001.SZ", "start_date": "20240102", "end_date": "20240102"},
        )

    with ThreadPoolExecutor(max_workers=5) as executor:
        responses = list(executor.map(lambda _: run_query(), range(5)))

    assert len(provider.calls) == 1
    assert all(response.data.items for response in responses)


class TradingCalendarProvider(FakeProvider):
    def fetch(self, endpoint, params, fields=None):
        if endpoint == "trade_cal":
            self.calls.append((endpoint, dict(params), fields))
            return self._trade_cal_response(endpoint, params["cal_date"])
        return super().fetch(endpoint, params, fields)

    def _trade_cal_response(self, endpoint, cal_date):
        from stock_data_gateway.domain.provider import ProviderResponse

        is_open = "0" if cal_date == "20240103" else "1"
        return ProviderResponse.from_rows(
            provider=self.provider_name,
            endpoint=endpoint,
            rows=[{"exchange": "SSE", "cal_date": cal_date, "is_open": is_open}],
        )


def test_cyq_chips_resolves_trade_calendar_before_fetching_dates(tmp_path) -> None:
    provider = TradingCalendarProvider()
    gateway = service(tmp_path, provider)

    response = gateway.query(
        "fake",
        "cyq_chips",
        {"ts_code": "000001.SZ", "start_date": "20240102", "end_date": "20240104"},
    )

    cyq_calls = [call for call in provider.calls if call[0] == "cyq_chips"]
    assert response.meta["status"] == "ok"
    assert [call[1]["trade_date"] for call in cyq_calls] == ["20240102", "20240104"]
