from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode
from stock_data_gateway.domain.provider import ProviderHealth, ProviderResponse

_EASTMONEY_SECTOR_CODE_ALIASES = {
    "机器人": "BK1090",
    "机器人概念": "BK1090",
}


class AkshareProvider:
    provider_name = "akshare"

    def __init__(self, *, module_factory=None, urlopen_fn=None, timeout_seconds: float = 15.0) -> None:
        self._module_factory = module_factory
        self._urlopen = urlopen_fn or urlopen
        self._timeout_seconds = timeout_seconds
        self._module = None

    def fetch(self, endpoint: str, params: dict[str, Any], fields: Optional[str] = None) -> ProviderResponse:
        del fields
        if endpoint == "sector_concepts":
            return self._fetch_sector_concepts(params)
        if endpoint == "sector_constituents":
            return self._fetch_sector_constituents(params)
        if endpoint == "etf_basic":
            return self._fetch_etf_basic(params)
        if endpoint == "etf_spot":
            return self._fetch_etf_spot(params)
        if endpoint == "etf_flow":
            return self._fetch_etf_flow(params)
        if endpoint == "index_constituents":
            return self._fetch_index_constituents(params)
        if endpoint == "margin_summary":
            return self._fetch_margin_summary(params)
        if endpoint == "margin_detail":
            return self._fetch_margin_detail(params)
        if endpoint == "earnings_calendar":
            return self._fetch_earnings_calendar(params)
        if endpoint == "limit_up_down":
            return self._fetch_limit_up_down(params)
        if endpoint == "dragon_tiger":
            return self._fetch_dragon_tiger(params)
        raise GatewayError(GatewayErrorCode.INVALID_REQUEST, f"Unsupported AkShare endpoint: {endpoint}")

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, ok=True, message="configured_with_eastmoney_fallback")

    def _fetch_sector_concepts(self, params: dict[str, Any]) -> ProviderResponse:
        limit = _optional_int(params.get("limit"))
        try:
            frame = self._akshare().stock_board_concept_name_em()
        except Exception:
            return self._fetch_sector_concepts_from_eastmoney(limit)
        rows = []
        for item in _rows_from_frame(frame)[:limit]:
            rows.append(
                {
                    "sector_name": _first_present(item, "sector_name", "板块名称", "概念名称", "名称"),
                    "pct_change": _first_present(item, "pct_change", "涨跌幅", "涨跌幅%"),
                    "source": self.provider_name,
                }
            )
        rows = [row for row in rows if row["sector_name"] not in (None, "")]
        if not rows:
            return self._fetch_sector_concepts_from_eastmoney(limit)
        return ProviderResponse.from_rows(provider=self.provider_name, endpoint="sector_concepts", rows=rows)

    def _fetch_sector_constituents(self, params: dict[str, Any]) -> ProviderResponse:
        sector_name = _required_text(params.get("sector_name"), "sector_name")
        trade_date = _optional_iso_date(params.get("trade_date"))
        limit = _optional_int(params.get("limit"))
        if self._module_factory is None and sector_name in _EASTMONEY_SECTOR_CODE_ALIASES:
            try:
                return self._fetch_sector_constituents_from_eastmoney(sector_name, trade_date, limit)
            except GatewayError:
                pass
        try:
            frame = self._akshare().stock_board_concept_cons_em(symbol=sector_name)
        except Exception:
            return self._fetch_sector_constituents_from_eastmoney(sector_name, trade_date, limit)
        rows = [
            _normalized_sector_constituent_row(
                item,
                sector_name=sector_name,
                trade_date=trade_date,
                source=self.provider_name,
                provider=self.provider_name,
            )
            for item in _rows_from_frame(frame)[:limit]
        ]
        rows = [row for row in rows if row["symbol"] and row["name"]]
        if not rows:
            return self._fetch_sector_constituents_from_eastmoney(sector_name, trade_date, limit)
        return ProviderResponse.from_rows(provider=self.provider_name, endpoint="sector_constituents", rows=rows)

    def _fetch_etf_basic(self, params: dict[str, Any]) -> ProviderResponse:
        market = str(params.get("market") or "cn").strip().lower()
        if market not in {"cn", "china"}:
            raise GatewayError(GatewayErrorCode.INVALID_REQUEST, "market must be cn")
        limit = _optional_int(params.get("limit"))
        try:
            module = self._akshare()
            fetcher = getattr(module, "fund_etf_category_ths", None)
            if not callable(fetcher):
                raise AttributeError("fund_etf_category_ths is unavailable")
            frame = fetcher(symbol="ETF", date="")
        except Exception:
            return self._fetch_etf_basic_from_eastmoney(limit)
        rows = [
            _normalized_etf_basic_row(item, source=self.provider_name, provider=self.provider_name)
            for item in _rows_from_frame(frame)[:limit]
        ]
        rows = [row for row in rows if row["symbol"] and row["name"]]
        if not rows:
            return self._fetch_etf_basic_from_eastmoney(limit)
        return ProviderResponse.from_rows(provider=self.provider_name, endpoint="etf_basic", rows=rows)

    def _fetch_etf_spot(self, params: dict[str, Any]) -> ProviderResponse:
        limit = _optional_int(params.get("limit"))
        try:
            frame = self._akshare().fund_etf_spot_em()
        except GatewayError:
            return self._fetch_etf_spot_from_eastmoney(limit)
        rows = []
        for item in _rows_from_frame(frame)[:limit]:
            row = _normalized_etf_spot_row(item, source=self.provider_name, provider=self.provider_name)
            if row["symbol"] and row["name"]:
                rows.append(row)
        if not rows:
            return self._fetch_etf_spot_from_eastmoney(limit)
        return ProviderResponse.from_rows(provider=self.provider_name, endpoint="etf_spot", rows=rows)

    def _fetch_etf_flow(self, params: dict[str, Any]) -> ProviderResponse:
        trade_date = _iso_date(params.get("trade_date"))
        limit = _optional_int(params.get("limit"))
        try:
            module = self._akshare()
        except GatewayError:
            return self._fetch_etf_flow_from_eastmoney(trade_date, limit)

        fetcher = getattr(module, "fund_etf_fund_flow_rank_em", None)
        if not callable(fetcher):
            return self._fetch_etf_flow_from_eastmoney(trade_date, limit)
        try:
            frame = fetcher()
        except Exception:
            return self._fetch_etf_flow_from_eastmoney(trade_date, limit)
        rows = [
            _normalized_etf_flow_row(
                item,
                trade_date=trade_date,
                source=self.provider_name,
                provider=self.provider_name,
            )
            for item in _rows_from_frame(frame)[:limit]
        ]
        rows = [row for row in rows if row["symbol"] and row["net_inflow"] is not None]
        if not rows:
            return self._fetch_etf_flow_from_eastmoney(trade_date, limit)
        return ProviderResponse.from_rows(provider=self.provider_name, endpoint="etf_flow", rows=rows)

    def _fetch_index_constituents(self, params: dict[str, Any]) -> ProviderResponse:
        index_symbol = _required_text(params.get("index_symbol"), "index_symbol").upper()
        trade_date = _optional_iso_date(params.get("trade_date"))
        compact_symbol = _index_symbol_for_akshare(index_symbol)
        limit = _optional_int(params.get("limit"))
        try:
            module = self._akshare()
            frame = module.index_stock_cons_weight_csindex(symbol=compact_symbol)
        except Exception as error:
            raise GatewayError(
                GatewayErrorCode.PROVIDER_UNAVAILABLE,
                "AkShare index constituents request failed.",
            ) from error
        rows = [
            _normalized_index_constituent_row(
                item,
                index_symbol=index_symbol,
                trade_date=trade_date,
                source=self.provider_name,
                provider=self.provider_name,
            )
            for item in _rows_from_frame(frame)[:limit]
        ]
        rows = [row for row in rows if row["symbol"] and row["name"]]
        if not rows:
            raise GatewayError(GatewayErrorCode.EMPTY_DATA, "AkShare index constituents returned no rows.")
        return ProviderResponse.from_rows(provider=self.provider_name, endpoint="index_constituents", rows=rows)

    def _fetch_margin_summary(self, params: dict[str, Any]) -> ProviderResponse:
        trade_date = _iso_date(params.get("trade_date"))
        compact_date = trade_date.replace("-", "")
        try:
            module = self._akshare()
            sse_rows = _rows_from_frame(module.stock_margin_sse(start_date=compact_date, end_date=compact_date))
            szse_rows = _rows_from_frame(module.stock_margin_szse(date=compact_date))
        except Exception as error:
            raise GatewayError(
                GatewayErrorCode.PROVIDER_UNAVAILABLE,
                "AkShare margin summary request failed.",
            ) from error
        row = _normalized_margin_summary_row(sse_rows[:1], szse_rows[:1], trade_date=trade_date)
        if row is None:
            raise GatewayError(GatewayErrorCode.EMPTY_DATA, "AkShare margin summary returned no rows.")
        return ProviderResponse.from_rows(provider=self.provider_name, endpoint="margin_summary", rows=[row])

    def _fetch_margin_detail(self, params: dict[str, Any]) -> ProviderResponse:
        trade_date = _iso_date(params.get("trade_date"))
        compact_date = trade_date.replace("-", "")
        limit = _optional_int(params.get("limit"))
        try:
            module = self._akshare()
            sse_rows = _rows_from_frame(module.stock_margin_detail_sse(date=compact_date))
            szse_rows = _rows_from_frame(module.stock_margin_detail_szse(date=compact_date))
        except Exception as error:
            raise GatewayError(
                GatewayErrorCode.PROVIDER_UNAVAILABLE,
                "AkShare margin detail request failed.",
            ) from error
        rows = [
            _normalized_margin_detail_row(
                item,
                trade_date=trade_date,
                source=self.provider_name,
                provider=self.provider_name,
            )
            for item in [*sse_rows, *szse_rows]
        ][:limit]
        rows = [row for row in rows if row["symbol"] and row["financing_balance"] is not None]
        if not rows:
            raise GatewayError(GatewayErrorCode.EMPTY_DATA, "AkShare margin detail returned no rows.")
        return ProviderResponse.from_rows(
            provider=self.provider_name,
            endpoint="margin_detail",
            rows=_rows_with_union_fields(rows),
        )

    def _fetch_earnings_calendar(self, params: dict[str, Any]) -> ProviderResponse:
        start_date = _iso_date(params.get("start_date"))
        end_date = _iso_date(params.get("end_date"))
        limit = _optional_int(params.get("limit"))
        try:
            module = self._akshare()
        except Exception as error:
            raise GatewayError(GatewayErrorCode.PROVIDER_UNAVAILABLE, "AkShare package is not available.") from error
        rows: list[dict[str, Any]] = []
        for date in _date_range(start_date, end_date):
            try:
                frame = module.stock_notice_report(symbol="全部", date=date.replace("-", ""))
            except Exception as error:
                raise GatewayError(
                    GatewayErrorCode.PROVIDER_UNAVAILABLE,
                    "AkShare earnings calendar request failed.",
                ) from error
            rows.extend(
                _normalized_earnings_calendar_row(item, source=self.provider_name, provider=self.provider_name)
                for item in _rows_from_frame(frame)
            )
            if limit is not None and len(rows) >= limit:
                break
        rows = [row for row in rows if row["symbol"] and row["ann_date"] and row["event_type"]]
        rows = rows[:limit]
        return ProviderResponse.from_rows(provider=self.provider_name, endpoint="earnings_calendar", rows=rows)

    def _fetch_limit_up_down(self, params: dict[str, Any]) -> ProviderResponse:
        trade_date = _iso_date(params.get("trade_date"))
        compact_date = trade_date.replace("-", "")
        try:
            module = self._akshare()
        except GatewayError:
            return self._fetch_limit_up_down_from_eastmoney(trade_date)
        limit_up_rows = _rows_from_frame(module.stock_zt_pool_em(date=compact_date))
        limit_down_rows = _rows_from_frame(module.stock_zt_pool_dtgc_em(date=compact_date))
        return ProviderResponse.from_rows(
            provider=self.provider_name,
            endpoint="limit_up_down",
            rows=[
                {
                    "trade_date": trade_date,
                    "limit_up_count": len(limit_up_rows),
                    "limit_down_count": len(limit_down_rows),
                    "source": self.provider_name,
                }
            ],
        )

    def _fetch_dragon_tiger(self, params: dict[str, Any]) -> ProviderResponse:
        trade_date = _iso_date(params.get("trade_date"))
        compact_date = trade_date.replace("-", "")
        limit = _optional_int(params.get("limit"))
        try:
            module = self._akshare()
        except GatewayError:
            return self._fetch_dragon_tiger_from_eastmoney(trade_date, limit)

        fetcher = getattr(module, "stock_lhb_detail_em", None)
        if not callable(fetcher):
            return self._fetch_dragon_tiger_from_eastmoney(trade_date, limit)
        try:
            frame = fetcher(date=compact_date)
        except Exception:
            return self._fetch_dragon_tiger_from_eastmoney(trade_date, limit)
        rows = [
            _normalized_dragon_tiger_row(
                item,
                trade_date=trade_date,
                source=self.provider_name,
                provider=self.provider_name,
            )
            for item in _rows_from_frame(frame)[:limit]
        ]
        rows = [row for row in rows if row["symbol"] and row["reason"]]
        if not rows:
            return self._fetch_dragon_tiger_from_eastmoney(trade_date, limit)
        return ProviderResponse.from_rows(provider=self.provider_name, endpoint="dragon_tiger", rows=rows)

    def _fetch_sector_concepts_from_eastmoney(self, limit: Optional[int]) -> ProviderResponse:
        query = urlencode(
            {
                "pn": "1",
                "pz": str(limit or 100),
                "po": "1",
                "np": "1",
                "fltt": "2",
                "invt": "2",
                "fid": "f3",
                "fs": "m:90+t:3",
                "fields": "f12,f14,f3",
            }
        )
        payload = self._eastmoney_json(f"https://push2.eastmoney.com/api/qt/clist/get?{query}")
        diff = _eastmoney_diff(payload)
        rows = [
            {
                "sector_name": str(item.get("f14") or item.get("f12") or "unknown"),
                "pct_change": _number_or_zero(item.get("f3")),
                "source": "eastmoney",
            }
            for item in diff[:limit]
            if isinstance(item, dict)
        ]
        if not rows:
            rows = [{"sector_name": "provider_unavailable", "pct_change": 0.0, "source": "fallback_unavailable"}]
        return ProviderResponse.from_rows(provider=self.provider_name, endpoint="sector_concepts", rows=rows)

    def _fetch_sector_constituents_from_eastmoney(
        self,
        sector_name: str,
        trade_date: Optional[str],
        limit: Optional[int],
    ) -> ProviderResponse:
        sector_code = sector_name if sector_name.upper().startswith("BK") else self._eastmoney_sector_code(sector_name)
        query = urlencode(
            {
                "pn": "1",
                "pz": str(limit or 100),
                "po": "1",
                "np": "1",
                "fltt": "2",
                "invt": "2",
                "fid": "f12",
                "fs": f"b:{sector_code} f:!50",
                "fields": "f12,f14,f3",
            }
        )
        payload = self._eastmoney_json(f"https://push2.eastmoney.com/api/qt/clist/get?{query}")
        rows = [
            _normalized_sector_constituent_row(
                item,
                sector_name=sector_name,
                trade_date=trade_date,
                source="eastmoney",
                provider="eastmoney",
            )
            for item in _eastmoney_diff(payload)[:limit]
            if isinstance(item, dict)
        ]
        rows = [row for row in rows if row["symbol"] and row["name"]]
        if not rows:
            raise GatewayError(GatewayErrorCode.EMPTY_DATA, "EastMoney sector constituents returned no rows.")
        return ProviderResponse.from_rows(provider=self.provider_name, endpoint="sector_constituents", rows=rows)

    def _eastmoney_sector_code(self, sector_name: str) -> str:
        if sector_name in _EASTMONEY_SECTOR_CODE_ALIASES:
            return _EASTMONEY_SECTOR_CODE_ALIASES[sector_name]
        query = urlencode(
            {
                "pn": "1",
                "pz": "1000",
                "po": "1",
                "np": "1",
                "fltt": "2",
                "invt": "2",
                "fid": "f12",
                "fs": "m:90+t:3+f:!50",
                "fields": "f12,f14",
            }
        )
        payload = self._eastmoney_json(f"https://push2.eastmoney.com/api/qt/clist/get?{query}")
        boards = _eastmoney_diff(payload)
        exact = next((item for item in boards if str(item.get("f14") or "") == sector_name), None)
        partial = next((item for item in boards if sector_name in str(item.get("f14") or "")), None)
        selected = exact or partial
        if not selected or not selected.get("f12"):
            raise GatewayError(GatewayErrorCode.EMPTY_DATA, f"EastMoney sector code not found: {sector_name}")
        return str(selected["f12"])

    def _fetch_etf_basic_from_eastmoney(self, limit: Optional[int]) -> ProviderResponse:
        query = urlencode(
            {
                "pn": "1",
                "pz": str(limit or 100),
                "po": "1",
                "np": "1",
                "fltt": "2",
                "invt": "2",
                "fid": "f3",
                "fs": "b:MK0021,b:MK0022,b:MK0023,b:MK0024",
                "fields": "f12,f14",
            }
        )
        payload = self._eastmoney_json(f"https://push2.eastmoney.com/api/qt/clist/get?{query}")
        rows = [
            _normalized_etf_basic_row(item, source="eastmoney", provider="eastmoney")
            for item in _eastmoney_diff(payload)[:limit]
            if isinstance(item, dict)
        ]
        rows = [row for row in rows if row["symbol"] and row["name"]]
        if not rows:
            raise GatewayError(GatewayErrorCode.EMPTY_DATA, "EastMoney ETF basic returned no rows.")
        return ProviderResponse.from_rows(provider=self.provider_name, endpoint="etf_basic", rows=rows)

    def _fetch_etf_spot_from_eastmoney(self, limit: Optional[int]) -> ProviderResponse:
        query = urlencode(
            {
                "pn": "1",
                "pz": str(limit or 100),
                "po": "1",
                "np": "1",
                "fltt": "2",
                "invt": "2",
                "fid": "f3",
                "fs": "b:MK0021,b:MK0022,b:MK0023,b:MK0024",
                "fields": "f12,f14,f2,f3,f5,f6",
            }
        )
        try:
            payload = self._eastmoney_json(f"https://push2.eastmoney.com/api/qt/clist/get?{query}")
            diff = _eastmoney_diff(payload)
        except GatewayError:
            diff = []
        rows = [
            _normalized_etf_spot_row(item, source="eastmoney", provider="eastmoney")
            for item in diff[:limit]
            if isinstance(item, dict)
        ]
        rows = [row for row in rows if row["symbol"] and row["name"]]
        if not rows:
            rows = [
                {
                    "symbol": "provider_unavailable",
                    "name": "provider_unavailable",
                    "pct_change": 0.0,
                    "source": "fallback_unavailable",
                    "provider": "fallback_unavailable",
                }
            ]
        return ProviderResponse.from_rows(provider=self.provider_name, endpoint="etf_spot", rows=rows)

    def _fetch_etf_flow_from_eastmoney(self, trade_date: str, limit: Optional[int]) -> ProviderResponse:
        query = urlencode(
            {
                "pn": "1",
                "pz": str(limit or 100),
                "po": "1",
                "np": "1",
                "fltt": "2",
                "invt": "2",
                "fid": "f62",
                "fs": "b:MK0021,b:MK0022,b:MK0023,b:MK0024",
                "fields": "f12,f14,f62,f3,f6",
            }
        )
        payload = self._eastmoney_json(f"https://push2.eastmoney.com/api/qt/clist/get?{query}")
        diff = _eastmoney_diff(payload)
        rows = [
            _normalized_etf_flow_row(item, trade_date=trade_date, source="eastmoney", provider="eastmoney")
            for item in diff[:limit]
            if isinstance(item, dict)
        ]
        rows = [row for row in rows if row["symbol"] and row["net_inflow"] is not None]
        if not rows:
            raise GatewayError(GatewayErrorCode.EMPTY_DATA, "EastMoney ETF capital flow returned no rows.")
        return ProviderResponse.from_rows(provider=self.provider_name, endpoint="etf_flow", rows=rows)

    def _fetch_limit_up_down_from_eastmoney(self, trade_date: str) -> ProviderResponse:
        query = urlencode(
            {
                "pn": "1",
                "pz": "5000",
                "po": "1",
                "np": "1",
                "fltt": "2",
                "invt": "2",
                "fid": "f3",
                "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
                "fields": "f12,f14,f3",
            }
        )
        try:
            payload = self._eastmoney_json(f"https://push2.eastmoney.com/api/qt/clist/get?{query}")
            diff = _eastmoney_diff(payload)
        except GatewayError:
            diff = []
        pct_changes = [_number(item.get("f3")) for item in diff if isinstance(item, dict)]
        pct_changes = [value for value in pct_changes if value is not None]
        rows = [
            {
                "trade_date": trade_date,
                "limit_up_count": sum(1 for value in pct_changes if value >= 9.8),
                "limit_down_count": sum(1 for value in pct_changes if value <= -9.8),
                "source": "eastmoney" if pct_changes else "fallback_unavailable",
            }
        ]
        return ProviderResponse.from_rows(provider=self.provider_name, endpoint="limit_up_down", rows=rows)

    def _fetch_dragon_tiger_from_eastmoney(self, trade_date: str, limit: Optional[int]) -> ProviderResponse:
        query = urlencode(
            {
                "reportName": "RPT_DAILYBILLBOARD_DETAILS",
                "columns": "ALL",
                "pageNumber": "1",
                "pageSize": str(limit or 100),
                "sortColumns": "TRADE_DATE,SECURITY_CODE",
                "sortTypes": "-1,1",
                "source": "WEB",
                "client": "WEB",
                "filter": f"(TRADE_DATE='{trade_date}')",
            }
        )
        payload = self._eastmoney_json(f"https://datacenter-web.eastmoney.com/api/data/v1/get?{query}")
        rows = [
            _normalized_dragon_tiger_row(item, trade_date=trade_date, source="eastmoney", provider="eastmoney")
            for item in _eastmoney_datacenter_rows(payload)[:limit]
            if isinstance(item, dict)
        ]
        rows = [row for row in rows if row["symbol"] and row["reason"]]
        if not rows:
            raise GatewayError(GatewayErrorCode.EMPTY_DATA, "EastMoney dragon-tiger list returned no rows.")
        return ProviderResponse.from_rows(provider=self.provider_name, endpoint="dragon_tiger", rows=rows)

    def _akshare(self):
        if self._module is not None:
            return self._module
        try:
            self._module = self._module_factory() if self._module_factory else __import__("akshare")
        except Exception as error:
            raise GatewayError(GatewayErrorCode.PROVIDER_UNAVAILABLE, "AkShare package is not available.") from error
        return self._module

    def _eastmoney_json(self, url: str) -> dict[str, Any]:
        request = Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "stock-data-gateway/0.1"},
        )
        last_error: Exception | None = None
        for _ in range(3):
            try:
                with self._urlopen(request, timeout=self._timeout_seconds) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                return payload if isinstance(payload, dict) else {}
            except Exception as error:
                last_error = error
        raise GatewayError(GatewayErrorCode.PROVIDER_UNAVAILABLE, "EastMoney fallback request failed.") from last_error


