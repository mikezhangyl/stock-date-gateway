from __future__ import annotations

from stock_data_gateway.domain.provider import QueryResult, ResponseData
from stock_data_gateway.jobs.cyq_chips_backfill import CyqChipsBackfillRequest, run_cyq_chips_backfill


class FakeGateway:
    def __init__(self) -> None:
        self.calls = []

    def query(self, provider, endpoint, params):
        self.calls.append((provider, endpoint, params))
        return QueryResult(data=ResponseData(fields=["ts_code"], items=[[params["ts_code"]]]), meta={"status": "ok"})


def test_cyq_chips_backfill_delegates_to_gateway_query() -> None:
    gateway = FakeGateway()
    request = CyqChipsBackfillRequest(
        provider="tushare",
        ts_code="000001.SZ",
        start_date="20240102",
        end_date="20240103",
    )

    result = run_cyq_chips_backfill(gateway, request)

    assert result.meta["status"] == "ok"
    assert gateway.calls == [
        (
            "tushare",
            "cyq_chips",
            {"ts_code": "000001.SZ", "start_date": "20240102", "end_date": "20240103"},
        )
    ]
