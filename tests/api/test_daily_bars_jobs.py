from __future__ import annotations

import time
from typing import Any, Optional

from fastapi.testclient import TestClient

from stock_data_gateway.cache.service import ReadThroughQueryService
from stock_data_gateway.cache.sqlite_store import SQLiteCacheStore
from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode
from stock_data_gateway.domain.provider import ProviderResponse
from stock_data_gateway.main import create_app
from stock_data_gateway.policies.registry import PolicyRegistry
from stock_data_gateway.policies.tushare import register_tushare_policies
from tests.fakes import FakeProvider


class PartialFailingProvider(FakeProvider):
    def fetch(self, endpoint: str, params: dict[str, Any], fields: Optional[str] = None) -> ProviderResponse:
        if params.get("ts_code") == "BAD.SZ":
            raise GatewayError(GatewayErrorCode.PROVIDER_UNAVAILABLE, "symbol fetch failed")
        return super().fetch(endpoint, params, fields)


def make_client(tmp_path, provider: FakeProvider | None = None) -> TestClient:
    policies = PolicyRegistry()
    register_tushare_policies(policies, provider_name="tushare")
    store = SQLiteCacheStore(tmp_path / "market_data.sqlite3")
    store.initialize()
    gateway = ReadThroughQueryService({"tushare": provider or FakeProvider()}, policies, store)
    return TestClient(create_app(gateway))


def test_daily_bars_job_accepts_runs_and_returns_rows(tmp_path) -> None:
    client = make_client(tmp_path)

    response = client.post(
        "/api/v1/market-data/jobs/daily-bars",
        json={
            "provider": "tushare",
            "symbols": ["000001.SZ", "600519.SH"],
            "start_date": "2024-01-02",
            "end_date": "2024-01-02",
            "include_turnover": True,
            "batch_size": 1,
        },
    )

    assert response.status_code == 202
    create_data = response.json()["data"]
    assert create_data["status"] == "accepted"
    job_id = create_data["job_id"]
    status = _wait_for_status(client, job_id)
    assert status["status"] == "completed"
    assert status["requested_symbols"] == 2
    assert status["completed_symbols"] == 2
    assert status["failed_symbols"] == 0
    assert status["rows_available"] == 2

    rows_response = client.get(f"/api/v1/market-data/jobs/{job_id}/rows")

    assert rows_response.status_code == 200
    rows = rows_response.json()["data"]["rows"]
    assert {row["symbol"] for row in rows} == {"000001.SZ", "600519.SH"}
    assert rows[0]["trade_date"] == "2024-01-02"
    assert rows[0]["turnover_rate"] == 1.2


def test_daily_bars_job_create_is_idempotent_for_same_semantic_request(tmp_path) -> None:
    client = make_client(tmp_path)
    payload = {
        "provider": "tushare",
        "symbols": ["000001.SZ"],
        "start_date": "2024-01-02",
        "end_date": "2024-01-02",
        "include_turnover": False,
    }

    first = client.post("/api/v1/market-data/jobs/daily-bars", json=payload)
    second = client.post(
        "/api/v1/market-data/jobs/daily-bars",
        json={**payload, "batch_size": 1},
    )

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json()["data"]["job_id"] == first.json()["data"]["job_id"]


def test_daily_bars_job_can_complete_with_structured_failures_and_partial_rows(tmp_path) -> None:
    client = make_client(tmp_path, PartialFailingProvider())

    response = client.post(
        "/api/v1/market-data/jobs/daily-bars",
        json={
            "provider": "tushare",
            "symbols": ["000001.SZ", "BAD.SZ"],
            "start_date": "2024-01-02",
            "end_date": "2024-01-02",
            "include_turnover": False,
        },
    )

    job_id = response.json()["data"]["job_id"]
    status = _wait_for_status(client, job_id)
    rows = client.get(f"/api/v1/market-data/jobs/{job_id}/rows").json()["data"]["rows"]

    assert status["status"] == "completed_with_failures"
    assert status["completed_symbols"] == 1
    assert status["failed_symbols"] == 1
    assert status["failures"][0]["symbol"] == "BAD.SZ"
    assert status["failures"][0]["error_code"] == GatewayErrorCode.PROVIDER_UNAVAILABLE.value
    assert [row["symbol"] for row in rows] == ["000001.SZ"]


def test_daily_bars_job_returns_429_when_queue_is_full(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_JOB_QUEUE_LIMIT", "0")
    client = make_client(tmp_path)

    response = client.post(
        "/api/v1/market-data/jobs/daily-bars",
        json={
            "provider": "tushare",
            "symbols": ["000001.SZ"],
            "start_date": "2024-01-02",
            "end_date": "2024-01-02",
        },
    )

    assert response.status_code == 429
    assert response.json()["error"]["code"] == GatewayErrorCode.QUEUE_FULL.value


def test_daily_bars_job_uses_job_symbol_limit_not_sync_route_symbol_limit(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_MAX_SYMBOLS_PER_REQUEST", "1")
    monkeypatch.setenv("GATEWAY_JOB_MAX_SYMBOLS", "3")
    client = make_client(tmp_path)

    response = client.post(
        "/api/v1/market-data/jobs/daily-bars",
        json={
            "provider": "tushare",
            "symbols": ["000001.SZ", "600519.SH"],
            "start_date": "2024-01-02",
            "end_date": "2024-01-02",
        },
    )

    assert response.status_code == 202


def _wait_for_status(client: TestClient, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 3
    last_status: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/market-data/jobs/{job_id}")
        assert response.status_code == 200
        last_status = response.json()["data"]
        if last_status["status"] in {"completed", "completed_with_failures", "failed"}:
            return last_status
        time.sleep(0.02)
    raise AssertionError(f"job did not finish in time: {last_status}")
