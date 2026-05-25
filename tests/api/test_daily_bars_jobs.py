from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from fastapi.testclient import TestClient

from stock_data_gateway.cache.service import ReadThroughQueryService
from stock_data_gateway.cache.sqlite_store import SQLiteCacheStore
from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode
from stock_data_gateway.domain.provider import ProviderResponse
from stock_data_gateway.jobs.daily_bars import DailyBarsJobManager, DailyBarsJobRequest
from stock_data_gateway.main import create_app
from stock_data_gateway.policies.registry import PolicyRegistry
from stock_data_gateway.policies.tushare import register_tushare_policies
from tests.fakes import FakeProvider


class PartialFailingProvider(FakeProvider):
    def fetch(self, endpoint: str, params: dict[str, Any], fields: Optional[str] = None) -> ProviderResponse:
        if params.get("ts_code") == "BAD.SZ":
            raise GatewayError(GatewayErrorCode.PROVIDER_UNAVAILABLE, "symbol fetch failed")
        return super().fetch(endpoint, params, fields)


class SlowProvider(FakeProvider):
    def fetch(self, endpoint: str, params: dict[str, Any], fields: Optional[str] = None) -> ProviderResponse:
        if endpoint in {"daily", "daily_basic"}:
            time.sleep(0.04)
        return super().fetch(endpoint, params, fields)


def make_gateway(db_path: Path, provider: FakeProvider | None = None) -> ReadThroughQueryService:
    policies = PolicyRegistry()
    register_tushare_policies(policies, provider_name="tushare")
    store = SQLiteCacheStore(db_path)
    store.initialize()
    return ReadThroughQueryService({"tushare": provider or FakeProvider()}, policies, store)


def make_client(tmp_path, provider: FakeProvider | None = None) -> TestClient:
    return TestClient(create_app(make_gateway(tmp_path / "market_data.sqlite3", provider)))


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
    assert status["job_type"] == "daily-bars"
    assert status["cache_hit_symbols"] + status["upstream_fetch_symbols"] + status["stale_cache_symbols"] == 2
    assert status["last_progress_at"]
    assert status["coverage"]["expected_pairs"] == 2
    assert status["coverage"]["returned_pairs"] == 2
    assert status["coverage"]["missing_pairs"] == 0

    rows_response = client.get(f"/api/v1/market-data/jobs/{job_id}/rows")

    assert rows_response.status_code == 200
    rows = rows_response.json()["data"]["rows"]
    assert rows_response.json()["meta"]["coverage"]["expected_pairs"] == 2
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


def test_job_list_filters_daily_bars_jobs(tmp_path) -> None:
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
    job_id = response.json()["data"]["job_id"]
    _wait_for_status(client, job_id)

    list_response = client.get("/api/v1/market-data/jobs?job_type=daily-bars&status=completed")

    assert list_response.status_code == 200
    jobs = list_response.json()["data"]["jobs"]
    assert [job["job_id"] for job in jobs] == [job_id]
    assert jobs[0]["is_active"] is False
    assert jobs[0]["is_running"] is False


def test_cancel_running_job_is_idempotent_and_keeps_partial_rows(tmp_path) -> None:
    client = make_client(tmp_path, SlowProvider())
    response = client.post(
        "/api/v1/market-data/jobs/daily-bars",
        json={
            "provider": "tushare",
            "symbols": ["000001.SZ", "600519.SH", "000002.SZ"],
            "start_date": "2024-01-02",
            "end_date": "2024-01-02",
            "include_turnover": False,
            "batch_size": 1,
        },
    )
    job_id = response.json()["data"]["job_id"]
    _wait_for_progress(client, job_id, completed_symbols=1)

    cancel = client.post(f"/api/v1/market-data/jobs/{job_id}/cancel")
    cancel_again = client.post(f"/api/v1/market-data/jobs/{job_id}/cancel")
    status = _wait_for_status(client, job_id, terminal_statuses={"cancelled"})
    rows = client.get(f"/api/v1/market-data/jobs/{job_id}/rows").json()["data"]["rows"]

    assert cancel.status_code == 200
    assert cancel_again.status_code == 200
    assert status["status"] == "cancelled"
    assert len(rows) >= 1
    assert status["rows_available"] == len(rows)


