from __future__ import annotations

from fastapi.testclient import TestClient

from stock_data_gateway.cache.service import ReadThroughQueryService
from stock_data_gateway.cache.sqlite_store import SQLiteCacheStore
from stock_data_gateway.main import create_app
from stock_data_gateway.policies.registry import PolicyRegistry
from stock_data_gateway.policies.tushare import register_tushare_policies
from tests.fakes import FakeProvider


def make_client(tmp_path):
    provider = FakeProvider()
    policies = PolicyRegistry()
    register_tushare_policies(policies, provider_name="tushare")
    store = SQLiteCacheStore(tmp_path / "market_data.sqlite3")
    store.initialize()
    gateway = ReadThroughQueryService({"tushare": provider, "fake": provider}, policies, store)
    app = create_app(gateway)
    return TestClient(app), provider


def test_tushare_facade_keeps_tushare_shape_and_ignores_request_token(tmp_path) -> None:
    client, provider = make_client(tmp_path)

    response = client.post(
        "/tushare",
        json={
            "api_name": "daily",
            "token": "caller-token-must-not-be-used",
            "params": {"ts_code": "000001.SZ", "start_date": "20240102", "end_date": "20240102"},
            "fields": "ts_code,trade_date,close",
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["code"] == 0
    assert body["msg"] == ""
    assert body["data"]["fields"] == ["ts_code", "trade_date", "close"]
    assert len(provider.calls) == 1
    assert "token" not in provider.calls[0][1]


def test_tushare_facade_malformed_request_returns_400(tmp_path) -> None:
    client, _ = make_client(tmp_path)

    response = client.post("/tushare", json={"params": {}})

    assert response.status_code == 400
    assert response.json()["code"] == 400


def test_health_reports_injected_provider_status(tmp_path) -> None:
    client, _ = make_client(tmp_path)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["providers"]["tushare"]["ok"] is True


def test_default_app_health_handles_missing_tushare_token(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARKET_DATA_HOME", str(tmp_path))
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    app = create_app()
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["providers"]["tushare"]["ok"] is False
