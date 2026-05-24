from __future__ import annotations

from typing import Any, Optional, Protocol

from stock_data_gateway.domain.provider import ProviderHealth, ProviderResponse


class ExternalDataProvider(Protocol):
    provider_name: str

    def fetch(self, endpoint: str, params: dict[str, Any], fields: Optional[str] = None) -> ProviderResponse: ...

    def health_check(self) -> ProviderHealth: ...
