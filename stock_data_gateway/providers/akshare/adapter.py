from __future__ import annotations

from typing import Any, Optional

from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode
from stock_data_gateway.domain.provider import ProviderHealth, ProviderResponse


class AkshareProvider:
    provider_name = "akshare"

    def __init__(self, *, module_factory=None) -> None:
        self._module_factory = module_factory
        self._module = None

    def fetch(self, endpoint: str, params: dict[str, Any], fields: Optional[str] = None) -> ProviderResponse:
        del fields
        if endpoint == "sector_concepts":
            return self._fetch_sector_concepts(params)
        if endpoint == "limit_up_down":
            return self._fetch_limit_up_down(params)
        raise GatewayError(GatewayErrorCode.INVALID_REQUEST, f"Unsupported AkShare endpoint: {endpoint}")

    def health_check(self) -> ProviderHealth:
        try:
            self._akshare()
        except GatewayError as error:
            return ProviderHealth(provider=self.provider_name, ok=False, message=error.message)
        return ProviderHealth(provider=self.provider_name, ok=True, message="configured")

    def _fetch_sector_concepts(self, params: dict[str, Any]) -> ProviderResponse:
        frame = self._akshare().stock_board_concept_name_em()
        limit = _optional_int(params.get("limit"))
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
        return ProviderResponse.from_rows(provider=self.provider_name, endpoint="sector_concepts", rows=rows)

    def _fetch_limit_up_down(self, params: dict[str, Any]) -> ProviderResponse:
        trade_date = _iso_date(params.get("trade_date"))
        compact_date = trade_date.replace("-", "")
        module = self._akshare()
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

    def _akshare(self):
        if self._module is not None:
            return self._module
        try:
            self._module = self._module_factory() if self._module_factory else __import__("akshare")
        except Exception as error:
            raise GatewayError(GatewayErrorCode.PROVIDER_UNAVAILABLE, "AkShare package is not available.") from error
        return self._module


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
