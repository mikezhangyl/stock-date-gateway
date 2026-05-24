from __future__ import annotations

import threading
from typing import Any, Optional

from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode
from stock_data_gateway.domain.provider import QueryResult, ResponseData
from stock_data_gateway.policies.models import CoverageRequirement, stable_json_hash
from stock_data_gateway.policies.registry import PolicyRegistry
from stock_data_gateway.providers.base import ExternalDataProvider

from .sqlite_store import CacheRecord, SQLiteCacheStore


class ReadThroughQueryService:
    def __init__(
        self,
        providers: dict[str, ExternalDataProvider],
        policies: PolicyRegistry,
        store: SQLiteCacheStore,
        offline_mode: bool = False,
    ) -> None:
        self.providers = providers
        self.policies = policies
        self.store = store
        self.offline_mode = offline_mode
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def query(
        self,
        provider_name: str,
        endpoint: str,
        params: dict[str, Any],
        fields: Optional[str] = None,
    ) -> QueryResult:
        safe_params = {key: value for key, value in params.items() if key != "token"}
        params_hash = stable_json_hash(
            {"provider": provider_name, "endpoint": endpoint, "params": safe_params, "fields": fields}
        )
        try:
            if endpoint == "cyq_chips":
                safe_params = self._with_cyq_trade_dates(provider_name, safe_params)
                params_hash = stable_json_hash(
                    {"provider": provider_name, "endpoint": endpoint, "params": safe_params, "fields": fields}
                )
            policy = self.policies.get(provider_name, endpoint)
            requirements = policy.build_requirements(safe_params, fields)
            requested_fields = policy.requested_fields(fields)
            existing_records = [self.store.get_current(requirement.key) for requirement in requirements]
            missing = [requirement for requirement, record in zip(requirements, existing_records) if record is None]
            if missing and self.offline_mode:
                return self._error_result(
                    provider_name,
                    endpoint,
                    params_hash,
                    GatewayErrorCode.OFFLINE_MISS,
                    "offline cache miss",
                )

            fetched_count = 0
            records: list[CacheRecord] = []
            for requirement, existing in zip(requirements, existing_records):
                if existing is not None:
                    records.append(existing)
                    continue
                fetched = self._fetch_and_store(provider_name, endpoint, requirement, ",".join(policy.default_fields))
                fetched_count += 1
                records.append(fetched)

            rows = [row for record in records if record.payload_kind == "ROWS" for row in record.rows]
            data = ResponseData(fields=requested_fields, items=_project_rows(rows, requested_fields))
            if fetched_count == 0:
                source = "cache"
            elif fetched_count == len(requirements):
                source = "external"
            else:
                source = "partial_hit"
            self.store.write_request_audit(
                provider=provider_name,
                endpoint=endpoint,
                params_hash=params_hash,
                source=source,
                cache_hit=fetched_count == 0,
                row_count=len(data.items),
                status="ok",
            )
            return QueryResult(
                data=data,
                meta={
                    "provider": provider_name,
                    "endpoint": endpoint,
                    "source": source,
                    "cache_hit": fetched_count == 0,
                    "fetched_external": fetched_count > 0,
                    "row_count": len(data.items),
                    "status": "ok",
                },
            )
        except GatewayError as error:
            return self._error_result(provider_name, endpoint, params_hash, error.code, error.message)
        except ValueError as error:
            return self._error_result(
                provider_name,
                endpoint,
                params_hash,
                GatewayErrorCode.INVALID_REQUEST,
                str(error),
            )

    def _with_cyq_trade_dates(self, provider_name: str, params: dict[str, Any]) -> dict[str, Any]:
        if params.get("trade_date") or not params.get("start_date") or not params.get("end_date"):
            return params
        trade_cal = self.query(
            provider_name,
            "trade_cal",
            {
                "exchange": params.get("exchange", "SSE"),
                "start_date": params["start_date"],
                "end_date": params["end_date"],
            },
            fields="cal_date,is_open",
        )
        if trade_cal.meta.get("status") != "ok":
            raise GatewayError(
                GatewayErrorCode.PROVIDER_UNAVAILABLE,
                "Unable to resolve trading days for cyq_chips.",
            )
        cal_date_index = trade_cal.data.fields.index("cal_date")
        is_open_index = trade_cal.data.fields.index("is_open") if "is_open" in trade_cal.data.fields else None
        trade_dates = [
            str(item[cal_date_index])
            for item in trade_cal.data.items
            if is_open_index is None or str(item[is_open_index]) == "1"
        ]
        expanded = dict(params)
        expanded["_coverage_dates"] = trade_dates
        return expanded

    def _fetch_and_store(
        self,
        provider_name: str,
        endpoint: str,
        requirement: CoverageRequirement,
        fields: Optional[str],
    ) -> CacheRecord:
        lock = self._lock_for(requirement.key.cache_key())
        with lock:
            cached = self.store.get_current(requirement.key)
            if cached is not None:
                return cached
            if not self.store.acquire_fetch_lease(requirement.key):
                cached_after_lease = self.store.get_current(requirement.key)
                if cached_after_lease is not None:
                    return cached_after_lease
                raise GatewayError(
                    GatewayErrorCode.PROVIDER_UNAVAILABLE,
                    "cache fetch lease is held by another request",
                )
            try:
                response = self.providers[provider_name].fetch(endpoint, requirement.fetch_params, fields=fields)
                rows = response.rows()
                payload_kind = "ROWS" if rows else "PROVISIONAL_NO_DATA"
                return self.store.write_entry(
                    requirement.key,
                    rows=rows,
                    source_params=requirement.fetch_params,
                    payload_kind=payload_kind,
                )
            except GatewayError as error:
                self.store.write_provider_event(
                    provider_name,
                    endpoint,
                    {"status": "failed", "error_code": error.code.value},
                )
                raise
            finally:
                self.store.release_fetch_lease(requirement.key)

    def _lock_for(self, key: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock

    def close(self) -> None:
        self.store.close()

    def _error_result(
        self,
        provider_name: str,
        endpoint: str,
        params_hash: str,
        code: GatewayErrorCode,
        message: str,
    ) -> QueryResult:
        self.store.write_request_audit(
            provider=provider_name,
            endpoint=endpoint,
            params_hash=params_hash,
            source="error",
            cache_hit=False,
            row_count=0,
            status="error",
            error_code=code.value,
        )
        return QueryResult(
            data=ResponseData(fields=[], items=[]),
            meta={
                "provider": provider_name,
                "endpoint": endpoint,
                "source": "error",
                "cache_hit": False,
                "fetched_external": False,
                "row_count": 0,
                "status": "error",
                "error_code": code.value,
                "error_message": message,
            },
        )


def _project_rows(rows: list[dict[str, Any]], requested_fields: list[str]) -> list[list[Any]]:
    items: list[list[Any]] = []
    for row in rows:
        missing = [field for field in requested_fields if field not in row]
        if missing:
            raise GatewayError(GatewayErrorCode.SCHEMA_CHANGED, f"Cached payload missing fields: {','.join(missing)}")
        items.append([row[field] for field in requested_fields])
    return items
