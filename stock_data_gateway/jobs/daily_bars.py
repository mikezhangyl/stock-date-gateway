from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

from stock_data_gateway.cache.service import ReadThroughQueryService
from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode
from stock_data_gateway.domain.provider import QueryResult
from stock_data_gateway.policies.models import stable_json_hash

JOB_ACTIVE_STATUSES = {"accepted", "running"}
JOB_TERMINAL_STATUSES = {"completed", "completed_with_failures", "failed", "cancelled", "interrupted"}


@dataclass(frozen=True)
class DailyBarsJobRequest:
    job_type: str
    provider: str
    symbols: tuple[str, ...]
    start_date: str
    end_date: str
    trade_dates: tuple[str, ...]
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
        values = _base_request_values(payload, max_symbols=max_symbols, max_batch_size=max_batch_size)
        start_date = _compact_date(payload.get("start_date"))
        end_date = _compact_date(payload.get("end_date"))
        return cls(
            job_type="daily-bars",
            provider=values["provider"],
            symbols=values["symbols"],
            start_date=start_date,
            end_date=end_date,
            trade_dates=tuple(_dates_inclusive(start_date, end_date)),
            include_turnover=values["include_turnover"],
            mode=values["mode"],
            allow_stale=values["allow_stale"],
            force_refresh=values["force_refresh"],
            batch_size=values["batch_size"],
        )

    @classmethod
    def breadth_window(
        cls,
        payload: dict[str, Any],
        *,
        trade_dates: tuple[str, ...],
        max_symbols: int,
        max_batch_size: int,
    ) -> "DailyBarsJobRequest":
        values = _base_request_values(payload, max_symbols=max_symbols, max_batch_size=max_batch_size)
        if not trade_dates:
            raise GatewayError(GatewayErrorCode.INVALID_REQUEST, "breadth-window trade dates must not be empty.")
        return cls(
            job_type="breadth-window",
            provider=values["provider"],
            symbols=values["symbols"],
            start_date=trade_dates[0],
            end_date=trade_dates[-1],
            trade_dates=trade_dates,
            include_turnover=values["include_turnover"],
            mode=values["mode"],
            allow_stale=values["allow_stale"],
            force_refresh=values["force_refresh"],
            batch_size=values["batch_size"],
        )

    def semantic_payload(self) -> dict[str, Any]:
        return {
            "job_type": self.job_type,
            "provider": self.provider,
            "endpoint": "daily",
            "symbols": sorted(self.symbols),
            "start_date": self.start_date,
            "end_date": self.end_date,
            "trade_dates": list(self.trade_dates),
            "include_turnover": self.include_turnover,
            "mode": self.mode,
            "allow_stale": self.allow_stale,
            "force_refresh": self.force_refresh,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_type": self.job_type,
            "provider": self.provider,
            "symbols": list(self.symbols),
            "start_date": self.start_date,
            "end_date": self.end_date,
            "trade_dates": list(self.trade_dates),
            "include_turnover": self.include_turnover,
            "mode": self.mode,
            "allow_stale": self.allow_stale,
            "force_refresh": self.force_refresh,
            "batch_size": self.batch_size,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DailyBarsJobRequest":
        return cls(
            job_type=str(payload["job_type"]),
            provider=str(payload["provider"]),
            symbols=tuple(str(value) for value in payload["symbols"]),
            start_date=str(payload["start_date"]),
            end_date=str(payload["end_date"]),
            trade_dates=tuple(str(value) for value in payload["trade_dates"]),
            include_turnover=bool(payload["include_turnover"]),
            mode=str(payload["mode"]),
            allow_stale=bool(payload["allow_stale"]),
            force_refresh=bool(payload["force_refresh"]),
            batch_size=int(payload["batch_size"]),
        )


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
    cancel_requested: bool = False
    current_symbol: Optional[str] = None
    current_batch_index: int = 0
    batch_count: int = 0
    cache_hit_symbols: int = 0
    upstream_fetch_symbols: int = 0
    stale_cache_symbols: int = 0
    last_progress_at: Optional[str] = None
    last_error: Optional[dict[str, Any]] = None

    @property
    def requested_symbols(self) -> int:
        return len(self.request.symbols)

    def cache_mode(self) -> str:
        if not self.cache_modes:
            return "upstream"
        if len(self.cache_modes) == 1:
            return next(iter(self.cache_modes))
        return "mixed"

    def coverage_payload(self) -> dict[str, Any]:
        expected_pairs = {
            (symbol, trade_date)
            for symbol in self.request.symbols
            for trade_date in self.request.trade_dates
        }
        returned_pairs = {
            (str(row.get("symbol")), _compact_date_value(row.get("trade_date")))
            for row in self.rows
            if row.get("symbol") and row.get("trade_date")
        }
        missing_pairs = sorted(expected_pairs - returned_pairs)
        return {
            "expected_pairs": len(expected_pairs),
            "returned_pairs": len(returned_pairs),
            "missing_pairs": len(missing_pairs),
            "missing_reasons": [
                {
                    "symbol": symbol,
                    "trade_date": _iso_date(trade_date),
                    "reason": "provider_error" if symbol in self.failed_symbols else "no_provider_row",
                }
                for symbol, trade_date in missing_pairs
            ],
        }

    def status_payload(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "job_type": self.request.job_type,
            "provider": self.request.provider,
            "endpoint": "daily",
            "status": self.status,
            "requested_symbols": self.requested_symbols,
            "completed_symbols": len(self.completed_symbols),
            "failed_symbols": len(self.failed_symbols),
            "rows_available": len(self.rows),
            "row_count": len(self.rows),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "cache": {"mode": self.cache_mode()},
            "cache_hit_symbols": self.cache_hit_symbols,
            "upstream_fetch_symbols": self.upstream_fetch_symbols,
            "stale_cache_symbols": self.stale_cache_symbols,
            "current_symbol": self.current_symbol,
            "current_batch_index": self.current_batch_index,
            "batch_count": self.batch_count,
            "symbols_per_minute": self.symbols_per_minute(),
            "last_progress_at": self.last_progress_at,
            "last_error": self.last_error,
            "is_active": self.status in JOB_ACTIVE_STATUSES,
            "is_running": self.status == "running",
            "coverage": self.coverage_payload(),
            "failures": list(self.failures),
        }

    def symbols_per_minute(self) -> float:
        started = _parse_datetime(self.started_at)
        updated = _parse_datetime(self.last_progress_at or self.updated_at)
        if started is None or updated is None:
            return 0.0
        elapsed_minutes = max((updated - started).total_seconds() / 60.0, 0.0)
        if elapsed_minutes == 0:
            return 0.0
        return round((len(self.completed_symbols) + len(self.failed_symbols)) / elapsed_minutes, 2)


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
        self._store = SQLiteJobStore(gateway.store.path)
        self._store.initialize()
        self._jobs_by_id: dict[str, DailyBarsJob] = {}
        self._job_id_by_semantic_hash: dict[str, str] = {}
        self._load_persisted_jobs()

    def create_job(self, request: DailyBarsJobRequest) -> DailyBarsJob:
        semantic_hash = stable_json_hash(request.semantic_payload())
        with self._lock:
            existing_id = self._job_id_by_semantic_hash.get(semantic_hash)
            if existing_id is not None:
                return self._snapshot(self._jobs_by_id[existing_id])
            if self._active_job_count_locked() >= self.max_active_jobs:
                raise GatewayError(GatewayErrorCode.QUEUE_FULL, "Daily-bars job queue is full.")
            now = _utc_now()
            job_id = f"{request.job_type}-{semantic_hash[:16]}"
            job = DailyBarsJob(
                job_id=job_id,
                semantic_hash=semantic_hash,
                request=request,
                status="accepted",
                created_at=now,
                started_at=now,
                updated_at=now,
                batch_count=_batch_count(request.symbols, request.batch_size),
            )
            self._jobs_by_id[job_id] = job
            self._job_id_by_semantic_hash[semantic_hash] = job_id
            self._store.save_job(job)
            accepted_snapshot = self._snapshot(job)

        self._thread_starter(lambda: self._run_job(job_id))
        return accepted_snapshot

    def create_breadth_window_request(
        self,
        payload: dict[str, Any],
        *,
        max_symbols: int,
        max_batch_size: int,
    ) -> DailyBarsJobRequest:
        end_date = _compact_date(payload.get("end_date"))
        lookback = _positive_int(
            payload.get("lookback_trading_days"),
            default=20,
            maximum=job_max_lookback_trading_days(),
            field_name="lookback_trading_days",
        )
        trade_dates = self._resolve_trade_dates(end_date=end_date, lookback_trading_days=lookback)
        return DailyBarsJobRequest.breadth_window(
            payload,
            trade_dates=tuple(trade_dates),
            max_symbols=max_symbols,
            max_batch_size=max_batch_size,
        )

    def list_jobs(
        self,
        *,
        provider: Optional[str] = None,
        endpoint: Optional[str] = None,
        job_type: Optional[str] = None,
        status: Optional[str] = None,
        created_after: Optional[str] = None,
        updated_after: Optional[str] = None,
    ) -> list[DailyBarsJob]:
        with self._lock:
            jobs = [self._snapshot(job) for job in self._jobs_by_id.values()]
        created_after_dt = _parse_datetime(created_after)
        updated_after_dt = _parse_datetime(updated_after)
        filtered = []
        for job in jobs:
            if provider and job.request.provider != provider:
                continue
            if endpoint and endpoint != "daily":
                continue
            if job_type and job.request.job_type != job_type:
                continue
            if status and job.status != status:
                continue
            if created_after_dt is not None and _parse_datetime(job.created_at) <= created_after_dt:
                continue
            if updated_after_dt is not None and _parse_datetime(job.updated_at) <= updated_after_dt:
                continue
            filtered.append(job)
        return sorted(filtered, key=lambda job: job.created_at, reverse=True)

    def get_job(self, job_id: str) -> Optional[DailyBarsJob]:
        with self._lock:
            job = self._jobs_by_id.get(job_id)
            return self._snapshot(job) if job is not None else None

    def cancel_job(self, job_id: str) -> Optional[DailyBarsJob]:
        with self._lock:
            job = self._jobs_by_id.get(job_id)
            if job is None:
                return None
            if job.status in JOB_TERMINAL_STATUSES:
                return self._snapshot(job)
            now = _utc_now()
            job.cancel_requested = True
            job.status = "cancelled"
            job.updated_at = now
            job.last_progress_at = job.last_progress_at or now
            self._store.save_job(job)
            return self._snapshot(job)

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

    def close(self) -> None:
        self._store.close()

    def _load_persisted_jobs(self) -> None:
        for job in self._store.load_jobs():
            if job.status in JOB_ACTIVE_STATUSES:
                now = _utc_now()
                job.status = "interrupted"
                job.updated_at = now
                job.last_error = {
                    "error_code": "GATEWAY_RESTARTED",
                    "message": "Gateway restarted before the job reached a terminal state.",
                }
                self._store.save_job(job)
            self._jobs_by_id[job.job_id] = job
            self._job_id_by_semantic_hash[job.semantic_hash] = job.job_id

    def _run_job(self, job_id: str) -> None:
        self._mark_running(job_id)
        job = self.get_job(job_id)
        if job is None:
            return
        for batch_index, batch in enumerate(_chunks(list(job.request.symbols), job.request.batch_size), start=1):
            for symbol in batch:
                if self._cancel_requested(job_id):
                    return
                self._mark_current_symbol(job_id, symbol, batch_index)
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
            {
                "ts_code": symbol,
                "start_date": request.start_date,
                "end_date": request.end_date,
                "_coverage_dates": list(request.trade_dates),
            },
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
                {
                    "ts_code": symbol,
                    "start_date": request.start_date,
                    "end_date": request.end_date,
                    "_coverage_dates": list(request.trade_dates),
                },
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

    def _resolve_trade_dates(self, *, end_date: str, lookback_trading_days: int) -> list[str]:
        calendar_start = (
            datetime.strptime(end_date, "%Y%m%d") - timedelta(days=max(lookback_trading_days * 3 + 14, 30))
        ).strftime("%Y%m%d")
        params = {"exchange": "SSE", "start_date": calendar_start, "end_date": end_date}
        open_dates = self._resolve_trade_dates_from_provider(params=params, end_date=end_date)
        if len(open_dates) < lookback_trading_days:
            result = self.gateway.query("tushare", "trade_cal", params, fields="cal_date,is_open")
            _raise_query_error(result)
            open_dates = _open_trade_dates(_rows_from_query(result), end_date=end_date)
        if len(open_dates) < lookback_trading_days:
            raise GatewayError(
                GatewayErrorCode.PROVIDER_UNAVAILABLE,
                f"Unable to resolve {lookback_trading_days} trading days ending at {end_date}.",
            )
        return open_dates[-lookback_trading_days:]

    def _resolve_trade_dates_from_provider(self, *, params: dict[str, Any], end_date: str) -> list[str]:
        try:
            response = self.gateway.providers["tushare"].fetch("trade_cal", params, fields="cal_date,is_open")
        except (KeyError, GatewayError):
            return []
        return _open_trade_dates(response.rows(), end_date=end_date)

    def _mark_running(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs_by_id.get(job_id)
            if job is None or job.status == "cancelled":
                return
            job.status = "running"
            job.updated_at = _utc_now()
            self._store.save_job(job)

    def _mark_current_symbol(self, job_id: str, symbol: str, batch_index: int) -> None:
        with self._lock:
            job = self._jobs_by_id.get(job_id)
            if job is None or job.status == "cancelled":
                return
            job.current_symbol = symbol
            job.current_batch_index = batch_index
            job.updated_at = _utc_now()
            self._store.save_job(job)

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
            row_start_index = len(job.rows)
            job.rows.extend(rows)
            if symbol not in job.completed_symbols and symbol not in job.failed_symbols:
                _record_cache_progress(job, cache_modes)
            job.completed_symbols.add(symbol)
            job.cache_modes.update(cache_modes)
            now = _utc_now()
            job.last_progress_at = now
            job.updated_at = now
            self._store.append_rows(job_id, row_start_index, rows)
            self._store.save_job(job)

    def _record_symbol_failure(self, job_id: str, symbol: str, error_code: str, message: str) -> None:
        with self._lock:
            job = self._jobs_by_id.get(job_id)
            if job is None:
                return
            failure = {
                "symbol": symbol,
                "start_date": _iso_date(job.request.start_date),
                "end_date": _iso_date(job.request.end_date),
                "endpoint": "daily",
                "error_code": error_code,
                "message": message,
            }
            job.failed_symbols.add(symbol)
            job.failures.append(failure)
            job.last_error = failure
            now = _utc_now()
            job.last_progress_at = now
            job.updated_at = now
            self._store.save_job(job)

    def _finish(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs_by_id.get(job_id)
            if job is None or job.status == "cancelled":
                return
            if len(job.rows) == 0 and job.failures:
                job.status = "failed"
            elif job.failures:
                job.status = "completed_with_failures"
            else:
                job.status = "completed"
            job.current_symbol = None
            job.updated_at = _utc_now()
            self._store.save_job(job)

    def _cancel_requested(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs_by_id.get(job_id)
            return job is None or job.status == "cancelled" or job.cancel_requested

    def _active_job_count_locked(self) -> int:
        return sum(1 for job in self._jobs_by_id.values() if job.status in JOB_ACTIVE_STATUSES)

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
            cancel_requested=job.cancel_requested,
            current_symbol=job.current_symbol,
            current_batch_index=job.current_batch_index,
            batch_count=job.batch_count,
            cache_hit_symbols=job.cache_hit_symbols,
            upstream_fetch_symbols=job.upstream_fetch_symbols,
            stale_cache_symbols=job.stale_cache_symbols,
            last_progress_at=job.last_progress_at,
            last_error=dict(job.last_error) if job.last_error is not None else None,
        )


class SQLiteJobStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()

    def initialize(self) -> None:
        with self._lock, self._connection:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS market_data_jobs (
                  job_id TEXT PRIMARY KEY,
                  semantic_hash TEXT NOT NULL UNIQUE,
                  job_type TEXT NOT NULL,
                  provider TEXT NOT NULL,
                  endpoint TEXT NOT NULL,
                  status TEXT NOT NULL,
                  state_json TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_market_data_jobs_status
                  ON market_data_jobs(status);

                CREATE INDEX IF NOT EXISTS idx_market_data_jobs_type_updated
                  ON market_data_jobs(job_type, updated_at);

                CREATE TABLE IF NOT EXISTS market_data_job_rows (
                  job_id TEXT NOT NULL,
                  row_index INTEGER NOT NULL,
                  row_json TEXT NOT NULL,
                  PRIMARY KEY (job_id, row_index)
                );
                """
            )

    def load_jobs(self) -> list[DailyBarsJob]:
        with self._lock:
            rows = self._connection.execute("SELECT state_json FROM market_data_jobs").fetchall()
        jobs = []
        for row in rows:
            state = json.loads(row["state_json"])
            job_rows = self.load_rows(str(state["job_id"]))
            jobs.append(_job_from_state(state, job_rows))
        return jobs

    def load_rows(self, job_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT row_json
                FROM market_data_job_rows
                WHERE job_id = ?
                ORDER BY row_index
                """,
                (job_id,),
            ).fetchall()
        return [json.loads(row["row_json"]) for row in rows]

    def save_job(self, job: DailyBarsJob) -> None:
        state_json = json.dumps(_job_to_state(job), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO market_data_jobs (
                  job_id, semantic_hash, job_type, provider, endpoint, status, state_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id)
                DO UPDATE SET
                  status = excluded.status,
                  state_json = excluded.state_json,
                  updated_at = excluded.updated_at
                """,
                (
                    job.job_id,
                    job.semantic_hash,
                    job.request.job_type,
                    job.request.provider,
                    "daily",
                    job.status,
                    state_json,
                    job.created_at,
                    job.updated_at,
                ),
            )

    def append_rows(self, job_id: str, start_index: int, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        with self._lock, self._connection:
            self._connection.executemany(
                """
                INSERT OR REPLACE INTO market_data_job_rows (job_id, row_index, row_json)
                VALUES (?, ?, ?)
                """,
                [
                    (
                        job_id,
                        start_index + offset,
                        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    )
                    for offset, row in enumerate(rows)
                ],
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __del__(self) -> None:
        with suppress(Exception):
            self.close()


def job_max_symbols() -> int:
    return max(1, int(os.getenv("GATEWAY_JOB_MAX_SYMBOLS", "5000")))


def job_max_batch_size() -> int:
    return max(1, int(os.getenv("GATEWAY_JOB_MAX_BATCH_SIZE", "100")))


def job_queue_limit() -> int:
    return max(0, int(os.getenv("GATEWAY_JOB_QUEUE_LIMIT", "2")))


def job_max_lookback_trading_days() -> int:
    return max(1, int(os.getenv("GATEWAY_JOB_MAX_LOOKBACK_TRADING_DAYS", "260")))


def _base_request_values(payload: dict[str, Any], *, max_symbols: int, max_batch_size: int) -> dict[str, Any]:
    provider = str(payload.get("provider") or "tushare")
    if provider != "tushare":
        raise GatewayError(
            GatewayErrorCode.INVALID_REQUEST,
            "Only provider=tushare is supported for daily-bars jobs.",
        )
    mode = str(payload.get("mode") or "read_through_cache")
    if mode != "read_through_cache":
        raise GatewayError(GatewayErrorCode.INVALID_REQUEST, "Only mode=read_through_cache is supported.")
    return {
        "provider": provider,
        "symbols": tuple(_symbols(payload.get("symbols"), max_symbols=max_symbols)),
        "include_turnover": bool(payload.get("include_turnover", False)),
        "mode": mode,
        "allow_stale": bool(payload.get("allow_stale", True)),
        "force_refresh": bool(payload.get("force_refresh", False)),
        "batch_size": _positive_int(
            payload.get("batch_size"),
            default=50,
            maximum=max_batch_size,
            field_name="batch_size",
        ),
    }


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


def _compact_date_value(value: Any) -> str:
    try:
        return _compact_date(value)
    except GatewayError:
        return str(value)


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
    if source in {"external", "external_refresh"}:
        return "upstream"
    if source in {"partial_hit", "mixed"}:
        return "mixed"
    return "upstream"


def _record_cache_progress(job: DailyBarsJob, cache_modes: set[str]) -> None:
    if cache_modes == {"cache"}:
        job.cache_hit_symbols += 1
    elif "stale_cache" in cache_modes and cache_modes <= {"cache", "stale_cache"}:
        job.stale_cache_symbols += 1
    else:
        job.upstream_fetch_symbols += 1


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _batch_count(values: tuple[str, ...], size: int) -> int:
    if not values:
        return 0
    return (len(values) + size - 1) // size


def _dates_inclusive(start_date: str, end_date: str) -> list[str]:
    start = datetime.strptime(start_date, "%Y%m%d")
    end = datetime.strptime(end_date, "%Y%m%d")
    if start > end:
        raise GatewayError(GatewayErrorCode.INVALID_REQUEST, "start_date must be <= end_date.")
    dates = []
    current = start
    while current <= end:
        dates.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return dates


def _truthy_open_flag(value: Any) -> bool:
    return value in (True, 1, "1", "true", "True")


def _open_trade_dates(rows: list[dict[str, Any]], *, end_date: str) -> list[str]:
    return sorted(
        {
            str(row.get("cal_date"))
            for row in rows
            if _truthy_open_flag(row.get("is_open")) and str(row.get("cal_date")) <= end_date
        }
    )


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _start_daemon_thread(target: Callable[[], None]) -> None:
    thread = threading.Thread(target=target, daemon=True)
    thread.start()


def _job_to_state(job: DailyBarsJob) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "semantic_hash": job.semantic_hash,
        "request": job.request.to_dict(),
        "status": job.status,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "updated_at": job.updated_at,
        "failures": list(job.failures),
        "completed_symbols": sorted(job.completed_symbols),
        "failed_symbols": sorted(job.failed_symbols),
        "cache_modes": sorted(job.cache_modes),
        "cancel_requested": job.cancel_requested,
        "current_symbol": job.current_symbol,
        "current_batch_index": job.current_batch_index,
        "batch_count": job.batch_count,
        "cache_hit_symbols": job.cache_hit_symbols,
        "upstream_fetch_symbols": job.upstream_fetch_symbols,
        "stale_cache_symbols": job.stale_cache_symbols,
        "last_progress_at": job.last_progress_at,
        "last_error": job.last_error,
    }


def _job_from_state(state: dict[str, Any], rows: list[dict[str, Any]]) -> DailyBarsJob:
    return DailyBarsJob(
        job_id=str(state["job_id"]),
        semantic_hash=str(state["semantic_hash"]),
        request=DailyBarsJobRequest.from_dict(state["request"]),
        status=str(state["status"]),
        created_at=str(state["created_at"]),
        started_at=str(state["started_at"]),
        updated_at=str(state["updated_at"]),
        rows=rows,
        failures=list(state.get("failures") or []),
        completed_symbols=set(state.get("completed_symbols") or []),
        failed_symbols=set(state.get("failed_symbols") or []),
        cache_modes=set(state.get("cache_modes") or []),
        cancel_requested=bool(state.get("cancel_requested", False)),
        current_symbol=state.get("current_symbol"),
        current_batch_index=int(state.get("current_batch_index") or 0),
        batch_count=int(state.get("batch_count") or 0),
        cache_hit_symbols=int(state.get("cache_hit_symbols") or 0),
        upstream_fetch_symbols=int(state.get("upstream_fetch_symbols") or 0),
        stale_cache_symbols=int(state.get("stale_cache_symbols") or 0),
        last_progress_at=state.get("last_progress_at"),
        last_error=state.get("last_error"),
    )
