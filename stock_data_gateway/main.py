from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import Body, FastAPI, Query, Request
from fastapi.responses import JSONResponse

from stock_data_gateway.cache.service import ReadThroughQueryService
from stock_data_gateway.cache.sqlite_store import SQLiteCacheStore
from stock_data_gateway.core.config import Settings
from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode, tushare_error_code
from stock_data_gateway.domain.provider import QueryResult
from stock_data_gateway.funds import FundDataResult, FundDataService
from stock_data_gateway.jobs.daily_bars import (
    DailyBarsJobManager,
    DailyBarsJobRequest,
    job_max_batch_size,
    job_max_symbols,
    job_queue_limit,
)
from stock_data_gateway.policies.registry import create_default_policy_registry
from stock_data_gateway.providers.akshare.adapter import AkshareProvider
from stock_data_gateway.providers.cninfo.adapter import CninfoProvider
from stock_data_gateway.providers.eastmoney.adapter import EastmoneyProvider
from stock_data_gateway.providers.sec_edgar.adapter import SecEdgarProvider
from stock_data_gateway.providers.stocktwits.adapter import StocktwitsProvider
from stock_data_gateway.providers.tushare.adapter import TushareProvider
from stock_data_gateway.providers.tushare.client import TushareMarketDataClient
from stock_data_gateway.sector_memberships import SectorMembershipIndex, SectorMembershipResult
from stock_data_gateway.source_events import SourceEventResult, SourceEventService

JSON_BODY = Body(default_factory=dict)
_FETCH_SEMAPHORE = threading.BoundedSemaphore(int(os.getenv("GATEWAY_FETCH_CONCURRENCY", "4")))


@asynccontextmanager
async def _lifespan(app: FastAPI) -> Iterator[None]:
    yield
    if app.state.daily_bars_jobs is not None:
        app.state.daily_bars_jobs.close()
    if app.state.stock_sector_memberships is not None:
        app.state.stock_sector_memberships.close()
    if app.state.fund_data is not None:
        app.state.fund_data.close()
    if app.state.source_events is not None:
        app.state.source_events.close()


