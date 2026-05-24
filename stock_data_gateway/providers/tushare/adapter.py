from __future__ import annotations

from typing import Any, Callable, Optional

from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode
from stock_data_gateway.domain.provider import ProviderHealth, ProviderResponse
from stock_data_gateway.providers.tushare.client import TushareMarketDataClient


class TushareProvider:
    provider_name = "tushare"

    def __init__(
        self,
        client: Optional[TushareMarketDataClient] = None,
        client_factory: Optional[Callable[[], TushareMarketDataClient]] = None,
    ) -> None:
        self._client = client
        self._client_factory = client_factory or TushareMarketDataClient

    @property
    def client(self) -> TushareMarketDataClient:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    def fetch(self, endpoint: str, params: dict[str, Any], fields: Optional[str] = None) -> ProviderResponse:
        frame = self.client.fetch_dataframe(endpoint, params, fields)
        rows = _frame_to_rows(frame)
        provider_response = ProviderResponse.from_rows(provider=self.provider_name, endpoint=endpoint, rows=rows)
        if fields:
            requested_fields = [field.strip() for field in fields.split(",") if field.strip()]
            return _project_response(provider_response, requested_fields)
        return provider_response

    def health_check(self) -> ProviderHealth:
        try:
            _ = self.client
        except GatewayError as error:
            return ProviderHealth(provider=self.provider_name, ok=False, message=error.code.value)
        return ProviderHealth(provider=self.provider_name, ok=True, message=None)


def _frame_to_rows(frame: Any) -> list[dict[str, Any]]:
    if getattr(frame, "empty", False):
        return []
    columns = [str(column) for column in getattr(frame, "columns", [])]
    rows: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        rows.append({column: _json_safe_cell(row[column]) for column in columns})
    return rows


def _json_safe_cell(value: Any) -> Any:
    if value is None:
        return None
    try:
        if isinstance(value, float) and value != value:
            return None
    except TypeError:
        pass
    try:
        import pandas as pd

        if bool(pd.isna(value)):
            return None
    except (ImportError, TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            return value
    return value


def _project_response(response: ProviderResponse, requested_fields: list[str]) -> ProviderResponse:
    projected_items: list[list[Any]] = []
    for row in response.rows():
        missing = [field for field in requested_fields if field not in row]
        if missing:
            raise GatewayError(
                GatewayErrorCode.SCHEMA_CHANGED,
                f"Provider response missing fields: {','.join(missing)}",
            )
        projected_items.append([row[field] for field in requested_fields])
    return ProviderResponse(
        provider=response.provider,
        endpoint=response.endpoint,
        fields=requested_fields,
        items=projected_items,
        raw=response.raw,
    )