def _rows_from_frame(frame: Any) -> list[dict[str, Any]]:
    if frame is None:
        return []
    if hasattr(frame, "to_dict"):
        try:
            rows = frame.to_dict(orient="records")
            if isinstance(rows, list):
                return [dict(row) for row in rows if isinstance(row, dict)]
        except TypeError:
            pass
    if hasattr(frame, "iterrows"):
        return [dict(row) for _, row in frame.iterrows()]
    if isinstance(frame, list):
        return [dict(row) for row in frame if isinstance(row, dict)]
    return []


def _first_present(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def _optional_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 100
    return max(parsed, 0)


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise GatewayError(GatewayErrorCode.INVALID_REQUEST, f"{field} is required")
    return text


def _iso_date(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return text
    raise GatewayError(GatewayErrorCode.INVALID_REQUEST, "trade_date must be YYYY-MM-DD or YYYYMMDD")


def _optional_iso_date(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    return _iso_date(value)


def _date_range(start_date: str, end_date: str) -> list[str]:
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    if end < start:
        raise GatewayError(GatewayErrorCode.INVALID_REQUEST, "end_date must be on or after start_date")
    days = (end - start).days
    if days > 31:
        raise GatewayError(GatewayErrorCode.INVALID_REQUEST, "date range must be 31 days or less")
    return [(start + timedelta(days=offset)).strftime("%Y-%m-%d") for offset in range(days + 1)]


def _index_symbol_for_akshare(index_symbol: str) -> str:
    return index_symbol.upper().replace(".SH", "").replace(".SZ", "")


def _eastmoney_diff(payload: dict[str, Any]) -> list[dict[str, Any]]:
    diff = (payload.get("data") or {}).get("diff") if isinstance(payload, dict) else []
    if isinstance(diff, dict):
        diff = list(diff.values())
    if not isinstance(diff, list):
        return []
    return [item for item in diff if isinstance(item, dict)]


def _eastmoney_datacenter_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = (payload.get("result") or {}).get("data") if isinstance(payload, dict) else []
    if isinstance(rows, dict):
        rows = list(rows.values())
    if not isinstance(rows, list):
        return []
    return [item for item in rows if isinstance(item, dict)]


def _number(value: Any) -> Optional[float]:
    if value in (None, "", "-"):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return None if parsed != parsed else parsed


def _number_or_zero(value: Any) -> float:
    parsed = _number(value)
    return parsed if parsed is not None else 0.0


def _normalized_etf_spot_row(item: dict[str, Any], *, source: str, provider: str) -> dict[str, Any]:
    row: dict[str, Any] = {
        "symbol": str(_first_present(item, "symbol", "代码", "基金代码", "f12") or ""),
        "name": str(_first_present(item, "name", "名称", "基金简称", "f14") or ""),
        "pct_change": _number_or_zero(_first_present(item, "pct_change", "涨跌幅", "涨跌幅%", "f3")),
        "source": source,
    }
    optional_values = {
        "trade_date": _first_present(item, "trade_date", "日期"),
        "close": _number(_first_present(item, "close", "最新价", "收盘价", "f2")),
        "amount": _number(_first_present(item, "amount", "成交额", "f6")),
        "volume": _number(_first_present(item, "volume", "成交量", "f5")),
        "provider": provider,
    }
    for key, value in optional_values.items():
        if value not in (None, ""):
            row[key] = value
    return row


def _normalized_sector_constituent_row(
    item: dict[str, Any],
    *,
    sector_name: str,
    trade_date: Optional[str],
    source: str,
    provider: str,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "sector_name": sector_name,
        "symbol": str(_first_present(item, "symbol", "代码", "股票代码", "f12") or ""),
        "name": str(_first_present(item, "name", "名称", "股票简称", "f14") or ""),
        "source": source,
    }
    optional_values = {
        "trade_date": trade_date,
        "pct_change": _number(_first_present(item, "pct_change", "涨跌幅", "涨跌幅%", "f3")),
        "weight": _number(_first_present(item, "weight", "权重")),
        "provider": provider,
    }
    for key, value in optional_values.items():
        if value not in (None, ""):
            row[key] = value
    return row


def _normalized_etf_basic_row(item: dict[str, Any], *, source: str, provider: str) -> dict[str, Any]:
    row: dict[str, Any] = {
        "symbol": str(_first_present(item, "symbol", "基金代码", "代码", "f12") or ""),
        "name": str(_first_present(item, "name", "基金名称", "名称", "f14") or ""),
        "source": source,
    }
    optional_values = {
        "category": _first_present(item, "category", "基金类别") or "ETF",
        "fund_type": _first_present(item, "fund_type", "基金类型"),
        "issuer": _first_present(item, "issuer", "基金公司", "管理人"),
        "tracking_index": _first_present(item, "tracking_index", "跟踪标的", "跟踪指数"),
        "list_date": _optional_iso_date(_first_present(item, "list_date", "上市日期")),
        "provider": provider,
    }
    for key, value in optional_values.items():
        if value not in (None, ""):
            row[key] = value
    return row


def _normalized_etf_flow_row(
    item: dict[str, Any],
    *,
    trade_date: str,
    source: str,
    provider: str,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "trade_date": trade_date,
        "symbol": str(_first_present(item, "symbol", "代码", "基金代码", "f12") or ""),
        "net_inflow": _number(_first_present(item, "net_inflow", "主力净流入", "今日主力净流入", "净流入", "f62")),
        "source": source,
    }
    optional_values = {
        "name": _first_present(item, "name", "名称", "基金简称", "f14"),
        "pct_change": _number(_first_present(item, "pct_change", "涨跌幅", "涨跌幅%", "f3")),
        "amount": _number(_first_present(item, "amount", "成交额", "f6")),
        "provider": provider,
    }
    for key, value in optional_values.items():
        if value not in (None, ""):
            row[key] = value
    return row


def _normalized_index_constituent_row(
    item: dict[str, Any],
    *,
    index_symbol: str,
    trade_date: Optional[str],
    source: str,
    provider: str,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "index_symbol": index_symbol,
        "symbol": str(_first_present(item, "symbol", "成分券代码", "品种代码", "代码", "f12") or ""),
        "name": str(_first_present(item, "name", "成分券名称", "品种名称", "名称", "f14") or ""),
        "source": source,
    }
    optional_values = {
        "trade_date": trade_date,
        "weight": _number(_first_present(item, "weight", "权重")),
        "industry": _first_present(item, "industry", "行业"),
        "provider": provider,
    }
    for key, value in optional_values.items():
        if value not in (None, ""):
            row[key] = value
    return row


def _normalized_margin_summary_row(
    sse_rows: list[dict[str, Any]],
    szse_rows: list[dict[str, Any]],
    *,
    trade_date: str,
) -> dict[str, Any] | None:
    sse = sse_rows[0] if sse_rows else {}
    szse = szse_rows[0] if szse_rows else {}
    financing_balance = _sum_present(
        _number(_first_present(sse, "financing_balance", "融资余额")),
        _summary_amount(_first_present(szse, "financing_balance", "融资余额")),
    )
    if financing_balance is None:
        return None
    row: dict[str, Any] = {
        "trade_date": trade_date,
        "financing_balance": financing_balance,
        "source": "akshare",
    }
    optional_values = {
        "securities_lending_balance": _sum_present(
            _number(_first_present(sse, "securities_lending_balance", "融券余量金额", "融券余额")),
            _summary_amount(_first_present(szse, "securities_lending_balance", "融券余额")),
        ),
        "financing_buy_amount": _sum_present(
            _number(_first_present(sse, "financing_buy_amount", "融资买入额")),
            _summary_amount(_first_present(szse, "financing_buy_amount", "融资买入额")),
        ),
        "repayment_amount": _sum_present(
            _number(_first_present(sse, "repayment_amount", "融资偿还额")),
            _summary_amount(_first_present(szse, "repayment_amount", "融资偿还额")),
        ),
        "provider": "akshare",
    }
    for key, value in optional_values.items():
        if value not in (None, ""):
            row[key] = value
    return row


def _normalized_margin_detail_row(
    item: dict[str, Any],
    *,
    trade_date: str,
    source: str,
    provider: str,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "trade_date": trade_date,
        "symbol": str(_first_present(item, "symbol", "标的证券代码", "证券代码", "代码") or ""),
        "financing_balance": _number(_first_present(item, "financing_balance", "融资余额")),
        "source": source,
    }
    optional_values = {
        "name": _first_present(item, "name", "标的证券简称", "证券简称", "名称"),
        "financing_buy_amount": _number(_first_present(item, "financing_buy_amount", "融资买入额")),
        "securities_lending_balance": _number(_first_present(item, "securities_lending_balance", "融券余额")),
        "provider": provider,
    }
    for key, value in optional_values.items():
        if value not in (None, ""):
            row[key] = value
    return row


def _normalized_earnings_calendar_row(item: dict[str, Any], *, source: str, provider: str) -> dict[str, Any]:
    row: dict[str, Any] = {
        "symbol": str(_first_present(item, "symbol", "代码", "股票代码", "ts_code") or ""),
        "name": _first_present(item, "name", "名称", "股票简称"),
        "ann_date": _optional_iso_date(_first_present(item, "ann_date", "公告日期", "预告日期", "披露日期")),
        "event_type": _first_present(item, "event_type", "公告类型", "类型") or "announcement",
        "source": source,
    }
    optional_values = {
        "forecast_type": _first_present(item, "forecast_type", "业绩变动", "预告类型"),
        "net_profit_min": _number(_first_present(item, "net_profit_min", "净利润下限")),
        "net_profit_max": _number(_first_present(item, "net_profit_max", "净利润上限")),
        "provider": provider,
    }
    for key, value in optional_values.items():
        if value not in (None, ""):
            row[key] = value
    return row


def _summary_amount(value: Any) -> Optional[float]:
    parsed = _number(value)
    if parsed is None:
        return None
    return parsed * 100_000_000 if abs(parsed) < 100_000_000 else parsed


def _sum_present(*values: Optional[float]) -> Optional[float]:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return float(sum(present))


def _rows_with_union_fields(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    return [{field: row.get(field) for field in fields} for row in rows]


def _normalized_dragon_tiger_row(
    item: dict[str, Any],
    *,
    trade_date: str,
    source: str,
    provider: str,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "trade_date": trade_date,
        "symbol": str(_first_present(item, "symbol", "代码", "股票代码", "SECURITY_CODE", "f12") or ""),
        "reason": str(_first_present(item, "reason", "上榜原因", "EXPLANATION", "EXPLAIN") or ""),
        "source": source,
    }
    optional_values = {
        "name": _first_present(item, "name", "名称", "股票简称", "SECURITY_NAME_ABBR", "f14"),
        "buy_amount": _number(_first_present(item, "buy_amount", "买入额", "BILLBOARD_BUY_AMT")),
        "sell_amount": _number(_first_present(item, "sell_amount", "卖出额", "BILLBOARD_SELL_AMT")),
        "net_buy_amount": _number(_first_present(item, "net_buy_amount", "净买额", "BILLBOARD_NET_AMT")),
        "provider": provider,
    }
    for key, value in optional_values.items():
        if value not in (None, ""):
            row[key] = value
    return row
