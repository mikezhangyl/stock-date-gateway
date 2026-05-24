from __future__ import annotations

from typing import Any, Optional

from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode
from stock_data_gateway.domain.provider import ProviderHealth, ProviderResponse


class EastmoneyProvider:
    provider_name = "eastmoney"

    def fetch(self, endpoint: str, params: dict[str, Any], fields: Optional[str] = None) -> ProviderResponse:
        raise GatewayError(GatewayErrorCode.PROVIDER_UNAVAILABLE, "EastMoney provider is not implemented yet.")

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, ok=False, message="not_implemented")
