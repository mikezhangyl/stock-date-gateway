from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from stock_data_gateway.cache.service import ReadThroughQueryService
from stock_data_gateway.cache.sqlite_store import SQLiteCacheStore
from stock_data_gateway.core.config import Settings
from stock_data_gateway.core.errors import GatewayErrorCode, tushare_error_code
from stock_data_gateway.policies.registry import create_default_policy_registry
from stock_data_gateway.providers.tushare.adapter import TushareProvider
from stock_data_gateway.providers.tushare.client import TushareMarketDataClient


def create_app(gateway: Optional[ReadThroughQueryService] = None) -> FastAPI:
    app = FastAPI(title="Local Market Data Gateway")
    app.state.gateway = gateway

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

        result = _gateway(app).query("tushare", endpoint, params, fields)
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

    return app


def _gateway(app: FastAPI) -> ReadThroughQueryService:
    if app.state.gateway is None:
        app.state.gateway = create_default_gateway()
    return app.state.gateway


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
    return ReadThroughQueryService({"tushare": provider}, policies, store, offline_mode=resolved_settings.offline_mode)


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
