from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Optional

from stock_data_gateway.cache.service import ReadThroughQueryService
from stock_data_gateway.cache.sqlite_store import SQLiteCacheStore
from stock_data_gateway.domain.provider import ProviderHealth, ProviderResponse
from stock_data_gateway.main import create_default_gateway
from stock_data_gateway.policies.registry import PolicyRegistry
from stock_data_gateway.policies.tushare import register_tushare_policies
from stock_data_gateway.providers.base import ExternalDataProvider


class ValidationFakeProvider(ExternalDataProvider):
    provider_name = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, endpoint: str, params: dict[str, Any], fields: Optional[str] = None) -> ProviderResponse:
        self.calls += 1
        if endpoint == "daily":
            return ProviderResponse.from_rows(
                provider=self.provider_name,
                endpoint=endpoint,
                rows=[
                    {
                        "ts_code": params["ts_code"],
                        "trade_date": params["trade_date"],
                        "open": 10.0,
                        "high": 11.0,
                        "low": 9.5,
                        "close": 10.5,
                        "vol": 1000.0,
                        "amount": 10500.0,
                    }
                ],
            )
        if endpoint == "trade_cal":
            return ProviderResponse.from_rows(
                provider=self.provider_name,
                endpoint=endpoint,
                rows=[{"exchange": "SSE", "cal_date": params["cal_date"], "is_open": "1"}],
            )
        if endpoint == "stock_basic":
            return ProviderResponse.from_rows(
                provider=self.provider_name,
                endpoint=endpoint,
                rows=[{"ts_code": params.get("ts_code", "000001.SZ"), "name": "平安银行"}],
            )
        if endpoint == "cyq_chips":
            return ProviderResponse.from_rows(
                provider=self.provider_name,
                endpoint=endpoint,
                rows=[
                    {
                        "ts_code": params["ts_code"],
                        "trade_date": params["trade_date"],
                        "price": 10.0,
                        "percent": 0.2,
                    }
                ],
            )
        return ProviderResponse.from_rows(provider=self.provider_name, endpoint=endpoint, rows=[])

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, ok=True, message=None)


def run_fake_validation(base_path: Path) -> dict[str, Any]:
    provider = ValidationFakeProvider()
    policies = PolicyRegistry()
    register_tushare_policies(policies, provider_name="fake")
    store = SQLiteCacheStore(Path(base_path) / "market_data.sqlite3")
    store.initialize()
    gateway = ReadThroughQueryService({"fake": provider}, policies, store)

    before_first = provider.calls
    first = gateway.query("fake", "daily", {"ts_code": "000001.SZ", "start_date": "20240102", "end_date": "20240102"})
    after_first = provider.calls
    second = gateway.query("fake", "daily", {"ts_code": "000001.SZ", "start_date": "20240102", "end_date": "20240102"})
    after_second = provider.calls
    return {
        "ok": first.meta["status"] == "ok" and second.meta["source"] == "cache",
        "external_calls_first": after_first - before_first,
        "external_calls_second": after_second - after_first,
        "rows": len(second.data.items),
    }


def run_live_validation() -> dict[str, Any]:
    if os.getenv("RUN_LIVE_PROVIDER_TESTS") != "1":
        return {"ok": False, "error": "RUN_LIVE_PROVIDER_TESTS=1 is required for live validation"}
    gateway = create_default_gateway()
    checks = [
        ("trade_cal", {"exchange": "SSE", "start_date": "20240102", "end_date": "20240102"}),
        ("stock_basic", {"ts_code": "000001.SZ"}),
        ("daily", {"ts_code": "000001.SZ", "start_date": "20240102", "end_date": "20240102"}),
        ("cyq_chips", {"ts_code": "000001.SZ", "start_date": "20240102", "end_date": "20240102"}),
    ]
    results = []
    for endpoint, params in checks:
        result = gateway.query("tushare", endpoint, params)
        results.append({"endpoint": endpoint, "status": result.meta.get("status"), "rows": len(result.data.items)})
    return {"ok": all(item["status"] == "ok" for item in results), "results": results}


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the local market data gateway.")
    parser.add_argument("--fake", action="store_true", help="Run offline fake-provider validation.")
    parser.add_argument("--live", action="store_true", help="Run live provider validation.")
    parser.add_argument("--data-dir", default="./data/validation", help="Validation cache directory.")
    args = parser.parse_args()

    result = run_live_validation() if args.live else run_fake_validation(Path(args.data_dir))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
