from __future__ import annotations

import json
import sqlite3
import time

from fastapi.testclient import TestClient

from stock_data_gateway.cache.service import ReadThroughQueryService
from stock_data_gateway.cache.sqlite_store import SQLiteCacheStore
from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode
from stock_data_gateway.domain.provider import ProviderHealth, ProviderResponse
from stock_data_gateway.main import create_app
from stock_data_gateway.policies.registry import PolicyRegistry
from stock_data_gateway.policies.tushare import register_tushare_policies
from stock_data_gateway.providers.base import ExternalDataProvider
from tests.fakes import FakeProvider


class FakeEastmoneyProvider(ExternalDataProvider):
    provider_name = "eastmoney"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def fetch(self, endpoint: str, params: dict, fields: str | None = None) -> ProviderResponse:
        self.calls.append((endpoint, dict(params)))
        if endpoint == "fund_profile":
            return ProviderResponse.from_rows(
                provider=self.provider_name,
                endpoint=endpoint,
                rows=[
                    {
                        "fund_code": params.get("fund_code", "161725"),
                        "fund_name": "EastMoney Fund 161725",
                        "fund_type": "fund",
                        "currency": "CNY",
                        "as_of_date": "2026-05-22",
                        "provider": "eastmoney",
                        "source": "eastmoney",
                        "source_url": "https://fundmobapi.eastmoney.com/FundMNewApi/FundMNInverstPosition?FCODE=161725",
                        "retrieved_at": "2026-05-27T00:00:00+00:00",
                        "data_quality": "fresh",
                    }
                ],
            )
        if endpoint == "fund_holdings":
            return ProviderResponse.from_rows(
                provider=self.provider_name,
                endpoint=endpoint,
                rows=[
                    {
                        "fund_code": params.get("fund_code", "161725"),
                        "as_of_date": "2026-05-22",
                        "rank": 1,
                        "stock_code": "600519",
                        "stock_name": "贵州茅台",
                        "weight": 0.098,
                        "holding_change": 0.001,
                        "industry": "白酒",
                        "market": "SH",
                        "ts_code": "600519.SH",
                        "provider": "eastmoney",
                        "source": "eastmoney",
                        "source_url": "https://fundmobapi.eastmoney.com/FundMNewApi/FundMNInverstPosition?FCODE=161725",
                        "retrieved_at": "2026-05-27T00:00:00+00:00",
                    },
                    {
                        "fund_code": params.get("fund_code", "161725"),
                        "as_of_date": "2026-05-22",
                        "rank": 2,
                        "stock_code": "000858",
                        "stock_name": "五粮液",
                        "weight": 0.081,
                        "holding_change": 0.0,
                        "industry": "白酒",
                        "market": "SZ",
                        "ts_code": "000858.SZ",
                        "provider": "eastmoney",
                        "source": "eastmoney",
                        "source_url": "https://fundmobapi.eastmoney.com/FundMNewApi/FundMNInverstPosition?FCODE=161725",
                        "retrieved_at": "2026-05-27T00:00:00+00:00",
                    },
                ],
            )
        if endpoint == "northbound_capital":
            return ProviderResponse.from_rows(
                provider=self.provider_name,
                endpoint=endpoint,
                rows=[
                    {
                        "trade_date": "2026-05-22",
                        "net_buy_amount": 12.5,
                        "buy_amount": 100.0,
                        "sell_amount": 87.5,
                        "source": "eastmoney",
                        "provider": "eastmoney",
                    }
                ],
            )
        if endpoint == "main_capital_flow":
            return ProviderResponse.from_rows(
                provider=self.provider_name,
                endpoint=endpoint,
                rows=[
                    {
                        "trade_date": "2026-05-22",
                        "symbol": "600519",
                        "name": "贵州茅台",
                        "main_net_inflow": 123.0,
                        "pct_change": 1.2,
                        "amount": 999.0,
                        "source": "eastmoney",
                        "provider": "eastmoney",
                    }
                ],
            )
        return ProviderResponse.from_rows(
            provider=self.provider_name,
            endpoint=endpoint,
            rows=[
                {
                    "stock_code": "600519",
                    "stock_name": "贵州茅台",
                    "latest_price": 1688.0,
                    "change_percent": 1.2,
                    "retrieved_at": "2026-05-25T00:00:00+00:00",
                    "source": "eastmoney",
                }
            ],
        )

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, ok=True)


class FakeSecEdgarProvider(ExternalDataProvider):
    provider_name = "sec_edgar"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def fetch(self, endpoint: str, params: dict, fields: str | None = None) -> ProviderResponse:
        self.calls.append((endpoint, dict(params)))
        assert endpoint == "official_filings"
        return ProviderResponse.from_rows(
            provider=self.provider_name,
            endpoint=endpoint,
            rows=[
                {
                    "cik": "0000320193",
                    "entity_name": "Apple Inc.",
                    "market": "US",
                    "form": "10-Q",
                    "filing_date": "2026-05-29",
                    "accession_number": "0000320193-26-000001",
                    "primary_document": "aapl-20260529.htm",
                    "title": "10-Q quarterly report",
                    "source_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/aapl-20260529.htm",
                    "raw_hash": "sec-hash",
                    "blob_uri": "bronze/sec_edgar/submissions/CIK0000320193.json",
                    "metadata_only": True,
                }
            ],
        )

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, ok=True)


class SequencedSecEdgarProvider(ExternalDataProvider):
    provider_name = "sec_edgar"

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls = 0

    def fetch(self, endpoint: str, params: dict, fields: str | None = None) -> ProviderResponse:
        assert endpoint == "official_filings"
        row = self.rows[min(self.calls, len(self.rows) - 1)]
        self.calls += 1
        return ProviderResponse.from_rows(provider=self.provider_name, endpoint=endpoint, rows=[row])

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, ok=True)


class FlakySecEdgarProvider(ExternalDataProvider):
    provider_name = "sec_edgar"

    def __init__(self) -> None:
        self.fail = True
        self.calls = 0

    def fetch(self, endpoint: str, params: dict, fields: str | None = None) -> ProviderResponse:
        self.calls += 1
        if self.fail:
            raise GatewayError(GatewayErrorCode.REQUEST_TIMEOUT, "transient SEC timeout")
        return ProviderResponse.from_rows(
            provider=self.provider_name,
            endpoint=endpoint,
            rows=[_sec_row(accession_number="0000320193-26-000099")],
        )

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, ok=not self.fail)


class FakeStocktwitsProvider(ExternalDataProvider):
    provider_name = "stocktwits"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def fetch(self, endpoint: str, params: dict, fields: str | None = None) -> ProviderResponse:
        self.calls.append((endpoint, dict(params)))
        assert endpoint == "social_heat"
        return ProviderResponse.from_rows(
            provider=self.provider_name,
            endpoint=endpoint,
            rows=[
                {
                    "message_id": "123",
                    "symbol": params.get("symbol", "AAPL"),
                    "body": "AAPL momentum is strong",
                    "created_at": "2026-06-01T09:00:00Z",
                    "source_url": "https://stocktwits.com/symbol/AAPL",
                    "rate_limit_remaining": "199",
                }
            ],
        )

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, ok=True)


class FakeOfficialFeedProvider(ExternalDataProvider):
    provider_name = "official_feed"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def fetch(self, endpoint: str, params: dict, fields: str | None = None) -> ProviderResponse:
        self.calls.append((endpoint, dict(params)))
        assert endpoint == "feed_events"
        source = params["source"]
        return ProviderResponse.from_rows(
            provider=self.provider_name,
            endpoint=endpoint,
            rows=[
                {
                    "source_id": source["source_id"],
                    "source_kind": source["source_kind"],
                    "provider": source["provider"],
                    "source_domain": source["domain"],
                    "market": source["market"],
                    "event_type": source["event_type"],
                    "trust_tier": source["trust_tier"],
                    "license_scope": source["license_scope"],
                    "retention_policy": source["retention_policy"],
                    "quality_label": source["quality_label"],
                    "language": source["language"],
                    "parser_version": source["parser_version"],
                    "metadata_only": True,
                    "provider_item_id": f"{source['source_id']}:item-1",
                    "title": f"{source['provider']} official update",
                    "source_url": source["base_url"],
                    "published_at": "2026-06-04T12:00:00Z",
                    "summary": "Official metadata update.",
                    "raw_hash": f"{source['source_id']}-hash",
                    "fetched_at": "2026-06-04T12:01:00Z",
                }
            ],
        )

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, ok=True)


class FakeGdeltProvider(ExternalDataProvider):
    provider_name = "gdelt"

    def __init__(self, *, rows: list[dict] | None = None, error: GatewayError | None = None) -> None:
        self.rows = rows or [
            {
                "title": "Apple expands AI infrastructure",
                "source_url": "https://example.com/apple-ai",
                "source_domain": "example.com",
                "published_at": "20260604120000",
                "fetched_at": "2026-06-04T12:01:00Z",
                "language": "English",
                "source_country": "United States",
                "query": "Apple AI",
                "topic_hints": ["Apple AI"],
                "provider_item_id": "gdelt-item-1",
                "raw_hash": "gdelt-hash",
                "metadata_only": True,
            }
        ]
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    def fetch(self, endpoint: str, params: dict, fields: str | None = None) -> ProviderResponse:
        self.calls.append((endpoint, dict(params)))
        assert endpoint == "doc_articles"
        if self.error is not None:
            raise self.error
        return ProviderResponse.from_rows(provider=self.provider_name, endpoint=endpoint, rows=self.rows)

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, ok=self.error is None)


class FakeCninfoProvider(ExternalDataProvider):
    provider_name = "cninfo"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def fetch(self, endpoint: str, params: dict, fields: str | None = None) -> ProviderResponse:
        self.calls.append((endpoint, dict(params)))
        assert endpoint == "official_disclosures"
        return ProviderResponse.from_rows(
            provider=self.provider_name,
            endpoint=endpoint,
            rows=[
                {
                    "symbol": params.get("symbol", "000001"),
                    "name": "平安银行",
                    "ann_date": "2026-05-22",
                    "title": "2025年度报告",
                    "event_type": "performance_report_forecast",
                    "event_label_zh": "业绩预告/报告",
                    "sentiment": "mixed",
                    "category": "年度报告",
                    "source": "cninfo",
                    "provider": "cninfo",
                    "source_url": "https://static.cninfo.com.cn/finalpage/2026-05-22/123456.PDF",
                    "provider_item_id": "000001:2026-05-22:2025年度报告",
                    "metadata_only": True,
                }
            ],
        )

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, ok=True)


