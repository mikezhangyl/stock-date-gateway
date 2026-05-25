from __future__ import annotations

import pytest

from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode
from stock_data_gateway.providers.akshare.adapter import AkshareProvider
from stock_data_gateway.providers.eastmoney.adapter import EastmoneyProvider


@pytest.mark.parametrize("provider_cls", [AkshareProvider, EastmoneyProvider])
def test_provider_rejects_unknown_endpoint(provider_cls) -> None:
    provider = provider_cls()

    with pytest.raises(GatewayError) as raised:
        provider.fetch("anything", {})

    assert raised.value.code == GatewayErrorCode.INVALID_REQUEST


def test_eastmoney_health_reports_configured_provider() -> None:
    assert EastmoneyProvider().health_check().ok is True


def test_akshare_health_reports_eastmoney_fallback_without_importing_dependency() -> None:
    provider = AkshareProvider(module_factory=lambda: (_ for _ in ()).throw(ImportError("missing")))

    health = provider.health_check()

    assert health.ok is True
    assert health.message == "configured_with_eastmoney_fallback"
