from __future__ import annotations

import json

import pytest

from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode
from stock_data_gateway.providers.akshare.adapter import AkshareProvider
from tests.fakes import FakeFrame


class FakeAkshareModule:
    def stock_board_concept_name_em(self):
        return FakeFrame([{"板块名称": "白酒", "涨跌幅": 2.3}])

    def stock_board_concept_cons_em(self, symbol: str):
        assert symbol == "机器人"
        return FakeFrame([{"代码": "300024", "名称": "机器人", "涨跌幅": 2.1}])

    def fund_etf_spot_em(self):
        return FakeFrame([{"代码": "510300", "名称": "沪深300ETF", "涨跌幅": 1.4, "最新价": 4.12, "成交额": 1200000}])

    def fund_etf_category_ths(self, symbol: str = "ETF", date: str = ""):
        assert symbol == "ETF"
        assert date == ""
        return FakeFrame(
            [{"基金代码": "510300", "基金名称": "沪深300ETF", "基金类型": "股票型", "查询日期": "2026-05-26"}]
        )

    def fund_etf_fund_flow_rank_em(self):
        return FakeFrame(
            [{"代码": "510300", "名称": "沪深300ETF", "主力净流入": 8800000, "涨跌幅": 1.4, "成交额": 1200000}]
        )

    def index_stock_cons_weight_csindex(self, symbol: str):
        assert symbol == "000300"
        return FakeFrame(
            [
                {
                    "日期": "2026-04-30",
                    "指数代码": "000300",
                    "成分券代码": "000001",
                    "成分券名称": "平安银行",
                    "权重": 0.425,
                }
            ]
        )

    def stock_margin_sse(self, start_date: str, end_date: str):
        assert start_date == "20260522"
        assert end_date == "20260522"
        return FakeFrame([{"信用交易日期": "20260522", "融资余额": 1000, "融资买入额": 70, "融券余量金额": 20}])

    def stock_margin_szse(self, date: str):
        assert date == "20260522"
        return FakeFrame([{"融资余额": 2, "融资买入额": 0.1, "融券余额": 0.2}])

    def stock_margin_detail_sse(self, date: str):
        assert date == "20260522"
        return FakeFrame(
            [{"信用交易日期": "20260522", "标的证券代码": "600000", "标的证券简称": "浦发银行", "融资余额": 600}]
        )

    def stock_margin_detail_szse(self, date: str):
        assert date == "20260522"
        return FakeFrame(
            [{"证券代码": "000001", "证券简称": "平安银行", "融资余额": 500, "融资买入额": 50, "融券余额": 5}]
        )

    def stock_notice_report(self, symbol: str, date: str):
        assert symbol == "全部"
        assert date == "20260522"
        return FakeFrame([{"代码": "000001", "名称": "平安银行", "公告日期": "2026-05-22", "公告类型": "年度报告"}])

    def stock_zt_pool_em(self, date: str):
        assert date == "20260522"
        return FakeFrame([{"代码": "000001"}, {"代码": "600519"}])

    def stock_zt_pool_dtgc_em(self, date: str):
        assert date == "20260522"
        return FakeFrame([{"代码": "000002"}])

    def stock_lhb_detail_em(self, date: str):
        assert date == "20260522"
        return FakeFrame(
            [
                {
                    "代码": "600519",
                    "名称": "贵州茅台",
                    "上榜原因": "日涨幅偏离值达7%",
                    "买入额": 100000000,
                    "卖出额": 60000000,
                    "净买额": 40000000,
                }
            ]
        )


class FailingAkshareModule(FakeAkshareModule):
    def fund_etf_fund_flow_rank_em(self):
        raise RuntimeError("akshare upstream changed")

    def stock_lhb_detail_em(self, date: str):
        raise RuntimeError("akshare upstream changed")


class FakeHttpResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def __enter__(self) -> "FakeHttpResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_akshare_sector_concepts_maps_dataframe() -> None:
    provider = AkshareProvider(module_factory=lambda: FakeAkshareModule())

    response = provider.fetch("sector_concepts", {"limit": "1"})

    assert response.rows() == [{"sector_name": "白酒", "pct_change": 2.3, "source": "akshare"}]


def test_akshare_sector_constituents_maps_dataframe() -> None:
    provider = AkshareProvider(module_factory=lambda: FakeAkshareModule())

    response = provider.fetch(
        "sector_constituents",
        {"sector_name": "机器人", "trade_date": "2026-05-22", "limit": "1"},
    )

    assert response.rows() == [
        {
            "sector_name": "机器人",
            "symbol": "300024",
            "name": "机器人",
            "source": "akshare",
            "trade_date": "2026-05-22",
            "pct_change": 2.1,
            "provider": "akshare",
        }
    ]


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


def test_akshare_etf_spot_maps_dataframe() -> None:
    provider = AkshareProvider(module_factory=lambda: FakeAkshareModule())

    response = provider.fetch("etf_spot", {"limit": "1"})

    assert response.rows() == [
        {
            "symbol": "510300",
            "name": "沪深300ETF",
            "pct_change": 1.4,
            "source": "akshare",
            "close": 4.12,
            "amount": 1200000.0,
            "provider": "akshare",
        }
    ]