def create_app(gateway: Optional[ReadThroughQueryService] = None) -> FastAPI:
    app = FastAPI(title="Local Market Data Gateway", lifespan=_lifespan)
    app.state.gateway = gateway
    app.state.daily_bars_jobs = None
    app.state.stock_sector_memberships = None
    app.state.fund_data = None
    app.state.source_events = None

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        providers = {}
        for name, provider in _gateway(app).providers.items():
            health_result = provider.health_check()
            providers[name] = {"ok": health_result.ok, "message": health_result.message}
        return {"ok": True, "providers": providers}

    @app.post("/tushare")
    async def tushare_facade(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception:
            return _tushare_error(GatewayErrorCode.INVALID_REQUEST, "Request body must be JSON.", status_code=400)
        malformed = (
            not isinstance(payload, dict)
            or not payload.get("api_name")
            or not isinstance(payload.get("params", {}), dict)
        )
        if malformed:
            return _tushare_error(
                GatewayErrorCode.INVALID_REQUEST,
                "Request requires api_name and params.",
                status_code=400,
            )

        endpoint = str(payload["api_name"])
        params = dict(payload.get("params") or {})
        fields = payload.get("fields")
        if fields is not None:
            fields = str(fields)

        if _comma_count(params.get("ts_code")) > _max_symbols_per_request():
            return _tushare_error(
                GatewayErrorCode.INVALID_REQUEST,
                f"ts_code list exceeds maximum symbols per request: {_max_symbols_per_request()}",
                status_code=400,
            )

        try:
            with _fetch_slot():
                result = _gateway(app).query("tushare", endpoint, params, fields)
        except GatewayError as error:
            return _tushare_error(error.code, error.message, status_code=_error_status(error))
        if result.meta.get("status") == "error":
            code = GatewayErrorCode(result.meta.get("error_code", GatewayErrorCode.UNKNOWN_ERROR.value))
            return JSONResponse(
                {
                    "code": tushare_error_code(code),
                    "msg": result.meta.get("error_message", code.value),
                    "data": {"fields": [], "items": []},
                    "meta": result.meta,
                }
            )
        return JSONResponse(
            {
                "code": 0,
                "msg": "",
                "data": {"fields": result.data.fields, "items": result.data.items},
                "meta": result.meta,
            }
        )

    @app.post("/api/v1/market-data/tushare/daily")
    def tushare_daily(payload: dict[str, Any] = JSON_BODY) -> JSONResponse:
        try:
            with _fetch_slot():
                return _normalized_tushare_bars(
                    gateway=_gateway(app),
                    endpoint="daily",
                    payload=payload,
                    include_turnover=bool(payload.get("include_turnover", False)),
                )
        except GatewayError as error:
            return _normalized_exception(error)

    @app.post("/api/v1/market-data/tushare/index-daily")
    def tushare_index_daily(payload: dict[str, Any] = JSON_BODY) -> JSONResponse:
        try:
            with _fetch_slot():
                return _normalized_tushare_bars(gateway=_gateway(app), endpoint="index_daily", payload=payload)
        except GatewayError as error:
            return _normalized_exception(error)

    @app.post("/api/v1/market-data/tushare/fund-daily")
    def tushare_fund_daily(payload: dict[str, Any] = JSON_BODY) -> JSONResponse:
        try:
            with _fetch_slot():
                return _normalized_tushare_bars(gateway=_gateway(app), endpoint="fund_daily", payload=payload)
        except GatewayError as error:
            return _normalized_exception(error)

    @app.post("/api/v1/market-data/tushare/stock-basic")
    def tushare_stock_basic(payload: dict[str, Any] = JSON_BODY) -> JSONResponse:
        try:
            limit = _optional_positive_int(payload.get("limit"), default=5000, maximum=10000)
            with _fetch_slot():
                result = _gateway(app).query(
                    "tushare",
                    "stock_basic",
                    {"list_status": str(payload.get("list_status") or "L")},
                    fields="ts_code,symbol,name,industry,list_date,exchange,list_status",
                    force_refresh=bool(payload.get("force_refresh", False)),
                    allow_stale_on_error=bool(payload.get("allow_stale", True)),
                )
            error = _query_error_response(result)
            if error is not None:
                return error
            rows = _rows_from_query(result)[:limit]
            rows = [{**row, "source": "tushare"} for row in rows]
            return _normalized_success("tushare", "stock_basic", rows, [result])
        except (ValueError, GatewayError) as error:
            return _normalized_exception(error)

    @app.post("/api/v1/market-data/tushare/trade-cal")
    def tushare_trade_cal(payload: dict[str, Any] = JSON_BODY) -> JSONResponse:
        try:
            start_date = _compact_date(_required(payload, "start_date"))
            end_date = _compact_date(_required(payload, "end_date"))
            with _fetch_slot():
                result = _gateway(app).query(
                    "tushare",
                    "trade_cal",
                    {
                        "exchange": str(payload.get("exchange") or "SSE"),
                        "start_date": start_date,
                        "end_date": end_date,
                    },
                    fields="exchange,cal_date,is_open,pretrade_date",
                    force_refresh=bool(payload.get("force_refresh", False)),
                    allow_stale_on_error=bool(payload.get("allow_stale", True)),
                )
            error = _query_error_response(result)
            if error is not None:
                return error
            rows = [
                {
                    "exchange": row.get("exchange"),
                    "cal_date": _iso_date(row.get("cal_date")),
                    "is_open": _truthy_open_flag(row.get("is_open")),
                    "pretrade_date": _iso_date(row.get("pretrade_date")),
                    "source": "tushare",
                }
                for row in _rows_from_query(result)
            ]
            return _normalized_success("tushare", "trade_cal", rows, [result])
        except (ValueError, GatewayError) as error:
            return _normalized_exception(error)

    @app.get("/api/v1/market-data/eastmoney/market-quotes")
    def eastmoney_market_quotes(stock_codes: str = Query(default="")) -> JSONResponse:
        if not stock_codes.strip():
            return _normalized_error(GatewayErrorCode.INVALID_REQUEST.value, "stock_codes is required", 400)
        return _provider_success_or_empty(
            _gateway(app),
            provider_name="eastmoney",
            endpoint="market_quotes",
            params={"stock_codes": stock_codes},
        )

    @app.get("/api/v1/market-data/eastmoney/northbound-capital")
    def eastmoney_northbound_capital(trade_date: str = Query(default="")) -> JSONResponse:
        return _provider_success_or_empty(
            _gateway(app),
            provider_name="eastmoney",
            endpoint="northbound_capital",
            params={"trade_date": trade_date},
        )

    @app.get("/api/v1/market-data/eastmoney/main-capital-flow")
    def eastmoney_main_capital_flow(trade_date: str = Query(default="")) -> JSONResponse:
        return _provider_success_or_empty(
            _gateway(app),
            provider_name="eastmoney",
            endpoint="main_capital_flow",
            params={"trade_date": trade_date},
        )

    @app.get("/api/v1/market-data/akshare/sector-concepts")
    def akshare_sector_concepts(limit: str = Query(default="100")) -> JSONResponse:
        return _provider_success_or_empty(
            _gateway(app),
            provider_name="akshare",
            endpoint="sector_concepts",
            params={"limit": limit},
        )

    @app.get("/api/v1/market-data/akshare/limit-up-down")
    def akshare_limit_up_down(trade_date: str = Query(default="")) -> JSONResponse:
        if not trade_date.strip():
            return _normalized_error(GatewayErrorCode.INVALID_REQUEST.value, "trade_date is required", 400)
        return _provider_success_or_empty(
            _gateway(app),
            provider_name="akshare",
            endpoint="limit_up_down",
            params={"trade_date": trade_date},
        )

    @app.get("/api/v1/market-data/sectors/concepts")
    def sector_concepts(trade_date: str = Query(default=""), limit: str = Query(default="100")) -> JSONResponse:
        try:
            params: dict[str, Any] = {"limit": _optional_positive_int(limit, default=100, maximum=5000)}
            if trade_date.strip():
                params["trade_date"] = _iso_date(_compact_date(trade_date))
            return _provider_success_or_empty(
                _gateway(app),
                provider_name="akshare",
                endpoint="sector_concepts",
                params=params,
                response_provider_name="local_gateway",
                response_endpoint="sectors_concepts",
            )
        except (ValueError, GatewayError) as error:
            return _normalized_exception(error)

    @app.get("/api/v1/market-data/sectors/constituents")
    def sector_constituents(
        sector_name: str = Query(default=""),
        trade_date: str = Query(default=""),
        limit: str = Query(default="50"),
    ) -> JSONResponse:
        try:
            resolved_sector_name = str(_required({"sector_name": sector_name}, "sector_name")).strip()
            if not resolved_sector_name:
                raise ValueError("sector_name is required")
            params: dict[str, Any] = {
                "sector_name": resolved_sector_name,
                "limit": _optional_positive_int(limit, default=50, maximum=5000),
            }
            if trade_date.strip():
                params["trade_date"] = _iso_date(_compact_date(trade_date))
            return _provider_success_or_empty(
                _gateway(app),
                provider_name="akshare",
                endpoint="sector_constituents",
                params=params,
                response_provider_name="local_gateway",
                response_endpoint="sectors_constituents",
            )
        except (ValueError, GatewayError) as error:
            return _normalized_exception(error)

    @app.post("/api/v1/market-data/stocks/sector-memberships")
    def stock_sector_memberships(payload: dict[str, Any] = JSON_BODY) -> JSONResponse:
        try:
            symbols = _symbols(payload)
            trade_date = _iso_date(_compact_date(_required(payload, "trade_date")))
            sector_types = _sector_types(payload.get("sector_types"))
            limit_per_symbol = _optional_positive_int(payload.get("limit_per_symbol"), default=50, maximum=200)
            sector_universe_limit = _optional_positive_int(
                payload.get("sector_universe_limit"),
                default=80,
                maximum=1000,
            )
            request_timeout_seconds = _optional_positive_float(
                payload.get("timeout_seconds"),
                default=12.0,
                maximum=15.0,
                field_name="timeout_seconds",
            )
            upstream_timeout_seconds = _optional_positive_float(
                payload.get("upstream_timeout_seconds"),
                default=4.0,
                maximum=request_timeout_seconds,
                field_name="upstream_timeout_seconds",
            )
            with _fetch_slot():
                result = _stock_sector_membership_index(app).memberships(
                    symbols=symbols,
                    trade_date=trade_date,
                    sector_types=sector_types,
                    limit_per_symbol=limit_per_symbol,
                    force_refresh=bool(payload.get("force_refresh", False)),
                    sector_universe_limit=sector_universe_limit,
                    request_timeout_seconds=request_timeout_seconds,
                    upstream_timeout_seconds=upstream_timeout_seconds,
                )
            return _normalized_sector_membership_response(result)
        except (ValueError, GatewayError) as error:
            return _normalized_exception(error)

    @app.get("/api/v1/market-data/funds/profile")
    def fund_profile(
        fund_code: str = Query(default=""),
        force_refresh: bool = Query(default=False),
        timeout_seconds: str = Query(default=""),
        upstream_timeout_seconds: str = Query(default=""),
    ) -> JSONResponse:
        try:
            resolved_fund_code = _fund_code(fund_code)
            request_timeout_seconds = _optional_positive_float(
                timeout_seconds,
                default=12.0,
                maximum=15.0,
                field_name="timeout_seconds",
            )
            resolved_upstream_timeout_seconds = _optional_positive_float(
                upstream_timeout_seconds,
                default=4.0,
                maximum=request_timeout_seconds,
                field_name="upstream_timeout_seconds",
            )
            with _fetch_slot():
                result = _fund_data_service(app).profile(
                    fund_code=resolved_fund_code,
                    force_refresh=force_refresh,
                    request_timeout_seconds=request_timeout_seconds,
                    upstream_timeout_seconds=resolved_upstream_timeout_seconds,
                )
            return _normalized_fund_data_response("fund_profile", result)
        except (ValueError, GatewayError) as error:
            return _normalized_exception(error)

    @app.get("/api/v1/market-data/funds/holdings")
    def fund_holdings(
        fund_code: str = Query(default=""),
        limit: str = Query(default="10"),
        force_refresh: bool = Query(default=False),
        timeout_seconds: str = Query(default=""),
        upstream_timeout_seconds: str = Query(default=""),
    ) -> JSONResponse:
        try:
            resolved_fund_code = _fund_code(fund_code)
            resolved_limit = _optional_positive_int(limit, default=10, maximum=100)
            request_timeout_seconds = _optional_positive_float(
                timeout_seconds,
                default=12.0,
                maximum=15.0,
                field_name="timeout_seconds",
            )
            resolved_upstream_timeout_seconds = _optional_positive_float(
                upstream_timeout_seconds,
                default=4.0,
                maximum=request_timeout_seconds,
                field_name="upstream_timeout_seconds",
            )
            with _fetch_slot():
                result = _fund_data_service(app).holdings(
                    fund_code=resolved_fund_code,
                    limit=resolved_limit,
                    force_refresh=force_refresh,
                    request_timeout_seconds=request_timeout_seconds,
                    upstream_timeout_seconds=resolved_upstream_timeout_seconds,
                )
            return _normalized_fund_data_response("fund_holdings", result)
        except (ValueError, GatewayError) as error:
            return _normalized_exception(error)

    @app.get("/api/v1/market-data/source-events/official-filings")
    def source_events_official_filings(
        cik: str = Query(default="0000320193"),
        limit: str = Query(default="20"),
        force_refresh: bool = Query(default=False),
        timeout_seconds: str = Query(default=""),
        upstream_timeout_seconds: str = Query(default=""),
    ) -> JSONResponse:
        try:
            resolved_limit = _optional_positive_int(limit, default=20, maximum=100)
            request_timeout_seconds, resolved_upstream_timeout_seconds = _source_event_timeouts(
                timeout_seconds,
                upstream_timeout_seconds,
            )
            with _fetch_slot():
                result = _source_event_service(app).official_filings(
                    cik=cik,
                    limit=resolved_limit,
                    force_refresh=force_refresh,
                    request_timeout_seconds=request_timeout_seconds,
                    upstream_timeout_seconds=resolved_upstream_timeout_seconds,
                )
            return _normalized_source_event_response("official_filings", result)
        except (ValueError, GatewayError) as error:
            return _normalized_exception(error)

    @app.get("/api/v1/market-data/source-events/official-disclosures")
    def source_events_official_disclosures(
        symbol: str = Query(default="000001"),
        start_date: str = Query(default=""),
        end_date: str = Query(default=""),
        limit: str = Query(default="20"),
        force_refresh: bool = Query(default=False),
        timeout_seconds: str = Query(default=""),
        upstream_timeout_seconds: str = Query(default=""),
    ) -> JSONResponse:
        try:
            resolved_start_date, resolved_end_date = _source_event_date_range(start_date, end_date)
            resolved_limit = _optional_positive_int(limit, default=20, maximum=100)
            request_timeout_seconds, resolved_upstream_timeout_seconds = _source_event_timeouts(
                timeout_seconds,
                upstream_timeout_seconds,
            )
            with _fetch_slot():
                result = _source_event_service(app).official_disclosures(
                    symbol=symbol.strip().upper(),
                    start_date=resolved_start_date,
                    end_date=resolved_end_date,
                    limit=resolved_limit,
                    force_refresh=force_refresh,
                    request_timeout_seconds=request_timeout_seconds,
                    upstream_timeout_seconds=resolved_upstream_timeout_seconds,
                )
            return _normalized_source_event_response("official_disclosures", result)
        except (ValueError, GatewayError) as error:
            return _normalized_exception(error)

    @app.get("/api/v1/market-data/source-events/news-context")
    def source_events_news_context(
        src: str = Query(default="sina"),
        start_datetime: str = Query(default=""),
        end_datetime: str = Query(default=""),
        query: str = Query(default=""),
        limit: str = Query(default="20"),
        force_refresh: bool = Query(default=False),
        timeout_seconds: str = Query(default=""),
        upstream_timeout_seconds: str = Query(default=""),
    ) -> JSONResponse:
        try:
            resolved_start_datetime, resolved_end_datetime = _source_event_datetime_range(
                start_datetime,
                end_datetime,
            )
            resolved_limit = _optional_positive_int(limit, default=20, maximum=100)
            request_timeout_seconds, resolved_upstream_timeout_seconds = _source_event_timeouts(
                timeout_seconds,
                upstream_timeout_seconds,
            )
            with _fetch_slot():
                result = _source_event_service(app).news_context(
                    src=src.strip() or "sina",
                    start_datetime=resolved_start_datetime,
                    end_datetime=resolved_end_datetime,
                    query=query.strip(),
                    limit=resolved_limit,
                    force_refresh=force_refresh,
                    request_timeout_seconds=request_timeout_seconds,
                    upstream_timeout_seconds=resolved_upstream_timeout_seconds,
                )
            return _normalized_source_event_response("news_context", result)
        except (ValueError, GatewayError) as error:
            return _normalized_exception(error)

    @app.get("/api/v1/market-data/source-events/social-heat")
    def source_events_social_heat(
        symbol: str = Query(default="AAPL"),
        limit: str = Query(default="5"),
        enabled: bool = Query(default=False),
        force_refresh: bool = Query(default=False),
        timeout_seconds: str = Query(default=""),
        upstream_timeout_seconds: str = Query(default=""),
    ) -> JSONResponse:
        try:
            resolved_limit = _optional_positive_int(limit, default=5, maximum=30)
            request_timeout_seconds, resolved_upstream_timeout_seconds = _source_event_timeouts(
                timeout_seconds,
                upstream_timeout_seconds,
            )
            with _fetch_slot():
                result = _source_event_service(app).social_heat(
                    symbol=symbol.strip().upper() or "AAPL",
                    limit=resolved_limit,
                    enabled=enabled or _truthy_env("GATEWAY_SOURCE_EVENTS_ENABLE_SOCIAL_HEAT"),
                    force_refresh=force_refresh,
                    request_timeout_seconds=request_timeout_seconds,
                    upstream_timeout_seconds=resolved_upstream_timeout_seconds,
                )
            return _normalized_source_event_response("social_heat", result)
        except (ValueError, GatewayError) as error:
            return _normalized_exception(error)

    @app.post("/api/v1/market-data/source-events/news-permission-smoke")
    def source_events_news_permission_smoke(payload: dict[str, Any] = JSON_BODY) -> JSONResponse:
        try:
            start_datetime, end_datetime = _source_event_datetime_range(
                str(payload.get("start_datetime") or ""),
                str(payload.get("end_datetime") or ""),
            )
            src_values = _optional_string_list(payload.get("src_values"))
            limit_per_src = _optional_positive_int(payload.get("limit_per_src"), default=1, maximum=20)
            upstream_timeout_seconds = _optional_positive_float(
                payload.get("upstream_timeout_seconds"),
                default=5.0,
                maximum=15.0,
                field_name="upstream_timeout_seconds",
            )
            with _fetch_slot():
                result = _source_event_service(app).news_permission_smoke(
                    src_values=src_values,
                    start_datetime=start_datetime,
                    end_datetime=end_datetime,
                    limit_per_src=limit_per_src,
                    upstream_timeout_seconds=upstream_timeout_seconds,
                )
            return _normalized_source_event_response("news_permission_smoke", result)
        except (ValueError, GatewayError) as error:
            return _normalized_exception(error)

    @app.post("/api/v1/market-data/narrative/source-events/official-filings")
    def narrative_source_events_official_filings(payload: dict[str, Any] = JSON_BODY) -> JSONResponse:
        try:
            resolved_limit = _optional_positive_int(payload.get("limit"), default=10, maximum=100)
            request_timeout_seconds, resolved_upstream_timeout_seconds = _source_event_timeouts(
                payload.get("timeout_seconds", ""),
                payload.get("upstream_timeout_seconds", ""),
            )
            symbols = _optional_symbols(payload.get("symbols"))
            with _fetch_slot():
                result = _source_event_service(app).official_filings(
                    cik=str(payload.get("cik") or _cik_from_symbols(symbols) or "0000320193"),
                    limit=resolved_limit,
                    force_refresh=bool(payload.get("force_refresh", False)),
                    request_timeout_seconds=request_timeout_seconds,
                    upstream_timeout_seconds=resolved_upstream_timeout_seconds,
                )
            return _normalized_narrative_source_event_response(
                "narrative_official_filings",
                result,
                requested_symbols=symbols,
                query=str(payload.get("query") or ""),
            )
        except (ValueError, GatewayError) as error:
            return _normalized_exception(error)

    @app.post("/api/v1/market-data/narrative/source-events/official-disclosures")
    def narrative_source_events_official_disclosures(payload: dict[str, Any] = JSON_BODY) -> JSONResponse:
        try:
            resolved_start_date, resolved_end_date = _source_event_date_range(
                str(payload.get("start_date") or ""),
                str(payload.get("end_date") or ""),
            )
            resolved_limit = _optional_positive_int(payload.get("limit"), default=10, maximum=100)
            request_timeout_seconds, resolved_upstream_timeout_seconds = _source_event_timeouts(
                payload.get("timeout_seconds", ""),
                payload.get("upstream_timeout_seconds", ""),
            )
            symbols = _optional_symbols(payload.get("symbols"))
            symbol = str(payload.get("symbol") or _first_base_symbol(symbols) or "000001").upper()
            with _fetch_slot():
                result = _source_event_service(app).official_disclosures(
                    symbol=symbol,
                    start_date=resolved_start_date,
                    end_date=resolved_end_date,
                    limit=resolved_limit,
                    force_refresh=bool(payload.get("force_refresh", False)),
                    request_timeout_seconds=request_timeout_seconds,
                    upstream_timeout_seconds=resolved_upstream_timeout_seconds,
                )
            return _normalized_narrative_source_event_response(
                "narrative_official_disclosures",
                result,
                requested_symbols=[symbol],
                query=str(payload.get("query") or ""),
            )
        except (ValueError, GatewayError) as error:
            return _normalized_exception(error)

    @app.post("/api/v1/market-data/narrative/source-events/news-context")
    def narrative_source_events_news_context(payload: dict[str, Any] = JSON_BODY) -> JSONResponse:
        try:
            resolved_start_datetime, resolved_end_datetime = _source_event_datetime_range(
                str(payload.get("start_datetime") or ""),
                str(payload.get("end_datetime") or ""),
            )
            resolved_limit = _optional_positive_int(payload.get("limit"), default=10, maximum=100)
            request_timeout_seconds, resolved_upstream_timeout_seconds = _source_event_timeouts(
                payload.get("timeout_seconds", ""),
                payload.get("upstream_timeout_seconds", ""),
            )
            query = str(payload.get("query") or "")
            with _fetch_slot():
                result = _source_event_service(app).news_context(
                    src=str(payload.get("src") or "sina").strip() or "sina",
                    start_datetime=resolved_start_datetime,
                    end_datetime=resolved_end_datetime,
                    query=query,
                    limit=resolved_limit,
                    force_refresh=bool(payload.get("force_refresh", False)),
                    request_timeout_seconds=request_timeout_seconds,
                    upstream_timeout_seconds=resolved_upstream_timeout_seconds,
                )
            return _normalized_narrative_source_event_response(
                "narrative_news_context",
                result,
                requested_symbols=_optional_symbols(payload.get("symbols")),
                query=query,
            )
        except (ValueError, GatewayError) as error:
            return _normalized_exception(error)

    @app.post("/api/v1/market-data/narrative/source-events/social-heat")
    def narrative_source_events_social_heat(payload: dict[str, Any] = JSON_BODY) -> JSONResponse:
        try:
            resolved_limit = _optional_positive_int(payload.get("limit"), default=5, maximum=30)
            request_timeout_seconds, resolved_upstream_timeout_seconds = _source_event_timeouts(
                payload.get("timeout_seconds", ""),
                payload.get("upstream_timeout_seconds", ""),
            )
            symbols = _optional_symbols(payload.get("symbols"))
            symbol = str(payload.get("symbol") or _first_base_symbol(symbols) or "AAPL").upper()
            with _fetch_slot():
                result = _source_event_service(app).social_heat(
                    symbol=symbol,
                    limit=resolved_limit,
                    enabled=bool(payload.get("enabled", False))
                    or _truthy_env("GATEWAY_SOURCE_EVENTS_ENABLE_SOCIAL_HEAT"),
                    force_refresh=bool(payload.get("force_refresh", False)),
                    request_timeout_seconds=request_timeout_seconds,
                    upstream_timeout_seconds=resolved_upstream_timeout_seconds,
                )
            return _normalized_narrative_source_event_response(
                "narrative_social_heat",
                result,
                requested_symbols=[symbol],
                query=str(payload.get("query") or ""),
            )
        except (ValueError, GatewayError) as error:
            return _normalized_exception(error)

    @app.get("/api/v1/market-data/etf/spot")
    def etf_spot(limit: str = Query(default="100")) -> JSONResponse:
        try:
            return _provider_success_or_empty(
                _gateway(app),
                provider_name="akshare",
                endpoint="etf_spot",
                params={"limit": _optional_positive_int(limit, default=100, maximum=5000)},
                response_provider_name="local_gateway",
                response_endpoint="etf_spot",
            )
        except (ValueError, GatewayError) as error:
            return _normalized_exception(error)

    @app.get("/api/v1/market-data/etf/basic")
    def etf_basic(market: str = Query(default="cn"), limit: str = Query(default="50")) -> JSONResponse:
        try:
            return _provider_success_or_empty(
                _gateway(app),
                provider_name="akshare",
                endpoint="etf_basic",
                params={
                    "market": market,
                    "limit": _optional_positive_int(limit, default=50, maximum=5000),
                },
                response_provider_name="local_gateway",
                response_endpoint="etf_basic",
            )
        except (ValueError, GatewayError) as error:
            return _normalized_exception(error)

    @app.get("/api/v1/market-data/capital/northbound")
    def capital_northbound(trade_date: str = Query(default="")) -> JSONResponse:
        try:
            resolved_trade_date = _iso_date(_compact_date(_required({"trade_date": trade_date}, "trade_date")))
            return _provider_success_or_empty(
                _gateway(app),
                provider_name="eastmoney",
                endpoint="northbound_capital",
                params={"trade_date": resolved_trade_date},
                response_provider_name="local_gateway",
                response_endpoint="capital_northbound",
            )
        except (ValueError, GatewayError) as error:
            return _normalized_exception(error)

    @app.get("/api/v1/market-data/capital/main-flow")
    def capital_main_flow(trade_date: str = Query(default=""), limit: str = Query(default="50")) -> JSONResponse:
        try:
            resolved_trade_date = _iso_date(_compact_date(_required({"trade_date": trade_date}, "trade_date")))
            return _provider_success_or_empty(
                _gateway(app),
                provider_name="eastmoney",
                endpoint="main_capital_flow",
                params={
                    "trade_date": resolved_trade_date,
                    "limit": _optional_positive_int(limit, default=50, maximum=5000),
                },
                response_provider_name="local_gateway",
                response_endpoint="capital_main_flow",
            )
        except (ValueError, GatewayError) as error:
            return _normalized_exception(error)

    @app.get("/api/v1/market-data/etf/flow")
    def etf_flow(trade_date: str = Query(default=""), limit: str = Query(default="50")) -> JSONResponse:
        try:
            resolved_trade_date = _iso_date(_compact_date(_required({"trade_date": trade_date}, "trade_date")))
            return _provider_success_or_empty(
                _gateway(app),
                provider_name="akshare",
                endpoint="etf_flow",
                params={
                    "trade_date": resolved_trade_date,
                    "limit": _optional_positive_int(limit, default=50, maximum=5000),
                },
                response_provider_name="local_gateway",
                response_endpoint="etf_flow",
            )
        except (ValueError, GatewayError) as error:
            return _normalized_exception(error)

    @app.get("/api/v1/market-data/index/constituents")
    def index_constituents(
        index_symbol: str = Query(default=""),
        trade_date: str = Query(default=""),
        limit: str = Query(default="50"),
    ) -> JSONResponse:
        try:
            resolved_index_symbol = str(_required({"index_symbol": index_symbol}, "index_symbol")).strip().upper()
            if not resolved_index_symbol:
                raise ValueError("index_symbol is required")
            params: dict[str, Any] = {
                "index_symbol": resolved_index_symbol,
                "limit": _optional_positive_int(limit, default=50, maximum=5000),
            }
            if trade_date.strip():
                params["trade_date"] = _iso_date(_compact_date(trade_date))
            return _provider_success_or_empty(
                _gateway(app),
                provider_name="akshare",
                endpoint="index_constituents",
                params=params,
                response_provider_name="local_gateway",
                response_endpoint="index_constituents",
            )
        except (ValueError, GatewayError) as error:
            return _normalized_exception(error)

    @app.get("/api/v1/market-data/margin/summary")
    def margin_summary(trade_date: str = Query(default="")) -> JSONResponse:
        try:
            resolved_trade_date = _iso_date(_compact_date(_required({"trade_date": trade_date}, "trade_date")))
            return _provider_success_or_empty(
                _gateway(app),
                provider_name="akshare",
                endpoint="margin_summary",
                params={"trade_date": resolved_trade_date},
                response_provider_name="local_gateway",
                response_endpoint="margin_summary",
            )
        except (ValueError, GatewayError) as error:
            return _normalized_exception(error)

    @app.get("/api/v1/market-data/margin/detail")
    def margin_detail(trade_date: str = Query(default=""), limit: str = Query(default="50")) -> JSONResponse:
        try:
            resolved_trade_date = _iso_date(_compact_date(_required({"trade_date": trade_date}, "trade_date")))
            return _provider_success_or_empty(
                _gateway(app),
                provider_name="akshare",
                endpoint="margin_detail",
                params={
                    "trade_date": resolved_trade_date,
                    "limit": _optional_positive_int(limit, default=50, maximum=5000),
                },
                response_provider_name="local_gateway",
                response_endpoint="margin_detail",
            )
        except (ValueError, GatewayError) as error:
            return _normalized_exception(error)

    @app.get("/api/v1/market-data/fundamentals/earnings-calendar")
    def earnings_calendar(
        start_date: str = Query(default=""),
        end_date: str = Query(default=""),
        limit: str = Query(default="50"),
    ) -> JSONResponse:
        try:
            resolved_start_date = _iso_date(_compact_date(_required({"start_date": start_date}, "start_date")))
            resolved_end_date = _iso_date(_compact_date(_required({"end_date": end_date}, "end_date")))
            return _provider_success_or_empty(
                _gateway(app),
                provider_name="akshare",
                endpoint="earnings_calendar",
                params={
                    "start_date": resolved_start_date,
                    "end_date": resolved_end_date,
                    "limit": _optional_positive_int(limit, default=50, maximum=5000),
                },
                response_provider_name="local_gateway",
                response_endpoint="earnings_calendar",
            )
        except (ValueError, GatewayError) as error:
            return _normalized_exception(error)

    @app.get("/api/v1/market-data/market/dragon-tiger")
    def market_dragon_tiger(trade_date: str = Query(default=""), limit: str = Query(default="50")) -> JSONResponse:
        try:
            resolved_trade_date = _iso_date(_compact_date(_required({"trade_date": trade_date}, "trade_date")))
            return _provider_success_or_empty(
                _gateway(app),
                provider_name="akshare",
                endpoint="dragon_tiger",
                params={
                    "trade_date": resolved_trade_date,
                    "limit": _optional_positive_int(limit, default=50, maximum=5000),
                },
                response_provider_name="local_gateway",
                response_endpoint="market_dragon_tiger",
            )
        except (ValueError, GatewayError) as error:
            return _normalized_exception(error)

    @app.get("/api/v1/market-data/market/limit-up-down")
    def market_limit_up_down(trade_date: str = Query(default="")) -> JSONResponse:
        try:
            resolved_trade_date = _iso_date(_compact_date(_required({"trade_date": trade_date}, "trade_date")))
            return _provider_success_or_empty(
                _gateway(app),
                provider_name="akshare",
                endpoint="limit_up_down",
                params={"trade_date": resolved_trade_date},
                response_provider_name="local_gateway",
                response_endpoint="market_limit_up_down",
            )
        except (ValueError, GatewayError) as error:
            return _normalized_exception(error)

    @app.post("/api/v1/market-data/news/briefs")
    def news_briefs(payload: dict[str, Any] = JSON_BODY) -> JSONResponse:
        try:
            source_provider = str(payload.get("source_provider") or "tushare").strip().lower()
            if source_provider != "tushare":
                return _normalized_error(
                    GatewayErrorCode.INVALID_REQUEST.value,
                    "source_provider must be tushare for news briefs.",
                    400,
                )
            src = str(_required(payload, "src")).strip()
            if not src:
                raise ValueError("src is required")
            start_datetime = str(payload.get("start_datetime") or payload.get("start_date") or "").strip()
            end_datetime = str(payload.get("end_datetime") or payload.get("end_date") or "").strip()
            if not start_datetime:
                raise ValueError("start_datetime is required")
            if not end_datetime:
                raise ValueError("end_datetime is required")
            limit = _optional_positive_int(payload.get("limit"), default=20, maximum=1500)
            params = {"src": src, "start_date": start_datetime, "end_date": end_datetime}
            with _fetch_slot():
                response = _gateway(app).providers["tushare"].fetch(
                    "news",
                    params,
                    fields="datetime,title,content,channels",
                )
            rows = _normalized_news_rows(response.rows(), src=src)[:limit]
            return _normalized_success("tushare", "news", rows, [], cache_mode="upstream")
        except KeyError:
            return _normalized_error(
                GatewayErrorCode.PROVIDER_UNAVAILABLE.value,
                "Provider is not registered: tushare",
                503,
            )
        except GatewayError as error:
            if error.code == GatewayErrorCode.NO_PERMISSION:
                return _normalized_error("PROVIDER_PERMISSION_REQUIRED", error.message, 403)
            return _normalized_exception(error)
        except ValueError as error:
            return _normalized_exception(error)

    @app.post("/api/v1/market-data/chips/cyq")
    def chips_cyq(payload: dict[str, Any] = JSON_BODY) -> JSONResponse:
        try:
            symbols = _symbols(payload)
            trade_date = _compact_date(_required(payload, "trade_date"))
            results = []
            cost_rows: dict[tuple[str, str], list[dict[str, Any]]] = {}
            deadline = _request_deadline()
            with _fetch_slot():
                for symbol in symbols:
                    _check_deadline(deadline)
                    result = _gateway(app).query(
                        "tushare",
                        "cyq_chips",
                        {"ts_code": symbol, "trade_date": trade_date},
                        fields="ts_code,trade_date,price,percent",
                        force_refresh=bool(payload.get("force_refresh", False)),
                        allow_stale_on_error=bool(payload.get("allow_stale", True)),
                    )
                    error = _query_error_response(result)
                    if error is not None:
                        return error
                    results.append(result)
                    for row in _rows_from_query(result):
                        key = (str(row.get("ts_code")), _iso_date(row.get("trade_date")))
                        cost_rows.setdefault(key, []).append({"price": row.get("price"), "percent": row.get("percent")})
            rows = [
                {
                    "symbol": symbol,
                    "trade_date": trade_date,
                    "cost_distribution": distribution,
                    "source": "tushare",
                }
                for (symbol, trade_date), distribution in cost_rows.items()
            ]
            return _normalized_success("local_gateway", "cyq_chips", rows, results)
        except (ValueError, GatewayError) as error:
            return _normalized_exception(error)

    @app.post("/api/v1/market-data/jobs/daily-bars")
    def create_daily_bars_job(payload: dict[str, Any] = JSON_BODY) -> JSONResponse:
        try:
            request = DailyBarsJobRequest.from_payload(
                payload,
                max_symbols=job_max_symbols(),
                max_batch_size=job_max_batch_size(),
            )
            job = _daily_bars_jobs(app).create_job(request)
            return JSONResponse(
                {
                    "data": {"job_id": job.job_id, "status": job.status},
                    "meta": _job_meta(job),
                },
                status_code=202,
            )
        except GatewayError as error:
            return _normalized_exception(error)

    @app.post("/api/v1/market-data/jobs/breadth-window")
    def create_breadth_window_job(payload: dict[str, Any] = JSON_BODY) -> JSONResponse:
        try:
            request = _daily_bars_jobs(app).create_breadth_window_request(
                payload,
                max_symbols=job_max_symbols(),
                max_batch_size=job_max_batch_size(),
            )
            job = _daily_bars_jobs(app).create_job(request)
            return JSONResponse(
                {
                    "data": {"job_id": job.job_id, "status": job.status},
                    "meta": _job_meta(job),
                },
                status_code=202,
            )
        except GatewayError as error:
            return _normalized_exception(error)

    @app.get("/api/v1/market-data/jobs")
    def list_market_data_jobs(
        provider: str = Query(default=""),
        endpoint: str = Query(default=""),
        job_type: str = Query(default=""),
        status: str = Query(default=""),
        created_after: str = Query(default=""),
        updated_after: str = Query(default=""),
    ) -> JSONResponse:
        jobs = _daily_bars_jobs(app).list_jobs(
            provider=provider or None,
            endpoint=endpoint or None,
            job_type=job_type or None,
            status=status or None,
            created_after=created_after or None,
            updated_after=updated_after or None,
        )
        return JSONResponse(
            {
                "data": {"jobs": [job.status_payload() for job in jobs]},
                "meta": {
                    "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                    "row_count": len(jobs),
                    "status": "ok",
                },
            }
        )

    @app.get("/api/v1/market-data/jobs/{job_id}")
    def get_daily_bars_job(job_id: str) -> JSONResponse:
        job = _daily_bars_jobs(app).get_job(job_id)
        if job is None:
            return _normalized_error("JOB_NOT_FOUND", f"Unknown job_id: {job_id}", 404)
        return JSONResponse(
            {
                "data": job.status_payload(),
                "meta": _job_meta(job),
            }
        )

    @app.post("/api/v1/market-data/jobs/{job_id}/cancel")
    def cancel_daily_bars_job(job_id: str) -> JSONResponse:
        job = _daily_bars_jobs(app).cancel_job(job_id)
        if job is None:
            return _normalized_error("JOB_NOT_FOUND", f"Unknown job_id: {job_id}", 404)
        return JSONResponse({"data": job.status_payload(), "meta": _job_meta(job)})

    @app.get("/api/v1/market-data/jobs/{job_id}/rows")
    def get_daily_bars_job_rows(
        job_id: str,
        offset: int = Query(default=0),
        limit: int = Query(default=10000),
    ) -> JSONResponse:
        if offset < 0:
            return _normalized_error(GatewayErrorCode.INVALID_REQUEST.value, "offset must be non-negative", 400)
        try:
            resolved_limit = _optional_positive_int(limit, default=10000, maximum=50000)
        except ValueError as error:
            return _normalized_exception(error)
        result = _daily_bars_jobs(app).rows(job_id, offset=offset, limit=resolved_limit)
        if result is None:
            return _normalized_error("JOB_NOT_FOUND", f"Unknown job_id: {job_id}", 404)
        job, rows = result
        return JSONResponse(
            {
                "data": {"rows": rows},
                "meta": {
                    **_job_meta(job),
                    "pagination": {
                        "offset": offset,
                        "limit": resolved_limit,
                        "returned": len(rows),
                        "total": len(job.rows),
                    },
                    "coverage": job.coverage_payload(),
                },
            }
        )

    return app


def _gateway(app: FastAPI) -> ReadThroughQueryService:
    if app.state.gateway is None:
        app.state.gateway = create_default_gateway()
    return app.state.gateway


def _daily_bars_jobs(app: FastAPI) -> DailyBarsJobManager:
    if app.state.daily_bars_jobs is None:
        app.state.daily_bars_jobs = DailyBarsJobManager(
            _gateway(app),
            max_active_jobs=job_queue_limit(),
        )
    return app.state.daily_bars_jobs


def _stock_sector_membership_index(app: FastAPI) -> SectorMembershipIndex:
    if app.state.stock_sector_memberships is None:
        gateway = _gateway(app)
        app.state.stock_sector_memberships = SectorMembershipIndex(
            db_path=gateway.store.path,
            providers=gateway.providers,
        )
    return app.state.stock_sector_memberships


def _fund_data_service(app: FastAPI) -> FundDataService:
    if app.state.fund_data is None:
        gateway = _gateway(app)
        app.state.fund_data = FundDataService(
            db_path=gateway.store.path,
            providers=gateway.providers,
        )
    return app.state.fund_data


def _source_event_service(app: FastAPI) -> SourceEventService:
    if app.state.source_events is None:
        gateway = _gateway(app)
        app.state.source_events = SourceEventService(
            db_path=gateway.store.path,
            providers=gateway.providers,
        )
    return app.state.source_events


def create_default_gateway(settings: Optional[Settings] = None) -> ReadThroughQueryService:
    resolved_settings = settings or Settings.from_env()
    cache_path = Path(resolved_settings.market_data_home) / "market_data.sqlite3"
    store = SQLiteCacheStore(cache_path)
    store.initialize()
    policies = create_default_policy_registry()
    provider = TushareProvider(
        client_factory=lambda: TushareMarketDataClient(
            token=resolved_settings.tushare_token,
            max_retries=resolved_settings.tushare_max_retries,
            rate_limit_per_minute=resolved_settings.tushare_rate_limit_per_minute,
        )
    )
    return ReadThroughQueryService(
        {
            "tushare": provider,
            "eastmoney": EastmoneyProvider(),
            "akshare": AkshareProvider(),
            "cninfo": CninfoProvider(),
            "sec_edgar": SecEdgarProvider(),
            "stocktwits": StocktwitsProvider(),
        },
        policies,
        store,
        offline_mode=resolved_settings.offline_mode,
    )


def _normalized_tushare_bars(
    *,
    gateway: ReadThroughQueryService,
    endpoint: str,
    payload: dict[str, Any],
    include_turnover: bool = False,
) -> JSONResponse:
    try:
        symbols = _symbols(payload)
        start_date = _compact_date(_required(payload, "start_date"))
        end_date = _compact_date(_required(payload, "end_date"))
        deadline = _request_deadline()
        force_refresh = bool(payload.get("force_refresh", False))
        allow_stale = bool(payload.get("allow_stale", True))
        results = []
        price_rows: list[dict[str, Any]] = []
        turnover_by_key: dict[tuple[str, str], Any] = {}

        for symbol in symbols:
            _check_deadline(deadline)
            result = gateway.query(
                "tushare",
                endpoint,
                {"ts_code": symbol, "start_date": start_date, "end_date": end_date},
                fields="ts_code,trade_date,open,high,low,close,pre_close,vol,amount",
                force_refresh=force_refresh,
                allow_stale_on_error=allow_stale,
            )
            error = _query_error_response(result)
            if error is not None:
                return error
            results.append(result)
            price_rows.extend(_rows_from_query(result))

            if include_turnover and endpoint == "daily":
                _check_deadline(deadline)
                turnover_result = gateway.query(
                    "tushare",
                    "daily_basic",
                    {"ts_code": symbol, "start_date": start_date, "end_date": end_date},
                    fields="ts_code,trade_date,turnover_rate",
                    force_refresh=force_refresh,
                    allow_stale_on_error=allow_stale,
                )
                error = _query_error_response(turnover_result)
                if error is not None:
                    return error
                results.append(turnover_result)
                for row in _rows_from_query(turnover_result):
                    turnover_by_key[(str(row.get("ts_code")), str(row.get("trade_date")))] = row.get("turnover_rate")

        rows = []
        for row in price_rows:
            symbol = str(row.get("ts_code"))
            trade_date = str(row.get("trade_date"))
            normalized = {
                "symbol": symbol,
                "trade_date": _iso_date(trade_date),
                "open": row.get("open"),
                "high": row.get("high"),
                "low": row.get("low"),
                "close": row.get("close"),
                "pre_close": row.get("pre_close"),
                "volume": row.get("vol"),
                "amount": row.get("amount"),
                "source": "tushare",
            }
            if include_turnover and endpoint == "daily":
                normalized["turnover_rate"] = turnover_by_key.get((symbol, trade_date))
            rows.append(normalized)
        return _normalized_success("tushare", endpoint, rows, results)
    except (ValueError, GatewayError) as error:
        return _normalized_exception(error)


def _provider_success_or_empty(
    gateway: ReadThroughQueryService,
    *,
    provider_name: str,
    endpoint: str,
    params: dict[str, Any],
    response_provider_name: Optional[str] = None,
    response_endpoint: Optional[str] = None,
) -> JSONResponse:
    output_provider_name = response_provider_name or provider_name
    output_endpoint = response_endpoint or endpoint
    try:
        with _fetch_slot():
            response = gateway.providers[provider_name].fetch(endpoint, params, fields=None)
        return _normalized_success(output_provider_name, output_endpoint, response.rows(), [], cache_mode="upstream")
    except GatewayError as error:
        payload = _normalized_payload(
            output_provider_name,
            output_endpoint,
            [],
            cache_mode="stale_cache",
            cache_hit=False,
        )
        payload["meta"]["status"] = "degraded"
        payload["meta"]["warning"] = {"code": error.code.value, "message": error.message}
        return JSONResponse(payload)
    except KeyError:
        return _normalized_error(
            GatewayErrorCode.PROVIDER_UNAVAILABLE.value,
            f"Provider is not registered: {provider_name}",
            503,
        )


def _normalized_news_rows(rows: list[dict[str, Any]], *, src: str) -> list[dict[str, Any]]:
    return [
        {
            **{
                "datetime": row.get("datetime"),
                "title": _news_title(row),
                "content": row.get("content"),
                "source": "tushare",
            },
            **({"channels": row.get("channels")} if row.get("channels") not in (None, "") else {}),
            "src": src,
            "provider": "tushare",
        }
        for row in rows
    ]


def _news_title(row: dict[str, Any]) -> str | None:
    title = str(row.get("title") or "").strip()
    if title:
        return title
    content = str(row.get("content") or "").strip()
    if content.startswith("【") and "】" in content:
        derived = content[1 : content.index("】")].strip()
        if derived:
            return derived
    if content:
        first_line = content.splitlines()[0].strip()
        return first_line[:80] if first_line else None
    return None


def _normalized_success(
    provider_name: str,
    endpoint: str,
    rows: list[dict[str, Any]],
    results: list[QueryResult],
    *,
    cache_mode: Optional[str] = None,
) -> JSONResponse:
    if cache_mode is None:
        cache_mode = _cache_mode(results)
    payload = _normalized_payload(
        provider_name,
        endpoint,
        rows,
        cache_mode=cache_mode,
        cache_hit=bool(results) and all(bool(result.meta.get("cache_hit")) for result in results),
    )
    return JSONResponse(payload)


def _normalized_payload(
    provider_name: str,
    endpoint: str,
    rows: list[dict[str, Any]],
    *,
    cache_mode: str,
    cache_hit: bool,
) -> dict[str, Any]:
    return {
        "data": {"rows": rows},
        "meta": {
            "provider": provider_name,
            "endpoint": endpoint,
            "cache": {"hit": cache_hit, "mode": cache_mode},
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "row_count": len(rows),
            "status": "ok",
        },
    }


def _job_meta(job: Any) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "job_type": job.request.job_type,
        "status": job.status,
        "provider": "tushare",
        "endpoint": "daily",
        "cache": {"mode": job.cache_mode()},
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }


