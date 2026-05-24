from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode
from stock_data_gateway.domain.provider import ProviderHealth, ProviderResponse


class EastmoneyProvider:
    provider_name = "eastmoney"

    def __init__(self, *, urlopen_fn=None, timeout_seconds: float = 8.0) -> None:
        self._urlopen = urlopen_fn or urlopen
        self._timeout_seconds = timeout_seconds

    def fetch(self, endpoint: str, params: dict[str, Any], fields: Optional[str] = None) -> ProviderResponse:
        del fields
        if endpoint == "market_quotes":
            return self._fetch_market_quotes(params)
        if endpoint in {"northbound_capital", "main_capital_flow"}:
            return ProviderResponse.from_rows(provider=self.provider_name, endpoint=endpoint, rows=[])
        raise GatewayError(GatewayErrorCode.INVALID_REQUEST, f"Unsupported EastMoney endpoint: {endpoint}")

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, ok=True, message="configured")

    def _fetch_market_quotes(self, params: dict[str, Any]) -> ProviderResponse:
        stock_codes = _stock_codes(params.get("stock_codes") or params.get("symbols"))
        if not stock_codes:
            raise GatewayError(GatewayErrorCode.INVALID_REQUEST, "stock_codes is required")

        query = urlencode(
            {
                "fltt": "2",
                "invt": "2",
                "fields": "f12,f14,f2,f3,f4,f5,f6",
                "secids": ",".join(_eastmoney_secid(code) for code in stock_codes),
            }
        )
        request = Request(
            f"https://push2.eastmoney.com/api/qt/ulist.np/get?{query}",
            headers={
                "Accept": "application/json",
                "User-Agent": "stock-data-gateway/0.1",
            },
        )
        try:
            with self._urlopen(request, timeout=self._timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as error:
            raise GatewayError(
                GatewayErrorCode.PROVIDER_UNAVAILABLE,
                "EastMoney market quote request failed.",
            ) from error

        diff = (payload.get("data") or {}).get("diff") if isinstance(payload, dict) else []
        if isinstance(diff, dict):
            diff = list(diff.values())
        if not isinstance(diff, list):
            diff = []

        retrieved_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        rows = [
            {
                "stock_code": str(item.get("f12") or ""),
                "stock_name": _clean_value(item.get("f14")),
                "latest_price": _clean_value(item.get("f2")),
                "change_percent": _clean_value(item.get("f3")),
                "change_amount": _clean_value(item.get("f4")),
                "volume": _clean_value(item.get("f5")),
                "amount": _clean_value(item.get("f6")),
                "retrieved_at": retrieved_at,
                "source": self.provider_name,
            }
            for item in diff
            if isinstance(item, dict) and item.get("f12")
        ]
        return ProviderResponse.from_rows(provider=self.provider_name, endpoint="market_quotes", rows=rows)


def _stock_codes(value: Any) -> list[str]:
    raw_values = value if isinstance(value, list) else str(value or "").split(",")
    return [str(item).strip().upper() for item in raw_values if str(item).strip()]


def _eastmoney_secid(code: str) -> str:
    clean = code.upper().replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
    if code.endswith(".SH") or clean.startswith(("5", "6", "9")):
        return f"1.{clean}"
    if code.endswith(".BJ") or clean.startswith(("4", "8")):
        return f"0.{clean}"
    return f"0.{clean}"


def _clean_value(value: Any) -> Any:
    if value in ("-", "", None):
        return None
    return value