def test_completed_job_status_and_rows_survive_manager_restart(tmp_path) -> None:
    db_path = tmp_path / "market_data.sqlite3"
    gateway = make_gateway(db_path)
    client = TestClient(create_app(gateway))
    response = client.post(
        "/api/v1/market-data/jobs/daily-bars",
        json={
            "provider": "tushare",
            "symbols": ["000001.SZ"],
            "start_date": "2024-01-02",
            "end_date": "2024-01-02",
        },
    )
    job_id = response.json()["data"]["job_id"]
    _wait_for_status(client, job_id)
    gateway.close()

    restarted = TestClient(create_app(make_gateway(db_path)))

    status_response = restarted.get(f"/api/v1/market-data/jobs/{job_id}")
    rows_response = restarted.get(f"/api/v1/market-data/jobs/{job_id}/rows")

    assert status_response.status_code == 200
    assert status_response.json()["data"]["status"] == "completed"
    assert rows_response.json()["data"]["rows"][0]["symbol"] == "000001.SZ"


def test_inflight_job_recovers_as_interrupted_after_manager_restart(tmp_path) -> None:
    db_path = tmp_path / "market_data.sqlite3"
    gateway = make_gateway(db_path)
    manager = DailyBarsJobManager(gateway, max_active_jobs=2, thread_starter=lambda target: None)
    request = DailyBarsJobRequest.from_payload(
        {
            "provider": "tushare",
            "symbols": ["000001.SZ"],
            "start_date": "2024-01-02",
            "end_date": "2024-01-02",
        },
        max_symbols=10,
        max_batch_size=10,
    )

    job = manager.create_job(request)
    restarted = DailyBarsJobManager(make_gateway(db_path), max_active_jobs=2, thread_starter=lambda target: None)

    assert restarted.get_job(job.job_id).status == "interrupted"


def test_breadth_window_job_resolves_trading_window_and_returns_rows(tmp_path) -> None:
    client = make_client(tmp_path)
    response = client.post(
        "/api/v1/market-data/jobs/breadth-window",
        json={
            "provider": "tushare",
            "symbols": ["000001.SZ", "600519.SH"],
            "end_date": "2024-01-05",
            "lookback_trading_days": 2,
            "include_turnover": True,
            "batch_size": 1,
        },
    )

    assert response.status_code == 202
    job_id = response.json()["data"]["job_id"]
    status = _wait_for_status(client, job_id)
    rows = client.get(f"/api/v1/market-data/jobs/{job_id}/rows").json()["data"]["rows"]

    assert status["job_type"] == "breadth-window"
    assert status["requested_symbols"] == 2
    assert status["coverage"]["expected_pairs"] == 4
    assert len(rows) == 4
    assert {row["trade_date"] for row in rows} == {"2024-01-04", "2024-01-05"}


def test_coverage_explains_missing_symbol_date_pairs(tmp_path) -> None:
    client = make_client(tmp_path)
    response = client.post(
        "/api/v1/market-data/jobs/daily-bars",
        json={
            "provider": "tushare",
            "symbols": ["000001.SZ"],
            "start_date": "2024-01-02",
            "end_date": "2024-01-03",
            "include_turnover": False,
        },
    )
    job_id = response.json()["data"]["job_id"]
    status = _wait_for_status(client, job_id)

    assert status["coverage"]["expected_pairs"] == 2
    assert status["coverage"]["returned_pairs"] == 2
    assert status["coverage"]["missing_reasons"] == []


def _wait_for_status(
    client: TestClient,
    job_id: str,
    *,
    terminal_statuses: set[str] | None = None,
) -> dict[str, Any]:
    terminal = terminal_statuses or {"completed", "completed_with_failures", "failed"}
    deadline = time.monotonic() + 3
    last_status: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/market-data/jobs/{job_id}")
        assert response.status_code == 200
        last_status = response.json()["data"]
        if last_status["status"] in terminal:
            return last_status
        time.sleep(0.02)
    raise AssertionError(f"job did not finish in time: {last_status}")


def _wait_for_progress(client: TestClient, job_id: str, *, completed_symbols: int) -> dict[str, Any]:
    deadline = time.monotonic() + 3
    last_status: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/market-data/jobs/{job_id}")
        assert response.status_code == 200
        last_status = response.json()["data"]
        if last_status["completed_symbols"] >= completed_symbols:
            return last_status
        time.sleep(0.02)
    raise AssertionError(f"job did not reach progress target: {last_status}")