def _cache_mode(results: list[QueryResult]) -> str:
    if not results:
        return "upstream"
    modes = {_source_to_cache_mode(str(result.meta.get("source") or "")) for result in results}
    if len(modes) == 1:
        return next(iter(modes))
    return "mixed"


def _source_to_cache_mode(source: str) -> str:
    if source == "cache":
        return "cache"
    if source == "stale_cache":
        return "stale_cache"
    if source in {"partial_hit", "mixed"}:
        return "mixed"
    return "upstream"


def _query_error_response(result: QueryResult) -> Optional[JSONResponse]:
    if result.meta.get("status") != "error":
        return None
    return _normalized_error(
        str(result.meta.get("error_code") or GatewayErrorCode.UNKNOWN_ERROR.value),
        str(result.meta.get("error_message") or "Gateway query failed."),
        502,
    )


def _normalized_sector_membership_response(result: SectorMembershipResult) -> JSONResponse:
    payload = _normalized_payload(
        "local_gateway",
        "stock_sector_memberships",
        result.rows,
        cache_mode=result.cache_mode,
        cache_hit=result.cache_hit,
    )
    payload["meta"]["coverage"] = result.coverage
    payload["meta"]["status"] = result.status
    if result.warning is not None:
        payload["meta"]["warning"] = result.warning
    return JSONResponse(payload)


