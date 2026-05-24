from __future__ import annotations

import pytest

from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode
from stock_data_gateway.providers.akshare.adapter import AkshareProvider
from stock_data_gateway.providers.eastmoney.adapter import EastmoneyProvider


@pytest.mark.parametrize("provider_cls", [AkshareProvider, EastmoneyProvider])
def test_placeholder_provider_reports_unavailable(provider_cls) -> None:
    provider = provider_cls()

    with pytest.raises(GatewayError) as raised:
        provider.fetch("anything", {})

    assert raised.value.code == GatewayErrorCode.PROVIDER_UNAVAILABLE
    assert provider.health_check().ok is False
