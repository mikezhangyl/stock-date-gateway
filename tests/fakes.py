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
        if endpoint in {"daily", "index_daily", "fund_daily"}:
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
                        "pre_close": 10.0,
                        "change": 0.5,
                        "pct_chg": 5.0,
                        "vol": 1000.0,
                        "amount": 10500.0,
                    }
                ],
            )
        if endpoint == "daily_basic":
            return ProviderResponse.from_rows(
                provider=self.provider_name,
                endpoint=endpoint,
                rows=[
                    {
                        "ts_code": params["ts_code"],
                        "trade_date": params.get("trade_date", "20240102"),
                        "close": 10.5,
                        "turnover_rate": 1.2,
                        "turnover_rate_f": 1.1,
                        "volume_ratio": 0.9,
                        "pe": 12.3,
                        "pe_ttm": 13.4,
                        "pb": 1.5,
                        "ps": 2.1,
                        "ps_ttm": 2.2,
                        "dv_ratio": 0.7,
                        "dv_ttm": 0.8,
                        "total_share": 1000000.0,
                        "float_share": 800000.0,
                        "free_share": 700000.0,
                        "total_mv": 10500000.0,
                        "circ_mv": 8400000.0,
                    }
                ],
            )
        if endpoint == "stock_basic":
            return ProviderResponse.from_rows(
                provider=self.provider_name,
                endpoint=endpoint,
                rows=[
                    {
                        "ts_code": params.get("ts_code", "000001.SZ"),
                        "symbol": "000001",
                        "name": "平安银行",
                        "area": "深圳",
                        "industry": "银行",
                        "market": "主板",
                        "exchange": "SZSE",
                        "list_status": "L",
                        "list_date": "19910403",
                    }
                ],
            )
        if endpoint == "trade_cal":
            return ProviderResponse.from_rows(
                provider=self.provider_name,
                endpoint=endpoint,
                rows=[
                    {
                        "exchange": params.get("exchange", "SSE"),
                        "cal_date": params["cal_date"],
                        "is_open": "1",
                        "pretrade_date": "20231229",
                    }
                ],
            )
        if endpoint == "income":
            return ProviderResponse.from_rows(
                provider=self.provider_name,
                endpoint=endpoint,
                rows=[
                    {
                        "ts_code": params["ts_code"],
                        "ann_date": "20240420",
                        "f_ann_date": "20240420",
                        "end_date": "20240331",
                        "report_type": "1",
                        "comp_type": "1",
                        "total_revenue": 100000.0,
                        "revenue": 90000.0,
                        "operate_profit": 20000.0,
                        "total_profit": 18000.0,
                        "n_income": 15000.0,
                        "n_income_attr_p": 14000.0,
                    }
                ],
            )
        if endpoint == "fina_indicator":
            return ProviderResponse.from_rows(
                provider=self.provider_name,
                endpoint=endpoint,
                rows=[
                    {
                        "ts_code": params["ts_code"],
                        "ann_date": "20240420",
                        "end_date": "20240331",
                        "eps": 0.5,
                        "dt_eps": 0.45,
                        "total_revenue_ps": 2.3,
                        "q_roe": 3.2,
                        "roe": 12.5,
                        "grossprofit_margin": 31.0,
                        "debt_to_assets": 42.0,
                        "tr_yoy": 8.0,
                        "netprofit_yoy": 9.0,
                        "dt_netprofit_yoy": 7.5,
                    }
                ],
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