def _normalized_fund_data_response(endpoint: str, result: FundDataResult) -> JSONResponse:
    payload = _normalized_payload(
        "local_gateway",
        endpoint,
        result.rows,
        cache_mode=result.cache_mode,
        cache_hit=result.cache_hit,
    )
    payload["meta"]["coverage"] = result.coverage
    payload["meta"]["status"] = result.status
    if result.warning is not None:
        payload["meta"]["warning"] = result.warning
    return JSONResponse(payload)


def _normalized_source_event_response(endpoint: str, result: SourceEventResult) -> JSONResponse:
    payload = _normalized_payload(
        "local_gateway",
        endpoint,
        result.rows,
        cache_mode=result.cache_mode,
        cache_hit=result.cache_hit,
    )
    payload["meta"].update(result.metadata)
    payload["meta"]["cache_hit"] = result.cache_hit
    payload["meta"]["status"] = result.status
    if result.warning is not None:
        payload["meta"]["warning"] = result.warning
    return JSONResponse(payload)


def _normalized_narrative_source_event_response(
    endpoint: str,
    result: SourceEventResult,
    *,
    requested_symbols: list[str],
    query: str,
) -> JSONResponse:
    rows = [
        _narrative_source_event_row(
            row,
            metadata=result.metadata,
            requested_symbols=requested_symbols,
            query=query,
        )
        for row in result.rows
    ]
    payload = _normalized_payload(
        "gateway",
        endpoint,
        rows,
        cache_mode=result.cache_mode,
        cache_hit=result.cache_hit,
    )
    payload["meta"].update(result.metadata)
    payload["meta"]["provider"] = "gateway"
    payload["meta"]["endpoint"] = endpoint
    payload["meta"]["cache_hit"] = result.cache_hit
    payload["meta"]["status"] = result.status
    if result.warning is not None:
        payload["meta"]["warning"] = result.warning
    return JSONResponse(payload)


