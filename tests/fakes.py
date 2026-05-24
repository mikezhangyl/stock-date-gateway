from __future__ import annotations

from typing import Any, Optional

from stock_data_gateway.domain.provider import ProviderResponse
from stock_data_gateway.providers.base import ExternalDataProvider


class FakeColumn:
    def __init__(self, values: list[Any]) -> None:
        self._values = values

    def tolist(self) -> list[Any]:
        return list(self._values)


class FakeIloc:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def __getitem__(self, index: int) -> dict[str, Any]:
        return self._rows[index]


class FakeFrame:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.columns = list(rows[0].keys()) if rows else []
        self.iloc = FakeIloc(rows)

    @property
    def empty(self) -> bool:
        return len(self._rows) == 0

    def __getitem__(self, column: str) -> FakeColumn:
        return FakeColumn([row[column] for row in self._rows])

    def iterrows(self):
        yield from enumerate(self._rows)


class FakeProvider(ExternalDataProvider):
    provider_name = "fake"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], Optional[str]]] = []
        self.fail = False

    def fetch(self, endpoint: str, params: dict[str, Any], fields: Optional[str] = None) -> ProviderResponse:
        self.calls.append((endpoint, dict(params), fields))
        if self.fail:
            from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode

            raise GatewayError(GatewayErrorCode.PROVIDER_UNAVAILABLE, "fake provider unavailable")
        if endpoint == "daily":
            trade_date = params.get("trade_date") or params.get("start_date")
            return ProviderResponse.from_rows(
                provider=self.provider_name,
                endpoint=endpoint,
                rows=[
                    {
                        "ts_code": params["ts_code"],
                        "trade_date": trade_date,
                        "open": 10.0,
                        "high": 11.0,
                        "low": 9.5,
                        "close": 10.5,
                        "vol": 1000.0,
                        "amount": 10500.0,
                    }
                ],
            )
        if endpoint == "stock_basic":
            return ProviderResponse.from_rows(
                provider=self.provider_name,
                endpoint=endpoint,
                rows=[{"ts_code": params.get("ts_code", "000001.SZ"), "name": "平安银行"}],
            )
        if endpoint == "trade_cal":
            return ProviderResponse.from_rows(
                provider=self.provider_name,
                endpoint=endpoint,
                rows=[{"exchange": params.get("exchange", "SSE"), "cal_date": params["cal_date"], "is_open": "1"}],
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
                        "percent": 0.25,
                    }
                ],
            )
        return ProviderResponse.from_rows(provider=self.provider_name, endpoint=endpoint, rows=[])

    def health_check(self):
        from stock_data_gateway.domain.provider import ProviderHealth

        return ProviderHealth(provider=self.provider_name, ok=not self.fail, message=None)