class FakeAkshareProvider(ExternalDataProvider):
    provider_name = "akshare"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def fetch(self, endpoint: str, params: dict, fields: str | None = None) -> ProviderResponse:
        self.calls.append((endpoint, dict(params)))
        if endpoint == "sector_concepts":
            rows = [
                {"sector_name": "白酒", "sector_code": "BK0896", "pct_change": 2.3, "source": "akshare"},
                {"sector_name": "机器人", "sector_code": "BK1090", "pct_change": 1.7, "source": "akshare"},
            ]
        elif endpoint == "etf_spot":
            rows = [{"symbol": "510300", "name": "沪深300ETF", "pct_change": 1.4, "source": "akshare"}]
        elif endpoint == "limit_up_down":
            rows = [{"trade_date": "2026-05-22", "limit_up_count": 60, "limit_down_count": 4, "source": "akshare"}]
        elif endpoint == "etf_flow":
            rows = [
                {
                    "trade_date": "2026-05-22",
                    "symbol": "510300",
                    "name": "沪深300ETF",
                    "net_inflow": 88.0,
                    "pct_change": 1.4,
                    "amount": 1200.0,
                    "source": "akshare",
                    "provider": "akshare",
                }
            ]
        elif endpoint == "sector_constituents":
            if params.get("sector_name") == "白酒":
                rows = [
                    {
                        "sector_name": "白酒",
                        "sector_code": "BK0896",
                        "trade_date": "2026-05-22",
                        "symbol": "600519",
                        "name": "贵州茅台",
                        "pct_change": 1.2,
                        "source": "akshare",
                        "provider": "akshare",
                    }
                ]
            elif params.get("sector_name") == "机器人":
                rows = [
                    {
                        "sector_name": "机器人",
                        "sector_code": "BK1090",
                        "trade_date": "2026-05-22",
                        "symbol": "300024",
                        "name": "机器人",
                        "pct_change": 2.1,
                        "source": "akshare",
                        "provider": "akshare",
                    }
                ]
            else:
                rows = []
        elif endpoint == "etf_basic":
            rows = [
                {
                    "symbol": "510300",
                    "name": "沪深300ETF",
                    "category": "ETF",
                    "fund_type": "股票型",
                    "source": "akshare",
                    "provider": "akshare",
                }
            ]
        elif endpoint == "index_constituents":
            rows = [
                {
                    "index_symbol": "000300.SH",
                    "trade_date": "2026-05-22",
                    "symbol": "000001",
                    "name": "平安银行",
                    "weight": 0.425,
                    "source": "akshare",
                    "provider": "akshare",
                }
            ]
        elif endpoint == "margin_summary":
            rows = [
                {
                    "trade_date": "2026-05-22",
                    "financing_balance": 1200.0,
                    "securities_lending_balance": 30.0,
                    "financing_buy_amount": 80.0,
                    "source": "akshare",
                    "provider": "akshare",
                }
            ]
        elif endpoint == "margin_detail":
            rows = [
                {
                    "trade_date": "2026-05-22",
                    "symbol": "000001",
                    "name": "平安银行",
                    "financing_balance": 500.0,
                    "financing_buy_amount": 50.0,
                    "securities_lending_balance": 5.0,
                    "source": "akshare",
                    "provider": "akshare",
                }
            ]
        elif endpoint == "earnings_calendar":
            rows = [
                {
                    "symbol": "000001",
                    "name": "平安银行",
                    "ann_date": "2026-05-22",
                    "event_type": "年度报告",
                    "source": "akshare",
                    "provider": "akshare",
                }
            ]
        else:
            rows = [
                {
                    "trade_date": "2026-05-22",
                    "symbol": "600519",
                    "name": "贵州茅台",
                    "reason": "日涨幅偏离值达7%",
                    "buy_amount": 100.0,
                    "sell_amount": 60.0,
                    "net_buy_amount": 40.0,
                    "source": "akshare",
                    "provider": "akshare",
                }
            ]
        return ProviderResponse.from_rows(provider=self.provider_name, endpoint=endpoint, rows=rows)

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, ok=True)


class FailingProvider(ExternalDataProvider):
    provider_name = "failing"

    def fetch(self, endpoint: str, params: dict, fields: str | None = None) -> ProviderResponse:
        raise GatewayError(GatewayErrorCode.PROVIDER_UNAVAILABLE, "planned outage")

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, ok=False)


class EmptySectorConstituentsAkshareProvider(FakeAkshareProvider):
    def fetch(self, endpoint: str, params: dict, fields: str | None = None) -> ProviderResponse:
        self.calls.append((endpoint, dict(params)))
        if endpoint == "sector_concepts":
            raise AssertionError("sector_concepts should not be called when sector_universe_limit=0")
        if endpoint == "sector_constituents":
            return ProviderResponse.from_rows(provider=self.provider_name, endpoint=endpoint, rows=[])
        return super().fetch(endpoint, params, fields)


class SlowSectorConstituentsAkshareProvider(FakeAkshareProvider):
    def fetch(self, endpoint: str, params: dict, fields: str | None = None) -> ProviderResponse:
        self.calls.append((endpoint, dict(params)))
        if endpoint == "sector_concepts":
            raise AssertionError("sector_concepts should not be called when sector_universe_limit=0")
        if endpoint == "sector_constituents":
            time.sleep(0.25)
            return ProviderResponse.from_rows(provider=self.provider_name, endpoint=endpoint, rows=[])
        return super().fetch(endpoint, params, fields)


class SlowFundProvider(ExternalDataProvider):
    provider_name = "slow"

    def fetch(self, endpoint: str, params: dict, fields: str | None = None) -> ProviderResponse:
        time.sleep(0.25)
        return ProviderResponse.from_rows(provider=self.provider_name, endpoint=endpoint, rows=[])

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, ok=True)


class PermissionDeniedTushareProvider(ExternalDataProvider):
    provider_name = "tushare"

    def fetch(self, endpoint: str, params: dict, fields: str | None = None) -> ProviderResponse:
        raise GatewayError(GatewayErrorCode.NO_PERMISSION, "Tushare news permission is unavailable.")

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, ok=True)


class SecretRuntimeFailureProvider(ExternalDataProvider):
    provider_name = "secret_failure"

    def __init__(self, message: str) -> None:
        self.message = message

    def fetch(self, endpoint: str, params: dict, fields: str | None = None) -> ProviderResponse:
        raise RuntimeError(self.message)

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, ok=False)


class InvalidSymbolCninfoProvider(ExternalDataProvider):
    provider_name = "cninfo"

    def fetch(self, endpoint: str, params: dict, fields: str | None = None) -> ProviderResponse:
        raise GatewayError(GatewayErrorCode.INVALID_SYMBOL, "CNINFO rejected the stock code.")

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, ok=True)


class MissingNewsFieldsTushareProvider(ExternalDataProvider):
    provider_name = "tushare"

    def fetch(self, endpoint: str, params: dict, fields: str | None = None) -> ProviderResponse:
        assert endpoint == "news"
        return ProviderResponse.from_rows(
            provider=self.provider_name,
            endpoint=endpoint,
            rows=[{"datetime": "2026-05-22 09:00:00"}],
        )

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, ok=True)


class RateLimitedStocktwitsProvider(ExternalDataProvider):
    provider_name = "stocktwits"

    def fetch(self, endpoint: str, params: dict, fields: str | None = None) -> ProviderResponse:
        raise GatewayError(GatewayErrorCode.RATE_LIMITED, "Stocktwits rate limit exceeded.")

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, ok=True)


class EmptyStocktwitsProvider(ExternalDataProvider):
    provider_name = "stocktwits"

    def fetch(self, endpoint: str, params: dict, fields: str | None = None) -> ProviderResponse:
        return ProviderResponse.from_rows(provider=self.provider_name, endpoint=endpoint, rows=[])

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, ok=True)


class NewsWithoutTitleTushareProvider(ExternalDataProvider):
    provider_name = "tushare"

    def fetch(self, endpoint: str, params: dict, fields: str | None = None) -> ProviderResponse:
        assert endpoint == "news"
        return ProviderResponse.from_rows(
            provider=self.provider_name,
            endpoint=endpoint,
            rows=[
                {
                    "datetime": params["start_date"],
                    "title": None,
                    "content": "【市场快讯】结构化新闻正文",
                    "channels": "财经",
                }
            ],
        )

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, ok=True)


def _sec_row(
    *,
    accession_number: str = "0000320193-26-000001",
    title: str = "10-Q quarterly report",
    source_url: str = "https://www.sec.gov/Archives/edgar/data/320193/shared-url.htm",
    raw_hash: str | None = None,
) -> dict:
    return {
        "cik": "0000320193",
        "entity_name": "Apple Inc.",
        "market": "US",
        "form": "10-Q",
        "filing_date": "2026-05-29",
        "accession_number": accession_number,
        "primary_document": "aapl-20260529.htm",
        "title": title,
        "source_url": source_url,
        "raw_hash": raw_hash or f"hash-{accession_number}",
        "blob_uri": "bronze/sec_edgar/submissions/CIK0000320193.json",
        "metadata_only": True,
    }


def make_client(tmp_path, provider_overrides: dict[str, ExternalDataProvider] | None = None) -> TestClient:
    policies = PolicyRegistry()
    register_tushare_policies(policies, provider_name="tushare")
    store = SQLiteCacheStore(tmp_path / "market_data.sqlite3")
    store.initialize()
    providers: dict[str, ExternalDataProvider] = {
        "tushare": FakeProvider(),
        "eastmoney": FakeEastmoneyProvider(),
        "akshare": FakeAkshareProvider(),
        "cninfo": FakeCninfoProvider(),
    }
    providers.update(provider_overrides or {})
    gateway = ReadThroughQueryService(
        providers,
        policies,
        store,
    )
    return TestClient(create_app(gateway))