def _narrative_source_event_row(
    row: dict[str, Any],
    *,
    metadata: dict[str, Any],
    requested_symbols: list[str],
    query: str,
) -> dict[str, Any]:
    degradation_events = row.get("degradation_warnings")
    if not isinstance(degradation_events, list):
        degradation_events = metadata.get("degradation_events")
    if not isinstance(degradation_events, list):
        degradation_events = []
    stock_codes = _narrative_stock_codes(row, requested_symbols=requested_symbols)
    return {
        "source_event_id": str(row.get("source_event_id") or ""),
        "source_type": _narrative_source_type(str(row.get("source_type") or "")),
        "source_provider": str(row.get("provider") or row.get("source_id") or "local_gateway"),
        "source_url": str(row.get("source_url") or ""),
        "title": str(row.get("title") or ""),
        "event_time": str(row.get("event_time") or row.get("published_at") or row.get("fetched_at") or ""),
        "fetched_at": str(row.get("fetched_at") or metadata.get("generated_at") or ""),
        "trust_tier": str(row.get("trust_tier") or metadata.get("trust_tier") or "candidate_untrusted"),
        "source_quality": _source_quality_label(metadata),
        "license_scope": str(row.get("license_scope") or metadata.get("license_scope") or "unspecified"),
        "retention_policy": str(row.get("retention_policy") or metadata.get("retention_policy") or "metadata_only"),
        "metadata_only": bool(row.get("metadata_only", True)),
        "degradation_events": degradation_events,
        "summary": str(row.get("summary") or ""),
        "stock_codes": stock_codes,
        "narrative_hints": [query] if query else [],
        "evidence_claims": [str(row.get("summary") or row.get("title") or "")],
        "provider_metadata": {
            "source_id": row.get("source_id"),
            "provider_item_id": row.get("provider_item_id"),
            "entity_type": row.get("entity_type"),
            "entity_id": row.get("entity_id"),
            "event_type": row.get("event_type"),
            "market": row.get("market"),
        },
        "source_document_id": str(row.get("provider_item_id") or ""),
        "source_document_title": str(row.get("title") or ""),
        "source_document_url": str(row.get("source_url") or ""),
        "excerpt": str(row.get("summary") or ""),
    }


