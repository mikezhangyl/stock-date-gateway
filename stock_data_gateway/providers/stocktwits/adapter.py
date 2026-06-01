from __future__ import annotations

import json
from typing import Any, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode
from stock_data_gateway.domain.provider import ProviderHealth, ProviderResponse

_STOCKTWITS_SYMBOL_URL = "https://api.stocktwits.com/api/2/streams/symbol"


class StocktwitsProvider:
    provider_name = "stocktwits"

    def __init__(self, *, urlopen_fn=None, timeout_seconds: float = 8.0) -> None:
        self._urlopen = urlopen_fn or urlopen
        self._timeout_seconds = timeout_seconds

    def fetch(self, endpoint: str, params: dict[str, Any], fields: Optional[str] = None) -> ProviderResponse:
        del fields
        if endpoint == "social_heat":
            return self._fetch_social_heat(params)
        raise GatewayError(GatewayErrorCode.INVALID_REQUEST, f"Unsupported Stocktwits endpoint: {endpoint}")

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, ok=True, message="configured_disabled_by_default")

    def _fetch_social_heat(self, params: dict[str, Any]) -> ProviderResponse:
        symbol = _symbol(params.get("symbol"))
        limit = _optional_int(params.get("limit"), default=5, maximum=30)
        query = urlencode({"limit": str(limit)})
        request = Request(
            f"{_STOCKTWITS_SYMBOL_URL}/{symbol}.json?{query}",
            headers={"Accept": "application/json", "User-Agent": "stock-data-gateway/0.1"},
        )
        try:
            with self._urlopen(request, timeout=self._timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
                rate_limit_remaining = response.headers.get("X-RateLimit-Remaining")
        except Exception as error:
            raise GatewayError(GatewayErrorCode.PROVIDER_UNAVAILABLE, "Stocktwits request failed.") from error
        messages = payload.get("messages") if isinstance(payload, dict) else None
        if not isinstance(messages, list):
            messages = []
        rows = [
            {
                "message_id": str(message.get("id")),
                "symbol": symbol,
                "body": str(message.get("body") or ""),
                "created_at": message.get("created_at"),
                "source_url": f"https://stocktwits.com/symbol/{symbol}",
                "rate_limit_remaining": rate_limit_remaining,
            }
            for message in messages[:limit]
            if isinstance(message, dict) and message.get("id")
        ]
        if not rows:
            raise GatewayError(GatewayErrorCode.EMPTY_DATA, "Stocktwits returned no messages.")
        return ProviderResponse.from_rows(provider=self.provider_name, endpoint="social_heat", rows=rows)


def _symbol(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        raise GatewayError(GatewayErrorCode.INVALID_REQUEST, "symbol is required")
    return "".join(character for character in text if character.isalnum() or character in {".", "-"})


def _optional_int(value: Any, *, default: int, maximum: int) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise GatewayError(GatewayErrorCode.INVALID_REQUEST, "limit must be an integer") from error
    if parsed < 0:
        raise GatewayError(GatewayErrorCode.INVALID_REQUEST, "limit must be non-negative")
    return min(parsed, maximum)
