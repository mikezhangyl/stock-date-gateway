from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable, Optional

from stock_data_gateway.cache.service import ReadThroughQueryService
from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode
from stock_data_gateway.domain.provider import QueryResult
from stock_data_gateway.policies.models import stable_json_hash

JOB_TERMINAL_STATUSES = {"completed", "completed_with_failures", "failed", "cancelled"}


@dataclass(frozen=True)
class DailyBarsJobRequest:
    provider: str
    symbols: tuple[str, ...]
    start_date: str
    end_date: str
    include_turnover: bool
    mode: str
    allow_stale: bool
    force_refresh: bool
    batch_size: int

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
        *,
        max_symbols: int,
        max_batch_size: int,
    ) -> "DailyBarsJobRequest":
        provider = str(payload.get("provider") or "tushare")
        if provider != "tushare":
            raise GatewayError(
                GatewayErrorCode.INVALID_REQUEST,
                "Only provider=tushare is supported for daily-bars jobs.",
            )
        mode = str(payload.get("mode") or "read_through_cache")
        if mode != "read_through_cache":
            raise GatewayError(GatewayErrorCode.INVALID_REQUEST, "Only mode=read_through_cache is supported.")
        symbols = _symbols(payload.get("symbols"), max_symbols=max_symbols)
        batch_size = _positive_int(
            payload.get("batch_size"),
            default=50,
            maximum=max_batch_size,
            field_name="batch_size",
        )
        return cls(
            provider=provider,
            symbols=tuple(symbols),
            start_date=_compact_date(payload.get("start_date")),
            end_date=_compact_date(payload.get("end_date")),
            include_turnover=bool(payload.get("include_turnover", False)),
            mode=mode,
            allow_stale=bool(payload.get("allow_stale", True)),
            force_refresh=bool(payload.get("force_refresh", False)),
            batch_size=batch_size,
        )

    def semantic_payload(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "endpoint": "daily",
            "symbols": sorted(self.symbols),
            "start_date": self.start_date,
            "end_date": self.end_date,
            "include_turnover": self.include_turnover,
            "mode": self.mode,
            "allow_stale": self.allow_stale,
            "force_refresh": self.force_refresh,
        }


@dataclass
class DailyBarsJob:
    job_id: str
    semantic_hash: str
    request: DailyBarsJobRequest
    status: str
    created_at: str
    started_at: str
    updated_at: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)
    completed_symbols: set[str] = field(default_factory=set)
    failed_symbols: set[str] = field(default_factory=set)
    cache_modes: set[str] = field(default_factory=set)

    @property
    def requested_symbols(self) -> int:
        return len(self.request.symbols)

    def status_payload(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "requested_symbols": self.requested_symbols,
            "completed_symbols": len(self.completed_symbols),
            "failed_symbols": len(self.failed_symbols),
            "rows_available": len(self.rows),
            "row_count": len(self.rows),
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "cache": {"mode": self.cache_mode()},
            "failures": list(self.failures),
        }

    def cache_mode(self) -> str:
        if not self.cache_modes:
            return "upstream"
        if len(self.cache_modes) == 1:
            return next(iter(self.cache_modes))
        return "mixed"


ThreadStarter = Callable[[Callable[[], None]], None]