def _source_quality_label(metadata: dict[str, Any]) -> str:
    source_quality = metadata.get("source_quality")
    if isinstance(source_quality, dict):
        return str(source_quality.get("label") or source_quality.get("parser_health") or "unspecified")
    return str(source_quality or "unspecified")


def _narrative_source_type(source_type: str) -> str:
    aliases = {
        "official_filing": "filing",
        "official_disclosure": "announcement",
        "public_news": "news",
        "social_heat": "social",
    }
    return aliases.get(source_type, source_type or "manual")


def _narrative_stock_codes(row: dict[str, Any], *, requested_symbols: list[str]) -> list[str]:
    if requested_symbols:
        return [_base_symbol(symbol) for symbol in requested_symbols]
    entity_type = str(row.get("entity_type") or "")
    entity_id = str(row.get("entity_id") or "")
    if entity_type in {"symbol", "company", "topic"} and entity_id:
        return [_base_symbol(entity_id)]
    return []


def _normalized_exception(error: Exception) -> JSONResponse:
    if isinstance(error, GatewayError):
        return _normalized_error(error.code.value, error.message, _error_status(error))
    return _normalized_error(GatewayErrorCode.INVALID_REQUEST.value, str(error), 400)


def _normalized_error(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status_code)


def _rows_from_query(result: QueryResult) -> list[dict[str, Any]]:
    return [dict(zip(result.data.fields, item)) for item in result.data.items]


