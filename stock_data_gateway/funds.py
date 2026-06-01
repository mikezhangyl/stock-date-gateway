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

_TUSHARE_PROFILE_FIELDS = "ts_code,name,fund_type,market,found_date,list_date"
_TUSHARE_HOLDING_FIELDS = "ts_code,ann_date,end_date,symbol,mkv,amount,stk_mkv_ratio,stk_float_ratio,stk_code,stk_name"
_HOLDING_FETCH_LIMIT = 100


@dataclass(frozen=True)
class FundDataResult:
    rows: list[dict[str, Any]]
    cache_hit: bool
    cache_mode: str
    coverage: dict[str, Any]
    status: str = "ok"
    warning: dict[str, Any] | None = None


class FundDataService:
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

    def profile(
        self,
        *,
        fund_code: str,
        force_refresh: bool = False,
        request_timeout_seconds: float = 12.0,
        upstream_timeout_seconds: float = 4.0,
    ) -> FundDataResult:
        code = _normalized_fund_code(fund_code)
        deadline_at = time.monotonic() + request_timeout_seconds
        attempts: list[dict[str, Any]] = []
        cached = self._read_profile(code)
        if cached and not force_refresh:
            attempts.append({"provider": "cache", "status": "hit"})
            return FundDataResult(
                rows=cached,
                cache_hit=True,
                cache_mode="cache",
                coverage=_profile_coverage(code, cached, attempts),
            )
        attempts.append({"provider": "cache", "status": "miss" if not cached else "bypass"})

        rows = self._try_fetch_profile(
            code=code,
            attempts=attempts,
            deadline_at=deadline_at,
            upstream_timeout_seconds=upstream_timeout_seconds,
        )
        if rows:
            self._write_profiles(rows)
            return FundDataResult(
                rows=rows,
                cache_hit=False,
                cache_mode="upstream",
                coverage=_profile_coverage(code, rows, attempts),
            )
        if cached:
            attempts.append({"provider": "cache", "status": "stale_hit"})
            return FundDataResult(
                rows=cached,
                cache_hit=True,
                cache_mode="stale_cache",
                coverage=_profile_coverage(code, cached, attempts),
                status="degraded",
                warning=_fund_warning("profile", attempts),
            )
        return FundDataResult(
            rows=[],
            cache_hit=False,
            cache_mode="upstream",
            coverage=_profile_coverage(code, [], attempts),
            status="degraded",
            warning=_fund_warning("profile", attempts),
        )

    def holdings(
        self,
        *,
        fund_code: str,
        limit: int = 10,
        force_refresh: bool = False,
        request_timeout_seconds: float = 12.0,
        upstream_timeout_seconds: float = 4.0,
    ) -> FundDataResult:
        code = _normalized_fund_code(fund_code)
        deadline_at = time.monotonic() + request_timeout_seconds
        attempts: list[dict[str, Any]] = []
        cached = self._read_holdings(code, limit=limit)
        if cached and not force_refresh:
            attempts.append({"provider": "cache", "status": "hit"})
            return FundDataResult(
                rows=cached,
                cache_hit=True,
                cache_mode="cache",
                coverage=_holdings_coverage(code, cached, attempts),
            )
        attempts.append({"provider": "cache", "status": "miss" if not cached else "bypass"})

        rows = self._try_fetch_holdings(
            code=code,
            attempts=attempts,
            deadline_at=deadline_at,
            upstream_timeout_seconds=upstream_timeout_seconds,
        )
        if rows:
            self._write_holdings(rows)
            selected = rows[:limit]
            return FundDataResult(
                rows=selected,
                cache_hit=False,
                cache_mode="upstream",
                coverage=_holdings_coverage(code, selected, attempts),
            )
        if cached:
            attempts.append({"provider": "cache", "status": "stale_hit"})
            return FundDataResult(
                rows=cached,
                cache_hit=True,
                cache_mode="stale_cache",
                coverage=_holdings_coverage(code, cached, attempts),
                status="degraded",
                warning=_fund_warning("holdings", attempts),
            )
        return FundDataResult(
            rows=[],
            cache_hit=False,
            cache_mode="upstream",
            coverage=_holdings_coverage(code, [], attempts),
            status="degraded",
            warning=_fund_warning("holdings", attempts),
        )

    def _initialize(self) -> None:
        with self._lock, self._connection:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS fund_profile_rows (
                  fund_code TEXT NOT NULL,
                  provider TEXT NOT NULL,
                  fund_name TEXT NOT NULL,
                  fund_type TEXT NOT NULL,
                  currency TEXT NOT NULL,
                  as_of_date TEXT,
                  source TEXT NOT NULL,
                  source_url TEXT,
                  retrieved_at TEXT,
                  data_quality TEXT,
                  refreshed_at TEXT NOT NULL,
                  PRIMARY KEY (fund_code, provider)
                );

                CREATE TABLE IF NOT EXISTS fund_holding_rows (
                  fund_code TEXT NOT NULL,
                  as_of_date TEXT NOT NULL,
                  provider TEXT NOT NULL,
                  rank INTEGER,
                  stock_code TEXT NOT NULL,
                  stock_name TEXT NOT NULL,
                  weight REAL NOT NULL,
                  holding_change REAL,
                  industry TEXT,
                  market TEXT,
                  ts_code TEXT,
                  source TEXT NOT NULL,
                  source_url TEXT,
                  retrieved_at TEXT,
                  refreshed_at TEXT NOT NULL,
                  PRIMARY KEY (fund_code, as_of_date, provider, stock_code)
                );

                CREATE TABLE IF NOT EXISTS fund_holding_coverage (
                  fund_code TEXT NOT NULL,
                  as_of_date TEXT NOT NULL,
                  provider TEXT NOT NULL,
                  row_count INTEGER NOT NULL,
                  refreshed_at TEXT NOT NULL,
                  PRIMARY KEY (fund_code, as_of_date, provider)
                );
                """
            )

    def _read_profile(self, fund_code: str) -> list[dict[str, Any]]:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT fund_code, fund_name, fund_type, currency, source, as_of_date,
                       provider, source_url, retrieved_at, data_quality
                FROM fund_profile_rows
                WHERE fund_code = ?
                ORDER BY CASE provider
                    WHEN 'tushare' THEN 0
                    WHEN 'eastmoney' THEN 1
                    ELSE 2
                  END,
                  refreshed_at DESC
                LIMIT 1
                """,
                (fund_code,),
            ).fetchone()
        return [_profile_row_payload(row)] if row is not None else []

    def _read_holdings(self, fund_code: str, *, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            latest = self._connection.execute(
                """
                SELECT as_of_date, provider
                FROM fund_holding_rows
                WHERE fund_code = ?
                GROUP BY as_of_date, provider
                ORDER BY as_of_date DESC,
                  CASE provider
                    WHEN 'tushare' THEN 0
                    WHEN 'eastmoney' THEN 1
                    ELSE 2
                  END
                LIMIT 1
                """,
                (fund_code,),
            ).fetchone()
            if latest is None:
                return []
            rows = self._connection.execute(
                """
                SELECT fund_code, as_of_date, stock_code, stock_name, weight, source,
                       rank, holding_change, industry, market, ts_code, provider,
                       source_url, retrieved_at
                FROM fund_holding_rows
                WHERE fund_code = ?
                  AND as_of_date = ?
                  AND provider = ?
                ORDER BY COALESCE(rank, 999999), stock_code
                LIMIT ?
                """,
                (fund_code, latest["as_of_date"], latest["provider"], limit),
            ).fetchall()
        return [_holding_row_payload(row) for row in rows]

    def _try_fetch_profile(
        self,
        *,
        code: str,
        attempts: list[dict[str, Any]],
        deadline_at: float,
        upstream_timeout_seconds: float,
    ) -> list[dict[str, Any]]:
        try:
            rows = self._fetch_tushare_profile(
                code=code,
                deadline_at=deadline_at,
                upstream_timeout_seconds=upstream_timeout_seconds,
            )
        except GatewayError as error:
            attempts.append(_failed_attempt("tushare", error))
        else:
            attempts.append({"provider": "tushare", "status": "ok"})
            return rows

        try:
            rows = self._fetch_eastmoney_profile(
                code=code,
                deadline_at=deadline_at,
                upstream_timeout_seconds=upstream_timeout_seconds,
            )
        except GatewayError as error:
            attempts.append(_failed_attempt("eastmoney", error))
        else:
            attempts.append({"provider": "eastmoney", "status": "ok"})
            return rows
        return []

    def _try_fetch_holdings(
        self,
        *,
        code: str,
        attempts: list[dict[str, Any]],
        deadline_at: float,
        upstream_timeout_seconds: float,
    ) -> list[dict[str, Any]]:
        try:
            rows = self._fetch_tushare_holdings(
                code=code,
                deadline_at=deadline_at,
                upstream_timeout_seconds=upstream_timeout_seconds,
            )
        except GatewayError as error:
            attempts.append(_failed_attempt("tushare", error))
        else:
            attempts.append({"provider": "tushare", "status": "ok"})
            return rows

        try:
            rows = self._fetch_eastmoney_holdings(
                code=code,
                deadline_at=deadline_at,
                upstream_timeout_seconds=upstream_timeout_seconds,
            )
        except GatewayError as error:
            attempts.append(_failed_attempt("eastmoney", error))
        else:
            attempts.append({"provider": "eastmoney", "status": "ok"})
            return rows
        return []

    def _fetch_tushare_profile(
        self,
        *,
        code: str,
        deadline_at: float,
        upstream_timeout_seconds: float,
    ) -> list[dict[str, Any]]:
        provider = _provider(self._providers, "tushare")
        for ts_code in _tushare_fund_candidates(code):
            _raise_if_deadline_expired(deadline_at)
            response = _run_with_timeout(
                lambda ts_code=ts_code: provider.fetch(
                    "fund_basic",
                    {"ts_code": ts_code},
                    fields=_TUSHARE_PROFILE_FIELDS,
                ),
                timeout_seconds=min(upstream_timeout_seconds, _remaining_seconds(deadline_at)),
                timeout_message="Tushare fund profile request timed out.",
            )
            rows = response.rows()
            if rows:
                return [_tushare_profile_row(code, rows[0])]
        raise GatewayError(GatewayErrorCode.EMPTY_DATA, "Tushare fund profile returned no rows.")

    def _fetch_tushare_holdings(
        self,
        *,
        code: str,
        deadline_at: float,
        upstream_timeout_seconds: float,
    ) -> list[dict[str, Any]]:
        provider = _provider(self._providers, "tushare")
        all_rows: list[dict[str, Any]] = []
        for ts_code in _tushare_fund_candidates(code):
            _raise_if_deadline_expired(deadline_at)
            response = _run_with_timeout(
                lambda ts_code=ts_code: provider.fetch(
                    "fund_portfolio",
                    {"ts_code": ts_code},
                    fields=_TUSHARE_HOLDING_FIELDS,
                ),
                timeout_seconds=min(upstream_timeout_seconds, _remaining_seconds(deadline_at)),
                timeout_message="Tushare fund holdings request timed out.",
            )
            all_rows.extend(response.rows())
            if all_rows:
                break
        normalized = _tushare_holding_rows(code, all_rows)
        if not normalized:
            raise GatewayError(GatewayErrorCode.EMPTY_DATA, "Tushare fund holdings returned no rows.")
        return normalized[:_HOLDING_FETCH_LIMIT]

    def _fetch_eastmoney_profile(
        self,
        *,
        code: str,
        deadline_at: float,
        upstream_timeout_seconds: float,
    ) -> list[dict[str, Any]]:
        provider = _provider(self._providers, "eastmoney")
        _raise_if_deadline_expired(deadline_at)
        response = _run_with_timeout(
            lambda: provider.fetch("fund_profile", {"fund_code": code}, fields=None),
            timeout_seconds=min(upstream_timeout_seconds, _remaining_seconds(deadline_at)),
            timeout_message="EastMoney fund profile request timed out.",
        )
        rows = [_normalized_profile_row(code, row, default_provider="eastmoney") for row in response.rows()]
        if not rows:
            raise GatewayError(GatewayErrorCode.EMPTY_DATA, "EastMoney fund profile returned no rows.")
        return rows[:1]

    def _fetch_eastmoney_holdings(
        self,
        *,
        code: str,
        deadline_at: float,
        upstream_timeout_seconds: float,
    ) -> list[dict[str, Any]]:
        provider = _provider(self._providers, "eastmoney")
        _raise_if_deadline_expired(deadline_at)
        response = _run_with_timeout(
            lambda: provider.fetch(
                "fund_holdings",
                {"fund_code": code, "limit": _HOLDING_FETCH_LIMIT},
                fields=None,
            ),
            timeout_seconds=min(upstream_timeout_seconds, _remaining_seconds(deadline_at)),
            timeout_message="EastMoney fund holdings request timed out.",
        )
        rows = [_normalized_holding_row(code, row, default_provider="eastmoney") for row in response.rows()]
        rows = [row for row in rows if row.get("stock_code") and row.get("weight") is not None]
        if not rows:
            raise GatewayError(GatewayErrorCode.EMPTY_DATA, "EastMoney fund holdings returned no rows.")
        return rows[:_HOLDING_FETCH_LIMIT]

    def _write_profiles(self, rows: list[dict[str, Any]]) -> None:
        now = _utc_now()
        with self._lock, self._connection:
            for row in rows:
                self._connection.execute(
                    """
                    INSERT INTO fund_profile_rows (
                      fund_code, provider, fund_name, fund_type, currency, as_of_date,
                      source, source_url, retrieved_at, data_quality, refreshed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(fund_code, provider)
                    DO UPDATE SET
                      fund_name = excluded.fund_name,
                      fund_type = excluded.fund_type,
                      currency = excluded.currency,
                      as_of_date = excluded.as_of_date,
                      source = excluded.source,
                      source_url = excluded.source_url,
                      retrieved_at = excluded.retrieved_at,
                      data_quality = excluded.data_quality,
                      refreshed_at = excluded.refreshed_at
                    """,
                    (
                        row["fund_code"],
                        row["provider"],
                        row["fund_name"],
                        row["fund_type"],
                        row["currency"],
                        row.get("as_of_date"),
                        row["source"],
                        row.get("source_url"),
                        row.get("retrieved_at"),
                        row.get("data_quality"),
                        now,
                    ),
                )

    def _write_holdings(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        now = _utc_now()
        coverage: dict[tuple[str, str, str], int] = {}
        coverage_keys = {(row["fund_code"], row["as_of_date"], row["provider"]) for row in rows}
        with self._lock, self._connection:
            for fund_code, as_of_date, provider in coverage_keys:
                self._connection.execute(
                    """
                    DELETE FROM fund_holding_rows
                    WHERE fund_code = ?
                      AND as_of_date = ?
                      AND provider = ?
                    """,
                    (fund_code, as_of_date, provider),
                )
            for row in rows:
                coverage_key = (row["fund_code"], row["as_of_date"], row["provider"])
                coverage[coverage_key] = coverage.get(coverage_key, 0) + 1
                self._connection.execute(
                    """
                    INSERT INTO fund_holding_rows (
                      fund_code, as_of_date, provider, rank, stock_code, stock_name,
                      weight, holding_change, industry, market, ts_code, source,
                      source_url, retrieved_at, refreshed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(fund_code, as_of_date, provider, stock_code)
                    DO UPDATE SET
                      rank = excluded.rank,
                      stock_name = excluded.stock_name,
                      weight = excluded.weight,
                      holding_change = excluded.holding_change,
                      industry = excluded.industry,
                      market = excluded.market,
                      ts_code = excluded.ts_code,
                      source = excluded.source,
                      source_url = excluded.source_url,
                      retrieved_at = excluded.retrieved_at,
                      refreshed_at = excluded.refreshed_at
                    """,
                    (
                        row["fund_code"],
                        row["as_of_date"],
                        row["provider"],
                        row.get("rank"),
                        row["stock_code"],
                        row["stock_name"],
                        row["weight"],
                        row.get("holding_change"),
                        row.get("industry"),
                        row.get("market"),
                        row.get("ts_code"),
                        row["source"],
                        row.get("source_url"),
                        row.get("retrieved_at"),
                        now,
                    ),
                )
            for (fund_code, as_of_date, provider), row_count in coverage.items():
                self._connection.execute(
                    """
                    INSERT INTO fund_holding_coverage (
                      fund_code, as_of_date, provider, row_count, refreshed_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(fund_code, as_of_date, provider)
                    DO UPDATE SET row_count = excluded.row_count, refreshed_at = excluded.refreshed_at
                    """,
                    (fund_code, as_of_date, provider, row_count, now),
                )


def _profile_row_payload(row: sqlite3.Row) -> dict[str, Any]:
    return _drop_empty(
        {
            "fund_code": row["fund_code"],
            "fund_name": row["fund_name"],
            "fund_type": row["fund_type"],
            "currency": row["currency"],
            "source": row["source"],
            "as_of_date": row["as_of_date"],
            "provider": row["provider"],
            "source_url": row["source_url"],
            "retrieved_at": row["retrieved_at"],
            "data_quality": row["data_quality"],
        }
    )


def _holding_row_payload(row: sqlite3.Row) -> dict[str, Any]:
    return _drop_empty(
        {
            "fund_code": row["fund_code"],
            "as_of_date": row["as_of_date"],
            "stock_code": row["stock_code"],
            "stock_name": row["stock_name"],
            "weight": row["weight"],
            "source": row["source"],
            "rank": row["rank"],
            "holding_change": row["holding_change"],
            "industry": row["industry"],
            "market": row["market"],
            "ts_code": row["ts_code"],
            "provider": row["provider"],
            "source_url": row["source_url"],
            "retrieved_at": row["retrieved_at"],
        }
    )


def _profile_coverage(
    fund_code: str,
    rows: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "requested_fund_code": fund_code,
        "as_of_date": _rows_as_of_date(rows),
        "provider_attempts": attempts,
    }


def _holdings_coverage(
    fund_code: str,
    rows: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "requested_fund_code": fund_code,
        "returned_holding_count": len(rows),
        "as_of_date": _rows_as_of_date(rows),
        "provider_attempts": attempts,
    }


def _fund_warning(kind: str, attempts: list[dict[str, Any]]) -> dict[str, Any]:
    provider_attempts = [attempt for attempt in attempts if attempt["provider"] != "cache"]
    code = GatewayErrorCode.PROVIDER_UNAVAILABLE.value
    if any(attempt.get("reason") == GatewayErrorCode.REQUEST_TIMEOUT.value for attempt in provider_attempts):
        code = GatewayErrorCode.REQUEST_TIMEOUT.value
    target = "profile" if kind == "profile" else "holdings"
    return {"code": code, "message": f"Unable to fetch fund {target}."}


def _failed_attempt(provider: str, error: GatewayError) -> dict[str, Any]:
    return {"provider": provider, "status": "failed", "reason": error.code.value}


def _provider(providers: dict[str, ExternalDataProvider], name: str) -> ExternalDataProvider:
    provider = providers.get(name)
    if provider is None:
        raise GatewayError(GatewayErrorCode.PROVIDER_UNAVAILABLE, f"Provider is not registered: {name}")
    return provider


def _normalized_fund_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        raise ValueError("fund_code is required")
    base = text.split(".", 1)[0]
    if not base or not base.isalnum():
        raise ValueError("fund_code is invalid")
    return base


def _tushare_fund_candidates(fund_code: str) -> list[str]:
    base = _normalized_fund_code(fund_code)
    candidates = []
    if base.startswith(("5", "6", "9")):
        candidates.append(f"{base}.SH")
    elif base.startswith(("0", "1", "2", "3")):
        candidates.append(f"{base}.SZ")
    candidates.append(f"{base}.OF")
    seen: set[str] = set()
    unique = []
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        unique.append(candidate)
    return unique


def _tushare_profile_row(fund_code: str, row: dict[str, Any]) -> dict[str, Any]:
    return _drop_empty(
        {
            "fund_code": fund_code,
            "fund_name": row.get("name") or f"Tushare Fund {fund_code}",
            "fund_type": row.get("fund_type") or row.get("type") or "fund",
            "currency": "CNY",
            "source": "tushare",
            "as_of_date": _iso_date(row.get("found_date") or row.get("list_date")),
            "provider": "tushare",
            "source_url": "tushare://fund_basic",
            "retrieved_at": _utc_now(),
            "data_quality": "fresh",
        }
    )


def _tushare_holding_rows(fund_code: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    latest_end_date = max((_compact_date_text(row.get("end_date")) for row in rows), default="")
    latest_rows = [row for row in rows if _compact_date_text(row.get("end_date")) == latest_end_date]
    ranked = sorted(
        latest_rows,
        key=lambda row: _holding_sort_weight(row.get("stk_mkv_ratio") or row.get("mkv")),
        reverse=True,
    )
    normalized = []
    for rank, row in enumerate(ranked, start=1):
        stock_code = _stock_code(row.get("symbol") or row.get("stk_code") or row.get("stock_code"))
        weight = _ratio(row.get("stk_mkv_ratio") or row.get("weight"))
        if not stock_code or weight is None:
            continue
        normalized.append(
            _drop_empty(
                {
                    "fund_code": fund_code,
                    "as_of_date": _iso_date(latest_end_date),
                    "stock_code": stock_code,
                    "stock_name": row.get("stk_name") or row.get("stock_name") or row.get("name") or stock_code,
                    "weight": weight,
                    "source": "tushare",
                    "rank": rank,
                    "holding_change": 0.0,
                    "provider": "tushare",
                    "source_url": "tushare://fund_portfolio",
                }
            )
        )
    return normalized


def _normalized_profile_row(fund_code: str, row: dict[str, Any], *, default_provider: str) -> dict[str, Any]:
    provider = str(row.get("provider") or default_provider)
    return _drop_empty(
        {
            "fund_code": fund_code,
            "fund_name": row.get("fund_name") or row.get("name") or f"{provider.title()} Fund {fund_code}",
            "fund_type": row.get("fund_type") or row.get("type") or "fund",
            "currency": row.get("currency") or "CNY",
            "source": row.get("source") or provider,
            "as_of_date": _iso_date(row.get("as_of_date")),
            "provider": provider,
            "source_url": row.get("source_url"),
            "retrieved_at": row.get("retrieved_at"),
            "data_quality": row.get("data_quality"),
        }
    )


def _normalized_holding_row(fund_code: str, row: dict[str, Any], *, default_provider: str) -> dict[str, Any]:
    provider = str(row.get("provider") or default_provider)
    return _drop_empty(
        {
            "fund_code": fund_code,
            "as_of_date": _iso_date(row.get("as_of_date")),
            "stock_code": _stock_code(row.get("stock_code") or row.get("symbol")),
            "stock_name": row.get("stock_name") or row.get("name"),
            "weight": _ratio(row.get("weight")),
            "source": row.get("source") or provider,
            "rank": _int_or_none(row.get("rank")),
            "holding_change": _ratio(row.get("holding_change")),
            "industry": row.get("industry"),
            "market": row.get("market"),
            "ts_code": row.get("ts_code"),
            "provider": provider,
            "source_url": row.get("source_url"),
            "retrieved_at": row.get("retrieved_at"),
        }
    )


def _rows_as_of_date(rows: list[dict[str, Any]]) -> Any:
    if not rows:
        return None
    return rows[0].get("as_of_date")


def _stock_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if "." in text:
        text = text.split(".", 1)[0]
    return "".join(character for character in text if character.isalnum())


def _ratio(value: Any) -> float | None:
    number = _number(value)
    if number is None:
        return None
    if abs(number) > 1:
        number = number / 100.0
    return round(number, 6)


def _holding_sort_weight(value: Any) -> float:
    return _ratio(value) or 0.0


def _number(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    if isinstance(value, str):
        value = value.strip().replace("%", "")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    if value in (None, "", "-"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _iso_date(value: Any) -> Any:
    if value in (None, ""):
        return value
    text = str(value).strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    if len(text) == 8 and text.isdigit():
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
    return text


def _compact_date_text(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10].replace("-", "")
    if len(text) == 8 and text.isdigit():
        return text
    return text


def _drop_empty(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in (None, "")}


def _raise_if_deadline_expired(deadline_at: float) -> None:
    if _remaining_seconds(deadline_at) <= 0:
        raise GatewayError(GatewayErrorCode.REQUEST_TIMEOUT, "Fund data request timed out before completion.")


def _remaining_seconds(deadline_at: float) -> float:
    return max(0.0, deadline_at - time.monotonic())


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
        raise GatewayError(GatewayErrorCode.PROVIDER_UNAVAILABLE, str(result)) from result
    return result


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