class DailyBarsJobManager:
    def __init__(
        self,
        gateway: ReadThroughQueryService,
        *,
        max_active_jobs: int,
        thread_starter: Optional[ThreadStarter] = None,
    ) -> None:
        self.gateway = gateway
        self.max_active_jobs = max_active_jobs
        self._thread_starter = thread_starter or _start_daemon_thread
        self._lock = threading.RLock()
        self._jobs_by_id: dict[str, DailyBarsJob] = {}
        self._job_id_by_semantic_hash: dict[str, str] = {}

    def create_job(self, request: DailyBarsJobRequest) -> DailyBarsJob:
        semantic_hash = stable_json_hash(request.semantic_payload())
        with self._lock:
            existing_id = self._job_id_by_semantic_hash.get(semantic_hash)
            if existing_id is not None:
                return self._snapshot(self._jobs_by_id[existing_id])
            if self._active_job_count_locked() >= self.max_active_jobs:
                raise GatewayError(GatewayErrorCode.QUEUE_FULL, "Daily-bars job queue is full.")
            now = _utc_now()
            job_id = f"daily-bars-{semantic_hash[:16]}"
            job = DailyBarsJob(
                job_id=job_id,
                semantic_hash=semantic_hash,
                request=request,
                status="accepted",
                created_at=now,
                started_at=now,
                updated_at=now,
            )
            self._jobs_by_id[job_id] = job
            self._job_id_by_semantic_hash[semantic_hash] = job_id
            accepted_snapshot = self._snapshot(job)

        self._thread_starter(lambda: self._run_job(job_id))
        return accepted_snapshot

    def get_job(self, job_id: str) -> Optional[DailyBarsJob]:
        with self._lock:
            job = self._jobs_by_id.get(job_id)
            return self._snapshot(job) if job is not None else None

    def rows(
        self,
        job_id: str,
        *,
        offset: int = 0,
        limit: int = 10000,
    ) -> Optional[tuple[DailyBarsJob, list[dict[str, Any]]]]:
        with self._lock:
            job = self._jobs_by_id.get(job_id)
            if job is None:
                return None
            snapshot = self._snapshot(job)
            return snapshot, list(job.rows[offset : offset + limit])

    def _run_job(self, job_id: str) -> None:
        self._mark_running(job_id)
        job = self.get_job(job_id)
        if job is None:
            return
        for batch in _chunks(list(job.request.symbols), job.request.batch_size):
            for symbol in batch:
                try:
                    rows, cache_modes = self._fetch_symbol_rows(job.request, symbol)
                    self._record_symbol_success(job_id, symbol, rows, cache_modes)
                except GatewayError as error:
                    self._record_symbol_failure(job_id, symbol, error.code.value, error.message)
                except Exception as error:
                    self._record_symbol_failure(job_id, symbol, GatewayErrorCode.UNKNOWN_ERROR.value, str(error))
        self._finish(job_id)

    def _fetch_symbol_rows(
        self,
        request: DailyBarsJobRequest,
        symbol: str,
    ) -> tuple[list[dict[str, Any]], set[str]]:
        price_result = self.gateway.query(
            "tushare",
            "daily",
            {"ts_code": symbol, "start_date": request.start_date, "end_date": request.end_date},
            fields="ts_code,trade_date,open,high,low,close,pre_close,vol,amount",
            force_refresh=request.force_refresh,
            allow_stale_on_error=request.allow_stale,
        )
        _raise_query_error(price_result)
        cache_modes = {_source_to_cache_mode(str(price_result.meta.get("source") or ""))}
        price_rows = _rows_from_query(price_result)
        turnover_by_key: dict[tuple[str, str], Any] = {}
        if request.include_turnover:
            turnover_result = self.gateway.query(
                "tushare",
                "daily_basic",
                {"ts_code": symbol, "start_date": request.start_date, "end_date": request.end_date},
                fields="ts_code,trade_date,turnover_rate",
                force_refresh=request.force_refresh,
                allow_stale_on_error=request.allow_stale,
            )
            _raise_query_error(turnover_result)
            cache_modes.add(_source_to_cache_mode(str(turnover_result.meta.get("source") or "")))
            for row in _rows_from_query(turnover_result):
                turnover_by_key[(str(row.get("ts_code")), str(row.get("trade_date")))] = row.get("turnover_rate")

        rows = []
        for row in price_rows:
            ts_code = str(row.get("ts_code"))
            trade_date = str(row.get("trade_date"))
            normalized = {
                "symbol": ts_code,
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
            if request.include_turnover:
                normalized["turnover_rate"] = turnover_by_key.get((ts_code, trade_date))
            rows.append(normalized)
        return rows, cache_modes

    def _mark_running(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs_by_id.get(job_id)
            if job is None:
                return
            job.status = "running"
            job.updated_at = _utc_now()

    def _record_symbol_success(
        self,
        job_id: str,
        symbol: str,
        rows: list[dict[str, Any]],
        cache_modes: set[str],
    ) -> None:
        with self._lock:
            job = self._jobs_by_id.get(job_id)
            if job is None:
                return
            job.rows.extend(rows)
            job.completed_symbols.add(symbol)
            job.cache_modes.update(cache_modes)
            job.updated_at = _utc_now()

    def _record_symbol_failure(self, job_id: str, symbol: str, error_code: str, message: str) -> None:
        with self._lock:
            job = self._jobs_by_id.get(job_id)
            if job is None:
                return
            job.failed_symbols.add(symbol)
            job.failures.append(
                {
                    "symbol": symbol,
                    "start_date": _iso_date(job.request.start_date),
                    "end_date": _iso_date(job.request.end_date),
                    "endpoint": "daily",
                    "error_code": error_code,
                    "message": message,
                }
            )
            job.updated_at = _utc_now()

    def _finish(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs_by_id.get(job_id)
            if job is None:
                return
            if len(job.rows) == 0 and job.failures:
                job.status = "failed"
            elif job.failures:
                job.status = "completed_with_failures"
            else:
                job.status = "completed"
            job.updated_at = _utc_now()

    def _active_job_count_locked(self) -> int:
        return sum(1 for job in self._jobs_by_id.values() if job.status not in JOB_TERMINAL_STATUSES)

    def _snapshot(self, job: DailyBarsJob) -> DailyBarsJob:
        return DailyBarsJob(
            job_id=job.job_id,
            semantic_hash=job.semantic_hash,
            request=job.request,
            status=job.status,
            created_at=job.created_at,
            started_at=job.started_at,
            updated_at=job.updated_at,
            rows=list(job.rows),
            failures=list(job.failures),
            completed_symbols=set(job.completed_symbols),
            failed_symbols=set(job.failed_symbols),
            cache_modes=set(job.cache_modes),
        )


def job_max_symbols() -> int:
    return max(1, int(os.getenv("GATEWAY_JOB_MAX_SYMBOLS", "5000")))


def job_max_batch_size() -> int:
    return max(1, int(os.getenv("GATEWAY_JOB_MAX_BATCH_SIZE", "100")))


def job_queue_limit() -> int:
    return max(0, int(os.getenv("GATEWAY_JOB_QUEUE_LIMIT", "2")))


def _symbols(value: Any, *, max_symbols: int) -> list[str]:
    if isinstance(value, str):
        raw_values = value.split(",")
    elif isinstance(value, list):
        raw_values = value
    else:
        raise GatewayError(GatewayErrorCode.INVALID_REQUEST, "symbols must be a list or comma-separated string.")
    symbols: list[str] = []
    seen: set[str] = set()
    for item in raw_values:
        symbol = str(item).strip()
        if symbol and symbol not in seen:
            symbols.append(symbol)
            seen.add(symbol)
    if not symbols:
        raise GatewayError(GatewayErrorCode.INVALID_REQUEST, "symbols must not be empty.")
    if len(symbols) > max_symbols:
        raise GatewayError(GatewayErrorCode.INVALID_REQUEST, f"symbols exceeds maximum job symbols: {max_symbols}.")
    return symbols


def _compact_date(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return text.replace("-", "")
    if len(text) == 8 and text.isdigit():
        return text
    raise GatewayError(GatewayErrorCode.INVALID_REQUEST, "date must be YYYY-MM-DD or YYYYMMDD.")


def _iso_date(value: Any) -> Any:
    if value in (None, ""):
        return value
    text = str(value)
    if len(text) == 8 and text.isdigit():
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
    return text


def _positive_int(value: Any, *, default: int, maximum: int, field_name: str) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise GatewayError(GatewayErrorCode.INVALID_REQUEST, f"{field_name} must be an integer.") from error
    if parsed <= 0:
        raise GatewayError(GatewayErrorCode.INVALID_REQUEST, f"{field_name} must be positive.")
    return min(parsed, maximum)


def _rows_from_query(result: QueryResult) -> list[dict[str, Any]]:
    return [dict(zip(result.data.fields, item)) for item in result.data.items]


def _raise_query_error(result: QueryResult) -> None:
    if result.meta.get("status") != "error":
        return
    code = GatewayErrorCode(result.meta.get("error_code", GatewayErrorCode.UNKNOWN_ERROR.value))
    message = str(result.meta.get("error_message") or code.value)
    raise GatewayError(code, message)


def _source_to_cache_mode(source: str) -> str:
    if source == "cache":
        return "cache"
    if source == "stale_cache":
        return "stale_cache"
    if source in {"partial_hit", "mixed"}:
        return "mixed"
    return "upstream"


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _start_daemon_thread(target: Callable[[], None]) -> None:
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