def test_akshare_etf_basic_maps_dataframe() -> None:
    provider = AkshareProvider(module_factory=lambda: FakeAkshareModule())

    response = provider.fetch("etf_basic", {"market": "cn", "limit": "1"})

    assert response.rows() == [
        {
            "symbol": "510300",
            "name": "沪深300ETF",
            "source": "akshare",
            "category": "ETF",
            "fund_type": "股票型",
            "provider": "akshare",
        }
    ]


def test_akshare_etf_flow_maps_dataframe() -> None:
    provider = AkshareProvider(module_factory=lambda: FakeAkshareModule())

    response = provider.fetch("etf_flow", {"trade_date": "2026-05-22", "limit": "1"})

    assert response.rows() == [
        {
            "trade_date": "2026-05-22",
            "symbol": "510300",
            "name": "沪深300ETF",
            "net_inflow": 8800000.0,
            "source": "akshare",
            "pct_change": 1.4,
            "amount": 1200000.0,
            "provider": "akshare",
        }
    ]


def test_akshare_index_constituents_maps_dataframe() -> None:
    provider = AkshareProvider(module_factory=lambda: FakeAkshareModule())

    response = provider.fetch(
        "index_constituents",
        {"index_symbol": "000300.SH", "trade_date": "2026-05-22", "limit": "1"},
    )

    assert response.rows() == [
        {
            "index_symbol": "000300.SH",
            "symbol": "000001",
            "name": "平安银行",
            "source": "akshare",
            "trade_date": "2026-05-22",
            "weight": 0.425,
            "provider": "akshare",
        }
    ]


def test_akshare_margin_summary_combines_exchange_rows() -> None:
    provider = AkshareProvider(module_factory=lambda: FakeAkshareModule())

    response = provider.fetch("margin_summary", {"trade_date": "2026-05-22"})

    assert response.rows() == [
        {
            "trade_date": "2026-05-22",
            "financing_balance": 200001000.0,
            "source": "akshare",
            "securities_lending_balance": 20000020.0,
            "financing_buy_amount": 10000070.0,
            "provider": "akshare",
        }
    ]


def test_akshare_margin_detail_combines_exchange_rows() -> None:
    provider = AkshareProvider(module_factory=lambda: FakeAkshareModule())

    response = provider.fetch("margin_detail", {"trade_date": "2026-05-22", "limit": "2"})

    assert response.rows() == [
        {
            "trade_date": "2026-05-22",
            "symbol": "600000",
            "financing_balance": 600.0,
            "source": "akshare",
            "name": "浦发银行",
            "provider": "akshare",
            "financing_buy_amount": None,
            "securities_lending_balance": None,
        },
        {
            "trade_date": "2026-05-22",
            "symbol": "000001",
            "financing_balance": 500.0,
            "source": "akshare",
            "name": "平安银行",
            "financing_buy_amount": 50.0,
            "securities_lending_balance": 5.0,
            "provider": "akshare",
        },
    ]


def test_akshare_earnings_calendar_maps_notice_rows() -> None:
    provider = AkshareProvider(module_factory=lambda: FakeAkshareModule())

    response = provider.fetch(
        "earnings_calendar",
        {"start_date": "2026-05-22", "end_date": "2026-05-22", "limit": "1"},
    )

    assert response.rows() == [
        {
            "symbol": "000001",
            "name": "平安银行",
            "ann_date": "2026-05-22",
            "event_type": "年度报告",
            "source": "akshare",
            "provider": "akshare",
        }
    ]