def _required(payload: dict[str, Any], field: str) -> Any:
    value = payload.get(field)
    if value in (None, ""):
        raise ValueError(f"{field} is required")
    return value


def _symbols(payload: dict[str, Any]) -> list[str]:
    raw = _required(payload, "symbols")
    if isinstance(raw, str):
        values = raw.split(",")
    elif isinstance(raw, list):
        values = raw
    else:
        raise ValueError("symbols must be a list or comma-separated string")
    symbols = [str(value).strip() for value in values if str(value).strip()]
    if not symbols:
        raise ValueError("symbols must not be empty")
    max_symbols = _max_symbols_per_request()
    if len(symbols) > max_symbols:
        raise ValueError(f"symbols exceeds maximum symbols per request: {max_symbols}")
    return symbols


def _sector_types(value: Any) -> list[str]:
    if value in (None, ""):
        return ["concept"]
    if isinstance(value, str):
        values = value.split(",")
    elif isinstance(value, list):
        values = value
    else:
        raise ValueError("sector_types must be a list or comma-separated string")
    sector_types = []
    seen = set()
    for item in values:
        sector_type = str(item).strip().lower()
        if not sector_type or sector_type in seen:
            continue
        seen.add(sector_type)
        sector_types.append(sector_type)
    if not sector_types:
        raise ValueError("sector_types must not be empty")
    return sector_types


