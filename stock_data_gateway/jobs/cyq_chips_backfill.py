from __future__ import annotations

from dataclasses import dataclass

from stock_data_gateway.cache.service import ReadThroughQueryService


@dataclass(frozen=True)
class CyqChipsBackfillRequest:
    provider: str
    ts_code: str
    start_date: str
    end_date: str


def run_cyq_chips_backfill(gateway: ReadThroughQueryService, request: CyqChipsBackfillRequest):
    return gateway.query(
        request.provider,
        "cyq_chips",
        {"ts_code": request.ts_code, "start_date": request.start_date, "end_date": request.end_date},
    )
