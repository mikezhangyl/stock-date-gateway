from __future__ import annotations

import pytest

from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode
from stock_data_gateway.providers.akshare.adapter import AkshareProvider
from tests.fakes import FakeFrame


class FakeAkshareModule:
    def stock_board_concept_name_em(self):
        return FakeFrame([{"板块名称": "白酒", "涨跌幅": 2.3}])

    def stock_zt_pool_em(self, date: str):
        assert date == "20260522"
        return FakeFrame([{"代码": "000001"}, {"代码": "600519"}])

    def stock_zt_pool_dtgc_em(self, date: str):
        assert date == "20260522"
        return FakeFrame([{"代码": "000002"}])


def test_akshare_sector_concepts_maps_dataframe() -> None:
    provider = AkshareProvider(module_factory=lambda: FakeAkshareModule())

    response = provider.fetch("sector_concepts", {"limit": "1"})

    assert response.rows() == [{"sector_name": "白酒", "pct_change": 2.3, "source": "akshare"}]


def test_akshare_limit_up_down_counts_rows() -> None:
    provider = AkshareProvider(module_factory=lambda: FakeAkshareModule())

    response = provider.fetch("limit_up_down", {"trade_date": "2026-05-22"})

    assert response.rows() == [
        {
            "trade_date": "2026-05-22",
            "limit_up_count": 2,
            "limit_down_count": 1,
            "source": "akshare",
        }
    ]


def test_akshare_health_reports_configured_module() -> None:
    provider = AkshareProvider(module_factory=lambda: FakeAkshareModule())

    assert provider.health_check().ok is True


def test_akshare_limit_up_down_rejects_bad_trade_date() -> None:
    provider = AkshareProvider(module_factory=lambda: FakeAkshareModule())

    with pytest.raises(GatewayError) as raised:
        provider.fetch("limit_up_down", {"trade_date": "bad-date"})

    assert raised.value.code == GatewayErrorCode.INVALID_REQUEST
