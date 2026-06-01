from __future__ import annotations

import json
from urllib.error import URLError

import pytest

from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode
from stock_data_gateway.providers.cninfo.adapter import (
    CNINFO_ANNOUNCEMENT_QUERY_URL,
    CninfoProvider,
    build_cninfo_announcement_payload,
    build_cninfo_stock_selector,
)


class FakeHttpResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def __enter__(self) -> "FakeHttpResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_builds_cninfo_payload_with_exchange_org_id_and_date_range() -> None:
    payload = build_cninfo_announcement_payload(
        stock_code="000001",
        start_date="2026-01-01",
        end_date="2026-05-13",
        page_num=2,
        page_size=50,
    )

    assert payload["pageNum"] == 2
    assert payload["pageSize"] == 50
    assert payload["column"] == "szse"
    assert payload["tabName"] == "fulltext"
    assert payload["stock"] == "000001,gssz0000001"
    assert payload["seDate"] == "2026-01-01~2026-05-13"


def test_builds_cninfo_stock_selector_for_cn_exchanges() -> None:
    assert build_cninfo_stock_selector("000001") == "000001,gssz0000001"
    assert build_cninfo_stock_selector("300750") == "300750,gssz0300750"
    assert build_cninfo_stock_selector("600519") == "600519,gssh0600519"
    assert build_cninfo_stock_selector("688981") == "688981,gssh0688981"
    assert build_cninfo_stock_selector("430047") == "430047"


def test_cninfo_official_disclosures_maps_announcement_metadata() -> None:
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request.full_url, request.data.decode("utf-8"), request.headers, timeout))
        return FakeHttpResponse(
            {
                "announcements": [
                    {
                        "secCode": "000001",
                        "secName": "平安银行",
                        "announcementTitle": "2025年度报告",
                        "adjunctUrl": "finalpage/2026-03-15/123456.PDF",
                        "announcementTime": "2026-03-15",
                        "categoryName": "年度报告",
                    }
                ]
            }
        )

    provider = CninfoProvider(urlopen_fn=fake_urlopen, timeout_seconds=4.0)

    response = provider.fetch(
        "official_disclosures",
        {"symbol": "000001", "start_date": "2026-03-01", "end_date": "2026-03-31", "limit": 1},
    )

    rows = response.rows()
    assert rows == [
        {
            "symbol": "000001",
            "name": "平安银行",
            "ann_date": "2026-03-15",
            "title": "2025年度报告",
            "event_type": "performance_forecast_report",
            "event_label_zh": "业绩预告/报告",
            "sentiment": "mixed",
            "category": "年度报告",
            "source": "cninfo",
            "provider": "cninfo",
            "source_url": "https://static.cninfo.com.cn/finalpage/2026-03-15/123456.PDF",
            "provider_item_id": "000001:2026-03-15:2025年度报告",
            "metadata_only": True,
        }
    ]
    assert requests[0][0] == CNINFO_ANNOUNCEMENT_QUERY_URL
    assert "stock=000001%2Cgssz0000001" in requests[0][1]
    assert requests[0][2]["Content-type"] == "application/x-www-form-urlencoded; charset=UTF-8"
    assert requests[0][3] == 4.0


def test_cninfo_invalid_symbol_is_gateway_error() -> None:
    provider = CninfoProvider(urlopen_fn=lambda request, timeout: FakeHttpResponse({}))

    with pytest.raises(GatewayError) as raised:
        provider.fetch("official_disclosures", {"symbol": "not-a-code"})

    assert raised.value.code == GatewayErrorCode.INVALID_REQUEST


def test_cninfo_network_error_is_gateway_error() -> None:
    def failing_urlopen(request, timeout):
        raise URLError("network unavailable")

    provider = CninfoProvider(urlopen_fn=failing_urlopen)

    with pytest.raises(GatewayError) as raised:
        provider.fetch("official_disclosures", {"symbol": "000001"})

    assert raised.value.code == GatewayErrorCode.PROVIDER_UNAVAILABLE
