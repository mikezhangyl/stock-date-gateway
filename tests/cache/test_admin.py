from __future__ import annotations

from pathlib import Path

from stock_data_gateway.cache.admin import audit_summary, clear_cache, inspect_cache
from stock_data_gateway.cache.service import ReadThroughQueryService
from stock_data_gateway.cache.sqlite_store import SQLiteCacheStore
from stock_data_gateway.policies.registry import PolicyRegistry
from stock_data_gateway.policies.tushare import register_tushare_policies
from tests.fakes import FakeProvider


def _gateway(tmp_path: Path) -> ReadThroughQueryService:
    policies = PolicyRegistry()
    register_tushare_policies(policies, provider_name="fake")
    store = SQLiteCacheStore(tmp_path / "market_data.sqlite3")
    store.initialize()
    return ReadThroughQueryService({"fake": FakeProvider()}, policies, store)


def test_inspect_cache_summarizes_entries_by_endpoint(tmp_path) -> None:
    gateway = _gateway(tmp_path)
    gateway.query(
        "fake",
        "daily",
        {"ts_code": "000001.SZ", "start_date": "20240102", "end_date": "20240102"},
    )
    gateway.close()

    summary = inspect_cache(tmp_path / "market_data.sqlite3")

    assert summary["ok"] is True
    assert summary["current_entries"] == 1
    assert summary["by_endpoint"] == [
        {
            "provider": "fake",
            "endpoint": "daily",
            "current_entries": 1,
            "rows": 1,
        }
    ]
    assert summary["entries"][0]["instrument_id"] == "000001.SZ"


def test_inspect_cache_filters_by_provider_and_endpoint(tmp_path) -> None:
    gateway = _gateway(tmp_path)
    gateway.query(
        "fake",
        "daily",
        {"ts_code": "000001.SZ", "start_date": "20240102", "end_date": "20240102"},
    )
    gateway.close()

    summary = inspect_cache(tmp_path / "market_data.sqlite3", provider="fake", endpoint="daily")

    assert summary["ok"] is True
    assert summary["current_entries"] == 1
    assert summary["by_endpoint"][0]["endpoint"] == "daily"


def test_cache_admin_reports_missing_database(tmp_path) -> None:
    result = inspect_cache(tmp_path / "missing.sqlite3")

    assert result["ok"] is False
    assert "does not exist" in result["error"]


def test_audit_summary_groups_request_sources(tmp_path) -> None:
    gateway = _gateway(tmp_path)
    params = {"ts_code": "000001.SZ", "start_date": "20240102", "end_date": "20240102"}
    gateway.query("fake", "daily", params)
    gateway.query("fake", "daily", params)
    gateway.close()

    summary = audit_summary(tmp_path / "market_data.sqlite3")

    assert summary["ok"] is True
    assert summary["total_requests"] == 2
    assert summary["by_endpoint"] == [
        {
            "provider": "fake",
            "endpoint": "daily",
            "source": "cache",
            "status": "ok",
            "requests": 1,
            "cache_hits": 1,
            "rows": 1,
        },
        {
            "provider": "fake",
            "endpoint": "daily",
            "source": "external",
            "status": "ok",
            "requests": 1,
            "cache_hits": 0,
            "rows": 1,
        },
    ]


def test_clear_cache_can_remove_one_endpoint_key(tmp_path) -> None:
    gateway = _gateway(tmp_path)
    gateway.query(
        "fake",
        "daily",
        {"ts_code": "000001.SZ", "start_date": "20240102", "end_date": "20240102"},
    )
    gateway.query("fake", "stock_basic", {"ts_code": "000001.SZ"})
    gateway.close()

    result = clear_cache(
        tmp_path / "market_data.sqlite3",
        provider="fake",
        endpoint="daily",
        instrument_id="000001.SZ",
        date_key="20240102",
    )

    assert result["ok"] is True
    assert result["current_entries_deleted"] == 1
    assert inspect_cache(tmp_path / "market_data.sqlite3")["current_entries"] == 1