def _fund_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        raise ValueError("fund_code is required")
    base = text.split(".", 1)[0]
    if not base or not base.isalnum():
        raise ValueError("fund_code is invalid")
    return base


def _source_event_timeouts(timeout_seconds: Any, upstream_timeout_seconds: Any) -> tuple[float, float]:
    request_timeout_seconds = _optional_positive_float(
        timeout_seconds,
        default=12.0,
        maximum=15.0,
        field_name="timeout_seconds",
    )
    resolved_upstream_timeout_seconds = _optional_positive_float(
        upstream_timeout_seconds,
        default=5.0,
        maximum=request_timeout_seconds,
        field_name="upstream_timeout_seconds",
    )
    return request_timeout_seconds, resolved_upstream_timeout_seconds


def _source_event_date_range(start_date: str, end_date: str) -> tuple[str, str]:
    today = datetime.now(timezone.utc).date()
    resolved_end = _compact_date(end_date) if end_date.strip() else today.strftime("%Y%m%d")
    resolved_start = (
        _compact_date(start_date)
        if start_date.strip()
        else (today - timedelta(days=30)).strftime("%Y%m%d")
    )
    return _iso_date(resolved_start), _iso_date(resolved_end)


def _source_event_datetime_range(start_datetime: str, end_datetime: str) -> tuple[str, str]:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    resolved_end = end_datetime.strip() or now.strftime("%Y-%m-%d %H:%M:%S")
    resolved_start = start_datetime.strip() or (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    return resolved_start, resolved_end


def _optional_string_list(value: Any) -> list[str] | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        values = value.split(",")
    elif isinstance(value, list):
        values = value
    else:
        raise ValueError("src_values must be a list or comma-separated string")
    src_values = [str(item).strip() for item in values if str(item).strip()]
    return src_values or None


def _optional_symbols(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        values = value.split(",")
    elif isinstance(value, list):
        values = value
    else:
        raise ValueError("symbols must be a list or comma-separated string")
    return [str(item).strip().upper() for item in values if str(item).strip()]


def _first_base_symbol(symbols: list[str]) -> str:
    return _base_symbol(symbols[0]) if symbols else ""


def _base_symbol(symbol: str) -> str:
    return str(symbol or "").strip().upper().split(".", 1)[0]


def _cik_from_symbols(symbols: list[str]) -> str:
    symbol_to_cik = {
        "AAPL": "0000320193",
        "MSFT": "0000789019",
        "NVDA": "0001045810",
        "TSLA": "0001318605",
        "GOOGL": "0001652044",
        "GOOG": "0001652044",
        "META": "0001326801",
        "AMZN": "0001018724",
    }
    if not symbols:
        return ""
    symbol = _base_symbol(symbols[0])
    if symbol.isdigit():
        return symbol
    return symbol_to_cik.get(symbol, "")


def _truthy_env(name: str) -> bool:
    return str(os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _compact_date(value: Any) -> str:
    text = str(value).strip()
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return text.replace("-", "")
    if len(text) == 8 and text.isdigit():
        return text
    raise ValueError("date must be YYYY-MM-DD or YYYYMMDD")


def _iso_date(value: Any) -> Any:
    if value in (None, ""):
        return value
    text = str(value)
    if len(text) == 8 and text.isdigit():
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
    return text


def _truthy_open_flag(value: Any) -> bool:
    return value in (True, 1, "1", "true", "True")


def _optional_positive_int(value: Any, *, default: int, maximum: int) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("limit must be an integer") from error
    if parsed < 0:
        raise ValueError("limit must be non-negative")
    return min(parsed, maximum)


def _optional_positive_float(value: Any, *, default: float, maximum: float, field_name: str) -> float:
    if value in (None, ""):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be a number") from error
    if parsed <= 0:
        raise ValueError(f"{field_name} must be positive")
    return min(parsed, maximum)


@contextmanager
def _fetch_slot() -> Iterator[None]:
    acquired = _FETCH_SEMAPHORE.acquire(timeout=_queue_timeout_seconds())
    if not acquired:
        raise GatewayError(GatewayErrorCode.REQUEST_TIMEOUT, "Gateway fetch queue is busy.")
    try:
        yield
    finally:
        _FETCH_SEMAPHORE.release()


def _request_deadline() -> float:
    return time.monotonic() + _request_timeout_seconds()


def _check_deadline(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise GatewayError(GatewayErrorCode.REQUEST_TIMEOUT, "Gateway request timed out before completion.")


def _request_timeout_seconds() -> float:
    return max(1.0, float(os.getenv("GATEWAY_REQUEST_TIMEOUT_SECONDS", "9.0")))


def _queue_timeout_seconds() -> float:
    return max(0.1, float(os.getenv("GATEWAY_QUEUE_TIMEOUT_SECONDS", "1.0")))


def _max_symbols_per_request() -> int:
    return max(1, int(os.getenv("GATEWAY_MAX_SYMBOLS_PER_REQUEST", "100")))


def _comma_count(value: Any) -> int:
    if not isinstance(value, str) or not value.strip():
        return 0
    return len([item for item in value.split(",") if item.strip()])


def _error_status(error: GatewayError) -> int:
    if error.code == GatewayErrorCode.INVALID_REQUEST:
        return 400
    if error.code == GatewayErrorCode.QUEUE_FULL:
        return 429
    if error.code == GatewayErrorCode.REQUEST_TIMEOUT:
        return 504
    return 502


def _tushare_error(code: GatewayErrorCode, message: str, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        {
            "code": tushare_error_code(code),
            "msg": message,
            "data": {"fields": [], "items": []},
            "meta": {"status": "error", "error_code": code.value},
        },
        status_code=status_code,
    )


app = create_app()
