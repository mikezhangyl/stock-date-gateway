from __future__ import annotations

import json
from typing import Any, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode
from stock_data_gateway.domain.provider import ProviderHealth, ProviderResponse


class AkshareProvider:
    provider_name = "akshare"

    def __init__(self, *, module_factory=None, urlopen_fn=None, timeout_seconds: float = 8.0) -> None:
        self._module_factory = module_factory
        self._urlopen = urlopen_fn or urlopen
        self._timeout_seconds = timeout_seconds
        self._module = None

    def fetch(self, endpoint: str, params: dict[str, Any], fields: Optional[str] = None) -> ProviderResponse:
        del fields
        if endpoint == "sector_concepts":
            return self._fetch_sector_concepts(params)
        if endpoint == "limit_up_down":
            return self._fetch_limit_up_down(params)
        raise GatewayError(GatewayErrorCode.INVALID_REQUEST, f"Unsupported AkShare endpoint: {endpoint}")

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, ok=True, message="configured_with_eastmoney_fallback")

    def _fetch_sector_concepts(self, params: dict[str, Any]) -> ProviderResponse:
        limit = _optional_int(params.get("limit"))
        try:
            frame = self._akshare().stock_board_concept_name_em()
        except GatewayError:
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
        try:
            with self._urlopen(request, timeout=self._timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as error:
            raise GatewayError(GatewayErrorCode.PROVIDER_UNAVAILABLE, "EastMoney fallback request failed.") from error
        return payload if isinstance(payload, dict) else {}


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


def _iso_date(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return text
    raise GatewayError(GatewayErrorCode.INVALID_REQUEST, "trade_date must be YYYY-MM-DD or YYYYMMDD")


def _eastmoney_diff(payload: dict[str, Any]) -> list[dict[str, Any]]:
    diff = (payload.get("data") or {}).get("diff") if isinstance(payload, dict) else []
    if isinstance(diff, dict):
        diff = list(diff.values())
    if not isinstance(diff, list):
        return []
    return [item for item in diff if isinstance(item, dict)]


def _number(value: Any) -> Optional[float]:
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _number_or_zero(value: Any) -> float:
    parsed = _number(value)
    return parsed if parsed is not None else 0.0