def test_normalized_tushare_daily_returns_fni_contract_shape(tmp_path) -> None:
    client = make_client(tmp_path)

    response = client.post(
        "/api/v1/market-data/tushare/daily",
        json={
            "symbols": ["000001.SZ"],
            "start_date": "2024-01-02",
            "end_date": "2024-01-02",
            "include_turnover": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["rows"] == [
        {
            "symbol": "000001.SZ",
            "trade_date": "2024-01-02",
            "open": 10.0,
            "high": 11.0,
            "low": 9.5,
            "close": 10.5,
            "pre_close": 10.0,
            "volume": 1000.0,
            "amount": 10500.0,
            "turnover_rate": 1.2,
            "source": "tushare",
        }
    ]
    assert body["meta"]["provider"] == "tushare"
    assert body["meta"]["endpoint"] == "daily"
    assert body["meta"]["cache"]["mode"] == "upstream"
    assert body["meta"]["cache"]["hit"] is False


def test_normalized_index_and_fund_routes_return_bars(tmp_path) -> None:
    client = make_client(tmp_path)

    index_response = client.post(
        "/api/v1/market-data/tushare/index-daily",
        json={"symbols": ["000001.SH"], "start_date": "2024-01-02", "end_date": "2024-01-02"},
    )
    fund_response = client.post(
        "/api/v1/market-data/tushare/fund-daily",
        json={"symbols": ["510300.SH"], "start_date": "2024-01-02", "end_date": "2024-01-02"},
    )

    assert index_response.status_code == 200
    assert index_response.json()["data"]["rows"][0]["symbol"] == "000001.SH"
    assert fund_response.status_code == 200
    assert fund_response.json()["data"]["rows"][0]["symbol"] == "510300.SH"


def test_normalized_stock_basic_and_trade_cal_routes(tmp_path) -> None:
    client = make_client(tmp_path)

    stock_basic = client.post("/api/v1/market-data/tushare/stock-basic", json={"list_status": "L", "limit": 1})
    trade_cal = client.post(
        "/api/v1/market-data/tushare/trade-cal",
        json={"exchange": "SSE", "start_date": "2024-01-02", "end_date": "2024-01-02"},
    )

    assert stock_basic.status_code == 200
    assert stock_basic.json()["data"]["rows"][0]["ts_code"] == "000001.SZ"
    assert trade_cal.status_code == 200
    assert trade_cal.json()["data"]["rows"][0] == {
        "exchange": "SSE",
        "cal_date": "2024-01-02",
        "is_open": True,
        "pretrade_date": "2023-12-29",
        "source": "tushare",
    }


def test_normalized_provider_routes_return_rows(tmp_path) -> None:
    client = make_client(tmp_path)

    quote = client.get("/api/v1/market-data/eastmoney/market-quotes?stock_codes=600519")
    sector = client.get("/api/v1/market-data/akshare/sector-concepts?limit=1")
    limit_stats = client.get("/api/v1/market-data/akshare/limit-up-down?trade_date=2026-05-22")

    assert quote.status_code == 200
    assert quote.json()["data"]["rows"][0]["stock_code"] == "600519"
    assert sector.status_code == 200
    assert sector.json()["data"]["rows"][0]["sector_name"] == "白酒"
    assert limit_stats.status_code == 200
    assert limit_stats.json()["data"]["rows"][0]["limit_up_count"] == 60


def test_provider_neutral_can_do_routes_return_contract_rows(tmp_path) -> None:
    client = make_client(tmp_path)

    sectors = client.get("/api/v1/market-data/sectors/concepts?trade_date=2026-05-22&limit=1")
    etfs = client.get("/api/v1/market-data/etf/spot?limit=1")
    limit_stats = client.get("/api/v1/market-data/market/limit-up-down?trade_date=2026-05-22")

    assert sectors.status_code == 200
    assert sectors.json()["data"]["rows"][0] == {
        "sector_name": "白酒",
        "sector_code": "BK0896",
        "pct_change": 2.3,
        "source": "akshare",
    }
    assert sectors.json()["meta"]["provider"] == "local_gateway"
    assert sectors.json()["meta"]["endpoint"] == "sectors_concepts"

    assert etfs.status_code == 200
    assert etfs.json()["data"]["rows"][0] == {
        "symbol": "510300",
        "name": "沪深300ETF",
        "pct_change": 1.4,
        "source": "akshare",
    }
    assert etfs.json()["meta"]["provider"] == "local_gateway"
    assert etfs.json()["meta"]["endpoint"] == "etf_spot"

    assert limit_stats.status_code == 200
    assert limit_stats.json()["data"]["rows"][0] == {
        "trade_date": "2026-05-22",
        "limit_up_count": 60,
        "limit_down_count": 4,
        "source": "akshare",
    }
    assert limit_stats.json()["meta"]["provider"] == "local_gateway"
    assert limit_stats.json()["meta"]["endpoint"] == "market_limit_up_down"


def test_provider_neutral_flow_and_event_routes_return_contract_rows(tmp_path) -> None:
    client = make_client(tmp_path)

    northbound = client.get("/api/v1/market-data/capital/northbound?trade_date=2026-05-22")
    main_flow = client.get("/api/v1/market-data/capital/main-flow?trade_date=2026-05-22&limit=1")
    etf_flow = client.get("/api/v1/market-data/etf/flow?trade_date=2026-05-22&limit=1")
    dragon_tiger = client.get("/api/v1/market-data/market/dragon-tiger?trade_date=2026-05-22&limit=1")

    assert northbound.status_code == 200
    assert northbound.json()["data"]["rows"][0] == {
        "trade_date": "2026-05-22",
        "net_buy_amount": 12.5,
        "buy_amount": 100.0,
        "sell_amount": 87.5,
        "source": "eastmoney",
        "provider": "eastmoney",
    }
    assert northbound.json()["meta"]["provider"] == "local_gateway"
    assert northbound.json()["meta"]["endpoint"] == "capital_northbound"

    assert main_flow.status_code == 200
    assert main_flow.json()["data"]["rows"][0] == {
        "trade_date": "2026-05-22",
        "symbol": "600519",
        "name": "贵州茅台",
        "main_net_inflow": 123.0,
        "pct_change": 1.2,
        "amount": 999.0,
        "source": "eastmoney",
        "provider": "eastmoney",
    }
    assert main_flow.json()["meta"]["provider"] == "local_gateway"
    assert main_flow.json()["meta"]["endpoint"] == "capital_main_flow"

    assert etf_flow.status_code == 200
    assert etf_flow.json()["data"]["rows"][0] == {
        "trade_date": "2026-05-22",
        "symbol": "510300",
        "name": "沪深300ETF",
        "net_inflow": 88.0,
        "pct_change": 1.4,
        "amount": 1200.0,
        "source": "akshare",
        "provider": "akshare",
    }
    assert etf_flow.json()["meta"]["provider"] == "local_gateway"
    assert etf_flow.json()["meta"]["endpoint"] == "etf_flow"

    assert dragon_tiger.status_code == 200
    assert dragon_tiger.json()["data"]["rows"][0] == {
        "trade_date": "2026-05-22",
        "symbol": "600519",
        "name": "贵州茅台",
        "reason": "日涨幅偏离值达7%",
        "buy_amount": 100.0,
        "sell_amount": 60.0,
        "net_buy_amount": 40.0,
        "source": "akshare",
        "provider": "akshare",
    }
    assert dragon_tiger.json()["meta"]["provider"] == "local_gateway"
    assert dragon_tiger.json()["meta"]["endpoint"] == "market_dragon_tiger"


def test_provider_neutral_structure_mapping_routes_return_contract_rows(tmp_path) -> None:
    client = make_client(tmp_path)

    sector = client.get(
        "/api/v1/market-data/sectors/constituents?sector_name=机器人&trade_date=2026-05-22&limit=1"
    )
    etf_basic = client.get("/api/v1/market-data/etf/basic?market=cn&limit=1")
    index = client.get(
        "/api/v1/market-data/index/constituents?index_symbol=000300.SH&trade_date=2026-05-22&limit=1"
    )
    margin_summary = client.get("/api/v1/market-data/margin/summary?trade_date=2026-05-22")
    margin_detail = client.get("/api/v1/market-data/margin/detail?trade_date=2026-05-22&limit=1")
    earnings = client.get(
        "/api/v1/market-data/fundamentals/earnings-calendar?start_date=2026-05-22&end_date=2026-06-05&limit=1"
    )

    assert sector.status_code == 200
    assert sector.json()["data"]["rows"][0] == {
        "sector_name": "机器人",
        "sector_code": "BK1090",
        "trade_date": "2026-05-22",
        "symbol": "300024",
        "name": "机器人",
        "pct_change": 2.1,
        "source": "akshare",
        "provider": "akshare",
    }
    assert sector.json()["meta"]["provider"] == "local_gateway"
    assert sector.json()["meta"]["endpoint"] == "sectors_constituents"

    assert etf_basic.status_code == 200
    assert etf_basic.json()["data"]["rows"][0] == {
        "symbol": "510300",
        "name": "沪深300ETF",
        "category": "ETF",
        "fund_type": "股票型",
        "source": "akshare",
        "provider": "akshare",
    }
    assert etf_basic.json()["meta"]["endpoint"] == "etf_basic"

    assert index.status_code == 200
    assert index.json()["data"]["rows"][0] == {
        "index_symbol": "000300.SH",
        "trade_date": "2026-05-22",
        "symbol": "000001",
        "name": "平安银行",
        "weight": 0.425,
        "source": "akshare",
        "provider": "akshare",
    }
    assert index.json()["meta"]["endpoint"] == "index_constituents"

    assert margin_summary.status_code == 200
    assert margin_summary.json()["data"]["rows"][0] == {
        "trade_date": "2026-05-22",
        "financing_balance": 1200.0,
        "securities_lending_balance": 30.0,
        "financing_buy_amount": 80.0,
        "source": "akshare",
        "provider": "akshare",
    }
    assert margin_summary.json()["meta"]["endpoint"] == "margin_summary"

    assert margin_detail.status_code == 200
    assert margin_detail.json()["data"]["rows"][0] == {
        "trade_date": "2026-05-22",
        "symbol": "000001",
        "name": "平安银行",
        "financing_balance": 500.0,
        "financing_buy_amount": 50.0,
        "securities_lending_balance": 5.0,
        "source": "akshare",
        "provider": "akshare",
    }
    assert margin_detail.json()["meta"]["endpoint"] == "margin_detail"

    assert earnings.status_code == 200
    assert earnings.json()["data"]["rows"][0] == {
        "symbol": "000001",
        "name": "平安银行",
        "ann_date": "2026-05-22",
        "event_type": "年度报告",
        "source": "akshare",
        "provider": "akshare",
    }
    assert earnings.json()["meta"]["endpoint"] == "earnings_calendar"


def test_stock_sector_memberships_return_reverse_index_rows_and_coverage(tmp_path) -> None:
    client = make_client(tmp_path)

    response = client.post(
        "/api/v1/market-data/stocks/sector-memberships",
        json={
            "symbols": ["600519.SH", "300024.SZ", "000000.SZ"],
            "trade_date": "2026-05-22",
            "sector_types": ["concept"],
            "limit_per_symbol": 20,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["rows"] == [
        {
            "symbol": "600519.SH",
            "name": "贵州茅台",
            "sector_name": "白酒",
            "sector_type": "concept",
            "source": "akshare",
            "sector_code": "BK0896",
            "trade_date": "2026-05-22",
            "pct_change": 1.2,
            "provider": "akshare",
            "membership_source": "reverse_index",
        },
        {
            "symbol": "300024.SZ",
            "name": "机器人",
            "sector_name": "机器人",
            "sector_type": "concept",
            "source": "akshare",
            "sector_code": "BK1090",
            "trade_date": "2026-05-22",
            "pct_change": 2.1,
            "provider": "akshare",
            "membership_source": "reverse_index",
        },
    ]
    assert body["meta"]["provider"] == "local_gateway"
    assert body["meta"]["endpoint"] == "stock_sector_memberships"
    assert body["meta"]["cache"] == {"hit": False, "mode": "upstream"}
    assert body["meta"]["coverage"]["covered_symbols"] == ["600519.SH", "300024.SZ"]
    assert body["meta"]["coverage"]["missing_symbols"] == [
        {"symbol": "000000.SZ", "reason": "symbol_uncovered"}
    ]


def test_stock_sector_memberships_prefer_materialized_index(tmp_path) -> None:
    akshare = FakeAkshareProvider()
    client = make_client(tmp_path, {"akshare": akshare})
    payload = {
        "symbols": ["600519.SH", "300024.SZ"],
        "trade_date": "2026-05-22",
        "sector_types": ["concept"],
        "limit_per_symbol": 20,
    }

    first = client.post("/api/v1/market-data/stocks/sector-memberships", json=payload)
    first_constituent_calls = [call for call in akshare.calls if call[0] == "sector_constituents"]
    second = client.post("/api/v1/market-data/stocks/sector-memberships", json=payload)
    second_constituent_calls = [call for call in akshare.calls if call[0] == "sector_constituents"]

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(first_constituent_calls) == 2
    assert len(second_constituent_calls) == 2
    assert second.json()["meta"]["cache"] == {"hit": True, "mode": "cache"}


def test_stock_sector_memberships_zero_universe_limit_only_scans_seed_sectors(tmp_path) -> None:
    akshare = FakeAkshareProvider()
    client = make_client(tmp_path, {"akshare": akshare})

    response = client.post(
        "/api/v1/market-data/stocks/sector-memberships",
        json={
            "symbols": ["300024.SZ"],
            "trade_date": "2026-05-22",
            "sector_types": ["concept"],
            "limit_per_symbol": 20,
            "sector_universe_limit": 0,
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["rows"][0]["symbol"] == "300024.SZ"
    assert [call[0] for call in akshare.calls] == ["sector_constituents"]
    assert akshare.calls[0][1]["sector_name"] == "机器人"


def test_stock_sector_memberships_empty_seed_boards_are_diagnostic(tmp_path) -> None:
    client = make_client(tmp_path, {"akshare": EmptySectorConstituentsAkshareProvider()})

    response = client.post(
        "/api/v1/market-data/stocks/sector-memberships",
        json={
            "symbols": ["600519.SH"],
            "trade_date": "2026-05-22",
            "sector_types": ["concept"],
            "limit_per_symbol": 20,
            "sector_universe_limit": 0,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["rows"] == []
    assert body["meta"]["status"] == "degraded"
    assert body["meta"]["coverage"]["missing_symbols"] == [{"symbol": "600519.SH", "reason": "empty_board"}]
    assert {result["reason"] for result in body["meta"]["coverage"]["board_results"]} == {"empty_board"}


def test_stock_sector_memberships_timeout_returns_degraded_without_waiting_for_socket_timeout(tmp_path) -> None:
    client = make_client(tmp_path, {"akshare": SlowSectorConstituentsAkshareProvider()})

    started_at = time.monotonic()
    response = client.post(
        "/api/v1/market-data/stocks/sector-memberships",
        json={
            "symbols": ["600519.SH"],
            "trade_date": "2026-05-22",
            "sector_types": ["concept"],
            "limit_per_symbol": 20,
            "sector_universe_limit": 0,
            "timeout_seconds": 0.08,
            "upstream_timeout_seconds": 0.02,
        },
    )
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.2
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["rows"] == []
    assert body["meta"]["status"] == "degraded"
    assert body["meta"]["warning"]["code"] == GatewayErrorCode.REQUEST_TIMEOUT.value
    assert body["meta"]["coverage"]["missing_symbols"] == [
        {"symbol": "600519.SH", "reason": "reverse_index_timeout"}
    ]


def test_stock_sector_memberships_reports_reverse_index_failures_without_negative_cache(tmp_path) -> None:
    client = make_client(tmp_path, {"akshare": FailingProvider()})
    payload = {
        "symbols": ["600519.SH"],
        "trade_date": "2026-05-22",
        "sector_types": ["concept"],
        "limit_per_symbol": 20,
    }

    first = client.post("/api/v1/market-data/stocks/sector-memberships", json=payload)
    second = client.post("/api/v1/market-data/stocks/sector-memberships", json=payload)

    for response in (first, second):
        assert response.status_code == 200
        body = response.json()
        assert body["data"]["rows"] == []
        assert body["meta"]["status"] == "degraded"
        assert body["meta"]["cache"] == {"hit": False, "mode": "upstream"}
        assert body["meta"]["coverage"]["missing_symbols"] == [
            {"symbol": "600519.SH", "reason": "upstream_failed"}
        ]


def test_fund_profile_returns_tushare_identity_with_coverage(tmp_path) -> None:
    client = make_client(tmp_path)

    response = client.get("/api/v1/market-data/funds/profile?fund_code=161725")

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["rows"][0]["fund_code"] == "161725"
    assert body["data"]["rows"][0]["fund_name"] == "招商中证白酒指数A"
    assert body["data"]["rows"][0]["fund_type"] == "股票型"
    assert body["data"]["rows"][0]["currency"] == "CNY"
    assert body["data"]["rows"][0]["source"] == "tushare"
    assert body["data"]["rows"][0]["provider"] == "tushare"
    assert body["meta"]["provider"] == "local_gateway"
    assert body["meta"]["endpoint"] == "fund_profile"
    assert body["meta"]["cache"] == {"hit": False, "mode": "upstream"}
    assert body["meta"]["coverage"]["requested_fund_code"] == "161725"
    assert body["meta"]["coverage"]["provider_attempts"][-1]["provider"] == "tushare"


def test_fund_holdings_returns_latest_tushare_holdings_with_coverage(tmp_path) -> None:
    client = make_client(tmp_path)

    response = client.get("/api/v1/market-data/funds/holdings?fund_code=161725&limit=1")

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["rows"] == [
        {
            "fund_code": "161725",
            "as_of_date": "2026-05-22",
            "stock_code": "600519",
            "stock_name": "贵州茅台",
            "weight": 0.098,
            "source": "tushare",
            "rank": 1,
            "holding_change": 0.0,
            "provider": "tushare",
            "source_url": "tushare://fund_portfolio",
        }
    ]
    assert body["meta"]["provider"] == "local_gateway"
    assert body["meta"]["endpoint"] == "fund_holdings"
    assert body["meta"]["coverage"]["requested_fund_code"] == "161725"
    assert body["meta"]["coverage"]["returned_holding_count"] == 1
    assert body["meta"]["coverage"]["as_of_date"] == "2026-05-22"
    assert body["meta"]["coverage"]["provider_attempts"][-1]["status"] == "ok"


def test_fund_holdings_prefer_cache_on_repeated_request(tmp_path) -> None:
    tushare = FakeProvider()
    eastmoney = FakeEastmoneyProvider()
    client = make_client(tmp_path, {"tushare": tushare, "eastmoney": eastmoney})

    first = client.get("/api/v1/market-data/funds/holdings?fund_code=161725&limit=10")
    first_fund_calls = [call for call in tushare.calls if call[0] == "fund_portfolio"]
    second = client.get("/api/v1/market-data/funds/holdings?fund_code=161725&limit=10")
    second_fund_calls = [call for call in tushare.calls if call[0] == "fund_portfolio"]

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(first_fund_calls) == 1
    assert len(second_fund_calls) == 1
    assert eastmoney.calls == []
    assert second.json()["meta"]["cache"] == {"hit": True, "mode": "cache"}
    assert second.json()["meta"]["coverage"]["provider_attempts"] == [{"provider": "cache", "status": "hit"}]


def test_fund_holdings_fallbacks_to_eastmoney_when_tushare_fails(tmp_path) -> None:
    client = make_client(tmp_path, {"tushare": FailingProvider(), "eastmoney": FakeEastmoneyProvider()})

    response = client.get("/api/v1/market-data/funds/holdings?fund_code=161725&limit=10")

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["rows"][0]["source"] == "eastmoney"
    assert body["meta"]["status"] == "ok"
    assert body["meta"]["coverage"]["provider_attempts"] == [
        {"provider": "cache", "status": "miss"},
        {"provider": "tushare", "status": "failed", "reason": "PROVIDER_UNAVAILABLE"},
        {"provider": "eastmoney", "status": "ok"},
    ]


def test_fund_holdings_returns_degraded_when_all_upstream_fails_without_negative_cache(tmp_path) -> None:
    client = make_client(tmp_path, {"tushare": FailingProvider(), "eastmoney": FailingProvider()})

    first = client.get("/api/v1/market-data/funds/holdings?fund_code=161725&limit=10")
    second = client.get("/api/v1/market-data/funds/holdings?fund_code=161725&limit=10")

    for response in (first, second):
        assert response.status_code == 200
        body = response.json()
        assert body["data"]["rows"] == []
        assert body["meta"]["status"] == "degraded"
        assert body["meta"]["warning"] == {
            "code": GatewayErrorCode.PROVIDER_UNAVAILABLE.value,
            "message": "Unable to fetch fund holdings.",
        }
        assert body["meta"]["cache"] == {"hit": False, "mode": "upstream"}
        assert body["meta"]["coverage"]["requested_fund_code"] == "161725"
        assert body["meta"]["coverage"]["returned_holding_count"] == 0


def test_fund_holdings_timeout_returns_degraded_without_waiting_for_socket_timeout(tmp_path) -> None:
    client = make_client(tmp_path, {"tushare": SlowFundProvider(), "eastmoney": SlowFundProvider()})

    started_at = time.monotonic()
    response = client.get(
        "/api/v1/market-data/funds/holdings"
        "?fund_code=161725&limit=10&timeout_seconds=0.08&upstream_timeout_seconds=0.02"
    )
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.2
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["rows"] == []
    assert body["meta"]["status"] == "degraded"
    assert body["meta"]["warning"]["code"] == GatewayErrorCode.REQUEST_TIMEOUT.value
    assert body["meta"]["coverage"]["provider_attempts"] == [
        {"provider": "cache", "status": "miss"},
        {"provider": "tushare", "status": "failed", "reason": GatewayErrorCode.REQUEST_TIMEOUT.value},
        {"provider": "eastmoney", "status": "failed", "reason": GatewayErrorCode.REQUEST_TIMEOUT.value},
    ]


def test_source_events_official_filings_return_trusted_fact_rows_and_cache(tmp_path) -> None:
    sec = FakeSecEdgarProvider()
    client = make_client(tmp_path, {"sec_edgar": sec})

    first = client.get("/api/v1/market-data/source-events/official-filings?cik=0000320193&limit=1")
    second = client.get("/api/v1/market-data/source-events/official-filings?cik=0000320193&limit=1")

    assert first.status_code == 200
    body = first.json()
    row = body["data"]["rows"][0]
    assert row["source_event_id"] == "sec_edgar:0000320193:0000320193-26-000001"
    assert row["source_id"] == "sec_edgar"
    assert row["source_type"] == "official_filing"
    assert row["trust_tier"] == "trusted_fact"
    assert row["entity_id"] == "0000320193"
    assert row["event_type"] == "10-Q"
    assert row["metadata_only"] is True
    assert body["meta"]["provider"] == "local_gateway"
    assert body["meta"]["endpoint"] == "official_filings"
    assert body["meta"]["trust_tier"] == "trusted_fact"
    assert body["meta"]["source_quality"]["metadata_only"] is True
    assert body["meta"]["provider_attempts"] == [
        {"provider": "cache", "status": "miss"},
        {"provider": "sec_edgar", "status": "ok"},
    ]
    assert len(sec.calls) == 1
    assert second.status_code == 200
    assert second.json()["meta"]["cache"] == {"hit": True, "mode": "cache"}
    assert second.json()["meta"]["provider_attempts"] == [{"provider": "cache", "status": "hit"}]
    assert len(sec.calls) == 1

    connection = sqlite3.connect(tmp_path / "market_data.sqlite3")
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'source_%'"
            )
        }
        assert {
            "source_registry",
            "source_fetch_runs",
            "source_documents",
            "source_events",
            "source_quality_snapshots",
            "source_blob_manifests",
        }.issubset(tables)
        assert connection.execute("SELECT COUNT(*) FROM source_events").fetchone()[0] == 1
    finally:
        connection.close()


def test_source_events_emit_governance_metadata_and_registry_fields(tmp_path) -> None:
    client = make_client(tmp_path, {"sec_edgar": FakeSecEdgarProvider()})

    response = client.get("/api/v1/market-data/source-events/official-filings?cik=0000320193&limit=1")

    assert response.status_code == 200
    body = response.json()
    row = body["data"]["rows"][0]
    assert row["governance"]["owner_service"] == "stock-data-gateway"
    assert row["governance"]["permission_status"] == "public_allowed"
    assert row["governance"]["robots_tos_status"] == "allowed"
    assert row["governance"]["request_policy"]["timeout_seconds"] == 12.0
    assert body["meta"]["governance"]["owner_service"] == "stock-data-gateway"

    connection = sqlite3.connect(tmp_path / "market_data.sqlite3")
    try:
        registry_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(source_registry)").fetchall()
        }
        assert {
            "permission_status",
            "robots_tos_status",
            "redistribution_policy",
            "anti_bot_risk",
            "owner_service",
            "parser_version",
            "request_policy_json",
        } <= registry_columns
        registry = connection.execute(
            """
            SELECT owner_service, parser_version, permission_status, request_policy_json
            FROM source_registry
            WHERE source_id = 'sec_edgar'
            """
        ).fetchone()
    finally:
        connection.close()
    assert registry[0] == "stock-data-gateway"
    assert registry[1] == "source-events.v1"
    assert registry[2] == "public_allowed"
    assert json.loads(registry[3])["cache_ttl_seconds"] == 86400


def test_source_event_dedupe_reuses_existing_event_for_duplicate_url(tmp_path) -> None:
    shared_url = "https://www.sec.gov/Archives/edgar/data/320193/shared-duplicate.htm"
    provider = SequencedSecEdgarProvider(
        [
            _sec_row(accession_number="0000320193-26-000010", source_url=shared_url, raw_hash="same-content"),
            _sec_row(accession_number="0000320193-26-000011", source_url=shared_url, raw_hash="same-content"),
        ]
    )
    client = make_client(tmp_path, {"sec_edgar": provider})

    first = client.get(
        "/api/v1/market-data/source-events/official-filings?cik=0000320193&limit=1&force_refresh=true"
    )
    second = client.get(
        "/api/v1/market-data/source-events/official-filings?cik=0000320193&limit=1&force_refresh=true"
    )

    first_row = first.json()["data"]["rows"][0]
    second_row = second.json()["data"]["rows"][0]
    assert first_row["freshness"]["state"] == "new"
    assert second_row["source_event_id"] == first_row["source_event_id"]
    assert second_row["freshness"]["state"] == "unchanged"
    assert second_row["freshness"]["duplicate_of"] == first_row["source_event_id"]
    assert second_row["freshness"]["first_seen_at"] == first_row["freshness"]["first_seen_at"]
    assert second_row["freshness"]["last_seen_at"] >= first_row["freshness"]["last_seen_at"]

    connection = sqlite3.connect(tmp_path / "market_data.sqlite3")
    try:
        count = connection.execute("SELECT COUNT(*) FROM source_events").fetchone()[0]
    finally:
        connection.close()
    assert count == 1


def test_source_event_dedupe_keeps_same_title_with_different_url(tmp_path) -> None:
    provider = SequencedSecEdgarProvider(
        [
            _sec_row(
                accession_number="0000320193-26-000020",
                title="Shared title",
                source_url="https://www.sec.gov/one.htm",
            ),
            _sec_row(
                accession_number="0000320193-26-000021",
                title="Shared title",
                source_url="https://www.sec.gov/two.htm",
            ),
        ]
    )
    client = make_client(tmp_path, {"sec_edgar": provider})

    client.get("/api/v1/market-data/source-events/official-filings?cik=0000320193&limit=1&force_refresh=true")
    client.get("/api/v1/market-data/source-events/official-filings?cik=0000320193&limit=1&force_refresh=true")

    connection = sqlite3.connect(tmp_path / "market_data.sqlite3")
    try:
        count = connection.execute("SELECT COUNT(*) FROM source_events").fetchone()[0]
    finally:
        connection.close()
    assert count == 2


def test_source_event_freshness_detects_parser_version_change(tmp_path, monkeypatch) -> None:
    import stock_data_gateway.source_events as source_events_module

    provider = SequencedSecEdgarProvider(
        [
            _sec_row(accession_number="0000320193-26-000030"),
            _sec_row(accession_number="0000320193-26-000030"),
        ]
    )
    client = make_client(tmp_path, {"sec_edgar": provider})

    first = client.get(
        "/api/v1/market-data/source-events/official-filings?cik=0000320193&limit=1&force_refresh=true"
    )
    monkeypatch.setattr(source_events_module, "_PARSER_VERSION", "source-events.v2")
    second = client.get(
        "/api/v1/market-data/source-events/official-filings?cik=0000320193&limit=1&force_refresh=true"
    )

    assert first.json()["data"]["rows"][0]["freshness"]["parser_version"] == "source-events.v1"
    freshness = second.json()["data"]["rows"][0]["freshness"]
    assert freshness["state"] == "updated"
    assert freshness["parser_version"] == "source-events.v2"
    assert freshness["previous_parser_version"] == "source-events.v1"
    assert freshness["parser_version_changed"] is True


def test_source_event_cache_read_marks_stale_rows(tmp_path) -> None:
    client = make_client(tmp_path, {"sec_edgar": FakeSecEdgarProvider()})

    client.get("/api/v1/market-data/source-events/official-filings?cik=0000320193&limit=1&force_refresh=true")
    connection = sqlite3.connect(tmp_path / "market_data.sqlite3")
    try:
        connection.execute("UPDATE source_events SET last_seen_at = '2000-01-01T00:00:00+00:00'")
        connection.commit()
    finally:
        connection.close()

    response = client.get("/api/v1/market-data/source-events/official-filings?cik=0000320193&limit=1")

    assert response.status_code == 200
    row = response.json()["data"]["rows"][0]
    assert row["freshness"]["state"] == "stale"
    assert row["freshness"]["last_seen_at"] == "2000-01-01T00:00:00+00:00"


def test_source_event_transient_failure_is_not_negative_cached(tmp_path) -> None:
    provider = FlakySecEdgarProvider()
    client = make_client(tmp_path, {"sec_edgar": provider})

    failed = client.get(
        "/api/v1/market-data/source-events/official-filings?cik=0000320193&limit=1&force_refresh=true"
    )
    provider.fail = False
    recovered = client.get(
        "/api/v1/market-data/source-events/official-filings?cik=0000320193&limit=1&force_refresh=true"
    )

    assert failed.status_code == 200
    assert failed.json()["meta"]["status"] == "degraded"
    assert recovered.status_code == 200
    assert recovered.json()["data"]["rows"][0]["freshness"]["state"] == "new"
    connection = sqlite3.connect(tmp_path / "market_data.sqlite3")
    try:
        count = connection.execute("SELECT COUNT(*) FROM source_events").fetchone()[0]
    finally:
        connection.close()
    assert count == 1


def test_source_events_provider_failure_degrades_without_secret_leakage(tmp_path) -> None:
    secret_message = "token=abc123 cookie=bad Authorization: Bearer hidden /Users/mikezhang/.secret"
    client = make_client(tmp_path, {"sec_edgar": SecretRuntimeFailureProvider(secret_message)})

    response = client.get("/api/v1/market-data/source-events/official-filings?cik=0000320193&limit=1")

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["rows"] == []
    assert body["meta"]["status"] == "degraded"
    assert body["meta"]["warning"]["code"] == GatewayErrorCode.PROVIDER_UNAVAILABLE.value
    assert body["meta"]["provider_attempts"] == [
        {"provider": "cache", "status": "miss"},
        {"provider": "sec_edgar", "status": "failed", "reason": GatewayErrorCode.PROVIDER_UNAVAILABLE.value},
    ]
    assert "abc123" not in response.text
    assert "hidden" not in response.text
    assert ".secret" not in response.text

    connection = sqlite3.connect(tmp_path / "market_data.sqlite3")
    try:
        warning_message = connection.execute("SELECT warning_message FROM source_fetch_runs").fetchone()[0]
    finally:
        connection.close()
    assert "abc123" not in warning_message
    assert "hidden" not in warning_message
    assert ".secret" not in warning_message


def test_source_events_news_context_returns_context_only_tushare_rows(tmp_path) -> None:
    client = make_client(tmp_path)

    response = client.get(
        "/api/v1/market-data/source-events/news-context"
        "?src=sina&start_datetime=2026-05-22%2009:00:00&end_datetime=2026-05-22%2010:00:00&limit=1"
    )

    assert response.status_code == 200
    body = response.json()
    row = body["data"]["rows"][0]
    assert row["source_type"] == "public_news"
    assert row["trust_tier"] == "context_only"
    assert row["provider"] == "tushare"
    assert row["license_scope"] == "context_only"
    assert row["retention_policy"] == "metadata_only"
    assert "结构化新闻正文" in row["summary"]
    assert body["meta"]["source_quality"]["label"] == "public_context"
    assert body["meta"]["source_quality"]["skipped_noise_count"] == 0


def test_source_events_news_context_missing_fields_degrades_with_stable_warning(tmp_path) -> None:
    client = make_client(tmp_path, {"tushare": MissingNewsFieldsTushareProvider()})

    response = client.get(
        "/api/v1/market-data/source-events/news-context"
        "?src=sina&start_datetime=2026-05-22%2009:00:00&end_datetime=2026-05-22%2010:00:00&limit=1"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["rows"] == []
    assert body["meta"]["status"] == "degraded"
    assert body["meta"]["warning"]["code"] == GatewayErrorCode.EMPTY_DATA.value
    assert body["meta"]["provider_attempts"] == [
        {"provider": "cache", "status": "miss"},
        {"provider": "tushare", "status": "failed", "reason": GatewayErrorCode.EMPTY_DATA.value},
    ]


def test_source_events_official_disclosures_return_trusted_metadata_rows(tmp_path) -> None:
    cninfo = FakeCninfoProvider()
    client = make_client(tmp_path, {"cninfo": cninfo})

    response = client.get(
        "/api/v1/market-data/source-events/official-disclosures"
        "?symbol=000001&start_date=2026-05-22&end_date=2026-05-22&limit=1"
    )

    assert response.status_code == 200
    body = response.json()
    row = body["data"]["rows"][0]
    assert row["source_id"] == "cninfo"
    assert row["source_type"] == "official_disclosure"
    assert row["provider"] == "cninfo"
    assert row["trust_tier"] == "trusted_fact"
    assert row["entity_id"] == "000001"
    assert row["event_type"] == "performance_report_forecast"
    assert row["source_url"] == "https://static.cninfo.com.cn/finalpage/2026-05-22/123456.PDF"
    assert row["metadata_only"] is True
    assert body["meta"]["source_quality"]["label"] == "official_metadata"
    assert body["meta"]["provider_attempts"] == [
        {"provider": "cache", "status": "miss"},
        {"provider": "cninfo", "status": "ok"},
    ]
    assert len(cninfo.calls) == 1


def test_source_events_official_disclosures_invalid_symbol_degrades_not_500(tmp_path) -> None:
    client = make_client(tmp_path, {"cninfo": InvalidSymbolCninfoProvider()})

    response = client.get(
        "/api/v1/market-data/source-events/official-disclosures"
        "?symbol=BAD&start_date=2026-05-22&end_date=2026-05-22&limit=1"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["rows"] == []
    assert body["meta"]["status"] == "degraded"
    assert body["meta"]["warning"]["code"] == GatewayErrorCode.INVALID_SYMBOL.value
    assert body["meta"]["provider_attempts"] == [
        {"provider": "cache", "status": "miss"},
        {"provider": "cninfo", "status": "failed", "reason": GatewayErrorCode.INVALID_SYMBOL.value},
    ]


def test_source_events_social_heat_is_disabled_by_default(tmp_path) -> None:
    client = make_client(tmp_path)

    response = client.get("/api/v1/market-data/source-events/social-heat?symbol=AAPL&limit=5")

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["rows"] == []
    assert body["meta"]["status"] == "degraded"
    assert body["meta"]["trust_tier"] == "heat_signal_only"
    assert body["meta"]["warning"]["code"] == "SOCIAL_SOURCE_DISABLED"
    assert body["meta"]["raw_storage_policy"] == "disabled"


def test_source_events_social_heat_enabled_returns_heat_signal_only_rows(tmp_path) -> None:
    stocktwits = FakeStocktwitsProvider()
    client = make_client(tmp_path, {"stocktwits": stocktwits})

    response = client.get("/api/v1/market-data/source-events/social-heat?symbol=AAPL&limit=1&enabled=true")

    assert response.status_code == 200
    body = response.json()
    row = body["data"]["rows"][0]
    assert row["source_type"] == "social_heat"
    assert row["trust_tier"] == "heat_signal_only"
    assert row["entity_id"] == "AAPL"
    assert "user" not in row
    assert body["meta"]["provider_attempts"] == [
        {"provider": "cache", "status": "miss"},
        {"provider": "stocktwits", "status": "ok"},
    ]
    assert len(stocktwits.calls) == 1


def test_source_events_social_heat_rate_limited_degrades_not_500(tmp_path) -> None:
    client = make_client(tmp_path, {"stocktwits": RateLimitedStocktwitsProvider()})

    response = client.get("/api/v1/market-data/source-events/social-heat?symbol=AAPL&limit=1&enabled=true")

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["rows"] == []
    assert body["meta"]["status"] == "degraded"
    assert body["meta"]["warning"]["code"] == GatewayErrorCode.RATE_LIMITED.value
    assert body["meta"]["provider_attempts"] == [
        {"provider": "cache", "status": "miss"},
        {"provider": "stocktwits", "status": "failed", "reason": GatewayErrorCode.RATE_LIMITED.value},
    ]


def test_source_events_social_heat_empty_result_degrades(tmp_path) -> None:
    client = make_client(tmp_path, {"stocktwits": EmptyStocktwitsProvider()})

    response = client.get("/api/v1/market-data/source-events/social-heat?symbol=AAPL&limit=1&enabled=true")

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["rows"] == []
    assert body["meta"]["status"] == "degraded"
    assert body["meta"]["warning"]["code"] == GatewayErrorCode.EMPTY_DATA.value


def test_source_events_news_permission_smoke_reports_tushare_access(tmp_path) -> None:
    client = make_client(tmp_path)

    response = client.post(
        "/api/v1/market-data/source-events/news-permission-smoke",
        json={
            "src_values": ["sina"],
            "start_datetime": "2026-05-22 09:00:00",
            "end_datetime": "2026-05-22 10:00:00",
            "limit_per_src": 1,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["rows"] == [
        {
            "src": "sina",
            "status": "ok",
            "row_count": 1,
            "fields_present": ["channels", "content", "datetime", "title"],
            "latency_ms": body["data"]["rows"][0]["latency_ms"],
            "permission_failure_reason": None,
            "throttled": False,
        }
    ]
    assert body["meta"]["endpoint"] == "news_permission_smoke"
    assert body["meta"]["provider_attempts"] == [{"provider": "tushare", "src": "sina", "status": "ok"}]


def test_source_events_capabilities_route_returns_inventory(tmp_path) -> None:
    client = make_client(tmp_path)

    response = client.get("/api/v1/market-data/source-events/capabilities")

    assert response.status_code == 200
    body = response.json()
    rows = body["data"]["rows"]
    assert body["meta"]["provider"] == "local_gateway"
    assert body["meta"]["endpoint"] == "source_event_capabilities"
    assert {
        "source_id",
        "provider",
        "route",
        "status",
        "enabled_by_default",
        "credential_required",
        "permission_probe_available",
        "trust_tier",
        "license_scope",
        "retention_policy",
        "cache_policy",
        "last_acceptance_status",
        "known_limitations",
    } <= rows[0].keys()
    by_source = {row["source_id"]: row for row in rows}
    assert by_source["official_filings"]["provider"] == "sec_edgar"
    assert by_source["official_filings"]["trust_tier"] == "trusted_fact"
    assert by_source["news_context"]["credential_required"] is True
    assert by_source["news_context"]["permission_probe_available"] is True
    assert by_source["social_heat"]["enabled_by_default"] is False
    assert by_source["social_heat"]["status"] == "disabled_by_default"


def test_official_source_registry_route_returns_seed_pack(tmp_path) -> None:
    client = make_client(tmp_path)

    response = client.get("/api/v1/market-data/source-events/official-source-registry")

    assert response.status_code == 200
    body = response.json()
    rows = body["data"]["rows"]
    assert body["meta"]["endpoint"] == "official_source_registry"
    assert len(rows) >= 5
    by_source = {row["source_id"]: row for row in rows}
    assert by_source["sec_press_releases"]["enabled"] is True
    assert by_source["sec_press_releases"]["parser_strategy"] == "rss"
    assert by_source["csrc_press_releases"]["enabled"] is False
    assert by_source["csrc_press_releases"]["permission_status"] == "unknown"


def test_official_source_events_fetches_enabled_registry_sources(tmp_path) -> None:
    feed = FakeOfficialFeedProvider()
    client = make_client(tmp_path, {"official_feed": feed})

    response = client.get("/api/v1/market-data/source-events/official-sources?limit=5")

    assert response.status_code == 200
    body = response.json()
    rows = body["data"]["rows"]
    assert body["meta"]["endpoint"] == "official_sources"
    assert body["meta"]["status"] == "ok"
    assert {row["source_id"] for row in rows} == {"sec_press_releases", "federal_reserve_press_releases"}
    assert all(row["trust_tier"] == "trusted_fact" for row in rows)
    assert all(row["metadata_only"] is True for row in rows)
    assert {call[1]["source"]["source_id"] for call in feed.calls} == {
        "sec_press_releases",
        "federal_reserve_press_releases",
    }
    with sqlite3.connect(tmp_path / "market_data.sqlite3") as connection:
        registry = connection.execute(
            "SELECT permission_status, robots_tos_status, owner_service FROM source_registry WHERE source_id = ?",
            ("sec_press_releases",),
        ).fetchone()
    assert registry == ("public_allowed", "allowed", "stock-data-gateway")


def test_official_source_events_skip_disabled_sources_by_default(tmp_path) -> None:
    feed = FakeOfficialFeedProvider()
    client = make_client(tmp_path, {"official_feed": feed})

    response = client.get("/api/v1/market-data/source-events/official-sources?source_id=csrc_press_releases")

    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["status"] == "degraded"
    assert body["meta"]["warning"]["code"] == "NO_ENABLED_OFFICIAL_SOURCES"
    assert body["data"]["rows"] == []
    assert feed.calls == []


def test_open_news_index_route_returns_context_only_metadata(tmp_path) -> None:
    gdelt = FakeGdeltProvider()
    client = make_client(tmp_path, {"gdelt": gdelt})

    response = client.get(
        "/api/v1/market-data/source-events/open-news-index"
        "?query=Apple%20AI&start_datetime=2026-06-04T00:00:00Z&limit=1"
    )

    assert response.status_code == 200
    body = response.json()
    row = body["data"]["rows"][0]
    assert body["meta"]["endpoint"] == "open_news_index"
    assert row["source_id"] == "gdelt_doc"
    assert row["source_type"] == "open_news_index"
    assert row["provider"] == "gdelt"
    assert row["trust_tier"] == "context_only"
    assert row["metadata_only"] is True
    assert row["source_domain"] == "example.com"
    assert row["extraction"]["text_available"] == "metadata_only"
    assert row["governance"]["request_policy"]["per_domain_pacing_seconds"] == 5.0
    assert gdelt.calls[0][1]["query"] == "Apple AI"


def test_open_news_index_route_degrades_on_provider_failure(tmp_path) -> None:
    gdelt = FakeGdeltProvider(error=GatewayError(GatewayErrorCode.PROVIDER_UNAVAILABLE, "rate limited token=abc123"))
    client = make_client(tmp_path, {"gdelt": gdelt})

    response = client.get("/api/v1/market-data/source-events/open-news-index?query=Apple&limit=1")

    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["status"] == "degraded"
    assert body["meta"]["warning"]["code"] == GatewayErrorCode.PROVIDER_UNAVAILABLE.value
    assert body["data"]["rows"] == []
    assert "abc123" not in response.text


def test_industry_media_route_fetches_allowlisted_feed_as_research_context(tmp_path) -> None:
    feed = FakeOfficialFeedProvider()
    client = make_client(tmp_path, {"official_feed": feed})

    response = client.get("/api/v1/market-data/source-events/industry-media?limit=1")

    assert response.status_code == 200
    body = response.json()
    row = body["data"]["rows"][0]
    assert body["meta"]["endpoint"] == "industry_media"
    assert row["source_id"] == "pv_tech_news"
    assert row["source_type"] == "public_industry_media"
    assert row["provider"] == "pv_tech"
    assert row["trust_tier"] == "research_context"
    assert row["metadata_only"] is True
    assert row["governance"]["robots_tos_status"] == "allowed"
    assert feed.calls[0][1]["source"]["source_kind"] == "industry_media"


def test_unified_narrative_source_events_query_filters_and_paginates_mixed_sources(tmp_path) -> None:
    client = make_client(
        tmp_path,
        {
            "sec_edgar": FakeSecEdgarProvider(),
            "cninfo": FakeCninfoProvider(),
        },
    )

    first = client.get(
        "/api/v1/market-data/narrative/source-events"
        "?source_kind=official_filings,official_disclosures"
        "&symbol=AAPL&limit=1&trust_tier=trusted_fact"
    )

    assert first.status_code == 200
    first_body = first.json()
    assert first_body["meta"]["endpoint"] == "narrative_source_events"
    assert first_body["meta"]["pagination"]["limit"] == 1
    assert first_body["meta"]["pagination"]["next_cursor"] == "1"
    assert first_body["meta"]["status"] == "ok"
    assert len(first_body["data"]["rows"]) == 1
    assert first_body["data"]["rows"][0]["trust_tier"] == "trusted_fact"
    assert first_body["data"]["rows"][0]["provider_metadata"]["governance"]["owner_service"] == "stock-data-gateway"
    assert first_body["data"]["rows"][0]["provider_metadata"]["freshness"]["state"] == "new"

    second = client.get(
        "/api/v1/market-data/narrative/source-events"
        "?source_kind=official_filings,official_disclosures"
        "&symbol=AAPL&limit=1&cursor=1"
    )
    second_body = second.json()

    assert second.status_code == 200
    assert second_body["meta"]["pagination"]["cursor"] == "1"
    assert second_body["meta"]["pagination"]["next_cursor"] is None
    assert second_body["data"]["rows"][0]["source_provider"] == "cninfo"
    assert {attempt["source_kind"] for attempt in second_body["meta"]["provider_attempts"]} == {
        "official_filings",
        "official_disclosures",
    }

    provider_filtered = client.get(
        "/api/v1/market-data/narrative/source-events"
        "?source_kind=official_filings,official_disclosures"
        "&symbol=AAPL&limit=5&source_provider=sec_edgar"
    )
    provider_filtered_body = provider_filtered.json()
    assert provider_filtered.status_code == 200
    assert {row["source_provider"] for row in provider_filtered_body["data"]["rows"]} == {"sec_edgar"}


def test_unified_narrative_source_events_query_includes_official_sources(tmp_path) -> None:
    client = make_client(tmp_path, {"official_feed": FakeOfficialFeedProvider()})

    response = client.get(
        "/api/v1/market-data/narrative/source-events"
        "?source_kind=official_sources&limit=1&trust_tier=trusted_fact"
    )

    assert response.status_code == 200
    body = response.json()
    row = body["data"]["rows"][0]
    assert row["source_type"] == "official"
    assert row["source_provider"] == "sec"
    assert row["trust_tier"] == "trusted_fact"
    assert row["provider_metadata"]["source_kind"] == "official_sources"
    assert row["provider_metadata"]["governance"]["permission_status"] == "public_allowed"
    assert body["meta"]["pagination"]["next_cursor"] == "1"


def test_unified_narrative_source_events_query_includes_open_news_index(tmp_path) -> None:
    client = make_client(tmp_path, {"gdelt": FakeGdeltProvider()})

    response = client.get(
        "/api/v1/market-data/narrative/source-events"
        "?source_kind=open_news_index&keyword=Apple%20AI&limit=1&trust_tier=context_only"
    )

    assert response.status_code == 200
    body = response.json()
    row = body["data"]["rows"][0]
    assert row["source_type"] == "news"
    assert row["source_provider"] == "gdelt"
    assert row["trust_tier"] == "context_only"
    assert row["provider_metadata"]["source_kind"] == "open_news_index"
    assert row["provider_metadata"]["source_domain"] == "example.com"
    assert row["provider_metadata"]["extraction"]["text_available"] == "metadata_only"


def test_unified_narrative_source_events_query_includes_industry_media(tmp_path) -> None:
    client = make_client(tmp_path, {"official_feed": FakeOfficialFeedProvider()})

    response = client.get(
        "/api/v1/market-data/narrative/source-events"
        "?source_kind=industry_media&limit=1&trust_tier=research_context"
    )

    assert response.status_code == 200
    body = response.json()
    row = body["data"]["rows"][0]
    assert row["source_type"] == "news"
    assert row["source_provider"] == "pv_tech"
    assert row["trust_tier"] == "research_context"
    assert row["provider_metadata"]["source_kind"] == "industry_media"


def test_unified_narrative_source_events_query_empty_filter_is_not_degraded(tmp_path) -> None:
    client = make_client(tmp_path, {"sec_edgar": FakeSecEdgarProvider()})

    response = client.get(
        "/api/v1/market-data/narrative/source-events"
        "?source_kind=official_filings&keyword=definitely-not-present&limit=5"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["rows"] == []
    assert body["meta"]["status"] == "ok"
    assert body["meta"]["pagination"]["total"] == 0
    assert body["meta"]["degradation_events"] == []


def test_unified_narrative_source_events_query_degrades_when_all_sources_fail(tmp_path) -> None:
    client = make_client(tmp_path, {"sec_edgar": SecretRuntimeFailureProvider("network timeout token=abc123")})

    response = client.get("/api/v1/market-data/narrative/source-events?source_kind=official_filings&limit=5")

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["rows"] == []
    assert body["meta"]["status"] == "degraded"
    assert body["meta"]["warning"]["code"] == "ALL_SOURCES_DEGRADED"
    assert body["meta"]["degradation_events"][0]["code"] == GatewayErrorCode.PROVIDER_UNAVAILABLE.value
    assert body["meta"]["provider_attempts"][-1] == {
        "source_kind": "official_filings",
        "provider": "sec_edgar",
        "status": "failed",
        "reason": GatewayErrorCode.PROVIDER_UNAVAILABLE.value,
    }
    assert "abc123" not in response.text


def test_narrative_source_events_official_filings_post_matches_fni_contract(tmp_path) -> None:
    sec = FakeSecEdgarProvider()
    client = make_client(tmp_path, {"sec_edgar": sec})

    response = client.post(
        "/api/v1/market-data/narrative/source-events/official-filings",
        json={"symbols": ["AAPL"], "query": "AI infrastructure", "limit": 1},
    )

    assert response.status_code == 200
    body = response.json()
    row = body["data"]["rows"][0]
    required_fields = {
        "source_event_id",
        "source_type",
        "source_provider",
        "source_url",
        "title",
        "event_time",
        "fetched_at",
        "trust_tier",
        "source_quality",
        "license_scope",
        "retention_policy",
        "metadata_only",
        "degradation_events",
        "summary",
        "stock_codes",
        "narrative_hints",
        "evidence_claims",
        "provider_metadata",
        "source_document_id",
        "source_document_title",
        "source_document_url",
        "excerpt",
    }
    assert required_fields <= row.keys()
    assert row["source_event_id"] == "sec_edgar:0000320193:0000320193-26-000001"
    assert row["source_type"] == "filing"
    assert row["source_provider"] == "sec_edgar"
    assert row["trust_tier"] == "trusted_fact"
    assert row["source_quality"] == "official_metadata"
    assert row["license_scope"] == "public_filing"
    assert row["retention_policy"] == "metadata_and_raw_payload"
    assert row["metadata_only"] is True
    assert row["degradation_events"] == []
    assert row["stock_codes"] == ["AAPL"]
    assert body["meta"]["provider"] == "gateway"
    assert body["meta"]["endpoint"] == "narrative_official_filings"
    assert len(sec.calls) == 1


def test_narrative_source_events_official_disclosures_post_maps_symbol_and_quality(tmp_path) -> None:
    cninfo = FakeCninfoProvider()
    client = make_client(tmp_path, {"cninfo": cninfo})

    response = client.post(
        "/api/v1/market-data/narrative/source-events/official-disclosures",
        json={"symbols": ["000001.SZ"], "query": "股东会", "limit": 1},
    )

    assert response.status_code == 200
    body = response.json()
    row = body["data"]["rows"][0]
    assert row["source_type"] == "announcement"
    assert row["source_provider"] == "cninfo"
    assert row["source_quality"] == "official_metadata"
    assert row["stock_codes"] == ["000001"]
    assert cninfo.calls[0][1]["symbol"] == "000001"


def test_narrative_source_events_news_context_post_returns_context_only_rows(tmp_path) -> None:
    client = make_client(tmp_path)

    response = client.post(
        "/api/v1/market-data/narrative/source-events/news-context",
        json={"query": "半导体 A股", "limit": 1},
    )

    assert response.status_code == 200
    body = response.json()
    row = body["data"]["rows"][0]
    assert row["source_type"] == "news"
    assert row["source_provider"] == "tushare"
    assert row["trust_tier"] == "context_only"
    assert row["source_quality"] == "public_context"
    assert row["retention_policy"] == "metadata_only"


def test_narrative_source_events_social_heat_post_is_not_404_when_disabled(tmp_path) -> None:
    client = make_client(tmp_path)

    response = client.post(
        "/api/v1/market-data/narrative/source-events/social-heat",
        json={"symbols": ["AAPL"], "query": "Apple", "limit": 5},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["rows"] == []
    assert body["meta"]["provider"] == "gateway"
    assert body["meta"]["endpoint"] == "narrative_social_heat"
    assert body["meta"]["status"] == "degraded"
    assert body["meta"]["warning"]["code"] == "SOCIAL_SOURCE_DISABLED"


def test_news_briefs_returns_tushare_rows(tmp_path) -> None:
    client = make_client(tmp_path)

    response = client.post(
        "/api/v1/market-data/news/briefs",
        json={
            "source_provider": "tushare",
            "src": "sina",
            "start_datetime": "2026-05-22 09:00:00",
            "end_datetime": "2026-05-22 15:30:00",
            "limit": 20,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["rows"] == [
        {
            "datetime": "2026-05-22 09:00:00",
            "title": "市场快讯",
            "content": "结构化新闻正文",
            "source": "tushare",
            "channels": "财经",
            "src": "sina",
            "provider": "tushare",
        }
    ]
    assert body["meta"]["provider"] == "tushare"
    assert body["meta"]["endpoint"] == "news"
    assert body["meta"]["cache"]["mode"] == "upstream"


def test_news_briefs_derives_title_from_content_prefix(tmp_path) -> None:
    client = make_client(tmp_path, {"tushare": NewsWithoutTitleTushareProvider()})

    response = client.post(
        "/api/v1/market-data/news/briefs",
        json={
            "source_provider": "tushare",
            "src": "sina",
            "start_datetime": "2026-05-22 09:00:00",
            "end_datetime": "2026-05-22 15:30:00",
            "limit": 20,
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["rows"][0]["title"] == "市场快讯"


def test_news_briefs_reports_permission_required(tmp_path) -> None:
    client = make_client(tmp_path, {"tushare": PermissionDeniedTushareProvider()})

    response = client.post(
        "/api/v1/market-data/news/briefs",
        json={
            "source_provider": "tushare",
            "src": "sina",
            "start_datetime": "2026-05-22 09:00:00",
            "end_datetime": "2026-05-22 15:30:00",
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PROVIDER_PERMISSION_REQUIRED"


def test_normalized_routes_validate_required_inputs(tmp_path) -> None:
    client = make_client(tmp_path)

    missing_symbols = client.post(
        "/api/v1/market-data/tushare/daily",
        json={"start_date": "2024-01-02", "end_date": "2024-01-02"},
    )
    invalid_limit = client.post("/api/v1/market-data/tushare/stock-basic", json={"limit": "bad"})
    missing_stock_codes = client.get("/api/v1/market-data/eastmoney/market-quotes")
    missing_northbound_date = client.get("/api/v1/market-data/capital/northbound")
    missing_etf_flow_date = client.get("/api/v1/market-data/etf/flow")
    missing_sector_name = client.get("/api/v1/market-data/sectors/constituents")
    missing_index_symbol = client.get("/api/v1/market-data/index/constituents?trade_date=2026-05-22")
    missing_margin_date = client.get("/api/v1/market-data/margin/summary")
    missing_earnings_end = client.get("/api/v1/market-data/fundamentals/earnings-calendar?start_date=2026-05-22")
    missing_membership_symbols = client.post(
        "/api/v1/market-data/stocks/sector-memberships",
        json={"trade_date": "2026-05-22"},
    )
    missing_membership_date = client.post(
        "/api/v1/market-data/stocks/sector-memberships",
        json={"symbols": ["600519.SH"]},
    )
    missing_fund_profile_code = client.get("/api/v1/market-data/funds/profile")
    missing_fund_holdings_code = client.get("/api/v1/market-data/funds/holdings")
    invalid_fund_holdings_limit = client.get("/api/v1/market-data/funds/holdings?fund_code=161725&limit=bad")
    unsupported_news_provider = client.post(
        "/api/v1/market-data/news/briefs",
        json={
            "source_provider": "news-site",
            "src": "sina",
            "start_datetime": "2026-05-22 09:00:00",
            "end_datetime": "2026-05-22 15:30:00",
        },
    )
    blank_news_source = client.post(
        "/api/v1/market-data/news/briefs",
        json={
            "source_provider": "tushare",
            "src": " ",
            "start_datetime": "2026-05-22 09:00:00",
            "end_datetime": "2026-05-22 15:30:00",
        },
    )

    assert missing_symbols.status_code == 400
    assert missing_symbols.json()["error"]["code"] == GatewayErrorCode.INVALID_REQUEST.value
    assert invalid_limit.status_code == 400
    assert missing_stock_codes.status_code == 400
    assert missing_northbound_date.status_code == 400
    assert missing_etf_flow_date.status_code == 400
    assert missing_sector_name.status_code == 400
    assert missing_index_symbol.status_code == 400
    assert missing_margin_date.status_code == 400
    assert missing_earnings_end.status_code == 400
    assert missing_membership_symbols.status_code == 400
    assert missing_membership_date.status_code == 400
    assert missing_fund_profile_code.status_code == 400
    assert missing_fund_holdings_code.status_code == 400
    assert invalid_fund_holdings_limit.status_code == 400
    assert unsupported_news_provider.status_code == 400
    assert blank_news_source.status_code == 400


def test_normalized_tushare_route_rejects_too_many_symbols(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_MAX_SYMBOLS_PER_REQUEST", "1")
    client = make_client(tmp_path)

    response = client.post(
        "/api/v1/market-data/tushare/daily",
        json={
            "symbols": ["000001.SZ", "600519.SH"],
            "start_date": "2024-01-02",
            "end_date": "2024-01-02",
        },
    )

    assert response.status_code == 400
    assert "maximum symbols" in response.json()["error"]["message"]


def test_unstable_provider_route_returns_degraded_empty_payload(tmp_path) -> None:
    client = make_client(tmp_path, {"eastmoney": FailingProvider()})

    response = client.get("/api/v1/market-data/eastmoney/market-quotes?stock_codes=600519")

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["rows"] == []
    assert body["meta"]["status"] == "degraded"
    assert body["meta"]["cache"]["mode"] == "stale_cache"


def test_cyq_route_groups_distribution_by_symbol_and_date(tmp_path) -> None:
    client = make_client(tmp_path)

    response = client.post(
        "/api/v1/market-data/chips/cyq",
        json={"symbols": ["000001.SZ"], "trade_date": "2024-01-02"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["rows"] == [
        {
            "symbol": "000001.SZ",
            "trade_date": "2024-01-02",
            "cost_distribution": [{"price": 10.0, "percent": 0.25}],
            "source": "tushare",
        }
    ]
