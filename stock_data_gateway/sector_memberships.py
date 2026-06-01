from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue
from typing import Any

from stock_data_gateway.core.errors import GatewayError, GatewayErrorCode
from stock_data_gateway.providers.base import ExternalDataProvider

SUPPORTED_SECTOR_TYPES = {"concept"}

_SEED_CONCEPT_SECTORS = (
    {"sector_name": "机器人", "sector_code": "BK1090", "source": "local_seed"},
    {"sector_name": "白酒", "sector_code": "BK0896", "source": "local_seed"},
)


@dataclass(frozen=True)
class SectorMembershipResult:
    rows: list[dict[str, Any]]
    cache_hit: bool
    cache_mode: str
    coverage: dict[str, Any]
    status: str = "ok"
    warning: dict[str, Any] | None = None


class SectorMembershipIndex:
    def __init__(self, *, db_path: Path, providers: dict[str, ExternalDataProvider]) -> None:
        self._providers = providers
        self._connection = sqlite3.connect(Path(db_path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._closed = False
        self._initialize()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._connection.close()
            self._closed = True

    def __del__(self) -> None:
        with suppress(Exception):
            self.close()

    def memberships(
        self,
        *,
        symbols: list[str],
        trade_date: str,
        sector_types: list[str],
        limit_per_symbol: int,
        force_refresh: bool = False,
        sector_universe_limit: int = 80,
        request_timeout_seconds: float = 12.0,
        upstream_timeout_seconds: float = 4.0,
    ) -> SectorMembershipResult:
        deadline_at = time.monotonic() + request_timeout_seconds
        requested = _requested_symbols(symbols)
        supported_sector_types = [sector_type for sector_type in sector_types if sector_type in SUPPORTED_SECTOR_TYPES]
        unsupported_sector_types = [
            sector_type for sector_type in sector_types if sector_type not in SUPPORTED_SECTOR_TYPES
        ]
        if not supported_sector_types:
            rows: list[dict[str, Any]] = []
            coverage = _coverage_payload(
                requested=requested,
                rows=rows,
                missing_reason="sector_type_unavailable",
                sector_types=sector_types,
                unsupported_sector_types=unsupported_sector_types,
                scanned_sector_count=0,
            )
            return SectorMembershipResult(
                rows=rows,
                cache_hit=True,
                cache_mode="cache",
                coverage=coverage,
                warning={
                    "code": "SECTOR_TYPE_UNAVAILABLE",
                    "message": f"Unsupported sector_types: {', '.join(unsupported_sector_types)}",
                },
            )

        rows = self._read_rows(
            requested=requested,
            trade_date=trade_date,
            sector_types=supported_sector_types,
            limit_per_symbol=limit_per_symbol,
        )
        coverage_markers = self._read_coverage_markers(
            symbol_keys=[symbol.key for symbol in requested],
            trade_date=trade_date,
            sector_types=supported_sector_types,
        )
        if not force_refresh and _cache_covers_request(requested, rows, coverage_markers):
            coverage = _coverage_payload(
                requested=requested,
                rows=rows,
                missing_markers=coverage_markers,
                sector_types=sector_types,
                unsupported_sector_types=unsupported_sector_types,
                scanned_sector_count=0,
            )
            return SectorMembershipResult(rows=rows, cache_hit=True, cache_mode="cache", coverage=coverage)

        materialized = self._materialize_reverse_index(
            requested=requested,
            trade_date=trade_date,
            sector_types=supported_sector_types,
            sector_universe_limit=sector_universe_limit,
            deadline_at=deadline_at,
            upstream_timeout_seconds=upstream_timeout_seconds,
        )
        rows = self._read_rows(
            requested=requested,
            trade_date=trade_date,
            sector_types=supported_sector_types,
            limit_per_symbol=limit_per_symbol,
        )
        coverage_markers = self._read_coverage_markers(
            symbol_keys=[symbol.key for symbol in requested],
            trade_date=trade_date,
            sector_types=supported_sector_types,
        )
        coverage = _coverage_payload(
            requested=requested,
            rows=rows,
            missing_markers=coverage_markers,
            missing_reason=materialized.missing_reason,
            sector_types=sector_types,
            unsupported_sector_types=unsupported_sector_types,
            scanned_sector_count=materialized.scanned_sector_count,
            board_results=materialized.board_results,
        )
        warning = materialized.warning
        if unsupported_sector_types:
            warning = {
                "code": "PARTIAL_SECTOR_TYPE_SUPPORT",
                "message": f"Unsupported sector_types: {', '.join(unsupported_sector_types)}",
            }
        return SectorMembershipResult(
            rows=rows,
            cache_hit=False,
            cache_mode="upstream",
            coverage=coverage,
            status=materialized.status,
            warning=warning,
        )

    def _initialize(self) -> None:
        with self._lock, self._connection:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS stock_sector_membership_rows (
                  trade_date TEXT NOT NULL,
                  sector_type TEXT NOT NULL,
                  symbol_key TEXT NOT NULL,
                  name TEXT,
                  sector_name TEXT NOT NULL,
                  sector_code TEXT,
                  pct_change REAL,
                  weight REAL,
                  source TEXT NOT NULL,
                  provider TEXT,
                  membership_source TEXT NOT NULL,
                  refreshed_at TEXT NOT NULL,
                  PRIMARY KEY (trade_date, sector_type, symbol_key, sector_name, source)
                );

                CREATE TABLE IF NOT EXISTS stock_sector_membership_coverage (
                  trade_date TEXT NOT NULL,
                  sector_type TEXT NOT NULL,
                  symbol_key TEXT NOT NULL,
                  reason TEXT NOT NULL,
                  refreshed_at TEXT NOT NULL,
                  PRIMARY KEY (trade_date, sector_type, symbol_key)
                );
                """
            )

    def _read_rows(
        self,
        *,
        requested: list["_RequestedSymbol"],
        trade_date: str,
        sector_types: list[str],
        limit_per_symbol: int,
    ) -> list[dict[str, Any]]:
        if not requested or not sector_types:
            return []
        rows: list[dict[str, Any]] = []
        symbol_by_key = {symbol.key: symbol.output_symbol for symbol in requested}
        with self._lock:
            for symbol in requested:
                fetched_rows = self._connection.execute(
                    """
                    SELECT symbol_key, name, sector_name, sector_type, source, sector_code,
                           trade_date, pct_change, weight, provider, membership_source
                    FROM stock_sector_membership_rows
                    WHERE trade_date = ?
                      AND symbol_key = ?
                      AND sector_type IN ({placeholders})
                    ORDER BY sector_type, sector_name, source
                    LIMIT ?
                    """.format(placeholders=",".join("?" for _ in sector_types)),
                    (trade_date, symbol.key, *sector_types, limit_per_symbol),
                ).fetchall()
                rows.extend(_row_payload(row, symbol_by_key) for row in fetched_rows)
        return rows

    def _read_coverage_markers(
        self,
        *,
        symbol_keys: list[str],
        trade_date: str,
        sector_types: list[str],
    ) -> dict[str, str]:
        if not symbol_keys or not sector_types:
            return {}
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT symbol_key, reason
                FROM stock_sector_membership_coverage
                WHERE trade_date = ?
                  AND symbol_key IN ({symbol_placeholders})
                  AND sector_type IN ({sector_placeholders})
                """.format(
                    symbol_placeholders=",".join("?" for _ in symbol_keys),
                    sector_placeholders=",".join("?" for _ in sector_types),
                ),
                (trade_date, *symbol_keys, *sector_types),
            ).fetchall()
        return {str(row["symbol_key"]): str(row["reason"]) for row in rows}

    def _materialize_reverse_index(
        self,
        *,
        requested: list["_RequestedSymbol"],
        trade_date: str,
        sector_types: list[str],
        sector_universe_limit: int,
        deadline_at: float,
        upstream_timeout_seconds: float,
    ) -> "_MaterializationResult":
        rows_to_write: list[dict[str, Any]] = []
        scanned_sector_count = 0
        target_keys = {symbol.key for symbol in requested}
        covered_keys: set[str] = set()
        visited_sector_names: set[str] = set()
        board_results: list[dict[str, Any]] = []
        issue_reasons: set[str] = set()
        successful_board_count = 0
        empty_board_count = 0

        def scan_sectors(sectors: list[dict[str, Any]], sector_type: str) -> bool:
            nonlocal empty_board_count, scanned_sector_count, successful_board_count
            for sector in sectors:
                if _deadline_expired(deadline_at):
                    issue_reasons.add("timeout")
                    board_results.append(
                        {
                            "sector_name": None,
                            "sector_type": sector_type,
                            "status": "degraded",
                            "reason": "timeout",
                            "row_count": 0,
                        }
                    )
                    return True
                sector_name = str(sector.get("sector_name") or "").strip()
                if not sector_name or sector_name in visited_sector_names:
                    continue
                visited_sector_names.add(sector_name)
                try:
                    provider_rows = self._fetch_sector_constituents(
                        sector_name=sector_name,
                        trade_date=trade_date,
                        timeout_seconds=min(upstream_timeout_seconds, _remaining_seconds(deadline_at)),
                    )
                except GatewayError as error:
                    reason = _reason_for_gateway_error(error)
                    issue_reasons.add(reason)
                    board_results.append(
                        {
                            "sector_name": sector_name,
                            "sector_type": sector_type,
                            "status": "degraded",
                            "reason": reason,
                            "message": error.message,
                            "row_count": 0,
                        }
                    )
                    continue
                scanned_sector_count += 1
                successful_board_count += 1
                normalized_rows = [
                    _membership_row(row, sector=sector, sector_type=sector_type, trade_date=trade_date)
                    for row in provider_rows
                ]
                if not normalized_rows:
                    empty_board_count += 1
                    board_results.append(
                        {
                            "sector_name": sector_name,
                            "sector_type": sector_type,
                            "status": "ok",
                            "reason": "empty_board",
                            "row_count": 0,
                        }
                    )
                else:
                    board_results.append(
                        {
                            "sector_name": sector_name,
                            "sector_type": sector_type,
                            "status": "ok",
                            "reason": "ok",
                            "row_count": len(normalized_rows),
                        }
                    )
                rows_to_write.extend(row for row in normalized_rows if row["symbol_key"])
                covered_keys.update(row["symbol_key"] for row in normalized_rows if row["symbol_key"] in target_keys)
                if target_keys and target_keys.issubset(covered_keys):
                    return True
            return False

        for sector_type in sector_types:
            if scan_sectors(_seed_sectors(sector_type), sector_type):
                continue
            if sector_universe_limit <= 0:
                continue
            try:
                sectors = self._sector_universe(
                    sector_type=sector_type,
                    limit=sector_universe_limit,
                    timeout_seconds=min(upstream_timeout_seconds, _remaining_seconds(deadline_at)),
                )
            except GatewayError as error:
                issue_reasons.add(_reason_for_gateway_error(error))
                board_results.append(
                    {
                        "sector_name": None,
                        "sector_type": sector_type,
                        "status": "degraded",
                        "reason": _reason_for_gateway_error(error),
                        "message": error.message,
                        "row_count": 0,
                    }
                )
                continue
            scan_sectors(sectors, sector_type)

        if rows_to_write:
            self._write_rows(rows_to_write)
        missing_reason = _missing_reason_for_materialization(
            issue_reasons=issue_reasons,
            rows_to_write=rows_to_write,
            covered_keys=covered_keys,
            target_keys=target_keys,
            successful_board_count=successful_board_count,
            empty_board_count=empty_board_count,
        )
        warning = _materialization_warning(
            issue_reasons=issue_reasons,
            rows_to_write=rows_to_write,
            covered_keys=covered_keys,
            target_keys=target_keys,
            successful_board_count=successful_board_count,
            empty_board_count=empty_board_count,
            board_results=board_results,
        )
        status = "degraded" if warning is not None and not target_keys.issubset(covered_keys) else "ok"
        if not rows_to_write and issue_reasons:
            return _MaterializationResult(
                status=status,
                scanned_sector_count=scanned_sector_count,
                warning=warning,
                missing_reason=missing_reason,
                board_results=board_results,
            )
        missing_keys = [symbol.key for symbol in requested if symbol.key not in covered_keys]
        if missing_keys and missing_reason not in {"reverse_index_timeout", "upstream_failed"}:
            self._write_coverage_markers(
                symbol_keys=missing_keys,
                trade_date=trade_date,
                sector_types=sector_types,
                reason=missing_reason,
            )
        if warning is not None:
            return _MaterializationResult(
                status=status,
                scanned_sector_count=scanned_sector_count,
                warning=warning,
                missing_reason=missing_reason,
                board_results=board_results,
            )
        return _MaterializationResult(
            status="ok",
            scanned_sector_count=scanned_sector_count,
            warning=None,
            missing_reason=missing_reason,
            board_results=board_results,
        )

    def _sector_universe(self, *, sector_type: str, limit: int, timeout_seconds: float) -> list[dict[str, Any]]:
        if sector_type != "concept":
            return []
        if limit <= 0:
            return []
        provider = self._providers.get("akshare")
        if provider is None:
            raise GatewayError(GatewayErrorCode.PROVIDER_UNAVAILABLE, "Provider is not registered: akshare")
        try:
            response = _run_with_timeout(
                lambda: provider.fetch("sector_concepts", {"limit": limit}, fields=None),
                timeout_seconds=timeout_seconds,
                timeout_message="AkShare sector universe request timed out.",
            )
        except GatewayError:
            raise
        except Exception as error:
            raise GatewayError(
                GatewayErrorCode.PROVIDER_UNAVAILABLE,
                "AkShare sector universe request failed.",
            ) from error
        return _merge_sectors([], response.rows()[:limit])

    def _fetch_sector_constituents(
        self,
        *,
        sector_name: str,
        trade_date: str,
        timeout_seconds: float,
    ) -> list[dict[str, Any]]:
        provider = self._providers.get("akshare")
        if provider is None:
            raise GatewayError(GatewayErrorCode.PROVIDER_UNAVAILABLE, "Provider is not registered: akshare")
        try:
            response = _run_with_timeout(
                lambda: provider.fetch(
                    "sector_constituents",
                    {"sector_name": sector_name, "trade_date": trade_date, "limit": 5000},
                    fields=None,
                ),
                timeout_seconds=timeout_seconds,
                timeout_message=f"AkShare sector constituent request timed out for {sector_name}.",
            )
        except GatewayError:
            raise
        except Exception as error:
            raise GatewayError(
                GatewayErrorCode.PROVIDER_UNAVAILABLE,
                f"AkShare sector constituent request failed for {sector_name}.",
            ) from error
        return response.rows()

    def _write_rows(self, rows: list[dict[str, Any]]) -> None:
        now = _utc_now()
        with self._lock, self._connection:
            for row in rows:
                self._connection.execute(
                    """
                    INSERT INTO stock_sector_membership_rows (
                      trade_date, sector_type, symbol_key, name, sector_name, sector_code,
                      pct_change, weight, source, provider, membership_source, refreshed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(trade_date, sector_type, symbol_key, sector_name, source)
                    DO UPDATE SET
                      name = excluded.name,
                      sector_code = excluded.sector_code,
                      pct_change = excluded.pct_change,
                      weight = excluded.weight,
                      provider = excluded.provider,
                      membership_source = excluded.membership_source,
                      refreshed_at = excluded.refreshed_at
                    """,
                    (
                        row["trade_date"],
                        row["sector_type"],
                        row["symbol_key"],
                        row.get("name"),
                        row["sector_name"],
                        row.get("sector_code"),
                        row.get("pct_change"),
                        row.get("weight"),
                        row["source"],
                        row.get("provider"),
                        row["membership_source"],
                        now,
                    ),
                )
                self._connection.execute(
                    """
                    DELETE FROM stock_sector_membership_coverage
                    WHERE trade_date = ?
                      AND sector_type = ?
                      AND symbol_key = ?
                    """,
                    (row["trade_date"], row["sector_type"], row["symbol_key"]),
                )

    def _write_coverage_markers(
        self,
        *,
        symbol_keys: list[str],
        trade_date: str,
        sector_types: list[str],
        reason: str,
    ) -> None:
        now = _utc_now()
        with self._lock, self._connection:
            for symbol_key in symbol_keys:
                for sector_type in sector_types:
                    self._connection.execute(
                        """
                        INSERT INTO stock_sector_membership_coverage (
                          trade_date, sector_type, symbol_key, reason, refreshed_at
                        ) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(trade_date, sector_type, symbol_key)
                        DO UPDATE SET reason = excluded.reason, refreshed_at = excluded.refreshed_at
                        """,
                        (trade_date, sector_type, symbol_key, reason, now),
                    )


@dataclass(frozen=True)
class _RequestedSymbol:
    key: str
    output_symbol: str


@dataclass(frozen=True)
class _MaterializationResult:
    status: str
    scanned_sector_count: int
    warning: dict[str, Any] | None
    missing_reason: str
    board_results: list[dict[str, Any]]


def _requested_symbols(symbols: list[str]) -> list[_RequestedSymbol]:
    seen: set[str] = set()
    requested = []
    for symbol in symbols:
        key = _symbol_key(symbol)
        if key in seen:
            continue
        seen.add(key)
        requested.append(_RequestedSymbol(key=key, output_symbol=str(symbol).strip().upper()))
    return requested


def _symbol_key(symbol: Any) -> str:
    text = str(symbol or "").strip().upper()
    if "." in text:
        text = text.split(".", 1)[0]
    return "".join(character for character in text if character.isalnum())


def _seed_sectors(sector_type: str) -> list[dict[str, Any]]:
    if sector_type != "concept":
        return []
    return [dict(row) for row in _SEED_CONCEPT_SECTORS]


def _merge_sectors(seed_rows: list[dict[str, Any]], provider_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = []
    seen = set()
    for row in [*seed_rows, *provider_rows]:
        sector_name = str(row.get("sector_name") or "").strip()
        if not sector_name or sector_name in seen:
            continue
        seen.add(sector_name)
        merged.append(dict(row))
    return merged


def _membership_row(
    row: dict[str, Any],
    *,
    sector: dict[str, Any],
    sector_type: str,
    trade_date: str,
) -> dict[str, Any]:
    symbol_key = _symbol_key(row.get("symbol") or row.get("stock_code") or row.get("code"))
    source = str(row.get("source") or row.get("provider") or "akshare")
    return {
        "symbol_key": symbol_key,
        "name": row.get("name") or row.get("stock_name"),
        "sector_name": row.get("sector_name") or sector.get("sector_name"),
        "sector_type": sector_type,
        "source": source,
        "sector_code": row.get("sector_code") or sector.get("sector_code"),
        "trade_date": row.get("trade_date") or trade_date,
        "pct_change": row.get("pct_change"),
        "weight": row.get("weight"),
        "provider": row.get("provider") or source,
        "membership_source": "reverse_index",
    }


def _row_payload(row: sqlite3.Row, symbol_by_key: dict[str, str]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "symbol": symbol_by_key.get(str(row["symbol_key"]), str(row["symbol_key"])),
        "name": row["name"],
        "sector_name": row["sector_name"],
        "sector_type": row["sector_type"],
        "source": row["source"],
        "sector_code": row["sector_code"],
        "trade_date": row["trade_date"],
        "pct_change": row["pct_change"],
        "provider": row["provider"],
        "membership_source": row["membership_source"],
    }
    if row["weight"] is not None:
        payload["weight"] = row["weight"]
    return {key: value for key, value in payload.items() if value is not None}


def _cache_covers_request(
    requested: list[_RequestedSymbol],
    rows: list[dict[str, Any]],
    coverage_markers: dict[str, str],
) -> bool:
    covered = {_symbol_key(row.get("symbol")) for row in rows}
    known = covered | set(coverage_markers)
    return all(symbol.key in known for symbol in requested)


def _coverage_payload(
    *,
    requested: list[_RequestedSymbol],
    rows: list[dict[str, Any]],
    sector_types: list[str],
    unsupported_sector_types: list[str],
    scanned_sector_count: int,
    missing_markers: dict[str, str] | None = None,
    missing_reason: str = "no_membership_rows",
    board_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    covered_keys = {_symbol_key(row.get("symbol")) for row in rows}
    missing_markers = missing_markers or {}
    missing_symbols = []
    for symbol in requested:
        if symbol.key in covered_keys:
            continue
        missing_symbols.append(
            {
                "symbol": symbol.output_symbol,
                "reason": missing_markers.get(symbol.key, missing_reason),
            }
        )
    return {
        "requested_symbols": [symbol.output_symbol for symbol in requested],
        "covered_symbols": [symbol.output_symbol for symbol in requested if symbol.key in covered_keys],
        "missing_symbols": missing_symbols,
        "sector_types": sector_types,
        "unsupported_sector_types": unsupported_sector_types,
        "scanned_sector_count": scanned_sector_count,
        "board_results": board_results or [],
    }


def _missing_reason_for_materialization(
    *,
    issue_reasons: set[str],
    rows_to_write: list[dict[str, Any]],
    covered_keys: set[str],
    target_keys: set[str],
    successful_board_count: int,
    empty_board_count: int,
) -> str:
    if "timeout" in issue_reasons:
        return "reverse_index_timeout"
    if "upstream_failed" in issue_reasons and not rows_to_write:
        return "upstream_failed"
    if successful_board_count > 0 and successful_board_count == empty_board_count:
        return "empty_board"
    if target_keys and not target_keys.issubset(covered_keys):
        return "symbol_uncovered"
    return "no_membership_rows"


def _materialization_warning(
    *,
    issue_reasons: set[str],
    rows_to_write: list[dict[str, Any]],
    covered_keys: set[str],
    target_keys: set[str],
    successful_board_count: int,
    empty_board_count: int,
    board_results: list[dict[str, Any]],
) -> dict[str, Any] | None:
    details = board_results[:5]
    if "timeout" in issue_reasons:
        return {
            "code": GatewayErrorCode.REQUEST_TIMEOUT.value,
            "message": "Stock sector membership reverse index materialization timed out.",
            "details": details,
        }
    if "upstream_failed" in issue_reasons and not rows_to_write:
        return {
            "code": GatewayErrorCode.PROVIDER_UNAVAILABLE.value,
            "message": "Unable to materialize stock sector membership reverse index.",
            "details": details,
        }
    if successful_board_count > 0 and successful_board_count == empty_board_count:
        return {
            "code": "EMPTY_BOARD",
            "message": "Seed sector boards returned no constituent rows.",
            "details": details,
        }
    if target_keys and not target_keys.issubset(covered_keys):
        return {
            "code": "NO_MEMBERSHIP_COVERAGE",
            "message": "No sector membership rows found for one or more requested symbols.",
            "details": details,
        }
    if issue_reasons:
        return {
            "code": "PARTIAL_REVERSE_INDEX_MATERIALIZATION",
            "message": "Some sector boards could not be scanned.",
            "details": details,
        }
    return None


def _reason_for_gateway_error(error: GatewayError) -> str:
    if error.code == GatewayErrorCode.REQUEST_TIMEOUT:
        return "timeout"
    return "upstream_failed"


def _remaining_seconds(deadline_at: float) -> float:
    return max(0.0, deadline_at - time.monotonic())


def _deadline_expired(deadline_at: float) -> bool:
    return _remaining_seconds(deadline_at) <= 0


def _run_with_timeout(
    operation: Callable[[], Any],
    *,
    timeout_seconds: float,
    timeout_message: str,
) -> Any:
    if timeout_seconds <= 0:
        raise GatewayError(GatewayErrorCode.REQUEST_TIMEOUT, timeout_message)

    result_queue: Queue[tuple[str, Any]] = Queue(maxsize=1)

    def target() -> None:
        try:
            result_queue.put(("ok", operation()), block=False)
        except Exception as error:
            result_queue.put(("error", error), block=False)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout_seconds)
    if thread.is_alive():
        raise GatewayError(GatewayErrorCode.REQUEST_TIMEOUT, timeout_message)

    status, result = result_queue.get_nowait()
    if status == "error":
        if isinstance(result, GatewayError):
            raise result
        raise result
    return result


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