def test_akshare_dragon_tiger_maps_dataframe() -> None:
    provider = AkshareProvider(module_factory=lambda: FakeAkshareModule())

    response = provider.fetch("dragon_tiger", {"trade_date": "2026-05-22", "limit": "1"})

    assert response.rows() == [
        {
            "trade_date": "2026-05-22",
            "symbol": "600519",
            "reason": "日涨幅偏离值达7%",
            "source": "akshare",
            "name": "贵州茅台",
            "buy_amount": 100000000.0,
            "sell_amount": 60000000.0,
            "net_buy_amount": 40000000.0,
            "provider": "akshare",
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


def test_akshare_sector_concepts_falls_back_to_eastmoney_when_package_missing() -> None:
    def fake_urlopen(request, timeout):
        return FakeHttpResponse({"data": {"diff": [{"f12": "BK0890", "f14": "MLCC", "f3": 5.64}]}})

    provider = AkshareProvider(
        module_factory=lambda: (_ for _ in ()).throw(ImportError("missing")),
        urlopen_fn=fake_urlopen,
    )

    response = provider.fetch("sector_concepts", {"limit": "1"})

    assert response.rows() == [{"sector_name": "MLCC", "pct_change": 5.64, "source": "eastmoney"}]


def test_akshare_sector_constituents_falls_back_to_eastmoney_when_package_missing() -> None:
    def fake_urlopen(request, timeout):
        assert "b%3ABK1090" in request.full_url
        return FakeHttpResponse({"data": {"diff": [{"f12": "300024", "f14": "机器人", "f3": 2.1}]}})

    provider = AkshareProvider(
        module_factory=lambda: (_ for _ in ()).throw(ImportError("missing")),
        urlopen_fn=fake_urlopen,
    )

    response = provider.fetch(
        "sector_constituents",
        {"sector_name": "机器人", "trade_date": "2026-05-22", "limit": "1"},
    )

    assert response.rows() == [
        {
            "sector_name": "机器人",
            "symbol": "300024",
            "name": "机器人",
            "source": "eastmoney",
            "trade_date": "2026-05-22",
            "pct_change": 2.1,
            "provider": "eastmoney",
        }
    ]


def test_akshare_limit_up_down_fallback_returns_one_row_when_package_missing() -> None:
    def fake_urlopen(request, timeout):
        return FakeHttpResponse(
            {"data": {"diff": [{"f12": "000001", "f3": 10.01}, {"f12": "000002", "f3": -10.02}]}}
        )

    provider = AkshareProvider(
        module_factory=lambda: (_ for _ in ()).throw(ImportError("missing")),
        urlopen_fn=fake_urlopen,
    )

    response = provider.fetch("limit_up_down", {"trade_date": "2026-05-22"})

    assert response.rows() == [
        {
            "trade_date": "2026-05-22",
            "limit_up_count": 1,
            "limit_down_count": 1,
            "source": "eastmoney",
        }
    ]


def test_akshare_etf_spot_falls_back_to_eastmoney_when_package_missing() -> None:
    def fake_urlopen(request, timeout):
        return FakeHttpResponse({"data": {"diff": [{"f12": "510500", "f14": "中证500ETF", "f3": 2.5, "f2": 6.4}]}})

    provider = AkshareProvider(
        module_factory=lambda: (_ for _ in ()).throw(ImportError("missing")),
        urlopen_fn=fake_urlopen,
    )

    response = provider.fetch("etf_spot", {"limit": "1"})

    assert response.rows() == [
        {
            "symbol": "510500",
            "name": "中证500ETF",
            "pct_change": 2.5,
            "source": "eastmoney",
            "close": 6.4,
            "provider": "eastmoney",
        }
    ]


def test_akshare_etf_flow_falls_back_to_eastmoney_when_package_missing() -> None:
    def fake_urlopen(request, timeout):
        return FakeHttpResponse(
            {"data": {"diff": [{"f12": "510500", "f14": "中证500ETF", "f62": 9200000, "f3": 2.5, "f6": 6.4}]}}
        )

    provider = AkshareProvider(
        module_factory=lambda: (_ for _ in ()).throw(ImportError("missing")),
        urlopen_fn=fake_urlopen,
    )

    response = provider.fetch("etf_flow", {"trade_date": "2026-05-22", "limit": "1"})

    assert response.rows() == [
        {
            "trade_date": "2026-05-22",
            "symbol": "510500",
            "net_inflow": 9200000.0,
            "source": "eastmoney",
            "name": "中证500ETF",
            "pct_change": 2.5,
            "amount": 6.4,
            "provider": "eastmoney",
        }
    ]


def test_akshare_flow_and_event_fall_back_when_package_call_fails() -> None:
    def fake_urlopen(request, timeout):
        if "RPT_DAILYBILLBOARD_DETAILS" in request.full_url:
            return FakeHttpResponse(
                {
                    "result": {
                        "data": [
                            {
                                "TRADE_DATE": "2026-05-22",
                                "SECURITY_CODE": "600519",
                                "SECURITY_NAME_ABBR": "贵州茅台",
                                "EXPLANATION": "日涨幅偏离值达7%",
                                "BILLBOARD_BUY_AMT": 100.0,
                                "BILLBOARD_SELL_AMT": 60.0,
                                "BILLBOARD_NET_AMT": 40.0,
                            }
                        ]
                    }
                }
            )
        return FakeHttpResponse(
            {"data": {"diff": [{"f12": "510500", "f14": "中证500ETF", "f62": 9200000, "f3": 2.5, "f6": 6.4}]}}
        )

    provider = AkshareProvider(module_factory=lambda: FailingAkshareModule(), urlopen_fn=fake_urlopen)

    etf_flow = provider.fetch("etf_flow", {"trade_date": "2026-05-22", "limit": "1"})
    dragon_tiger = provider.fetch("dragon_tiger", {"trade_date": "2026-05-22", "limit": "1"})

    assert etf_flow.rows()[0]["source"] == "eastmoney"
    assert dragon_tiger.rows()[0] == {
        "trade_date": "2026-05-22",
        "symbol": "600519",
        "reason": "日涨幅偏离值达7%",
        "source": "eastmoney",
        "name": "贵州茅台",
        "buy_amount": 100.0,
        "sell_amount": 60.0,
        "net_buy_amount": 40.0,
        "provider": "eastmoney",
    }
