from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import Body, FastAPI, Query, Request
from fastapi.responses import JSONResponse

from stock_data_gateway.cache.service import ReadThroughQueryService
from stock_data_gateway.cache.sqlite_store import SQLiteCacheStore
from stock_data_gateway.core.config import Settings
from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode, tushare_error_code
from stock_data_gateway.domain.provider import QueryResult
from stock_data_gateway.jobs.daily_bars import (
    DailyBarsJobManager,
    DailyBarsJobRequest,
    job_max_batch_size,
    job_max_symbols,
    job_queue_limit,
)
from stock_data_gateway.policies.registry import create_default_policy_registry
from stock_data_gateway.providers.akshare.adapter import AkshareProvider
from stock_data_gateway.providers.eastmoney.adapter import EastmoneyProvider
from stock_data_gateway.providers.tushare.adapter import TushareProvider
from stock_data_gateway.providers.tushare.client import TushareMarketDataClient

JSON_BODY = Body(default_factory=dict)
_FETCH_SEMAPHORE = threading.BoundedSemaphore(int(os.getenv("GATEWAY_FETCH_CONCURRENCY", "4")))


def create_app(gateway: Optional[ReadThroughQueryService] = None) -> FastAPI:
    app = FastAPI(title="Local Market Data Gateway")
    app.state.gateway = gateway
    app.state.daily_bars_jobs = None

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
                    "meta": _job_meta(job_id=job.job_id, status=job.status, cache_mode=job.cache_mode()),
                },
                status_code=202,
            )
        except GatewayError as error:
            return _normalized_exception(error)

    @app.get("/api/v1/market-data/jobs/{job_id}")
    def get_daily_bars_job(job_id: str) -> JSONResponse:
        job = _daily_bars_jobs(app).get_job(job_id)
        if job is None:
            return _normalized_error("JOB_NOT_FOUND", f"Unknown job_id: {job_id}", 404)
        return JSONResponse(
            {
                "data": job.status_payload(),
                "meta": _job_meta(job_id=job.job_id, status=job.status, cache_mode=job.cache_mode()),
            }
        )

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
                    **_job_meta(job_id=job.job_id, status=job.status, cache_mode=job.cache_mode()),
                    "pagination": {
                        "offset": offset,
                        "limit": resolved_limit,
                        "returned": len(rows),
                        "total": len(job.rows),
                    },
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
) -> JSONResponse:
    try:
        with _fetch_slot():
            response = gateway.providers[provider_name].fetch(endpoint, params, fields=None)
        return _normalized_success(provider_name, endpoint, response.rows(), [], cache_mode="upstream")
    except GatewayError as error:
        payload = _normalized_payload(provider_name, endpoint, [], cache_mode="stale_cache", cache_hit=False)
        payload["meta"]["status"] = "degraded"
        payload["meta"]["warning"] = {"code": error.code.value, "message": error.message}
        return JSONResponse(payload)
    except KeyError:
        return _normalized_error(
            GatewayErrorCode.PROVIDER_UNAVAILABLE.value,
            f"Provider is not registered: {provider_name}",
            503,
        )


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


def _job_meta(*, job_id: str, status: str, cache_mode: str) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "status": status,
        "provider": "tushare",
        "endpoint": "daily",
        "cache": {"mode": cache_mode},
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
